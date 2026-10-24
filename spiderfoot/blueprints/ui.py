import logging

from flask import Blueprint, current_app, render_template, request

from spiderfoot import SpiderFootDb
from spiderfoot.__version__ import __version__
from spiderfoot.helpers import SpiderFootHelpers
from spiderfoot.services.preset_service import serialize_preset
from spiderfoot.services.ai_persistence import (
    fetch_models_for_scan, fetch_cached
)

ui_bp = Blueprint('ui', __name__)

log = logging.getLogger(f"spiderfoot.{__name__}")


def _get_db():
    """Create a SpiderFootDb handle using the current app config."""
    return SpiderFootDb(current_app.config['SF_CONFIG'])


def _build_scan_list():
    """Return (scans, stats) for the dashboard.

    scans is a list of dicts with keys:
        id, name, target, created, started, ended, status, num_results,
        type_counts, progress

    stats is a dict with keys: running, completed, findings, total_events,
        queued, scans_with_findings, modules_total, modules_needing_keys
    """
    try:
        dbh = _get_db()
        rows = dbh.scanInstanceList()
    except Exception as e:
        log.warning("Could not load scan list: %s", e)
        return [], {'running': 0, 'completed': 0, 'findings': 0,
                    'total_events': 0, 'queued': 0, 'scans_with_findings': 0,
                    'modules_total': 0, 'modules_needing_keys': 0}

    INLINE_TYPES = {
        'INTERNET_NAME': 'hosts',
        'IP_ADDRESS': 'IPs',
        'EMAILADDR': 'emails',
        'TCP_PORT_OPEN': 'ports',
    }

    scans = []
    running = 0
    completed = 0
    findings = 0
    total_events = 0

    for row in rows:
        scan = {
            'id': row[0],
            'name': row[1],
            'target': row[2],
            'created': row[3],
            'started': row[4],
            'ended': row[5],
            'status': row[6],
            'num_results': int(row[7] or 0),
            'type_counts': [],
            'progress': None,
        }
        scans.append(scan)

        status = (scan['status'] or '').upper()
        if status == 'RUNNING':
            running += 1
        elif status == 'FINISHED':
            completed += 1
        total_events += scan['num_results']

        # Inline type counts for scan row breakdown
        try:
            summary = dbh.scanResultSummary(scan['id'], 'type')
            counts = []
            for s in summary:
                etype = s[0]
                total = int(s[3] or 0)
                if etype in INLINE_TYPES and total > 0:
                    counts.append({'label': INLINE_TYPES[etype], 'count': total})
            scan['type_counts'] = sorted(counts, key=lambda x: -x['count'])[:3]
        except Exception:
            pass

        # Progress for running scans
        if status == 'RUNNING':
            try:
                mod_summary = dbh.scanResultSummary(scan['id'], 'module')
                scan['progress'] = min(95, max(5, len(mod_summary) * 3))
            except Exception:
                pass

    # Count correlations/findings across all scans
    try:
        for scan in scans:
            corrs = dbh.scanCorrelationList(scan['id'])
            if corrs:
                findings += len(corrs)
    except Exception:
        pass

    # Count modules and those needing keys
    sf_config = current_app.config.get('SF_CONFIG', {})
    modules_cfg = sf_config.get('__modules__', {})
    try:
        saved_config = dbh.configGet()
    except Exception:
        saved_config = {}

    modules_total = 0
    modules_needing_keys = 0
    for mod_name, mod_cfg in modules_cfg.items():
        if mod_name.startswith('sfp__stor_'):
            continue
        modules_total += 1
        opts = mod_cfg.get('opts', {})
        api_key_opts = [k for k in opts if any(
            term in k.lower() for term in ('api_key', 'apikey')
        )]
        if api_key_opts:
            has_key = False
            for opt_name in api_key_opts:
                val = saved_config.get(f'{mod_name}:{opt_name}', '') or opts.get(opt_name, '')
                if val:
                    has_key = True
                    break
            if not has_key:
                modules_needing_keys += 1

    stats = {
        'running': running,
        'completed': completed,
        'findings': findings,
        'total_events': total_events,
        'queued': 0,
        'scans_with_findings': sum(1 for s in scans if s['num_results'] > 0),
        'modules_total': modules_total,
        'modules_needing_keys': modules_needing_keys,
    }
    return scans, stats


