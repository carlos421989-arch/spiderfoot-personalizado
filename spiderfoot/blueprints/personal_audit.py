"""Auditoria Pessoal: consultas de exposicao publica com consentimento.

Instalada como uma Blueprint separada; nao altera scans ou base SQLite do
SpiderFoot. Fotos sao candidatos vindos de paginas publicas, NUNCA identificacao
biometrica, e so sao baixadas apos opt-in do usuario.
"""

from __future__ import annotations

import datetime as dt
import html
from html.parser import HTMLParser
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import tempfile
from urllib.parse import urljoin, urlparse, parse_qs, unquote
from xml.etree import ElementTree

import requests
from flask import (Blueprint, abort, jsonify, render_template, request,
                   send_file, url_for, make_response)

log = logging.getLogger('spiderfoot.personal_audit')
personal_audit_bp = Blueprint('personal_audit', __name__)
# Token local independente: nao requer CSRF global da instalacao SpiderFoot.
_AUDIT_CSRF = secrets.token_urlsafe(32)


@personal_audit_bp.before_request
def _check_write_request():
    if request.method != 'POST':
        return None
    # Evita que uma pagina externa cause buscas com PII ou apague auditorias.
    if request.content_length is not None and request.content_length > 16 * 1024:
        return jsonify(error='Requisicao muito grande.'), 413
    header = request.headers.get('X-Audit-CSRF', '')
    if not secrets.compare_digest(header, _AUDIT_CSRF):
        return jsonify(error='Token local de protecao invalido. Atualize a pagina.'), 403
    return None


def _privacy_headers(resp):
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    resp.headers['X-Robots-Tag'] = 'noindex, nofollow'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


MAX_SOURCES = 140
MAX_FETCH_PAGES = 24
MAX_IMAGES = 32
MAX_HTML_BYTES = 600 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_IMAGES = {'image/jpeg': '.jpg', 'image/png': '.png',
                  'image/webp': '.webp', 'image/gif': '.gif'}


def _root() -> Path:
    base = os.environ.get('SPIDERFOOT_DATA')
    if not base:
        from spiderfoot.helpers import SpiderFootHelpers
        base = SpiderFootHelpers.dataPath()
    path = Path(base) / 'auditoria_pessoal'
    path.mkdir(parents=True, exist_ok=True)
    (path / 'runs').mkdir(exist_ok=True)
    (path / 'imagens').mkdir(exist_ok=True)
    return path


def _valid_public_url(url: str) -> bool:
    """Conservative anti-SSRF: HTTPS with publicly routable DNS answers."""
    if not isinstance(url, str) or len(url) > 1800:
        return False
    try:
        u = urlparse(url)
        if u.scheme != 'https' or not u.hostname or u.username or u.password:
            return False
        if u.port not in (None, 443):
            return False
        if u.hostname.lower() in {'localhost', 'localhost.localdomain'}:
            return False
        addresses = socket.getaddrinfo(u.hostname, 443, type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(record[4][0]) for record in addresses]
        return bool(ips) and all(ip.is_global for ip in ips)
    except (ValueError, OSError, TypeError):
        return False


def _session():
    s = requests.Session()
    s.trust_env = False  # No inherited proxy / TOR redirects.
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (compatible; PersonalExposureAudit/1.0)',
        'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.7',
    })
    return s


def _safe_get(s: requests.Session, url: str, max_bytes: int, allowed_types: tuple):
    """Bounded HTTP(S) request; reject private/loopback URLs on every redirect."""
    for _ in range(3):
        if not _valid_public_url(url):
            return None
        try:
            with s.get(url, stream=True, timeout=(5, 8),
                       allow_redirects=False) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    location = r.headers.get('Location', '')
                    url = urljoin(url, location)
                    continue
                if r.status_code != 200:
                    return None
                media = r.headers.get('Content-Type', '').split(';')[0].lower().strip()
                if media not in allowed_types:
                    return None
                buffer = bytearray()
                for chunk in r.iter_content(chunk_size=32768):
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        return None
                return (bytes(buffer), media, url)
        except requests.RequestException:
            return None
    return None


