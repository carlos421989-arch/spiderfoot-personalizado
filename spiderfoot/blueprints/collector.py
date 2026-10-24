# -*- coding: utf-8 -*-
"""Web integration for the ENEM/FATEC public-material collector."""
import os
import csv
import json
import math
import re
import signal
import subprocess
import sys
import threading
import uuid
import zipfile
from pathlib import Path
from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from io import BytesIO
from urllib.parse import urlparse

collector_bp = Blueprint('collector', __name__)

ENGINE_CHOICES = [
    ('duckduckgo', 'DuckDuckGo', True), ('brave', 'Brave Search', True), ('yandex', 'Yandex (página web)', True),
    ('google', 'Google', True), ('bing', 'Bing', True), ('yahoo', 'Yahoo', True),
    ('mojeek', 'Mojeek', True), ('wikipedia', 'Wikipedia', True), ('startpage', 'Startpage', True),
    ('swisscows', 'Swisscows', True), ('naver', 'Naver', True), ('gibiru', 'Gibiru', True),
    ('ecosia', 'Ecosia', True),
    ('youtube', 'YouTube (pesquisa e download opcional autorizado)', True), ('telegram_public', 'Telegram (páginas públicas e resultados indexados)', True),
    ('boardreader', 'Boardreader (fóruns)', True), ('archive_scholar', 'Internet Archive Scholar', True),
    ('aol', 'AOL Search', True), ('searchencrypt', 'Search Encrypt (disponibilidade a confirmar)', True),
    ('ask', 'Ask.com', True), ('wiki_com', 'Wiki.com', True),
    ('gigablast', 'Gigablast (disponibilidade a confirmar)', True), ('blackle', 'Blackle', True),
    ('baidu', 'Baidu', True), ('openverse', 'Openverse (mídia licenciada)', True),
    ('searxng', 'SearXNG (requer URL de instância)', False),
    ('fonte_curso_enem_gratuito', 'Curso Enem Gratuito (site)', True),
    ('fonte_brasil_escola_vestibular', 'Brasil Escola - Vestibular (site)', True),
    ('fonte_guia_estudante', 'Guia do Estudante - Abril (site)', True),
    ('fonte_inep_provas', 'INEP - Provas e Gabaritos (oficial)', True),
    ('onion_2fd6', 'Busca .onion - 2fd6cemt4g (Tor; nao verificado)', True),
    ('onion_tornetup', 'Busca .onion - tornetup (Tor; nao verificado)', True),
    ('onion_goscan24', 'Busca .onion - goscan24 (Tor; nao verificado)', True),
    ('onion_startpage', 'Busca .onion - startpage (Tor; nao verificado)', True),
    ('onion_torsearch', 'Busca .onion - torsearc (Tor; nao verificado)', True),
]

_lock = threading.Lock()


def _paths():
    project = Path(current_app.root_path).resolve().parent
    tool = project / 'tools' / 'Coletor_ENEM_FATEC'
    out = tool / 'resultados'
    return project, tool, out


def _state():
    if 'sf_enem_collector_state' not in current_app.extensions:
        current_app.extensions['sf_enem_collector_state'] = {'process': None, 'log_handle': None, 'log_path': None, 'started': None, 'run_id': None}
    return current_app.extensions['sf_enem_collector_state']


@collector_bp.route('/coletor-enem-fatec')
def page():
    # Restaurar a ultima lista de consultas enviada ao coletor apos reiniciar o servidor.
    _, _, output = _paths()
    latest = output / 'consultas_personalizadas_ultima_execucao.txt'
    saved_queries = ''
    if latest.is_file():
        try:
            saved_queries = latest.read_text(encoding='utf-8-sig')[:100000]
        except OSError:
            pass
    return render_template('pages/collector_enem_fatec.html', page_id='COLLECTOR', version=current_app.config.get('SF_VERSION', ''), engine_choices=ENGINE_CHOICES, saved_queries=saved_queries)