@ui_bp.route('/')
def dashboard():
    scans, stats = _build_scan_list()
    has_running = any(
        (s.get('status') or '').upper() in ('RUNNING', 'STARTED', 'STARTING')
        for s in scans
    )
    poll_interval = '5s' if has_running else '30s'
    return render_template(
        'pages/dashboard.html',
        page_id='DASHBOARD',
        version=__version__,
        scans=scans,
        stats=stats,
        poll_interval=poll_interval,
    )


def _build_modules_data():
    """Build module metadata for the new-scan page.

    Returns:
        tuple: (modules, categories)
            modules    — dict keyed by module name, ready for Jinja2 tojson
            categories — sorted list of unique category strings
    """
    sf_config = current_app.config.get('SF_CONFIG', {})
    raw = sf_config.get('__modules__', {})

    # Load saved config to check which API keys are configured
    try:
        dbh = _get_db()
        saved_config = dbh.configGet()
    except Exception:
        saved_config = {}

    modules = {}
    category_set = set()

    for mod_name, mod_cfg in raw.items():
        # Skip internal storage modules
        if mod_name.startswith('sfp__stor_'):
            continue

        meta = mod_cfg.get('meta', {})
        use_cases = meta.get('useCases', [])
        cats = meta.get('categories', [])
        flags = meta.get('flags', [])
        category = cats[0] if cats else 'Uncategorised'
        category_set.add(category)

        # Default enabled state: module is in 'Footprint' use case
        enabled = 'Footprint' in use_cases

        # Detect local tools: modules with 'tool' flag or that run local binaries
        is_local_tool = 'tool' in flags or mod_name.startswith('sfp_tool_')

        # Check if module requires an API key and if it's configured
        opts = mod_cfg.get('opts', {})
        api_key_opts = [k for k in opts if any(
            term in k.lower() for term in ('api_key', 'apikey', 'api_id', 'accesstoken')
        )]
        requires_key = len(api_key_opts) > 0
        key_configured = False
        if requires_key:
            # Check saved config for any of the API key options
            for opt_name in api_key_opts:
                config_key = f'{mod_name}:{opt_name}'
                val = saved_config.get(config_key, '') or opts.get(opt_name, '')
                if val:
                    key_configured = True
                    break

        modules[mod_name] = {
            'name': meta.get('name', mod_name),
            'summary': meta.get('summary', mod_cfg.get('descr', '')),
            'category': category,
            'useCases': use_cases,
            'enabled': enabled,
            'requiresKey': requires_key,
            'keyConfigured': key_configured,
            'isLocalTool': is_local_tool,
            'privateIpOk': mod_name in SpiderFootHelpers.PRIVATE_IP_COMPATIBLE_MODULES,
        }

    categories = sorted(category_set)
    return modules, categories



# Portuguese explanations for the module help page. Original module IDs,
# option keys and event codes are intentionally kept unchanged.
_MODULE_DESCRIPTIONS_PT = {
    'sfp_abstractapi': 'Consulta a AbstractAPI para obter informações sobre domínios, telefones e endereços IP. Os dados retornados dependem das APIs e dos créditos disponíveis.',
    'sfp_abusech': 'Verifica se um host, domínio, endereço IP ou bloco de rede aparece como malicioso nas fontes públicas do Abuse.ch.',
    'sfp_abuseipdb': 'Consulta a reputação de um endereço IP no AbuseIPDB para identificar denúncias públicas de abuso. Configure uma chave de API em Settings quando necessário.',
    'sfp_abusix': 'Verifica se um endereço IP ou bloco de rede consta em listas de reputação e bloqueio de e-mail do Abusix.',
    'sfp_accounts': 'Procura perfis que possam estar associados a um nome de usuário em serviços e sites públicos. Correspondências precisam ser verificadas manualmente.',
    'sfp_adblock': 'Compara URLs encontradas com uma lista de filtros para indicar quais páginas podem ser bloqueadas por regras semelhantes às do AdBlock Plus.',
    'sfp_adguard_dns': 'Verifica se um host pode ser bloqueado pelo serviço de filtragem AdGuard DNS.',
    'sfp_ahmia': 'Pesquisa menções ao alvo no mecanismo Ahmia, que indexa conteúdo acessível pela rede Tor. A disponibilidade e os resultados podem variar.',
    'sfp_alienvault': 'Consulta informações públicas de inteligência de ameaças no AlienVault Open Threat Exchange (OTX).',
    'sfp_alienvaultiprep': 'Consulta a reputação de um endereço IP no banco de reputação do AlienVault OTX.',
    'sfp_apple_itunes': 'Pesquisa aplicativos móveis relacionados ao alvo na loja Apple iTunes/App Store.',
    'sfp_archiveorg': 'Procura versões históricas de arquivos e páginas no Internet Archive/Wayback Machine. A existência de uma captura depende do arquivo histórico.',
    'sfp_arin': 'Consulta o registro regional ARIN para obter dados públicos de alocação de endereços IP e informações de contato associadas.',
}

