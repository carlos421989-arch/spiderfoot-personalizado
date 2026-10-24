"""Motor de busca ampliada para Minha Exposicao Digital.

Somente resultados indexados publicamente; sem bases privadas, logins ou reconhecimento facial.
O relatorio local NAO persiste termos de busca, apenas URL/titulo/resumo encontrados.
"""
from __future__ import annotations

from html.parser import HTMLParser
import html
import re
import time
from urllib.parse import parse_qs, unquote, urlparse, urlunparse, parse_qsl, urlencode
from xml.etree import ElementTree

import requests

from spiderfoot.blueprints import personal_audit_sources as sources_provider

MODOS = {
    'rapida': {'consultas': 10, 'paginas': 1, 'requisicoes': 18, 'segundos': 100, 'max_fontes': 50},
    'ampliada': {'consultas': 20, 'paginas': 2, 'requisicoes': 48, 'segundos': 210, 'max_fontes': 100},
    'profunda': {'consultas': 32, 'paginas': 3, 'requisicoes': 96, 'segundos': 360, 'max_fontes': 140},
}


def variantes(fields):
    """Variantes completas e buscas por dominio; sem consultas por partes do CPF."""
    out = []
    def add(tipo, consulta):
        if not any(t == tipo and q == consulta for t, q in out):
            out.append((tipo, consulta))

    email = fields.get('email', '').strip()
    if email:
        add('email', '"' + email + '"')
        for site in ('github.com', 'linkedin.com', 'pastebin.com', 'gravatar.com'):
            add('email', '"' + email + '" site:' + site)

    cpf = fields.get('cpf', '').strip()
    if cpf:
        digits = re.sub(r'\D', '', cpf)
        if len(digits) == 11:
            add('cpf', '"' + digits + '"')
            add('cpf', '"' + f'{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}' + '"')

    tel = fields.get('telefone', '').strip()
    if tel:
        digits = re.sub(r'\D', '', tel)
        if len(digits) >= 10:
            add('telefone', '"' + digits + '"')
            if digits.startswith('55') and len(digits) in (12, 13):
                local = digits[2:]
                add('telefone', '"' + local + '"')
                add('telefone', '"+' + digits + '"')
            else:
                local = digits
            if len(local) in (10, 11):
                add('telefone', '"(' + local[:2] + ') ' + local[2:-4] + '-' + local[-4:] + '"')
                add('telefone', '"' + local[:2] + ' ' + local[2:-4] + '-' + local[-4:] + '"')

    usuario = fields.get('usuario', '').strip().lstrip('@')
    if usuario:
        add('usuario', '"@' + usuario + '"')
        add('usuario', '"' + usuario + '"')
        for site in ('instagram.com', 'github.com', 'tiktok.com', 'linkedin.com', 'facebook.com', 'youtube.com', 'reddit.com'):
            add('usuario', '"' + usuario + '" site:' + site)

    nome = fields.get('nome', '').strip()
    if nome:
        add('nome', '"' + nome + '"')
        for site in ('linkedin.com/in', 'instagram.com', 'facebook.com', 'escavador.com',
                     'jusbrasil.com.br', 'gov.br', 'github.com', 'youtube.com',
                     'tiktok.com', 'researchgate.net', 'lattes.cnpq.br', 'pinterest.com'):
            add('nome', '"' + nome + '" site:' + site)
        add('nome', '"' + nome + '" filetype:pdf')

    # Intercala identificadores: uma pesquisa extensa nao deve consumir todo
    # o orcamento HTTP em consultas do primeiro campo preenchido.
    grupos = {}
    for tipo, consulta in out:
        grupos.setdefault(tipo, []).append((tipo, consulta))
    balanceadas = []
    prioridade = ('email', 'telefone', 'cpf', 'usuario', 'nome')
    while any(grupos.values()):
        for tipo in prioridade:
            if grupos.get(tipo):
                balanceadas.append(grupos[tipo].pop(0))
    # Identificadores completos primeiro. Noticias e enciclopedias recebem
    # apenas nomes, que nao podem esgotar o orcamento antes do e-mail/usuario.
    return ([entry for entry in balanceadas if entry[0] != 'nome'] +
            [entry for entry in balanceadas if entry[0] == 'nome'])