def _video_config(data):
    """Valida tambem parametros usados na execucao avulsa."""
    mode = str(data.get('telegram_video_modo', 'desativado')).strip().lower()
    if mode not in ('desativado', 'compactar', 'dividir'):
        raise ValueError('Selecione compactar, dividir ou desativado.')
    limit = float(data.get('telegram_video_limite_gb', 4.0))
    target = float(data.get('telegram_video_saida_gb', 3.5))
    if not (math.isfinite(limit) and math.isfinite(target) and 0.001 <= target < limit <= 100):
        raise ValueError('O tamanho de saida deve ser menor que o limite de acionamento (ambos entre 0,001 e 100 GB).')
    return mode, limit, target


@collector_bp.route('/api/collector/videos-processar', methods=['POST'])
def videos_processar():
    """Processa MP4 existentes em resultados/, sem redownload ou mudanca de historico."""
    data = request.get_json(silent=True) or {}
    try:
        mode, limit, target = _video_config(data)
        if mode == 'desativado':
            return jsonify({'error':'Selecione Compactar ou Dividir.'}), 400
    except (TypeError, ValueError, OverflowError) as exc:
        return jsonify({'error':str(exc)}), 400
    with _lock:
        state=_state()
        active=state.get('process')
        if active and active.poll() is None:
            return jsonify({'error':'Finalize a coleta antes de processar videos existentes.'}), 409
        previous=state.get('video_process')
        if previous and previous.poll() is None:
            return jsonify({'error':'Ja existe um processamento de videos em andamento.'}), 409
        _,tool,out=_paths()
        worker=tool/'video_telegram_v39.py'
        if not worker.is_file():
            return jsonify({'error':'Modulo video_telegram_v39.py nao encontrado.'}), 500
        out.mkdir(parents=True,exist_ok=True)
        logpath=out/'processamento_telegram_v39.log'
        cmd=[sys.executable,str(worker),'--pasta',str(out),'--modo',mode,
             '--limite-gb',str(limit),'--saida-gb',str(target)]
        kwargs={'cwd':str(tool),'stdout':None,'stderr':subprocess.STDOUT,'stdin':subprocess.DEVNULL}
        if os.name=='nt':kwargs['creationflags']=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
        else:kwargs['start_new_session']=True
        try:
            handle=open(logpath,'a',encoding='utf-8')
            try:
                handle.write('\nPROCESSAMENTO V39 INICIADO: modo=%s limite=%s saida=%s\n'%(mode,limit,target))
                handle.flush()
                kwargs['stdout']=handle
                proc=subprocess.Popen(cmd,**kwargs)
            finally:
                handle.close()
        except OSError as exc:
            return jsonify({'error':'Falha ao iniciar processamento: '+str(exc)}),500
        state.update({'video_process':proc,'video_log_path':str(logpath)})
    return jsonify({'ok':True,'pid':proc.pid})


@collector_bp.route('/api/collector/videos-cancelar', methods=['POST'])
def videos_cancelar():
    with _lock:
        process=_state().get('video_process')
        if not process or process.poll() is not None:
            return jsonify({'error':'Nao existe processamento ativo.'}),409
        try:
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                               capture_output=True,text=True,timeout=20)
            else:
                os.killpg(process.pid,signal.SIGTERM)
        except (OSError,subprocess.SubprocessError) as exc:
            return jsonify({'error':str(exc)}),500
    return jsonify({'ok':True,'message':'Cancelamento solicitado; o video original permanece intacto.'})