_TEXT_REPLACEMENTS_PT = [
    ('Look up domain, phone and IP address information from', 'Consulta informações de domínio, telefone e endereço IP em'),
    ('Check if a host/domain, IP address or netblock is malicious according to', 'Verifica se um host/domínio, endereço IP ou bloco de rede é considerado malicioso segundo'),
    ('Check if an IP address is malicious according to', 'Verifica se um endereço IP é considerado malicioso segundo'),
    ('Check if a netblock or IP address is in the', 'Verifica se um bloco de rede ou endereço IP consta na'),
    ('Check if linked pages would be blocked by', 'Verifica se páginas vinculadas seriam bloqueadas por'),
    ('Check if a host would be blocked by', 'Verifica se um host seria bloqueado por'),
    ('Search Tor', 'Pesquisa na rede Tor usando'),
    ("'Ahmia' search engine for mentions of the target.", "o mecanismo de busca 'Ahmia' por menções ao alvo."),
    ('Obtain information from', 'Obtém informações de'),
    ('Queries', 'Consulta'), ('registry for contact information.', 'o registro para encontrar informações públicas de contato.'),
    ('Search', 'Pesquisa'), ('for mentions of the target.', 'por menções ao alvo.'),
    ('Search Apple iTunes for mobile apps.', 'Pesquisa aplicativos móveis na Apple iTunes/App Store.'),
    ('Identifies historic versions of interesting files/pages from the Wayback Machine.', 'Localiza versões históricas de arquivos e páginas na Wayback Machine.'),
    ('Look for possible associated accounts on over 500 social and other websites such as Instagram, Reddit, etc.', 'Procura contas possivelmente associadas ao nome de usuário em centenas de sites e redes sociais. Os resultados são indícios, não confirmação de identidade.'),
    ('domain', 'domínio'), ('phone', 'telefone'), ('IP address', 'endereço IP'), ('host/domain', 'host/domínio'),
    ('netblock', 'bloco de rede'), ('malicious', 'malicioso'), ('according to', 'segundo'),
    ('blacklist', 'lista de bloqueio'), ('blacklist.', 'lista de bloqueio.'), ('information', 'informações'),
    ('historic versions', 'versões históricas'), ('interesting files/pages', 'arquivos/páginas relevantes'),
    ('mobile apps', 'aplicativos móveis'), ('social and other websites', 'redes sociais e outros sites'),
]

_EVENT_HELP_PT = {
    'DOMAIN_NAME': 'Nome de domínio, por exemplo example.org.',
    'INTERNET_NAME': 'Nome de host ou nome de internet associado ao alvo.',
    'IP_ADDRESS': 'Endereço IPv4.', 'IPV6_ADDRESS': 'Endereço IPv6.',
    'NETBLOCK_OWNER': 'Organização ou entidade associada a um bloco de endereços IP.',
    'EMAILADDR': 'Endereço de e-mail encontrado em fontes públicas.',
    'PHONE_NUMBER': 'Número de telefone encontrado ou associado ao alvo.',
    'HUMAN_NAME': 'Nome de pessoa identificado em fontes públicas.',
    'SOCIAL_MEDIA': 'Perfil ou referência em rede social.',
    'GEOINFO': 'Informação geográfica associada a um evento ou entidade.',
    'PHYSICAL_COORDINATES': 'Coordenadas geográficas.',
    'TCP_PORT_OPEN': 'Porta TCP identificada como aberta.',
    'LINKED_URL_INTERNAL': 'URL do mesmo site ou domínio relacionado.',
    'LINKED_URL_EXTERNAL': 'URL de outro site vinculada à página analisada.',
    'URL_WEB_FRAMEWORK': 'Indício de framework ou tecnologia web.',
    'URL_JAVASCRIPT': 'Arquivo ou recurso JavaScript relacionado à página.',
    'INTERESTING_FILE': 'Arquivo considerado relevante por regras do módulo.',
    'DARKNET_MENTION_URL': 'URL de menção localizada em fonte da rede Tor/dark web.',
    'DARKNET_MENTION_CONTENT': 'Conteúdo de menção localizado em fonte da rede Tor/dark web.',
    'MALICIOUS_IPADDR': 'Endereço IP marcado como malicioso por uma fonte de reputação.',
}