class _DDGParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hits = []
        self._link = None
        self._title = []
        self._capture_snippet = False
        self._snippet = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        cls = d.get('class') or ''
        if tag == 'a' and 'result__a' in cls:
            url = d.get('href', '')
            if url.startswith('//'):
                url = 'https:' + url
            if 'duckduckgo.com/l/' in url:
                url = parse_qs(urlparse(url).query).get('uddg', [''])[0]
            self._link = url
            self._title = []
        elif self.hits and ('result__snippet' in cls):
            self._capture_snippet = True
            self._snippet = []

    def handle_data(self, data):
        if self._link is not None:
            self._title.append(data)
        if self._capture_snippet:
            self._snippet.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self._link is not None:
            if self._link.startswith('https://'):
                self.hits.append({'url': self._link,
                                  'title': ' '.join(self._title).strip()[:220],
                                  'snippet': ''})
            self._link = None
        elif self._capture_snippet and tag in ('a', 'div'):
            if self.hits:
                self.hits[-1]['snippet'] = ' '.join(self._snippet).strip()[:550]
            self._capture_snippet = False


def _bounded_index_content(session, url, params, max_bytes):
    """Le a resposta HTTP aos poucos, interrompendo antes de exceder o limite."""
    r = session.get(url, params=params, timeout=(4, 8),
                    allow_redirects=False, stream=True)
    try:
        if r.status_code != 200:
            return None
        data = bytearray()
        for chunk in r.iter_content(chunk_size=32768):
            data.extend(chunk)
            if len(data) > max_bytes:
                return None
        return bytes(data)
    finally:
        r.close()


def _bing_rss(s, query, offset):
    try:
        content = _bounded_index_content(s, 'https://www.bing.com/search',
                   {'q': query, 'format': 'rss', 'count': 20,
                    'first': offset + 1, 'setlang': 'pt-BR'}, 350000)
        if content is None:
            return [], True
        root = ElementTree.fromstring(content)
        results = []
        for x in root.findall('./channel/item'):
            url = (x.findtext('link') or '').strip()
            if url.startswith('https://') and len(url) <= 1800:
                results.append({'url': url,
                                'title': html.unescape((x.findtext('title') or '')[:220]),
                                'snippet': re.sub(r'<[^>]*>', '', html.unescape(
                                    (x.findtext('description') or '')[:600]))})
        return results[:20], False
    except (requests.RequestException, OSError, ElementTree.ParseError, ValueError):
        return [], True


def _ddg(s, query, offset):
    try:
        content = _bounded_index_content(s, 'https://html.duckduckgo.com/html/',
                  {'q': query, 's': str(offset), 'kl': 'br-pt'}, 450000)
        if content is None:
            return [], True
        p = _DDGParser()
        p.feed(content.decode('utf-8', errors='replace'))
        return p.hits[:30], False
    except (requests.RequestException, OSError, ValueError):
        return [], True


def _canonical(url):
    """Desduplica parametros de rastreamento sem modificar o link apresentado."""
    p = urlparse(url)
    filtered = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                if not (k.lower().startswith('utm_') or k.lower() in ('fbclid', 'gclid')))
    return urlunparse((p.scheme.lower(), p.netloc.lower(),
                       p.path.rstrip('/') or '/', '', urlencode(filtered), ''))


def _owned_links(text, valid_url):
    """Incorpora somente paginas que o usuario afirmou ser suas, sem adivinhar propriedade."""
    results = []
    for line in str(text or '').splitlines()[:8]:
        url = line.strip()
        if url and valid_url(url) and url not in results:
            results.append(url)
    return results