def _cpf_valid(value: str) -> bool:
    digits = re.sub(r'\D', '', value)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for size in (9, 10):
        total = sum(int(digits[i]) * (size + 1 - i) for i in range(size))
        check = (total * 10) % 11
        if check == 10:
            check = 0
        if check != int(digits[size]):
            return False
    return True


def _inputs(data: dict):
    fields = {}
    nome = str(data.get('nome', '')).strip()
    email = str(data.get('email', '')).strip()
    usuario = str(data.get('usuario', '')).strip().lstrip('@')
    telefone = str(data.get('telefone', '')).strip()
    cpf = str(data.get('cpf', '')).strip()

    if nome:
        if len(nome) < 5 or len(nome) > 110 or any(ord(c) < 32 for c in nome):
            raise ValueError('Informe um nome completo valido.')
        fields['nome'] = nome
    if email:
        if len(email) > 180 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise ValueError('E-mail invalido.')
        fields['email'] = email
    if usuario:
        if not re.fullmatch(r'[A-Za-z0-9_.-]{2,64}', usuario):
            raise ValueError('Nome de usuario invalido.')
        fields['usuario'] = usuario
    if telefone:
        if not re.fullmatch(r'[+\d\s().-]{10,25}', telefone):
            raise ValueError('Telefone invalido.')
        digits = re.sub(r'\D', '', telefone)
        if len(digits) < 10 or len(digits) > 15:
            raise ValueError('Telefone deve ter 10 a 15 digitos.')
        fields['telefone'] = telefone
    if cpf:
        if not _cpf_valid(cpf):
            raise ValueError('CPF invalido (digitos verificadores).')
        if data.get('autorizar_cpf') is not True:
            raise ValueError('Autorize explicitamente a pesquisa publica do CPF.')
        fields['cpf'] = re.sub(r'\D', '', cpf)

    if not fields:
        raise ValueError('Informe ao menos um identificador seu.')
    if data.get('consentimento') is not True:
        raise ValueError('Confirme que os identificadores sao seus e autorize a consulta externa.')
    return fields



def _normalize_words(value: str) -> str:
    """Normaliza acentos e pontuacao para comparar FRASES INTEIRAS."""
    import unicodedata
    value = html.unescape(unquote(str(value)))
    value = unicodedata.normalize('NFKD', value).casefold()
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+', value))


def _digits_match(value: str, evidence: str) -> bool:
    """Correspondencia completa, aceitando separadores comuns em telefone/CPF."""
    digits = re.sub(r'\D', '', value)
    if not digits:
        return False
    pattern = r'(?<!\d)' + r'[\s().+\-]{0,3}'.join(map(re.escape, digits)) + r'(?!\d)'
    return re.search(pattern, evidence) is not None


def _matches_from_index(item: dict, fields: dict) -> list:
    """So aceita identificadores COMPLETOS no titulo, resumo ou caminho da URL.

    Buscadores podem IGNORAR aspas e devolver paginas sem relacao com a busca.
    Um nome completo encontrado tampouco prova que e a mesma pessoa.
    """
    parsed = urlparse(item.get('url', ''))
    texts = [item.get('title') or '', item.get('snippet') or '',
             unquote(parsed.path or '')]
    found = []

    for field, value in fields.items():
        if field == 'nome':
            target = _normalize_words(value)
            if len(target.split()) >= 2 and any(
                ' ' + target + ' ' in ' ' + _normalize_words(t) + ' '
                for t in texts
            ):
                found.append(field)
        elif field == 'email':
            val = value.casefold()
            # Limites de identificador para evitar emails que so o contem em parte.
            pattern = r'(?<![a-z0-9_.+\-])' + re.escape(val) + r'(?![a-z0-9_.\-])'
            if any(re.search(pattern, html.unescape(unquote(t)).casefold()) for t in texts):
                found.append(field)
        elif field == 'usuario':
            val = value.casefold().lstrip('@')
            pattern = r'(?<![a-z0-9_.\-])@' + re.escape(val) + r'(?![a-z0-9_.\-])'
            in_text = any(re.search(pattern, str(t).casefold()) for t in texts[:2])
            segments = [x.casefold().lstrip('@') for x in unquote(parsed.path).strip('/').split('/')]
            if in_text or val in segments:
                found.append(field)
        elif field in ('telefone', 'cpf'):
            variants = [re.sub(r'\D', '', value)]
            if field == 'telefone' and variants[0].startswith('55') and len(variants[0]) in (12, 13):
                variants.append(variants[0][2:])
            if any(_digits_match(v, str(t)) for v in variants for t in texts):
                found.append(field)
    return found