_GROUPS_PT = {
    'Footprint': 'Reconhecimento inicial', 'Investigate': 'Investigação',
    'Passive': 'Coleta passiva', 'All': 'Todos os grupos',
    'Search': 'Pesquisa', 'Other': 'Outros',
}

_OPTION_HELP_PT = {
    'api_key': 'Chave de API do provedor. Obtenha-a no site oficial do serviço e configure-a em Settings; não compartilhe a chave.',
    'api_key_name': 'Nome da chave de API usada pelo provedor. Consulte a documentação oficial do serviço.',
    'timeout': 'Tempo máximo de espera por resposta, normalmente em segundos. Valores maiores podem tornar a execução mais lenta.',
    'useragent': 'Identificação do cliente enviada nas requisições HTTP.',
    'max_pages': 'Número máximo de páginas que o módulo tentará consultar.',
    'maxdepth': 'Profundidade máxima de navegação a partir da página inicial.',
    'cacheperiod': 'Tempo de validade do cache antes de consultar novamente a fonte.',
    'fetchlinks': 'Quando ativado, tenta obter links encontrados nas páginas consultadas.',
    'fullnames': 'Quando ativado, tenta considerar nomes completos, quando suportado pela fonte.',
    'checkaffiliates': 'Verifica entidades ou endereços relacionados/afiliados quando o módulo oferece esse recurso.',
    'checkcohosts': 'Verifica outros hosts que compartilham a mesma infraestrutura, quando suportado.',
    'checknetblocks': 'Inclui verificações de blocos de rede relacionados.',
    'checksubnets': 'Inclui verificações de sub-redes relacionadas.',
    'intfiles': 'Inclui arquivos interessantes na verificação histórica, quando disponível.',
    'passwordpages': 'Inclui páginas relacionadas a senhas no escopo do módulo; isso não significa revelar senhas.',
    'formpages': 'Inclui páginas que contêm formulários, quando suportado.',
    'flashpages': 'Inclui recursos Flash históricos, quando suportado.',
    'javapages': 'Inclui páginas ou recursos Java históricos, quando suportado.',
    'staticpages': 'Inclui páginas e recursos estáticos.',
    'uploadpages': 'Inclui páginas relacionadas a uploads, quando suportado.',
    'webframeworkpages': 'Inclui páginas que indicam frameworks web.',
    'javascriptpages': 'Inclui páginas ou recursos JavaScript.',
    'blocklist': 'URL da lista de regras usada pelo módulo. Altere somente se souber qual lista é compatível.',
}

def _module_description_pt(name, description):
    if name in _MODULE_DESCRIPTIONS_PT:
        return _MODULE_DESCRIPTIONS_PT[name]
    text = str(description or '').strip()
    for old, new in _TEXT_REPLACEMENTS_PT:
        text = text.replace(old, new)
    if text and text != str(description or '').strip():
        return text
    if text:
        return (f'Este módulo realiza a seguinte consulta: {text} '
                'Use-o quando o alvo produzir um tipo de evento compatível. '
                'Os resultados dependem da fonte, das opções e das credenciais configuradas.')
    return ('O módulo não fornece uma descrição detalhada nos metadados desta instalação. '
            'Consulte o nome do módulo, os eventos de entrada/saída e as opções abaixo antes de ativá-lo.')

def _option_description_pt(key, value):
    key_lower = str(key).lower()
    if key_lower in _OPTION_HELP_PT:
        return _OPTION_HELP_PT[key_lower]
    for known, help_text in _OPTION_HELP_PT.items():
        if key_lower.endswith(known) or known in key_lower:
            return help_text
    if 'api' in key_lower and ('key' in key_lower or 'token' in key_lower or 'secret' in key_lower):
        return 'Credencial para acessar a API do provedor. Obtenha-a na fonte oficial e armazene-a em Settings; nunca a publique em relatórios.'
    if any(word in key_lower for word in ('limit', 'max', 'count', 'pages', 'results')):
        return 'Define um limite de itens consultados ou processados. Aumentar o valor pode ampliar o tempo de execução e o número de requisições.'
    if any(word in key_lower for word in ('timeout', 'period', 'delay', 'sleep', 'cache')):
        return 'Controla o tempo de espera, intervalo ou cache do módulo. Consulte a documentação da fonte para escolher um valor adequado.'
    if any(word in key_lower for word in ('enable', 'check', 'fetch', 'include', 'use', 'verify')):
        return 'Opção de ativação: True geralmente habilita o comportamento e False o desabilita. Confirme o efeito específico na documentação do módulo.'
    return ('Parâmetro específico do módulo. A chave original foi mantida para compatibilidade; '
            'como os metadados não fornecem uma descrição, consulte a documentação ou o código deste módulo antes de alterar o valor.')

