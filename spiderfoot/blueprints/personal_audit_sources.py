"""Conectores opcionais para indices publicos de Minha Exposicao Digital.

Nenhum conector acessa perfis autenticados, bases privadas ou pesquisa por rosto.
Credenciais sao lidas somente das variaveis de ambiente, nunca do formulario ou relatorio.
Todos os conectores devolvem (itens, falhou), com limite de resposta e sem redirects.
"""
from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import socket
from urllib.parse import quote, urlparse
from xml.etree import ElementTree

import requests

MAX_BODY = 550_000
MAX_RESULTS = 20
PUBLIC_ENGINES = ('Bing RSS', 'DuckDuckGo', 'Google Noticias RSS',
                  'Wikipedia PT', 'Wikipedia EN', 'GDELT Noticias')
SERP_ENGINES = {
    'SerpApi Google': ('google', 'q'),
    'SerpApi Bing': ('bing', 'q'),
    'SerpApi DuckDuckGo': ('duckduckgo', 'q'),
    'SerpApi Yahoo': ('yahoo', 'p'),
    'SerpApi Yandex': ('yandex', 'text'),
    'SerpApi Baidu': ('baidu', 'wd'),
    'SerpApi Naver': ('naver', 'query'),
}
# Fontes noticiosas e enciclopedicas nao devem receber CPF, telefone ou email.
SPECIALIZED = {'Google Noticias RSS', 'Wikipedia PT', 'Wikipedia EN', 'GDELT Noticias'}


def available_engines():
    """Somente as fontes efetivamente configuradas; nao inventa APIs habilitadas."""
    engines = list(PUBLIC_ENGINES)
    if os.environ.get('MED_BRAVE_API_KEY', '').strip():
        engines.append('Brave API')
    if os.environ.get('MED_MOJEEK_API_KEY', '').strip():
        engines.append('Mojeek API')
    if os.environ.get('MED_TAVILY_API_KEY', '').strip():
        engines.append('Tavily API')
    if os.environ.get('MED_GOOGLE_API_KEY', '').strip() and os.environ.get('MED_GOOGLE_CX', '').strip():
        engines.append('Google Custom Search (legado)')
    if os.environ.get('MED_SEARXNG_URL', '').strip():
        engines.append('SearXNG')
    if os.environ.get('MED_SERPAPI_API_KEY', '').strip():
        engines.extend(SERP_ENGINES)
    chosen = os.environ.get('MED_SEARCH_ENGINES', '').strip()
    if chosen:
        allowed = {x.strip().casefold() for x in chosen.split(',') if x.strip()}
        engines = [x for x in engines if x.casefold() in allowed]
    return engines


def supports(engine, field):
    return (field == 'nome' if engine in SPECIALIZED else
            field in ('nome', 'email', 'usuario', 'telefone', 'cpf'))


def _fetch(session, url, params=None, headers=None, *, method='get', body=None):
    """Le bytes limitados, sem seguir redirecionamentos nem revelar segredos em logs."""
    try:
        kwargs = dict(timeout=(4, 8), stream=True, allow_redirects=False)
        if headers:
            kwargs['headers'] = headers
        if params:
            kwargs['params'] = params
        if method == 'post':
            kwargs['json'] = body or {}
            resp = session.post(url, **kwargs)
        else:
            resp = session.get(url, **kwargs)
        try:
            if resp.status_code != 200:
                return None
            data = bytearray()
            for chunk in resp.iter_content(chunk_size=32768):
                data.extend(chunk)
                if len(data) > MAX_BODY:
                    return None
            return bytes(data)
        finally:
            resp.close()
    except (requests.RequestException, OSError, ValueError):
        return None


def _json(session, url, params=None, headers=None, *, method='get', body=None):
    data = _fetch(session, url, params, headers, method=method, body=body)
    if data is None:
        return None
    try:
        obj = json.loads(data.decode('utf-8'))
        return obj if isinstance(obj, dict) and not obj.get('error') else None
    except (ValueError, UnicodeError):
        return None


def _clean(value, limit):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', str(value or '')))).strip()[:limit]


def _rows(items, url_field='url', title_field='title', snippet_field='snippet'):
    if not isinstance(items, list):
        return []
    found = []
    for entry in items[:MAX_RESULTS]:
        if not isinstance(entry, dict):
            continue
        url = entry.get(url_field)
        if not isinstance(url, str) or not url.startswith('https://') or len(url) > 1800:
            continue
        result = {'url': url, 'title': _clean(entry.get(title_field), 220),
                  'snippet': _clean(entry.get(snippet_field), 550)}
        if 'engine' in entry and isinstance(entry['engine'], str):
            result['indice_original'] = _clean(entry['engine'], 60)
        found.append(result)
    return found


def _rss(session, url, params, limit=20):
    data = _fetch(session, url, params)
    if data is None:
        return [], True
    try:
        root = ElementTree.fromstring(data)
        entries = []
        for item in root.findall('./channel/item')[:limit]:
            entries.append({'url': (item.findtext('link') or '').strip(),
                            'title': item.findtext('title') or '',
                            'snippet': item.findtext('description') or ''})
        return _rows(entries), False
    except ElementTree.ParseError:
        return [], True