@collector_bp.route('/api/collector/status')
def status():
    state = _state()
    proc = state.get('process')
    code = proc.poll() if proc else None
    running = bool(proc and code is None)
    log_text = ''
    path = state.get('log_path')
    if path and Path(path).exists():
        try:
            lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
            log_text = '\n'.join(lines[-120:])
        except OSError:
            pass
    _, _, out = _paths()
    vidproc=state.get('video_process')
    videos_running=bool(vidproc and vidproc.poll() is None)
    videos_log=''
    if state.get('video_log_path') and Path(state['video_log_path']).is_file():
        try:
            videos_log='\n'.join(Path(state['video_log_path']).read_text(encoding='utf-8',errors='replace').splitlines()[-25:])
        except OSError:
            pass
    budget_status = None
    budget_path = out / 'limite_downloads_status.json'
    if budget_path.is_file():
        try:
            budget_status = json.loads(budget_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            pass
    type_status = None
    type_path = out / 'limites_por_tipo_status.json'
    if type_path.is_file():
        try:
            type_status = json.loads(type_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            pass
    # Indicadores originados dos arquivos JSON da execução. Não soma downloads antigos.
    def _budget_remaining(entry):
        if not isinstance(entry, dict):
            return None
        try:
            ceiling = float(entry.get('limite_gb', 0))
            used = float(entry.get('baixado_gb', 0))
        except (TypeError, ValueError, OverflowError):
            return None
        if not (0 < ceiling <= 10000 and 0 <= used < float('inf')):
            return None  # 0 = sem limite configurado, não calcular um saldo fictício.
        return {'limite_gb': ceiling, 'baixado_gb': used,
                'restante_gb': round(max(0, ceiling - used), 5),
                'percentual': round(min(100, max(0, used / ceiling * 100)), 2),
                'limite_atingido': bool(entry.get('limite_atingido', False)),
                'ciclo': int(entry.get('ciclo', 1)),
                'atualizado_em': entry.get('atualizado_em')}

    monitor = {'videos': _budget_remaining(budget_status), 'formatos': {}}
    if isinstance(type_status, dict):
        for kind, info in (type_status.get('formatos') or {}).items():
            entry = _budget_remaining(info)
            if entry is not None:
                monitor['formatos'][str(kind)] = entry
        monitor['atualizado_em'] = type_status.get('atualizado_em')
    if isinstance(budget_status, dict):
        monitor['atualizado_videos_em'] = budget_status.get('atualizado_em')
    # Não expor pedidos de outra execução; evita status antigo como se fosse atual.
    current_run = state.get('run_id')
    pending = []
    if running and current_run:
        commands = out / '_controle_limites_v38' / 'pendentes'
        if commands.is_dir():
            for cmd_path in commands.glob('*.json'):
                try:
                    payload = json.loads(cmd_path.read_text(encoding='utf-8'))
                    if payload.get('run_id') == current_run and isinstance(payload.get('tipo'), str):
                        pending.append(payload['tipo'])
                except (OSError, ValueError, AttributeError):
                    continue
    # Ao iniciar uma nova coleta, não apresentar os GB do processo anterior.
    if running and current_run:
        if not isinstance(budget_status, dict) or budget_status.get('run_id') != current_run:
            monitor['videos'] = None
            budget_status = None
        if not isinstance(type_status, dict) or type_status.get('run_id') != current_run:
            monitor['formatos'] = {}
            type_status = None
    return jsonify({'running': running, 'returncode': code, 'started': state.get('started'), 'log': log_text,
                    'download_budget': budget_status, 'type_budgets': type_status,
                    'videos_processamento': {'executando': videos_running, 'codigo': vidproc.poll() if vidproc else None, 'log': videos_log}, 'monitor_limites_gb': monitor, 'redefinicoes_pendentes': pending})


@collector_bp.route('/api/collector/redefinir-limite', methods=['POST'])
def redefinir_limite():
    """Enfileira a redefinição de UM limite, sem mexer no histórico/cotas no Flask."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get('tipo'), str):
        return jsonify({'error': 'Informe o tipo de limite.'}), 400
    kind = body['tipo'].strip()
    with _lock:
        state = _state()
        proc = state.get('process')
        current_run = state.get('run_id')
        if not proc or proc.poll() is not None or not current_run:
            return jsonify({'error': 'A redefinição ao vivo exige uma coleta ativa. Inicie uma nova coleta para novos contadores.'}), 409
        _, _, out = _paths()
        if kind == 'videos':
            source = out / 'limite_downloads_status.json'
            if not source.is_file():
                return jsonify({'error': 'O coletor ainda não publicou o limite de vídeos.'}), 409
            try:
                data = json.loads(source.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                return jsonify({'error': 'Não foi possível ler o estado dos limites.'}), 500
            info = data
        else:
            source = out / 'limites_por_tipo_status.json'
            if not source.is_file():
                return jsonify({'error': 'O coletor ainda não publicou os limites por tipo.'}), 409
            try:
                data = json.loads(source.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                return jsonify({'error': 'Não foi possível ler o estado dos limites.'}), 500
            info = (data.get('formatos') or {}).get(kind) if isinstance(data, dict) else None
        if not isinstance(data, dict) or data.get('run_id') != current_run:
            return jsonify({'error': 'Os dados de limite são de outra execução; atualize a página.'}), 409
        if not isinstance(info, dict) or not info.get('limite_atingido') or not float(info.get('limite_gb', 0)) > 0:
            return jsonify({'error': 'Este formato não possui um limite atingido nesta execução.'}), 409
        directory = out / '_controle_limites_v38' / 'pendentes'
        directory.mkdir(parents=True, exist_ok=True)
        for existing in directory.glob('*.json'):
            try:
                old = json.loads(existing.read_text(encoding='utf-8'))
                if old.get('run_id') == current_run and old.get('tipo') == kind:
                    return jsonify({'error': 'Já existe uma redefinição pendente para este tipo.'}), 409
            except (OSError, ValueError, AttributeError):
                continue
        request_id = uuid.uuid4().hex
        cmd = {'run_id': current_run, 'tipo': kind, 'pedido': request_id}
        destination = directory / (request_id + '.json')
        staging = directory / (request_id + '.tmp')
        try:
            staging.write_text(json.dumps(cmd, ensure_ascii=False), encoding='utf-8')
            os.replace(staging, destination)
        except OSError as exc:
            staging.unlink(missing_ok=True)
            return jsonify({'error': 'Não foi possível registrar o pedido: ' + str(exc)}), 500
    return jsonify({'ok': True, 'pendente': True, 'tipo': kind,
                    'message': 'Redefinição solicitada. O coletor confirmará quando zerar o contador; histórico preservado.'}), 202


def _extract_spiderfoot_queries(payload_bytes, filename):
    """Convert SpiderFoot CSV/JSON export rows into bounded searchable query lines."""
    if len(payload_bytes) > 10 * 1024 * 1024:
        raise ValueError('O arquivo deve ter no máximo 10 MB.')
    suffix = Path(filename or '').suffix.lower()
    text = payload_bytes.decode('utf-8-sig', errors='replace')
    records = []
    if suffix == '.csv':
        records = list(csv.DictReader(text.splitlines()))
    elif suffix == '.json':
        obj = json.loads(text)
        def flatten(value):
            if isinstance(value, list):
                for item in value: flatten(item)
            elif isinstance(value, dict):
                # SpiderFoot JSON exports usually have event/data/source/module keys.
                data = value.get('data') or value.get('value') or value.get('target') or value.get('url') or value.get('domain') or value.get('ip') or value.get('email')
                event = value.get('event') or value.get('event_type') or value.get('type') or ''
                if data is not None:
                    records.append({'data': data, 'event': event})
                for k, v in value.items():
                    if isinstance(v, (list, dict)): flatten(v)
        flatten(obj)
    else:
        raise ValueError('Formato não suportado. Envie uma exportação CSV ou JSON do SpiderFoot.')
    queries, seen = [], set()
    data_keys = ('data','value','target','url','domain','ip','email','name','username','phone','host','resource')
    event_keys = ('event','event_type','type','eventtype')
    for row in records:
        if not isinstance(row, dict):
            continue
        lower = {str(k).strip().lower(): v for k,v in row.items()}
        raw = next((lower.get(k) for k in data_keys if lower.get(k) not in (None,'')), None)
        if raw is None:
            continue
        val = re.sub(r'\s+', ' ', str(raw)).strip()
        if len(val) < 3 or len(val) > 500:
            continue
        event = next((str(lower.get(k)).strip() for k in event_keys if lower.get(k)), '')
        query = (re.sub(r'[_\-]+',' ',event) + ' ' + val).strip() if event and event.lower() not in ('data','value','target') else val
        query = query[:1000]
        key = query.casefold()
        if key not in seen:
            seen.add(key); queries.append(query)
        if len(queries) >= 1000:
            break
    if not queries:
        raise ValueError('Não encontrei campos de dados pesquisáveis no arquivo. Exporte os resultados como CSV ou JSON e tente novamente.')
    return queries


@collector_bp.route('/api/collector/import-results', methods=['POST'])
def import_results():
    upload = request.files.get('module_results')
    if not upload or not upload.filename:
        return jsonify({'error': 'Selecione um arquivo CSV ou JSON exportado do SpiderFoot.'}), 400
    try:
        raw = upload.stream.read(10 * 1024 * 1024 + 1)
        queries = _extract_spiderfoot_queries(raw, upload.filename)
    except (ValueError, UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        return jsonify({'error': str(exc)}), 400
    _, tool, out = _paths()
    imported_dir = out / 'imports'
    imported_dir.mkdir(parents=True, exist_ok=True)
    # Preserve the uploaded source for audit/reproducibility; only its extracted
    # query lines are sent to search engines.
    stamp = __import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_name = re.sub(r'[^A-Za-z0-9._-]+', '_', Path(upload.filename).name)[:100] or 'export'
    (imported_dir / f'{stamp}_{safe_name}').write_bytes(raw)
    query_file = out / 'consultas_importadas_spiderfoot.txt'
    old = query_file.read_text(encoding='utf-8-sig').splitlines() if query_file.exists() else []
    merged, seen = [], set()
    for q in old + queries:
        q = q.strip()
        if q and q.casefold() not in seen:
            seen.add(q.casefold()); merged.append(q)
    query_file.write_text('\n'.join(merged) + '\n', encoding='utf-8')
    return jsonify({'ok': True, 'imported': len(queries), 'total_saved': len(merged), 'queries': queries, 'message': f'{len(queries)} consultas extraídas; confira as novas linhas no campo de consultas personalizadas.'})


@collector_bp.route('/api/collector/start', methods=['POST'])
def start():
    data = request.get_json(silent=True) or request.form
    mode = 'personalizadas'  # Fixo no servidor; payload nao altera a regra.

    try:
        qlimit = int(data.get('limite_pesquisas', 5))
        custom_queries_raw = str(data.get('consultas_personalizadas', '') or '')
        flimit = int(data.get('limite_arquivos', 0))
        media_max_mb = int(data.get('tamanho_max_midia_mb', 0))
        pause = float(data.get('pausa', 2.0))
        year_start = int(data.get('ano_inicial', 1998))
        year_end = int(data.get('ano_final', 2026))
        mechanisms = data.get('mecanismos', [x[0] for x in ENGINE_CHOICES if x[2]])
        if isinstance(mechanisms, str):
            mechanisms = [x.strip() for x in mechanisms.split(',') if x.strip()]
        searxng_url = str(data.get('searxng_url', '')).strip()
        tor_socks_url = str(data.get('tor_socks_url', 'socks5h://127.0.0.1:9150')).strip() or 'socks5h://127.0.0.1:9150'
        baixar_videos_youtube = str(data.get('baixar_videos_youtube', 'false')).lower() in ('1', 'true', 'yes', 'sim')
        baixar_midias_publicas = str(data.get('baixar_midias_publicas', 'false')).lower() in ('1', 'true', 'yes', 'sim', 'on')
        repetir_concluidas = str(data.get('repetir_concluidas', 'false')).lower() in ('1', 'true', 'yes', 'sim', 'on')
        buscar_videos_relacionados = str(data.get('buscar_videos_relacionados', 'true')).lower() in ('1', 'true', 'yes', 'sim', 'on')
        max_buscas_relacionadas = int(data.get('max_buscas_relacionadas', 12))
        buscar_documentos_relacionados = str(data.get('buscar_documentos_relacionados', 'true')).lower() in ('1', 'true', 'yes', 'sim', 'on')
        max_buscas_documentos = int(data.get('max_buscas_documentos', 960))
        raw_limits = data.get('limites_por_tipo_gb', {})
        if not isinstance(raw_limits, dict):
            return jsonify({'error': 'Limites por tipo devem ser um objeto.'}), 400
        valid_budget_types = {'.mp4', '.webm', '.mkv', '.mov', '.avi', '.m4v', '.mpg', '.mpeg',
                              '.pdf', '.txt', '.doc', '.docx', '.zip', '.ppt', '.pptx', '.xls', '.xlsx', '.odt', '.ods',
                              'audio', 'imagem', 'outros'}
        if any(k not in valid_budget_types for k in raw_limits):
            return jsonify({'error': 'Tipo de arquivo não aceito nos limites em GB.'}), 400
        type_limits = {str(k): float(v) for k, v in raw_limits.items()}
        if any(not 0 <= limit <= 10000 for limit in type_limits.values()):
            return jsonify({'error': 'Limites por tipo devem estar entre 0 e 10000 GB.'}), 400
        refazer_downloads_antigos = str(data.get('refazer_downloads_antigos', 'false')).lower() in ('1', 'true', 'yes', 'sim', 'on')
        continuar_busca_encadeada = str(data.get('continuar_busca_encadeada', 'false')).lower() in ('1', 'true', 'yes', 'sim', 'on')
        telegram_video_modo, telegram_video_limite_gb, telegram_video_saida_gb = _video_config(data)
        limite_total_gb = float(data.get('limite_total_gb', 0))
        telegram_canais_raw = str(data.get('telegram_canais', '') or '')
    except (TypeError, ValueError, OverflowError):
        return jsonify({'error': 'Parâmetros numéricos inválidos (incluindo tamanho para Telegram).'}), 400
    valid_engines = {x[0] for x in ENGINE_CHOICES}
    if len(telegram_canais_raw) > 10000:
        return jsonify({'error': 'A lista de canais públicos do Telegram ultrapassa 10 KB.'}), 413
    telegram_canais = []
    for raw in telegram_canais_raw.splitlines():
        value = raw.strip().lstrip('@')
        value = re.sub(r'^https?://(?:www\.)?t\.me/(?:s/)?', '', value, flags=re.I).strip('/ ')
        value = value.split('/', 1)[0]
        if not value: continue
        if not re.fullmatch(r'[A-Za-z0-9_]{5,32}', value):
            return jsonify({'error': f'Username de canal público inválido: {raw[:80]}. Use username público, um por linha; links de convite privado não são aceitos.'}), 400
        if value not in telegram_canais: telegram_canais.append(value)
    if len(telegram_canais) > 100:
        return jsonify({'error': 'Informe no máximo 100 canais públicos do Telegram por execução.'}), 400
    if baixar_videos_youtube and 'youtube' not in mechanisms:
        return jsonify({'error': 'Marque YouTube na lista de mecanismos para baixar vídeos.'}), 400
    # V37: erro com nome de campo para diagnóstico. 0 MB = sem teto individual de mídia.
    checks = (
        ('Limite de pesquisas', qlimit, 0, 1235),
        ('Máximo de novos arquivos', flimit, 0, 5000),
        ('Tamanho máximo por mídia (MB)', media_max_mb, 0, 1048576),
        ('Pausa entre pesquisas (segundos)', pause, 0, 60),
        ('Máximo de buscas relacionadas de vídeos', max_buscas_relacionadas, 0, 100),
        ('Máximo de buscas encadeadas de documentos', max_buscas_documentos, 0, 1200),
        ('Limite máximo de vídeos (GB)', limite_total_gb, 0, 10000),
        ('Ano inicial', year_start, 1900, 2100),
        ('Ano final', year_end, 1900, 2100),
    )
    invalid = [f'{name}: {value} (permitido: {minimum} a {maximum})'
               for name, value, minimum, maximum in checks
               if not (minimum <= value <= maximum)]
    if year_start > year_end:
        invalid.append(f'Ano inicial {year_start} maior que ano final {year_end}')
    if invalid:
        return jsonify({'error': 'Parâmetros fora dos limites permitidos: ' + '; '.join(invalid)}), 400
    # Accept at most 1,000 custom query lines and 100 KB total to avoid oversized requests.
    if len(custom_queries_raw) > 100000:
        return jsonify({'error': 'As consultas personalizadas ultrapassam 100 KB. Divida a lista em lotes menores.'}), 413
    custom_queries = []
    seen_queries = set()
    for raw_line in custom_queries_raw.splitlines():
        query = raw_line.strip()
        if not query:
            continue
        if len(query) > 1000:
            return jsonify({'error': 'Cada consulta deve ter no máximo 1.000 caracteres.'}), 400
        key = query.casefold()
        if key not in seen_queries:
            seen_queries.add(key)
            custom_queries.append(query)
    if len(custom_queries) > 1000:
        return jsonify({'error': 'O limite é de 1.000 consultas personalizadas por execução.'}), 400
    # Consultas são exclusivamente as linhas enviadas no editor. Nenhum
    # arquivo importado, biblioteca ou portal oficial é incorporado sem o usuário
    # acrescentar a consulta ao campo.
    if not custom_queries:
        return jsonify({'error': 'Informe ao menos uma consulta personalizada no campo de texto.'}), 400
    if not isinstance(mechanisms, list) or not mechanisms or any(x not in valid_engines for x in mechanisms):
        return jsonify({'error': 'Selecione pelo menos um mecanismo válido.'}), 400
    if 'searxng' in mechanisms and searxng_url and not searxng_url.startswith(('https://', 'http://')):
        return jsonify({'error': 'A URL da instância SearXNG deve começar com http:// ou https://.'}), 400
    if any(x.startswith('onion_') for x in mechanisms):
        parsed_tor = urlparse(tor_socks_url)
        try:
            tor_port = parsed_tor.port
        except ValueError:
            tor_port = None
        if parsed_tor.scheme not in ('socks5h', 'socks5') or not parsed_tor.hostname or not tor_port:
            return jsonify({'error': 'Para os buscadores .onion, informe um proxy SOCKS5 valido, por exemplo socks5h://127.0.0.1:9150 ou socks5h://127.0.0.1:9050.'}), 400
    with _lock:
        state = _state()
        video_job = state.get('video_process')
        if video_job and video_job.poll() is None:
            return jsonify({'error':'Processamento de videos existentes ativo; finalize antes de iniciar coleta.'}),409
        old = state.get('process')
        if old and old.poll() is None:
            return jsonify({'error': 'O coletor já está em execução.'}), 409
        _, tool, out = _paths()
        script = tool / 'coletar_materiais.py'
        if not script.exists():
            return jsonify({'error': 'Arquivo do coletor não encontrado na instalação.'}), 500
        out.mkdir(parents=True, exist_ok=True)
        custom_queries_path = out / 'consultas_personalizadas_ultima_execucao.txt'
        # Keep a copy of user-provided queries in results so the run is reproducible.
        custom_queries_path.write_text('\n'.join(custom_queries) + ('\n' if custom_queries else ''), encoding='utf-8')
        telegram_channels_path = out / 'telegram_canais_publicos_ultima_execucao.txt'
        telegram_channels_path.write_text('\n'.join(telegram_canais) + ('\n' if telegram_canais else ''), encoding='utf-8')
        log_path = out / 'execucao.log'
        handle = open(log_path, 'a', encoding='utf-8')
        cmd = [sys.executable, str(script), '--modo', 'personalizadas', '--max-buscas-relacionadas', str(max_buscas_relacionadas),
               '--max-buscas-documentos', str(max_buscas_documentos),
               '--telegram-video-modo', telegram_video_modo,
               '--telegram-video-limite-gb', str(telegram_video_limite_gb),
               '--telegram-video-saida-gb', str(telegram_video_saida_gb),
               '--limites-por-tipo-gb', json.dumps(type_limits), '--limite-total-gb', str(limite_total_gb), '--limite-pesquisas', str(qlimit),
               '--limite-arquivos', str(flimit), '--tamanho-max-midia-mb', str(media_max_mb), '--pausa', str(pause), '--ano-inicial', str(year_start), '--ano-final', str(year_end),
               '--mecanismos', ','.join(mechanisms), '--searxng-url', searxng_url,
               '--tor-socks-url', tor_socks_url,
               '--consultas-arquivo', str(custom_queries_path), '--telegram-canais-arquivo', str(telegram_channels_path)]
        if buscar_videos_relacionados:
            cmd.append('--buscar-videos-relacionados')
        if buscar_documentos_relacionados:
            cmd.append('--buscar-documentos-relacionados')
        if refazer_downloads_antigos:
            cmd.append('--refazer-downloads-antigos')
        if continuar_busca_encadeada:
            cmd.append('--continuar-busca-encadeada')
        if baixar_videos_youtube:
            cmd.append('--baixar-videos-youtube')
        if baixar_midias_publicas:
            cmd.append('--baixar-midias-publicas')
        if repetir_concluidas:
            cmd.append('--repetir-concluidas')
        # A identificação da execução impede aplicar pedidos antigos após reiniciar.
        run_id = uuid.uuid4().hex
        child_env = os.environ.copy()
        child_env['SF_COLETOR_RUN_ID'] = run_id
        kwargs = {'cwd': str(tool), 'stdout': handle, 'stderr': subprocess.STDOUT,
                  'stdin': subprocess.DEVNULL, 'env': child_env}
        if os.name == 'nt':
            kwargs['creationflags'] = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        else:
            kwargs['start_new_session'] = True
        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except Exception as exc:
            handle.close()
            return jsonify({'error': f'Não foi possível iniciar o coletor: {exc}'}), 500
        state.update({'process': proc, 'log_handle': handle, 'log_path': str(log_path),
                      'started': __import__('datetime').datetime.now().isoformat(timespec='seconds'),
                      'run_id': run_id})
    return jsonify({'ok': True, 'pid': proc.pid})


@collector_bp.route('/api/collector/stop', methods=['POST'])
def stop():
    with _lock:
        state = _state()
        proc = state.get('process')
        if not proc or proc.poll() is not None:
            return jsonify({'error': 'Não há coleta em execução.'}), 409
        try:
            if os.name == 'nt':
                proc.send_signal(getattr(signal, 'CTRL_BREAK_EVENT', signal.SIGTERM))
            else:
                proc.send_signal(signal.SIGINT)
        except Exception:
            proc.terminate()
    return jsonify({'ok': True, 'message': 'Pedido de interrupção enviado; o progresso já gravado será preservado.'})




@collector_bp.route('/api/collector/organize', methods=['POST'])
def organize_existing():
    """Preview/organize files placed in resultados/entrada_arquivos."""
    data = request.get_json(silent=True) or request.form
    apply_changes = str(data.get('apply', 'false')).lower() in ('1','true','yes','sim')
    state = _state(); proc = state.get('process')
    if proc and proc.poll() is None:
        return jsonify({'error': 'Aguarde a coleta terminar ou interrompa-a antes de organizar arquivos.'}), 409
    _, tool, out = _paths()
    script = tool / 'organizador_inteligente.py'
    if not script.exists():
        return jsonify({'error': 'Organizador inteligente não encontrado.'}), 500
    cmd = [sys.executable, str(script), '--tool-dir', str(tool)]
    if apply_changes: cmd.append('--aplicar')
    try:
        result = subprocess.run(cmd, cwd=str(tool), capture_output=True, text=True, timeout=180)
        log_path = out / 'organizacao.log'; out.mkdir(parents=True, exist_ok=True)
        log_path.write_text((result.stdout or '') + '\n' + (result.stderr or ''), encoding='utf-8')
        if result.returncode != 0:
            return jsonify({'error': 'A organização falhou; consulte resultados/organizacao.log.', 'detail': (result.stderr or result.stdout)[-1500:]}), 500
        try: details = json.loads(result.stdout)
        except Exception: details = {'output': result.stdout[-1500:]}
        details['message'] = ('Arquivos organizados. Confira relatorio_organizacao.csv e manifesto_organizacao.csv.' if apply_changes else 'Prévia criada; nenhum arquivo foi movido. Confira relatorio_organizacao.csv antes de aplicar.')
        return jsonify({'ok': True, **details})
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'A organização excedeu 180 segundos. Consulte os arquivos de relatório e tente lotes menores.'}), 504
    except Exception as exc:
        return jsonify({'error': f'Não foi possível executar o organizador: {exc}'}), 500


@collector_bp.route('/api/collector/download')
def download():
    _, tool, out = _paths()
    state = _state()
    proc = state.get('process')
    if proc and proc.poll() is None:
        return jsonify({'error': 'Aguarde a coleta terminar ou interrompa-a antes de empacotar os resultados.'}), 409
    if not out.exists() or not any(out.iterdir()):
        return jsonify({'error': 'Ainda não existem resultados para baixar.'}), 404
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in out.rglob('*'):
            if path.is_file() and not path.name.endswith('.part'):
                archive.write(path, path.relative_to(tool))
    buffer.seek(0)
    return send_file(buffer, mimetype='application/zip', as_attachment=True, download_name='Resultados_ENEM_FATEC.zip')