@ui_bp.route('/module-help')
def module_help():
    """Referência de módulos com explicações em português e IDs originais."""
    cfg = current_app.config.get('SF_CONFIG', {})
    raw_modules = cfg.get('__modules__', {}) or {}
    modules = []
    for name in sorted(raw_modules):
        if '__' in name:
            continue
        meta = raw_modules.get(name, {}) or {}
        opts = meta.get('opts', {}) or {}
        consumes = meta.get('consumes', []) or []
        provides = meta.get('provides', []) or []
        groups = meta.get('group', []) or []
        options = []
        for key, val in opts.items():
            if isinstance(val, dict):
                default = val.get('default', '')
                raw_desc = val.get('description') or val.get('type') or ''
            else:
                default = val
                raw_desc = ''
            options.append({
                'key': key,
                'default': default,
                'description': _option_description_pt(key, val),
                'raw_description': raw_desc,
            })
        modules.append({
            'name': name,
            'description': meta.get('descr', 'Sem descrição fornecida pelo módulo.'),
            'description_pt': _module_description_pt(name, meta.get('descr', '')),
            'opts': opts,
            'options': options,
            'consumes': consumes,
            'provides': provides,
            'groups': groups,
            'groups_pt': [_GROUPS_PT.get(str(g), str(g)) for g in groups],
            'consume_help': [(str(ev), _EVENT_HELP_PT.get(str(ev), 'Tipo de dado recebido pelo módulo; o identificador original foi mantido.')) for ev in consumes],
            'provide_help': [(str(ev), _EVENT_HELP_PT.get(str(ev), 'Tipo de dado produzido pelo módulo; o identificador original foi mantido.')) for ev in provides],
        })
    return render_template('pages/module_help.html', page_id='MODULEHELP',
                           version=__version__, modules=modules)

@ui_bp.route('/newscan')
def newscan():
    modules, categories = _build_modules_data()
    dbh = _get_db()
    presets = [serialize_preset(r) for r in dbh.presetList()]
    return render_template(
        'pages/scan_new.html',
        page_id='NEWSCAN',
        version=__version__,
        modules=modules,
        categories=categories,
        presets=presets,
    )