def realizar_busca(session, fields, modo, perfis, is_public_url, match_index,
                  max_requests=None, verify_page=None, verification_budget=None):
    """Pesquisa indices; nao envia consultas a fontes privadas nem valida identidade por rosto.

    Returns: (sources, stats, warnings), sem armazenar os identificadores no relatorio.
    """
    if modo not in MODOS:
        raise ValueError('Modo de busca invalido.')
    opt = MODOS[modo]
    request_limit = opt['requisicoes'] if max_requests is None else min(
        opt['requisicoes'], max(0, int(max_requests)))
    queries = variantes(fields)[:opt['consultas']]
    sources = []
    by_url = {}
    warnings = []
    start = time.monotonic()
    total_calls = 0
    total_raw = 0
    rejected = 0
    possible_homonyms = 0
    blocked = 0
    executed = 0
    # Entradas de API so aparecem se configuradas com chave. Os mesmos limites
    # anteriores valem para o conjunto todo: adicionar fontes NAO amplia o teto.
    engines = sources_provider.available_engines()
    counts = {name: 0 for name in engines}
    failed_by_engine = dict.fromkeys(engines, 0)
    consecutive_failures = dict.fromkeys(engines, 0)
    paused_engines = set()
    raw_by_engine = dict.fromkeys(engines, 0)
    rejected_by_engine = dict.fromkeys(engines, 0)
    homonyms_by_engine = dict.fromkeys(engines, 0)
    accepted_by_engine = dict.fromkeys(engines, 0)
    stopped = False
    # Verifica o corpo publico de poucas paginas quando o resumo do buscador
    # omite o identificador. Sempre limitado pelo orcamento total da auditoria.
    verified_urls = set()
    fetched_domains = {}
    tried_pages = 0
    checked_pages = 0
    detected_in_pages = 0
    failed_page_checks = 0
    eligible_ids = any(fields.get(k) for k in ('email', 'telefone', 'cpf', 'usuario'))

    # Distribui as consultas entre as fontes em largura: sem monopolio do
    # primeiro motor, e sem encaminhar CPF/telefone/email para fontes noticiosas.
    visitadas = set()
    bing_encontrou = set()
    for page in range(opt['paginas']):
        for tipo, query in queries:
            for engine in engines:
                if engine in paused_engines or not sources_provider.supports(engine, tipo):
                    continue
                # Estas fontes publicas nao oferecem paginacao consistente.
                if page and engine in ('Google Noticias RSS', 'GDELT Noticias', 'Tavily API'):
                    continue
                if total_calls >= request_limit or (time.monotonic() - start) >= opt['segundos']:
                    stopped = True
                    break
                if modo == 'rapida' and engine == 'DuckDuckGo' and (tipo, query) in bing_encontrou:
                    continue
                if (tipo, query) not in visitadas:
                    visitadas.add((tipo, query))
                    executed += 1
                total_calls += 1
                counts[engine] += 1
                if engine == 'Bing RSS':
                    hits, failed = _bing_rss(session, query, 20 * page)
                elif engine == 'DuckDuckGo':
                    hits, failed = _ddg(session, query, 20 * page)
                else:
                    hits, failed = sources_provider.search(engine, session, query, 20 * page)
                if failed:
                    blocked += 1
                    failed_by_engine[engine] += 1
                    consecutive_failures[engine] += 1
                    if consecutive_failures[engine] >= 3:
                        paused_engines.add(engine)
                else:
                    consecutive_failures[engine] = 0
                total_raw += len(hits)
                raw_by_engine[engine] += len(hits)
                if engine == 'Bing RSS' and hits:
                    bing_encontrou.add((tipo, query))
                for item in hits:
                    url = item.get('url', '')
                    if not isinstance(url, str) or not url.startswith('https://'):
                        continue
                    matched = [x for x in match_index(item, fields)
                               if x in ('nome', 'email', 'telefone', 'cpf', 'usuario')]
                    # Somente fontes publicas. Nenhum resultado de nome isolado
                    # sera declarado como pertencente ao titular.
                    key = _canonical(url)
                    page_matches = []
                    if (eligible_ids and verify_page is not None and
                            verification_budget is not None and
                            verification_budget.get('remaining', 0) > 0 and
                            time.monotonic() < verification_budget.get('deadline', 0) and
                            key not in verified_urls and key not in by_url and
                            (tipo in ('email', 'telefone', 'cpf', 'usuario') or 'nome' in matched) and
                            is_public_url(url)):
                        # Evita consumir todo o orcamento em um mesmo dominio
                        # ou varios snippets duplicados de uma noticia.
                        host = urlparse(url).hostname or ''
                        domain_limit = 2
                        if fetched_domains.get(host, 0) < domain_limit:
                            verified_urls.add(key)
                            fetched_domains[host] = fetched_domains.get(host, 0) + 1
                            tried_pages += 1
                            try:
                                visible_text = verify_page(url, verification_budget)
                            except (OSError, ValueError, UnicodeError):
                                visible_text = None
                            if visible_text:
                                checked_pages += 1
                                page_matches = [x for x in match_index(
                                    {'url': url, 'title': '', 'snippet': visible_text}, fields)
                                    if x in ('nome', 'email', 'telefone', 'cpf', 'usuario')]
                                if any(x in page_matches for x in ('email', 'telefone', 'cpf', 'usuario')):
                                    detected_in_pages += 1
                            else:
                                failed_page_checks += 1
                    matched = list(dict.fromkeys(matched + page_matches))
                    if not matched:
                        rejected += 1
                        rejected_by_engine[engine] += 1
                        continue
                    # Nome identico nao e evidência suficiente de exposicao da pessoa.
                    # Um resultado so passa ao relatorio principal quando contem
                    # um identificador mais distintivo fornecido na pesquisa.
                    strong = any(x in matched for x in ('email', 'cpf', 'telefone', 'usuario'))
                    if not strong:
                        rejected += 1
                        rejected_by_engine[engine] += 1
                        possible_homonyms += 1
                        homonyms_by_engine[engine] += 1
                        continue
                    if key in by_url:
                        row = by_url[key]
                        row['coincidencias'] = sorted(set(row.get('coincidencias', [])) | set(matched))
                        row['buscadores'] = sorted(set(row.get('buscadores', [])) | {engine})
                        continue
                    if len(sources) >= opt['max_fontes']:
                        stopped = True
                        break
                    if not is_public_url(url):
                        rejected += 1
                        rejected_by_engine[engine] += 1
                        continue
                    row = {**item, 'tipo': tipo if tipo in matched else matched[0],
                           'coincidencias': matched, 'buscadores': [engine],
                           'confianca': 'identificador_literal',
                           'evidencia': ('Texto visivel da pagina publica (identidade nao comprovada)'
                                        if any(k in page_matches for k in ('email', 'telefone', 'cpf', 'usuario'))
                                        else 'Titulo, resumo ou caminho do indice')}
                    sources.append(row)
                    by_url[key] = row
                    accepted_by_engine[engine] += 1
                if stopped:
                    break
            if stopped:
                break
        if stopped:
            break

    manual = _owned_links(perfis, is_public_url)
    for url in manual:
        key = _canonical(url)
        if key in by_url:
            by_url[key]['informado_por_voce'] = True
            by_url[key]['confianca'] = 'informado_por_voce'
            continue
        row = {'url': url, 'title': 'Perfil informado por voce (nao verificado)',
               'snippet': 'Endereco fornecido manualmente para orientar a busca de imagens.',
               'tipo': 'perfil_informado', 'coincidencias': [],
               'buscadores': [], 'confianca': 'informado_por_voce',
               'informado_por_voce': True}
        sources.insert(0, row)
        by_url[key] = row

    if blocked:
        warnings.append(f'{blocked} requisicoes a buscadores falharam ou retornaram respostas inutilizaveis. O indice pode estar incompleto.')
    if paused_engines:
        warnings.append('Fontes temporariamente interrompidas apos 3 falhas consecutivas: ' +
                        ', '.join(sorted(paused_engines)) + '. Outras fontes continuam dentro dos limites.')
    if stopped:
        warnings.append('Limite de tempo, requisicoes ou fontes atingido: foram salvos os resultados parciais.')
    if tried_pages:
        warnings.append(f'{tried_pages} leituras de paginas publicas foram tentadas, '
                        f'{checked_pages} retornaram texto utilizavel e '
                        f'{detected_in_pages} continham algum identificador distintivo informado. '
                        'Correspondencia textual nao comprova identidade.')
    if possible_homonyms:
        warnings.append(f'{possible_homonyms} resultados de nome igual foram excluidos da exposicao pessoal por possivel homonimia.')
    if rejected - possible_homonyms:
        warnings.append(f'{rejected - possible_homonyms} resultados ignorados por nao apresentar identificador completo.')
    if not sources:
        warnings.append('Nenhuma fonte com identificador distintivo foi encontrada. Isso NAO demonstra ausencia de exposicao.')

    # Fontes com identificador mais distintivo primeiro; nomes iguais sempre suspeitos de homonimia.
    rank = {'informado_por_voce': 0, 'identificador_literal': 1, 'nome_igual': 2}
    sources.sort(key=lambda x: (rank.get(x.get('confianca'), 9), x.get('title', '')))
    stats = {
        'modo': modo, 'consultas': executed, 'requisicoes': total_calls,
        'brutos': total_raw, 'descartadas': rejected, 'falhas': blocked,
        'mecanismos': counts, 'falhas_por_mecanismo': failed_by_engine,
        'brutos_por_mecanismo': raw_by_engine,
        'descartados_por_mecanismo': rejected_by_engine,
        'homonimos_por_mecanismo': homonyms_by_engine,
        'homonimos_descartados': possible_homonyms,
        'aceitos_por_mecanismo': accepted_by_engine,
        'mecanismos_pausados': sorted(paused_engines),
        'perfis_informados': len(manual),
        'fortes': sum(1 for s in sources if s.get('confianca') == 'identificador_literal'),
        'nomes_iguais': sum(1 for s in sources if s.get('confianca') == 'nome_igual'),
        'tentativas_verificacao': tried_pages,
        'paginas_verificadas': checked_pages,
        'paginas_com_identificador': detected_in_pages,
        'falhas_verificacao_paginas': failed_page_checks,
        'requisicoes_verificacao': (verification_budget.get('used', 0)
                                   if verification_budget is not None else 0),
    }
    return sources, stats, warnings