# AUDITORIA_CONSOLIDADA_V5
# Nao substitui nem modifica o matcher anterior: agrega os dois.
from spiderfoot.blueprints.personal_audit_matcher_usuario import (
    _matches_from_index as _matches_from_index_usuario
)
_matches_from_index_anterior = _matches_from_index
def _matches_from_index(item, fields):
    anterior = _matches_from_index_anterior(item, fields)
    enviado = _matches_from_index_usuario(item, fields)
    return list(dict.fromkeys(anterior + enviado))

def _queries(fields: dict):
    out = []
    for key in ('nome', 'email', 'usuario', 'telefone', 'cpf'):
        if key not in fields:
            continue
        value = fields[key]
        if key == 'telefone':
            digits = re.sub(r'\D', '', value)
            out.append((key, '"' + digits + '"'))
            if digits.startswith('55') and len(digits) in (12, 13):
                out.append((key, '"+' + digits + '"'))
            elif value.startswith('+'):
                out.append((key, '"' + value + '"'))
        elif key == 'cpf':
            digits = value
            fmt = f'{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}'
            out.extend([(key, '"' + digits + '"'),
                        (key, '"' + fmt + '"')])
        else:
            out.append((key, '"' + value + '"'))
    return out[:8]


def _rss_search(s: requests.Session, query: str):
    try:
        r = s.get('https://www.bing.com/search',
                  params={'q': query, 'format': 'rss', 'count': 10,
                          'setlang': 'pt-BR'}, timeout=(5, 10),
                  allow_redirects=False)
        if r.status_code != 200 or len(r.content) > 250000:
            return None
        root = ElementTree.fromstring(r.content)
        items = root.findall('./channel/item')
        hits = []
        for item in items:
            url = (item.findtext('link') or '').strip()
            if url.startswith('https://') and len(url) <= 1800:
                hits.append({
                    'url': url,
                    'title': html.unescape((item.findtext('title') or '')[:220]),
                    'snippet': re.sub(r'<[^>]*>', '', html.unescape(
                        (item.findtext('description') or '')[:500])),
                })
        return hits[:10]
    except (requests.RequestException, ElementTree.ParseError, ValueError):
        return None


class _DDGParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._current = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == 'a' and 'result__a' in (d.get('class') or ''):
            href = d.get('href', '')
            if href.startswith('//'):
                href = 'https:' + href
            if 'duckduckgo.com/l/' in href:
                href = parse_qs(urlparse(href).query).get('uddg', [''])[0]
            self._current = href
            self._text = []

    def handle_data(self, data):
        if self._current is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self._current is not None:
            if self._current.startswith('https://'):
                self.links.append({
                    'url': self._current,
                    'title': ' '.join(self._text).strip()[:220],
                    'snippet': '',
                })
            self._current = None


def _ddg_search(s, query):
    try:
        r = s.get('https://html.duckduckgo.com/html/',
                  params={'q': query}, timeout=(5, 10),
                  allow_redirects=False)
        if r.status_code != 200 or len(r.content) > 350000:
            return None
        p = _DDGParser()
        p.feed(r.text)
        return p.links[:10]
    except (requests.RequestException, ValueError):
        return None


class _PhotosParser(HTMLParser):
    def __init__(self, tokens):
        super().__init__()
        self.tokens = tokens
        self.pictures = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            typ = (attrs.get('property') or attrs.get('name') or '').lower()
            if typ in ('og:image', 'twitter:image', 'og:image:secure_url'):
                self.pictures.append((attrs.get('content', ''), 'Imagem da pagina'))
        elif tag == 'img':
            alt = (attrs.get('alt') or '').lower()
            if self.tokens and all(token in alt for token in self.tokens):
                self.pictures.append((attrs.get('src') or attrs.get('data-src') or '',
                                      (attrs.get('alt') or 'Imagem associada')[:180]))