@ui_bp.route('/scaninfo')
def scaninfo():
    scan_id = request.args.get('id', '')
    if not scan_id:
        return render_template(
            'pages/scan_results.html',
            page_id='SCANINFO',
            version=__version__,
            scan=None,
            event_summary=[],
            correlations=[],
            findings=[],
            total_events=0,
            critical_count=0,
            warning_count=0,
            module_count=0,
            ai_enabled=False,
            default_model='',
            fallback_model='',
            all_models=[],
            cached=[],
            cached_models=[],
            selected='',
            active_cache=None,
        )

    try:
        dbh = _get_db()
        row = dbh.scanInstanceGet(scan_id)
    except Exception as e:
        log.warning("Could not load scan instance %s: %s", scan_id, e)
        row = None

    if not row:
        return render_template(
            'pages/scan_results.html',
            page_id='SCANINFO',
            version=__version__,
            scan=None,
            event_summary=[],
            correlations=[],
            findings=[],
            total_events=0,
            critical_count=0,
            warning_count=0,
            module_count=0,
            ai_enabled=False,
            default_model='',
            fallback_model='',
            all_models=[],
            cached=[],
            cached_models=[],
            selected='',
            active_cache=None,
        )

    # row: (name, seed_target, created, started, ended, status)
    scan = {
        'id': scan_id,
        'name': row[0],
        'target': row[1],
        'created': row[2],
        'started': row[3],
        'ended': row[4],
        'status': row[5],
    }

    # Event summary by type: (type, event_descr, last_in, total, utotal)
    try:
        event_summary = dbh.scanResultSummary(scan_id, 'type')
    except Exception:
        event_summary = []

    # Correlations: (id, title, rule_id, rule_risk, rule_name, rule_descr, rule_logic, event_count)
    try:
        correlations = dbh.scanCorrelationList(scan_id)
    except Exception:
        correlations = []

    # Build findings from correlations
    findings = []
    for c in correlations:
        findings.append({
            'id': c[0],
            'title': c[1],
            'rule_id': c[2],
            'risk': c[3] or 'INFO',
            'rule_name': c[4],
            'description': c[5] or '',
            'event_count': c[7] if len(c) > 7 else 0,
        })

    # Counts
    total_events = sum(int(row[3] or 0) for row in event_summary)
    unique_events = sum(int(row[4] or 0) for row in event_summary)
    high_count = sum(1 for f in findings if (f['risk'] or '').upper() == 'HIGH')
    medium_count = sum(1 for f in findings if (f['risk'] or '').upper() == 'MEDIUM')
    low_count = sum(1 for f in findings if (f['risk'] or '').upper() == 'LOW')
    critical_count = high_count
    warning_count = medium_count

    # Module count from summary by module
    try:
        mod_summary = dbh.scanResultSummary(scan_id, 'module')
        module_count = len(mod_summary)
    except Exception:
        module_count = 0

    # Scan duration
    scan_duration = ''
    if scan.get('started') and scan.get('ended'):
        try:
            duration_secs = int(scan['ended']) - int(scan['started'])
            if duration_secs > 0:
                mins, secs = divmod(duration_secs, 60)
                scan_duration = f"{mins}m {secs}s" if mins else f"{secs}s"
        except (ValueError, TypeError):
            pass

    # AI summary context (always populated; fragment hides itself if disabled)
    ai_enabled = bool(current_app.config['SF_CONFIG'].get('_ai_enabled'))
    default_model = current_app.config['SF_CONFIG'].get(
        '_ai_default_model', 'minimax/minimax-m3.1-flash:free'
    )
    fallback_model = current_app.config['SF_CONFIG'].get(
        '_ai_fallback_model', 'openrouter/free'
    )
    all_models = [
        {"id": "moonshotai/kimi-k2.6", "label": "Kimi K2.6"},
        {"id": "z-ai/glm-5.1", "label": "GLM 5.1"},
        {"id": "minimax/minimax-m3.1-flash:free", "label": "MiniMax M3.1 Flash (free)"},
        {"id": "ollama/llama3.2:latest", "label": "Llama 3.2 (Ollama local)"},
        {"id": "openrouter/free",         "label": "Free Models Router"},
    ]
    allowed_models = {item['id'] for item in all_models}
    selected_model = request.args.get('model', default_model)
    if selected_model not in allowed_models:
        selected_model = default_model if default_model in allowed_models else all_models[0]['id']

    cache_by_model = {}
    try:
        cached_models = fetch_models_for_scan(
            dbh, scan_id=scan_id, kind='scan', target_id=''
        )
        for entry in all_models:
            saved = fetch_cached(
                dbh, scan_id=scan_id, kind='scan', target_id='',
                model_requested=entry['id'],
            )
            if saved and (saved.get('content') or '').strip():
                cache_by_model[entry['id']] = saved
        cached_models = [
            item for item in cached_models
            if item.get('model_requested') in cache_by_model
        ]
    except Exception as exc:
        log.warning('Could not load AI summary cache: %s', exc)
        cached_models = []
        cache_by_model = {}

    active_cache = cache_by_model.get(selected_model)

    return render_template(
        'pages/scan_results.html',
        page_id='SCANINFO',
        version=__version__,
        scan=scan,
        event_summary=event_summary,
        correlations=correlations,
        findings=findings,
        total_events=total_events,
        unique_events=unique_events,
        critical_count=critical_count,
        warning_count=warning_count,
        high_count=high_count,
        medium_count=medium_count,
        low_count=low_count,
        module_count=module_count,
        scan_duration=scan_duration,
        ai_enabled=ai_enabled,
        default_model=default_model,
        fallback_model=fallback_model,
        all_models=all_models,
        cached=cached_models,
        cached_models=cached_models,
        selected=selected_model,
        active_cache=active_cache,
        cache_by_model=cache_by_model,
    )


@ui_bp.route('/opts')
def settings():
    # Load saved config values for the default (General) section
    try:
        dbh = _get_db()
        saved_config = dbh.configGet()
    except Exception as e:
        log.warning("Could not load settings config: %s", e)
        saved_config = {}

    # Merge saved config over the live SF_CONFIG so defaults are available
    sf_config = current_app.config.get('SF_CONFIG', {})
    config = dict(sf_config)
    config.update(saved_config)

    return render_template(
        'pages/settings.html',
        page_id='SETTINGS',
        version=__version__,
        config=config,
    )