def _public_searxng_url(value):
    """Requer host publico HTTPS. E uma configuracao de administrador, nao entrada web."""
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        if (parsed.scheme != 'https' or not host or parsed.username or parsed.password or
                parsed.port not in (None, 443) or parsed.query or parsed.fragment or
                host.lower() in ('localhost', 'localhost.localdomain') or host.endswith('.local')):
            return None
        addrs = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addrs or not all(ipaddress.ip_address(x[4][0]).is_global for x in addrs):
            return None
        path = parsed.path.rstrip('/')
        if path.endswith('/search'):
            return value.rstrip('/')
        if path not in ('', '/'):
            # evita aceitar endpoint arbitrario com caminho opaco
            return None
        return value.rstrip('/') + '/search'
    except (ValueError, OSError, TypeError):
        return None


def _call(engine, session, query, offset):
    if engine == 'Google Noticias RSS':
        return _rss(session, 'https://news.google.com/rss/search',
                    {'q': query, 'hl': 'pt-BR', 'gl': 'BR', 'ceid': 'BR:pt-419'})
    if engine in ('Wikipedia PT', 'Wikipedia EN'):
        lang = 'pt' if engine.endswith('PT') else 'en'
        obj = _json(session, f'https://{lang}.wikipedia.org/w/api.php',
                    {'action': 'query', 'list': 'search', 'format': 'json',
                     'srsearch': query, 'srlimit': 10, 'sroffset': offset})
        if obj is None or not isinstance(obj.get('query'), dict):
            return [], True
        raw = obj['query'].get('search')
        if not isinstance(raw, list):
            return [], True
        docs = [{'url': f'https://{lang}.wikipedia.org/wiki/' +
                 quote(str(x.get('title', '')).replace(' ', '_'), safe=''),
                 'title': x.get('title'), 'snippet': x.get('snippet')}
                for x in raw if isinstance(x, dict)]
        return _rows(docs), False
    if engine == 'GDELT Noticias':
        obj = _json(session, 'https://api.gdeltproject.org/api/v2/doc/doc',
                    {'query': query, 'mode': 'artlist', 'format': 'json', 'maxrecords': 20})
        if obj is None or not isinstance(obj.get('articles'), list):
            return [], True
        return _rows(obj['articles'], snippet_field='summary'), False
    if engine == 'Brave API':
        obj = _json(session, 'https://api.search.brave.com/res/v1/web/search',
                    {'q': query, 'count': 20, 'offset': min(offset, 9)},
                    {'X-Subscription-Token': os.environ['MED_BRAVE_API_KEY'],
                     'Accept': 'application/json'})
        if obj is None or not isinstance(obj.get('web'), dict):
            return [], True
        return _rows(obj['web'].get('results'), snippet_field='description'), False
    if engine == 'Mojeek API':
        obj = _json(session, 'https://api.mojeek.com/search',
                    {'api_key': os.environ['MED_MOJEEK_API_KEY'], 'q': query,
                     's': offset + 1, 't': 20, 'fmt': 'json'})
        res = obj.get('response') if isinstance(obj, dict) else None
        if not isinstance(res, dict) or res.get('status') != 'OK':
            return [], True
        return _rows(res.get('results'), snippet_field='desc'), False
    if engine == 'Tavily API':
        obj = _json(session, 'https://api.tavily.com/search',
                    headers={'Authorization': 'Bearer ' + os.environ['MED_TAVILY_API_KEY']},
                    method='post', body={'query': query, 'max_results': 20,
                                         'include_answer': False, 'include_raw_content': False})
        if obj is None or not isinstance(obj.get('results'), list):
            return [], True
        return _rows(obj['results'], snippet_field='content'), False
    if engine == 'Google Custom Search (legado)':
        obj = _json(session, 'https://www.googleapis.com/customsearch/v1',
                    {'key': os.environ['MED_GOOGLE_API_KEY'],
                     'cx': os.environ['MED_GOOGLE_CX'], 'q': query,
                     'num': 10, 'start': offset + 1})
        if obj is None or not isinstance(obj.get('items', []), list):
            return [], True
        return _rows(obj.get('items', []), url_field='link'), False
    if engine == 'SearXNG':
        url = _public_searxng_url(os.environ.get('MED_SEARXNG_URL', ''))
        if not url:
            return [], True
        params = {'q': query, 'format': 'json', 'pageno': offset // 20 + 1}
        allowed = os.environ.get('MED_SEARXNG_ENGINES', '').strip()
        if allowed:
            terms = [t for t in allowed.split(',') if re.fullmatch(r'[a-z0-9_\-]{1,32}', t)]
            if terms:
                params['engines'] = ','.join(terms[:20])
        obj = _json(session, url, params)
        if obj is None or not isinstance(obj.get('results'), list):
            return [], True
        return _rows(obj['results'], snippet_field='content'), False
    if engine in SERP_ENGINES:
        provider, key = SERP_ENGINES[engine]
        params = {'engine': provider, key: query,
                  'api_key': os.environ['MED_SERPAPI_API_KEY']}
        if provider == 'yandex':
            params['p'] = offset // 20
        elif provider == 'google':
            params['start'] = offset
        elif provider == 'bing':
            params['first'] = offset + 1
        obj = _json(session, 'https://serpapi.com/search.json', params)
        if obj is None or not isinstance(obj.get('organic_results', []), list):
            return [], True
        return _rows(obj.get('organic_results', []), url_field='link'), False
    return [], True


def search(engine, session, query, offset):
    """Dispatcher padronizado. Nunca tenta outra origem apos falha da fonte."""
    try:
        return _call(engine, session, str(query)[:600], offset)
    except (KeyError, TypeError, ValueError, OSError, requests.RequestException):
        return [], True