class _BingImagesParser(HTMLParser):
    """Best effort: image search HTML may change or require a CAPTCHA."""
    def __init__(self):
        super().__init__()
        self.results = []

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        attrs = dict(attrs)
        if 'iusc' not in (attrs.get('class') or ''):
            return
        try:
            meta = json.loads(attrs.get('m', '{}'))
            img_url = meta.get('murl', '')
            page_url = meta.get('purl', '')
            if img_url.startswith('https://') and page_url.startswith('https://'):
                self.results.append((img_url, page_url, meta.get('t', '')[:180]))
        except (ValueError, TypeError):
            pass


def _search_indexed_images(s, label: str):
    """Extra candidate images via a public image index; empty if blocked."""
    if not label:
        return []
    try:
        r = s.get('https://www.bing.com/images/search',
                  params={'q': '"' + label + '"', 'first': '1'},
                  timeout=(5, 12), allow_redirects=False)
        if r.status_code != 200 or len(r.content) > 1200000:
            return []
        parser = _BingImagesParser()
        parser.feed(r.text)
        return parser.results[:12]
    except (requests.RequestException, ValueError):
        return []



def _detect_photos(s, root: Path, run_id: str, sources, name_hint: str):
    tokens = [w.lower() for w in re.findall(r'\w+', name_hint)
              if len(w) >= 4][:4]
    images = []
    seen = set()
    # CORRECAO: nao pesquisar imagens pelo nome isolado em indices gerais.
    # Apenas paginas que passaram na verificacao textual podem gerar fotos.
    # Sem reconhecimento de rosto e sem afirmacao de que a foto seja do usuario.
    page_count = 0
    for source in sources:
        if len(images) >= MAX_IMAGES or page_count >= MAX_FETCH_PAGES:
            break
        if source['tipo'] not in ('nome', 'usuario', 'email', 'perfil_informado'):
            continue
        # Do not fetch photos from unrelated CPF/telephone listings.
        page_count += 1
        page = _safe_get(s, source['url'], MAX_HTML_BYTES,
                         ('text/html', 'application/xhtml+xml'))
        if not page:
            continue
        raw, _media, final_url = page
        parser = _PhotosParser(tokens)
        try:
            parser.feed(raw.decode('utf-8', errors='replace'))
        except Exception:
            continue
        for img_url, alt in parser.pictures[:8]:
            img_url = urljoin(final_url, img_url)
            if img_url in seen or len(images) >= MAX_IMAGES:
                continue
            seen.add(img_url)
            fetched = _safe_get(s, img_url, MAX_IMAGE_BYTES,
                                tuple(ALLOWED_IMAGES))
            if not fetched:
                continue
            content, mime, actual_url = fetched
            ident = secrets.token_hex(12)
            filename = f'{run_id}_{ident}{ALLOWED_IMAGES[mime]}'
            (root / 'imagens' / filename).write_bytes(content)
            images.append({
                'arquivo': filename,
                'origem': source['url'],
                'titulo': source['title'],
                'alt': alt,
                'url_origem_imagem': actual_url,
                'confirmacao': 'pendente',
            })
    return images


def _save_run(root: Path, run_id: str, data: dict):
    target = root / 'runs' / (run_id + '.json')
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=target.parent,
                                         prefix='.tmp-', delete=False) as fh:
            temp_name = fh.name
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(temp_name, target)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _read_run(root, run_id):
    if not re.fullmatch(r'[a-f0-9]{32}', run_id):
        abort(404)
    path = root / 'runs' / (run_id + '.json')
    try:
        return json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        abort(404)


def _history(root):
    entries = []
    for path in sorted((root / 'runs').glob('*.json'), reverse=True)[:20]:
        try:
            obj = json.loads(path.read_text('utf-8'))
            entries.append({
                'id': path.stem,
                'quando': obj.get('quando', ''),
                'fontes': len(obj.get('fontes', [])),
                'fotos': len(obj.get('fotos', [])),
                'tipos': ', '.join(obj.get('tipos', [])),
            })
        except (OSError, ValueError):
            continue
    return entries


@personal_audit_bp.get('/minha-exposicao')
def index():
    from spiderfoot.__version__ import __version__
    response = render_template('pages/personal_audit.html',
                               page_id='PERSONAL_AUDIT', version=__version__,
                               history=_history(_root()), report=None,
                               audit_csrf=_AUDIT_CSRF)
    return _privacy_headers(make_response(response))


