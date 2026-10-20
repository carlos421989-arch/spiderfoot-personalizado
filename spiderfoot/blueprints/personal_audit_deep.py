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
    return balanceadas


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


def _bing_rss(s, query, offset):
    try:
        r = s.get('https://www.bing.com/search',
                  params={'q': query, 'format': 'rss', 'count': 20,
                          'first': offset + 1, 'setlang': 'pt-BR'},
                  timeout=(4, 8), allow_redirects=False)
        if r.status_code != 200 or len(r.content) > 350000:
            return [], True
        root = ElementTree.fromstring(r.content)
        results = []
        for x in root.findall('./channel/item'):
            url = (x.findtext('link') or '').strip()
            if url.startswith('https://') and len(url) <= 1800:
                results.append({'url': url,
                                'title': html.unescape((x.findtext('title') or '')[:220]),
                                'snippet': re.sub(r'<[^>]*>', '', html.unescape(
                                    (x.findtext('description') or '')[:600]))})
        return results[:20], False
    except (OSError, ElementTree.ParseError, ValueError):
        return [], True


def _ddg(s, query, offset):
    try:
        r = s.get('https://html.duckduckgo.com/html/',
                  params={'q': query, 's': str(offset), 'kl': 'br-pt'},
                  timeout=(4, 8), allow_redirects=False)
        if r.status_code != 200 or len(r.content) > 450000:
            return [], True
        p = _DDGParser()
        p.feed(r.text)
        return p.hits[:30], False
    except (OSError, ValueError):
        return [], True


def _canonical(url):
    """Desduplica parametros de rastreamento sem modificar o link apresentado."""
    p = urlparse(url)
    filtered = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                if not (k.lower().startswith('utm_') or k.lower() in ('fbclid', 'gclid'))]
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


def realizar_busca(session, fields, modo, perfis, is_public_url, match_index):
    """Pesquisa indices; nao envia consultas a fontes privadas nem valida identidade por rosto.

    Returns: (sources, stats, warnings), sem armazenar os identificadores no relatorio.
    """
    if modo not in MODOS:
        raise ValueError('Modo de busca invalido.')
    opt = MODOS[modo]
    queries = variantes(fields)[:opt['consultas']]
    sources = []
    by_url = {}
    warnings = []
    start = time.monotonic()
    total_calls = 0
    total_raw = 0
    rejected = 0
    blocked = 0
    executed = 0
    counts = {'Bing RSS': 0, 'DuckDuckGo': 0}
    stopped = False

    # Consulta em largura: todos os identificadores recebem uma primeira
    # oportunidade antes de aprofundar as paginas de um so termo.
    visitadas = set()
    bing_encontrou = set()
    for page in range(opt['paginas']):
        for engine in ('Bing RSS', 'DuckDuckGo'):
            for tipo, query in queries:
                if total_calls >= opt['requisicoes'] or (time.monotonic() - start) > opt['segundos']:
                    stopped = True
                    break
                if modo == 'rapida' and engine == 'DuckDuckGo' and (tipo, query) in bing_encontrou:
                    continue
                if (tipo, query) not in visitadas:
                    visitadas.add((tipo, query))
                    executed += 1
                total_calls += 1
                counts[engine] += 1
                hits, failed = (_bing_rss(session, query, 20 * page) if engine == 'Bing RSS'
                                else _ddg(session, query, 20 * page))
                if failed:
                    blocked += 1
                total_raw += len(hits)
                if engine == 'Bing RSS' and hits:
                    bing_encontrou.add((tipo, query))
                for item in hits:
                    url = item.get('url', '')
                    if not url.startswith('https://'):
                        continue
                    # AUDITORIA_CONSOLIDADA_V5: identidade_confirmada nao prova identidade.
                    matched = [x for x in match_index(item, fields)
                               if x in ('nome', 'email', 'telefone', 'cpf', 'usuario')]
                    if tipo not in matched:
                        rejected += 1
                        continue
                    key = _canonical(url)
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
                        continue
                    strong = any(x in matched for x in ('email', 'cpf', 'telefone', 'usuario'))
                    row = {**item, 'tipo': tipo, 'coincidencias': matched,
                           'buscadores': [engine],
                           'confianca': 'identificador_literal' if strong else 'nome_igual',
                           'evidencia': 'Titulo, resumo ou caminho do indice'}
                    sources.append(row)
                    by_url[key] = row
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
        warnings.append(f'{blocked} consultas a buscadores falharam ou foram bloqueadas. O indice pode estar incompleto.')
    if stopped:
        warnings.append('Limite de tempo, requisicoes ou fontes atingido: foram salvos os resultados parciais.')
    if rejected:
        warnings.append(f'{rejected} resultados genericos ignorados por nao apresentar identificador completo.')
    if not sources:
        warnings.append('Nenhuma correspondencia textual encontrada. Isso NAO demonstra ausencia de exposicao.')

    # Fontes com identificador mais distintivo primeiro; nomes iguais sempre suspeitos de homonimia.
    rank = {'informado_por_voce': 0, 'identificador_literal': 1, 'nome_igual': 2}
    sources.sort(key=lambda x: (rank.get(x.get('confianca'), 9), x.get('title', '')))
    stats = {
        'modo': modo, 'consultas': executed, 'requisicoes': total_calls,
        'brutos': total_raw, 'descartadas': rejected, 'falhas': blocked,
        'mecanismos': counts, 'perfis_informados': len(manual),
        'fortes': sum(1 for s in sources if s.get('confianca') == 'identificador_literal'),
        'nomes_iguais': sum(1 for s in sources if s.get('confianca') == 'nome_igual'),
    }
    return sources, stats, warnings