@personal_audit_bp.post('/minha-exposicao/pesquisar')
def search():
    if not request.is_json:
        return jsonify(error='Requisicao JSON necessaria.'), 400
    try:
        data = request.get_json(silent=True) or {}
        fields = _inputs(data)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    run_id = secrets.token_hex(16)
    root = _root()
    sources = []
    seen_urls = set()
    warnings = []
    descartadas = 0
    # AUDITORIA_AMPLIADA_V4: varios indices/paginas e classificador de confianca.
    from spiderfoot.blueprints.personal_audit_deep import realizar_busca, MODOS
    modo = str(data.get('modo') or 'profunda')
    if modo not in MODOS:
        return jsonify(error='Modo de pesquisa invalido.'), 400
    perfis = data.get('perfis_confirmados') or ''
    if not isinstance(perfis, str) or len(perfis) > 8000:
        return jsonify(error='Lista de perfis muito extensa.'), 400
    with _session() as s:
        sources, resumo_busca, avisos_busca = realizar_busca(
            s, fields, modo, perfis,
            _valid_public_url, _matches_from_index,
        )
        warnings.extend(avisos_busca)
        descartadas = resumo_busca['descartadas']
        fotos = []
        if data.get('coletar_fotos') is True:
            fotos = _detect_photos(s, root, run_id, sources,
                                  fields.get('nome') or fields.get('usuario') or '')
            if not fotos:
                warnings.append('Nenhuma imagem candidata recuperada das paginas publicas consultadas.')

    report = {
        'filtro_exato': True,
        'descartadas': descartadas,
        'versao_busca': 'ampliada-v4',
        'modo': modo,
        'estatisticas': resumo_busca,
        'id': run_id,
        'quando': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'tipos': list(fields.keys()),
        'fontes': sources,
        'fotos': fotos,
        'avisos': warnings,
        'consultas': resumo_busca['consultas'],
    }
    _save_run(root, run_id, report)
    resp = jsonify(url=url_for('personal_audit.result', run_id=run_id),
                   fontes=len(sources), fotos=len(fotos))
    return _privacy_headers(resp)


@personal_audit_bp.get('/minha-exposicao/resultado/<run_id>')
def result(run_id):
    from spiderfoot.__version__ import __version__
    root = _root()
    report = _read_run(root, run_id)
    response = render_template('pages/personal_audit.html',
                               page_id='PERSONAL_AUDIT', version=__version__,
                               history=_history(root), report=report,
                               audit_csrf=_AUDIT_CSRF)
    return _privacy_headers(make_response(response))


@personal_audit_bp.get('/minha-exposicao/imagem/<run_id>/<filename>')
def image(run_id, filename):
    root = _root()
    report = _read_run(root, run_id)
    if not any(item.get('arquivo') == filename for item in report.get('fotos', [])):
        abort(404)
    path = root / 'imagens' / Path(filename).name
    if not path.is_file():
        abort(404)
    response = send_file(path, conditional=True)
    return _privacy_headers(response)


@personal_audit_bp.post('/minha-exposicao/foto/<run_id>/<int:index>')
def confirm_image(run_id, index):
    root = _root()
    report = _read_run(root, run_id)
    answer = (request.get_json(silent=True) or {}).get('confirmacao')
    if answer not in ('minha', 'nao_minha', 'pendente'):
        return jsonify(error='Valor de confirmacao invalido.'), 400
    images = report.get('fotos', [])
    if index < 0 or index >= len(images):
        abort(404)
    images[index]['confirmacao'] = answer
    _save_run(root, run_id, report)
    return jsonify(ok=True)


@personal_audit_bp.post('/minha-exposicao/excluir/<run_id>')
def delete_run(run_id):
    root = _root()
    report = _read_run(root, run_id)
    for item in report.get('fotos', []):
        filename = item.get('arquivo', '')
        if filename.startswith(run_id + '_') and Path(filename).name == filename:
            (root / 'imagens' / filename).unlink(missing_ok=True)
    (root / 'runs' / (run_id + '.json')).unlink(missing_ok=True)
    return jsonify(ok=True, url=url_for('personal_audit.index'))
