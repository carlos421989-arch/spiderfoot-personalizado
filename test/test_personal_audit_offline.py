"""Testes offline da extensão Minha Exposição Digital (sem rede real).

Executar em uma cópia de desenvolvimento: python -m pytest -q test/test_personal_audit_offline.py
Os testes usam Flask fake APENAS quando Flask não está instalado, portanto
não substituem os testes de integração com Flask real no projeto Windows.
"""
import importlib
import io
from pathlib import Path
import socket
import sys
import types
from urllib.parse import urlparse

import pytest
import requests


@pytest.fixture()
def modules(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    # Carrega somente os módulos do pacote local sob teste, sem importar
    # o SpiderFoot completo nem abrir bancos ou iniciar serviços.
    spiderfoot = types.ModuleType('spiderfoot')
    spiderfoot.__path__ = [str(root / 'spiderfoot')]
    blueprints = types.ModuleType('spiderfoot.blueprints')
    blueprints.__path__ = [str(root / 'spiderfoot' / 'blueprints')]
    version = types.ModuleType('spiderfoot.__version__')
    version.__version__ = '5.2.0'

    class Blueprint:
        def __init__(self, name, module):
            self.routes = {}
            self.name = name
        def get(self, route):
            def deco(fn): self.routes[(route, 'GET')] = fn; return fn
            return deco
        def post(self, route):
            def deco(fn): self.routes[(route, 'POST')] = fn; return fn
            return deco
        def before_request(self, fn):
            self.guard = fn
            return fn
        def after_request(self, fn):
            self.response_guard = fn
            return fn

    flask_stub = types.ModuleType('flask')
    flask_stub.Blueprint = Blueprint
    flask_stub.session = {}
    flask_stub.request = types.SimpleNamespace(method='GET', content_length=0, headers={}, is_json=True)
    flask_stub.abort = lambda status: (_ for _ in ()).throw(LookupError(status))
    flask_stub.jsonify = lambda **data: data
    flask_stub.render_template = lambda name, **data: {'template': name, **data}
    flask_stub.make_response = lambda data: types.SimpleNamespace(headers={}, data=data)
    flask_stub.url_for = lambda endpoint, **args: endpoint + str(args)
    flask_stub.send_file = lambda path, **kwargs: types.SimpleNamespace(headers={}, path=path)

    for k, v in {'flask': flask_stub, 'spiderfoot': spiderfoot,
                 'spiderfoot.blueprints': blueprints,
                 'spiderfoot.__version__': version}.items():
        monkeypatch.setitem(sys.modules, k, v)
    for k in ('spiderfoot.blueprints.personal_audit',
              'spiderfoot.blueprints.personal_audit_matcher_usuario',
              'spiderfoot.blueprints.personal_audit_deep'):
        monkeypatch.delitem(sys.modules, k, raising=False)
    deep = importlib.import_module('spiderfoot.blueprints.personal_audit_deep')
    # Compatibilidade: testes legados verificam exclusivamente Bing e DDG.
    deep.sources_provider._actual_available_engines = deep.sources_provider.available_engines
    monkeypatch.setattr(deep.sources_provider, 'available_engines',
                        lambda: ['Bing RSS', 'DuckDuckGo'])
    audit = importlib.import_module('spiderfoot.blueprints.personal_audit')
    return audit, deep, flask_stub


@pytest.mark.parametrize('value', [None, [], 1, True, 'texto', {'nome': []}, {'email': None}])
def test_json_invalido_rejeitado(modules, value):
    audit, _, _ = modules
    with pytest.raises(ValueError):
        audit._inputs(value)


def test_inputs_consentimento_e_positivas(modules):
    audit, _, _ = modules
    with pytest.raises(ValueError, match='Confirme'):
        audit._inputs({'nome': 'Maria Exemplo'})
    result = audit._inputs({'nome': 'Maria Exemplo', 'usuario': '@maria', 'consentimento': True})
    assert result == {'nome': 'Maria Exemplo', 'usuario': 'maria'}


def test_sem_identidade_automaticamente_confirmada(modules):
    audit, _, _ = modules
    fields = {'nome': 'Maria Exemplo', 'email': 'maria@example.test'}
    item = {'url': 'https://example.org/maria', 'title': 'Maria Exemplo',
            'snippet': 'Contato: maria@example.test'}
    matches = audit._matches_from_index(item, fields)
    assert set(matches) == {'nome', 'email'}
    assert 'identidade_confirmada' not in matches
    assert audit._matches_from_index(
        {'url': 'https://example.org/joao', 'title': 'João Sousa', 'snippet': 'Sem dados'}, fields) == []


def test_homonimo_nao_comprova_identidade(modules):
    audit, _, _ = modules
    result = audit._matches_from_index(
        {'url': 'https://example.org/perfil', 'title': 'Maria Exemplo', 'snippet': ''},
        {'nome': 'Maria Exemplo', 'email': 'outra@example.test'})
    assert result == ['nome']


def test_normalizacao_e_deduplicacao(modules):
    audit, deep, _ = modules
    assert audit._normalize_words('Ána   MARÍA!') == 'ana maria'
    assert deep._canonical('https://EXAMPLE.ORG/p/?b=2&utm_source=x&a=1#frag') == \
           deep._canonical('https://example.org/p?a=1&b=2')


def test_orcamento_requisicoes_buscadores(modules, monkeypatch):
    _, deep, _ = modules
    calls = []
    def bing(session, q, offset):
        calls.append(('bing', q, offset))
        return [], False
    def ddg(session, q, offset):
        calls.append(('ddg', q, offset))
        return [], False
    monkeypatch.setattr(deep, '_bing_rss', bing)
    monkeypatch.setattr(deep, '_ddg', ddg)
    sources, stats, warnings = deep.realizar_busca(
        None, {'nome': 'Maria Exemplo', 'usuario': 'maria'}, 'profunda', '',
        lambda _: True, lambda *a: [], max_requests=3)
    assert sources == []
    assert stats['requisicoes'] == len(calls) == 3
    assert any('Limite' in w for w in warnings)


def test_buscadores_http_falha_sem_rede(modules):
    _, deep, _ = modules
    class Session:
        def get(self, *args, **kwargs):
            raise requests.Timeout('tempo esgotado')
    assert deep._bing_rss(Session(), 'termo ficticio', 0) == ([], True)
    assert deep._ddg(Session(), 'termo ficticio', 0) == ([], True)


def test_bing_rss_resposta_invalida(modules):
    _, deep, _ = modules
    class Response:
        status_code = 200
        def iter_content(self, chunk_size):
            yield b'<rss><xml invalido'
        def close(self): pass
    class Session:
        def get(self, *args, **kwargs): return Response()
    assert deep._bing_rss(Session(), 'termo ficticio', 0) == ([], True)


def test_token_csrf_por_sessao(modules):
    audit, _, flask = modules
    first = audit._audit_csrf()
    assert first == audit._audit_csrf()
    flask.session.clear()
    second = audit._audit_csrf()
    assert first != second
    flask.request.method = 'POST'
    flask.request.headers = {'X-Audit-CSRF': first}
    assert audit._check_write_request()[1] == 403
    flask.request.headers = {'X-Audit-CSRF': second}
    assert audit._check_write_request() is None


def test_rotas_registradas_e_renderizacao_stub(modules, monkeypatch, tmp_path):
    audit, _, flask = modules
    routes = audit.personal_audit_bp.routes
    for route, method in [('/minha-exposicao', 'GET'),
                          ('/minha-exposicao/pesquisar', 'POST'),
                          ('/minha-exposicao/resultado/<run_id>', 'GET'),
                          ('/minha-exposicao/imagem/<run_id>/<filename>', 'GET'),
                          ('/minha-exposicao/foto/<run_id>/<int:index>', 'POST'),
                          ('/minha-exposicao/excluir/<run_id>', 'POST')]:
        assert (route, method) in routes
    monkeypatch.setenv('SPIDERFOOT_DATA', str(tmp_path))
    response = audit.index()
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.data['template'] == 'pages/personal_audit.html'
    assert response.data['audit_csrf'] == flask.session['_personal_audit_csrf']


def test_json_http_route_retorna_400(modules):
    audit, _, flask = modules
    flask.request.is_json = True
    flask.request.get_json = lambda silent: ['JSON', 'lista']
    reply, status = audit.search()
    assert status == 400
    assert 'objeto' in reply['error']


def test_url_privada_rejeitada(modules, monkeypatch):
    audit, _, _ = modules
    def dns(host, *args, **kwargs):
        address = '127.0.0.1' if host in ('localhost', 'rebind.test') else '93.184.216.34'
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]
    monkeypatch.setattr(audit.socket, 'getaddrinfo', dns)
    assert not audit._valid_public_url('https://localhost/')
    assert not audit._valid_public_url('https://rebind.test/segredo')
    assert not audit._valid_public_url('http://example.org/')
    assert not audit._valid_public_url('https://127.0.0.1/')
    assert audit._valid_public_url('https://example.org/')


class FakeHTTPResponse:
    def __init__(self, status=200, data=b'ok', mime='text/html', location=None):
        self.status = status
        self.buffer = io.BytesIO(data)
        self.headers = {'Content-Type': mime, 'Location': location}
    def getheader(self, name, default=''):
        return self.headers.get(name) or default
    def read(self, length): return self.buffer.read(length)


def test_https_ip_fixado_e_redirecionamento_privado(modules, monkeypatch):
    audit, _, _ = modules
    requests_to = []
    class Connection:
        def __init__(self, hostname, address):
            requests_to.append((hostname, address))
        def request(self, method, path, headers=None):
            requests_to.append((method, path, headers['Host']))
        def getresponse(self):
            return FakeHTTPResponse(302, location='https://127.0.0.1/admin')
        def close(self): pass
    monkeypatch.setattr(audit, '_PinnedHTTPSConnection', Connection)
    monkeypatch.setattr(audit, '_resolve_public_url', lambda url:
        None if url.startswith('https://127.') else (urlparse(url), '93.184.216.34'))
    s = types.SimpleNamespace(headers={'User-Agent': 'Teste'})
    assert audit._safe_get(s, 'https://example.org/old', 100, ('text/html',)) is None
    assert requests_to[0] == ('example.org', '93.184.216.34')
    assert len([v for v in requests_to if v[0] == 'example.org']) == 1


def test_https_limite_bytes_e_requisicoes(modules, monkeypatch):
    audit, _, _ = modules
    class Connection:
        def __init__(self, hostname, address): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return FakeHTTPResponse(200, b'0123456789')
        def close(self): pass
    monkeypatch.setattr(audit, '_PinnedHTTPSConnection', Connection)
    monkeypatch.setattr(audit, '_resolve_public_url', lambda url:
        (urlparse(url), '93.184.216.34'))
    s = types.SimpleNamespace(headers={})
    budget = {'remaining': 1, 'used': 0, 'deadline': float('inf')}
    assert audit._safe_get(s, 'https://example.org/', 5, ('text/html',), budget) is None
    assert budget == {'remaining': 0, 'used': 1, 'deadline': float('inf')}
    assert audit._safe_get(s, 'https://example.org/', 20, ('text/html',), budget) is None


def test_excluir_apenas_imagem_associada(modules, monkeypatch, tmp_path):
    audit, _, flask = modules
    monkeypatch.setenv('SPIDERFOOT_DATA', str(tmp_path))
    root = audit._root()
    run = 'a' * 32
    filename = run + '_001.png'
    outside = root / 'imagens' / 'nao_apagar.png'
    own = root / 'imagens' / filename
    own.write_bytes(b'image')
    outside.write_bytes(b'preserve')
    audit._save_run(root, run, {'audit_owner': audit._audit_owner_id(), 'fotos': [{'arquivo': filename},
                                           {'arquivo': '../../nao_apagar.png'}]})
    response = audit.delete_run(run)
    assert response['ok']
    assert not own.exists()
    assert outside.exists()
    assert not (root / 'runs' / (run + '.json')).exists()


def test_template_tem_csrf_duplo_e_confirmacao_manual(modules):
    audit, _, _ = modules
    template = Path(audit.__file__).parents[1] / 'templates' / 'pages' / 'personal_audit.html'
    content = template.read_text(encoding='utf-8')
    assert "const csrf = {{ audit_csrf | tojson }}" in content
    assert 'X-CSRF-Token' in content
    assert 'X-Audit-CSRF' in content
    assert 'É minha' in content
    assert 'Não é minha' in content


def test_buscador_limita_tamanho_e_fecha_resposta(modules):
    _, deep, _ = modules
    class OversizeResponse:
        status_code = 200
        closed = False
        def iter_content(self, chunk_size):
            for _ in range(20):
                yield b'A' * 32768
        def close(self): self.closed = True
    r = OversizeResponse()
    class Session:
        def get(self, *args, **kwargs):
            assert kwargs['stream'] is True
            assert kwargs['allow_redirects'] is False
            return r
    assert deep._bing_rss(Session(), 'identificador-ficticio', 0) == ([], True)
    assert r.closed


def test_blueprint_registrado_na_fabrica_app():
    path = Path(__file__).resolve().parents[1] / 'spiderfoot' / 'app.py'
    code = path.read_text(encoding='utf-8')
    assert 'from spiderfoot.blueprints.personal_audit import personal_audit_bp' in code
    assert 'app.register_blueprint(personal_audit_bp)' in code


def test_busca_dedup_e_classificacao_de_homonimo(modules, monkeypatch):
    audit, deep, _ = modules
    item1 = {'url': 'https://example.org/perfil/?utm_source=abc&a=1&b=2',
             'title': 'Maria Exemplo', 'snippet': 'Perfil público'}
    item2 = {'url': 'https://EXAMPLE.org/perfil?b=2&a=1',
             'title': 'Maria Exemplo', 'snippet': 'Perfil público'}
    monkeypatch.setattr(deep, 'variantes', lambda fields: [('nome', 'Maria Exemplo')])
    monkeypatch.setattr(deep, '_bing_rss', lambda *a: ([dict(item1)], False))
    monkeypatch.setattr(deep, '_ddg', lambda *a: ([dict(item2)], False))
    rows, stats, warnings = deep.realizar_busca(
        None, {'nome': 'Maria Exemplo'}, 'ampliada', '',
        lambda url: True, audit._matches_from_index, max_requests=2)
    assert rows == []  # nome sozinho nao e exposicao pessoal
    assert stats['homonimos_descartados'] == 2
    assert stats['aceitos_por_mecanismo']['Bing RSS'] == 0
    assert stats['aceitos_por_mecanismo']['DuckDuckGo'] == 0
    assert any('possivel homonimia' in w for w in warnings)
    assert stats['requisicoes'] == 2


def test_validacao_cpf_com_consentimento_especifico(modules):
    audit, _, _ = modules
    with pytest.raises(ValueError, match='Autorize'):
        audit._inputs({'cpf': '529.982.247-25', 'consentimento': True})
    result = audit._inputs({'cpf': '529.982.247-25', 'consentimento': True,
                            'autorizar_cpf': True})
    assert result['cpf'] == '52998224725'


def test_entrada_nao_completa_nao_coincide(modules):
    audit, _, _ = modules
    item = {'url': 'https://example.org/', 'title': 'Maria Exemplo Silva',
            'snippet': 'maria+novo@example.test'}
    assert audit._matches_from_index(item, {
        'nome': 'Maria Exemplo Sousa', 'email': 'maria@example.test'}) == []


def test_fotos_candidatas_offline_e_arquivo_criado(modules, monkeypatch, tmp_path):
    audit, _, _ = modules
    root = tmp_path
    (root / 'imagens').mkdir()
    data = {'https://example.org/perfil': (b'<meta property="og:image" content="/foto.jpg">', 'text/html',
                                              'https://example.org/perfil'),
            'https://example.org/foto.jpg': (b'foto-simulada', 'image/jpeg', 'https://example.org/foto.jpg')}
    def fake_safe_get(s, url, limit, mime_types, budget):
        if budget['remaining'] <= 0:
            return None
        budget['remaining'] -= 1
        budget['used'] += 1
        return data[url]
    monkeypatch.setattr(audit, '_safe_get', fake_safe_get)
    budget = {'remaining': 2, 'used': 0, 'deadline': float('inf')}
    photos = audit._detect_photos(None, root, 'a' * 32,
        [{'tipo': 'email', 'url': 'https://example.org/perfil',
          'title': 'Maria Exemplo', 'confianca': 'identificador_literal',
          'coincidencias': ['email']}],
        'Maria Exemplo', budget)
    assert len(photos) == 1
    assert photos[0]['confirmacao'] == 'pendente'
    assert (root / 'imagens' / photos[0]['arquivo']).read_bytes() == b'foto-simulada'
    assert budget['used'] == 2


def test_modo_lista_rejeitado_sem_500(modules):
    audit, _, flask = modules
    flask.request.is_json = True
    flask.request.get_json = lambda silent: {'nome': 'Maria Exemplo',
              'consentimento': True, 'modo': []}
    reply, status = audit.search()
    assert status == 400
    assert 'Modo' in reply['error']


def test_confirmacao_json_lista_rejeitada_sem_500(modules, monkeypatch, tmp_path):
    audit, _, flask = modules
    monkeypatch.setenv('SPIDERFOOT_DATA', str(tmp_path))
    run_id = 'b' * 32
    root = audit._root()
    audit._save_run(root, run_id, {'audit_owner': audit._audit_owner_id(), 'fotos': [{'arquivo': run_id + '_x.png',
                                            'confirmacao': 'pendente'}]})
    flask.request.get_json = lambda silent: ['minha']
    reply, status = audit.confirm_image(run_id, 0)
    assert status == 400
    assert 'objeto' in reply['error']


def test_historico_e_resultados_isolados_por_sessao(modules, monkeypatch, tmp_path):
    audit, _, flask = modules
    monkeypatch.setenv('SPIDERFOOT_DATA', str(tmp_path))
    root = audit._root()
    run_id = 'c' * 32
    sessao_a = audit._audit_owner_id()
    audit._save_run(root, run_id, {'audit_owner': sessao_a,
                                   'tipos': ['nome'], 'fontes': [], 'fotos': []})
    assert len(audit._history(root)) == 1
    assert audit._read_run(root, run_id)['audit_owner'] == sessao_a

    flask.session.clear()  # outro navegador/operador
    assert audit._history(root) == []
    with pytest.raises(LookupError):
        audit._read_run(root, run_id)
    with pytest.raises(LookupError):
        audit.delete_run(run_id)
    assert (root / 'runs' / (run_id + '.json')).exists()


def test_registros_legados_nao_sao_expostos(modules, monkeypatch, tmp_path):
    audit, _, _ = modules
    monkeypatch.setenv('SPIDERFOOT_DATA', str(tmp_path))
    root = audit._root()
    run_id = 'd' * 32
    audit._save_run(root, run_id, {'fontes': [], 'fotos': [], 'tipos': []})
    assert audit._history(root) == []
    with pytest.raises(LookupError):
        audit._read_run(root, run_id)


def test_respostas_do_blueprint_sem_cache(modules):
    audit, _, _ = modules
    resp = types.SimpleNamespace(headers={})
    assert audit._protect_all_responses(resp) is resp
    assert resp.headers['Cache-Control'] == 'no-store'
    assert resp.headers['Referrer-Policy'] == 'no-referrer'


def test_diagnostico_falhas_por_buscador_sem_termos_pesquisados(modules, monkeypatch):
    _, deep, _ = modules
    monkeypatch.setattr(deep, 'variantes', lambda fields: [('nome', 'Pessoa Teste'), ('usuario', 'conta_teste')])
    monkeypatch.setattr(deep, '_bing_rss', lambda *args: ([], True))
    monkeypatch.setattr(deep, '_ddg', lambda *args: ([], False))
    sources, stats, warnings = deep.realizar_busca(
        None, {'nome': 'Pessoa Teste'}, 'ampliada', '',
        lambda url: True, lambda *args: [], max_requests=4)
    assert sources == []
    assert stats['mecanismos'] == {'Bing RSS': 2, 'DuckDuckGo': 2}
    assert stats['falhas_por_mecanismo'] == {'Bing RSS': 2, 'DuckDuckGo': 0}
    assert stats['falhas'] == 2
    assert sum(stats['mecanismos'].values()) == stats['requisicoes'] == 4
    assert any('respostas inutilizaveis' in aviso for aviso in warnings)
    assert 'Pessoa Teste' not in repr(stats)


def test_contraste_e_diagnostico_no_template():
    page = (Path(__file__).resolve().parents[1] / 'spiderfoot' / 'templates' /
            'pages' / 'personal_audit.html').read_text(encoding='utf-8')
    assert '#btn-auditar {' in page
    assert 'background-color: #1d4ed8 !important' in page
    assert '.audit-history-link {' in page
    assert 'color: #f8fafc !important' in page
    assert 'Diagnóstico dos buscadores' in page
    assert "report.estatisticas.get('falhas_por_mecanismo')" in page


def test_buscador_com_falhas_repetidas_e_pausado_sem_prejudicar_outro(modules, monkeypatch):
    _, deep, _ = modules
    # Seis consultas ficticias; 3 falhas consecutivas desativam somente DDG.
    monkeypatch.setattr(deep, 'variantes', lambda fields:
                        [('nome', f'Pessoa Ficticia {i}') for i in range(6)])
    calls = {'bing': 0, 'ddg': 0}
    def bing(*args):
        calls['bing'] += 1
        return [], False
    def ddg(*args):
        calls['ddg'] += 1
        return [], True
    monkeypatch.setattr(deep, '_bing_rss', bing)
    monkeypatch.setattr(deep, '_ddg', ddg)
    sources, stats, warnings = deep.realizar_busca(
        None, {'nome': 'Pessoa Ficticia'}, 'profunda', '',
        lambda _: True, lambda *args: [], max_requests=25)
    assert not sources
    assert calls['ddg'] == 3
    assert calls['bing'] > 3
    assert stats['mecanismos_pausados'] == ['DuckDuckGo']
    assert stats['requisicoes'] == calls['bing'] + calls['ddg']
    assert stats['falhas_por_mecanismo']['DuckDuckGo'] == 3
    assert any('interrompidas' in warning for warning in warnings)


def test_falhas_intercaladas_nao_pausam_fonte(modules, monkeypatch):
    _, deep, _ = modules
    monkeypatch.setattr(deep, 'variantes', lambda fields:
                        [('nome', f'Pessoa Ficticia {i}') for i in range(6)])
    counter = [0]
    monkeypatch.setattr(deep, '_bing_rss', lambda *a: ([], False))
    def ddg(*args):
        counter[0] += 1
        return [], (counter[0] % 2 == 0)
    monkeypatch.setattr(deep, '_ddg', ddg)
    sources, stats, _ = deep.realizar_busca(
        None, {'nome': 'Pessoa Ficticia'}, 'ampliada', '',
        lambda _: True, lambda *a: [], max_requests=24)
    assert not sources
    assert counter[0] >= 6
    assert stats['mecanismos_pausados'] == []


def test_outro_identificador_completo_pode_validar_uma_pagina(modules, monkeypatch):
    _, deep, _ = modules
    query_item = {'url': 'https://example.org/perfil', 'title': 'Perfil',
                  'snippet': 'Contato: pessoa@example.test'}
    monkeypatch.setattr(deep, 'variantes', lambda fields: [('nome', 'Pessoa Ficticia')])
    monkeypatch.setattr(deep, '_bing_rss', lambda *a: ([query_item], False))
    monkeypatch.setattr(deep, '_ddg', lambda *a: ([], False))
    sources, stats, _ = deep.realizar_busca(
        None, {'nome': 'Pessoa Ficticia', 'email': 'pessoa@example.test'},
        'rapida', '', lambda _: True,
        lambda item, fields: ['email'] if 'pessoa@example.test' in item['snippet'] else [],
        max_requests=2)
    assert len(sources) == 1
    assert sources[0]['tipo'] == 'email'
    assert sources[0]['coincidencias'] == ['email']
    assert sources[0]['confianca'] == 'identificador_literal'
    assert stats['brutos_por_mecanismo']['Bing RSS'] == 1
    assert stats['aceitos_por_mecanismo']['Bing RSS'] == 1
    assert stats['descartados_por_mecanismo']['Bing RSS'] == 0


def test_resultado_sem_identificador_continua_descartado(modules, monkeypatch):
    _, deep, _ = modules
    monkeypatch.setattr(deep, 'variantes', lambda fields: [('nome', 'Pessoa Ficticia')])
    monkeypatch.setattr(deep, '_bing_rss', lambda *a: (
        [{'url': 'https://example.org/generico', 'title': 'Pagina generica',
          'snippet': 'Sem correspondencia'}], False))
    monkeypatch.setattr(deep, '_ddg', lambda *a: ([], False))
    sources, stats, _ = deep.realizar_busca(
        None, {'nome': 'Pessoa Ficticia'}, 'rapida', '',
        lambda _: True, lambda *a: [], max_requests=2)
    assert sources == []
    assert stats['descartados_por_mecanismo']['Bing RSS'] == 1

# ---------------- Novos conectores - todas as respostas sao simuladas ----------------

def test_fontes_publicas_e_opcionais_configuradas(modules, monkeypatch):
    _, deep, _ = modules
    prov = deep.sources_provider
    keys = ('MED_BRAVE_API_KEY', 'MED_MOJEEK_API_KEY', 'MED_TAVILY_API_KEY',
            'MED_GOOGLE_API_KEY', 'MED_GOOGLE_CX', 'MED_SEARXNG_URL',
            'MED_SERPAPI_API_KEY', 'MED_SEARCH_ENGINES')
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    assert len(prov._actual_available_engines()) == 6
    assert 'Google Noticias RSS' in prov._actual_available_engines()
    for key in keys[:-1]:
        monkeypatch.setenv(key, 'teste_credencial_falsa')
    engines = prov._actual_available_engines()
    assert len(engines) == 18
    assert all(name in engines for name in ('Brave API', 'SearXNG', 'Mojeek API',
                                           'Tavily API', 'SerpApi Yandex',
                                           'SerpApi Naver', 'Google Custom Search (legado)'))
    monkeypatch.setenv('MED_SEARCH_ENGINES', 'Mojeek API, Wikipedia PT')
    assert prov._actual_available_engines() == ['Wikipedia PT', 'Mojeek API']


def test_fontes_especializadas_nao_recebem_cpf_email_ou_telefone(modules):
    _, deep, _ = modules
    prov = deep.sources_provider
    for engine in prov.SPECIALIZED:
        assert prov.supports(engine, 'nome')
        for field in ('email', 'cpf', 'telefone', 'usuario'):
            assert not prov.supports(engine, field)


def test_http_conectores_limitados_sem_redirecionamento(modules):
    _, deep, _ = modules
    prov = deep.sources_provider
    class Response:
        def __init__(self, code, payload=b'', chunks=None):
            self.status_code = code
            self.payload = payload
            self.chunks = chunks
            self.closed = False
        def iter_content(self, chunk_size):
            yield from (self.chunks if self.chunks is not None else [self.payload])
        def close(self):
            self.closed = True
    class Session:
        def __init__(self, response): self.response = response; self.kwargs = {}
        def get(self, url, **kwargs):
            self.kwargs = kwargs
            return self.response
    resp = Response(302, b'<html>redirect</html>')
    s = Session(resp)
    assert prov._fetch(s, 'https://api.example.test/query') is None
    assert resp.closed
    assert s.kwargs['allow_redirects'] is False
    resp = Response(200, chunks=[b'X' * (prov.MAX_BODY + 1)])
    assert prov._fetch(Session(resp), 'https://api.example.test/query') is None
    assert resp.closed
    resp = Response(200, b'{json invalido')
    assert prov._json(Session(resp), 'https://api.example.test/query') is None


def test_rss_google_noticias_e_resultado_filtrado(modules):
    _, deep, _ = modules
    prov = deep.sources_provider
    class Response:
        status_code = 200
        def iter_content(self, chunk_size):
            yield (b'<rss><channel><item><title>Maria Exemplo</title>'
                   b'<link>https://news.google.com/article/id</link>'
                   b'<description>noticia &amp; mais</description></item>'
                   b'<item><title>Ignorar HTTP</title>'
                   b'<link>http://inseguro.test/a</link></item></channel></rss>')
        def close(self): pass
    class Session:
        def get(self, url, **kw):
            assert url == 'https://news.google.com/rss/search'
            assert kw['params']['q'] == '"Maria Exemplo"'
            return Response()
    found, failed = prov.search('Google Noticias RSS', Session(), '"Maria Exemplo"', 0)
    assert not failed and len(found) == 1
    assert found[0]['title'] == 'Maria Exemplo'
    assert found[0]['snippet'] == 'noticia & mais'


def test_wikipedia_pt_e_en_com_respostas_simuladas(modules, monkeypatch):
    _, deep, _ = modules
    prov = deep.sources_provider
    def fake_json(s, url, params=None, headers=None, **kwargs):
        assert params['srsearch'] == '"Maria Exemplo"'
        assert params['list'] == 'search'
        return {'query': {'search': [{'title': 'Maria Exemplo',
                                      'snippet': '<span>Maria</span> Exemplo'}]}}
    monkeypatch.setattr(prov, '_json', fake_json)
    for lang in ('PT', 'EN'):
        found, failed = prov.search('Wikipedia ' + lang, None, '"Maria Exemplo"', 0)
        assert not failed and len(found) == 1
        assert found[0]['url'].startswith('https://'+lang.lower()+'.wikipedia.org/wiki/')
        assert found[0]['snippet'] == 'Maria Exemplo'


def test_gdelt_normalizacao_resultados(modules, monkeypatch):
    _, deep, _ = modules
    prov = deep.sources_provider
    monkeypatch.setattr(prov, '_json', lambda *a, **k: {'articles': [
        {'url': 'https://jornal.example.test/a', 'title': 'Pessoa Ficticia',
         'summary': 'Pessoa Ficticia citada em noticia'}]})
    found, failed = prov.search('GDELT Noticias', None, '"Pessoa Ficticia"', 0)
    assert not failed and found[0]['title'] == 'Pessoa Ficticia'


def test_brave_mojeek_e_tavily_parametros_oficiais(modules, monkeypatch):
    _, deep, _ = modules
    prov = deep.sources_provider
    monkeypatch.setenv('MED_BRAVE_API_KEY', 'fake-brave')
    monkeypatch.setenv('MED_MOJEEK_API_KEY', 'fake-mojeek')
    monkeypatch.setenv('MED_TAVILY_API_KEY', 'fake-tavily')
    calls = []
    def fake_json(s, url, params=None, headers=None, **kw):
        calls.append((url, params, headers, kw))
        if 'brave' in url:
            return {'web': {'results': [{'url': 'https://exemplo.test/brave',
                                        'title': 'Pessoa Exemplo', 'description': 'resumo'}]}}
        if 'mojeek' in url:
            return {'response': {'status': 'OK', 'results': [
                {'url': 'https://exemplo.test/mojeek', 'title': 'Pessoa Exemplo', 'desc': 'descricao'}]}}
        return {'results': [{'url': 'https://exemplo.test/tavily', 'title': 'Pessoa Exemplo',
                             'content': 'conteudo da pagina'}]}
    monkeypatch.setattr(prov, '_json', fake_json)
    for engine in ('Brave API', 'Mojeek API', 'Tavily API'):
        found, failed = prov.search(engine, None, '"Pessoa Exemplo"', 0)
        assert not failed and found[0]['title'] == 'Pessoa Exemplo'
    assert calls[0][2]['X-Subscription-Token'] == 'fake-brave'
    assert calls[1][1]['fmt'] == 'json' and calls[1][1]['api_key'] == 'fake-mojeek'
    assert calls[2][2]['Authorization'] == 'Bearer fake-tavily'
    assert calls[2][3]['method'] == 'post'


def test_searxng_bloqueia_destinos_privados_e_validacao_de_endpoint(modules, monkeypatch):
    _, deep, _ = modules
    prov = deep.sources_provider
    assert prov._public_searxng_url('http://127.0.0.1:8888') is None
    assert prov._public_searxng_url('https://127.0.0.1/search') is None
    assert prov._public_searxng_url('https://conta:senha@searx.example.test') is None
    assert prov._public_searxng_url('https://searx.example.test:8080/search') is None
    def dns(host, port, type):
        return [(2, 1, 6, '', ('1.1.1.1', port))]
    monkeypatch.setattr(prov.socket, 'getaddrinfo', dns)
    assert prov._public_searxng_url('https://searx.example.test') == 'https://searx.example.test/search'
    monkeypatch.setenv('MED_SEARXNG_URL', 'https://searx.example.test')
    monkeypatch.setenv('MED_SEARXNG_ENGINES', 'google,bing')
    def fake_json(s, url, params=None, headers=None, **kw):
        assert url == 'https://searx.example.test/search'
        assert params['engines'] == 'google,bing'
        return {'results': [{'url': 'https://site.example.test/a', 'title': 'Pessoa Exemplo',
                            'content': 'Pessoa Exemplo vista aqui', 'engine': 'google'}]}
    monkeypatch.setattr(prov, '_json', fake_json)
    found, failed = prov.search('SearXNG', None, '"Pessoa Exemplo"', 0)
    assert not failed and found[0]['indice_original'] == 'google'


@pytest.mark.parametrize('engine,key', [
    ('SerpApi Google', 'q'), ('SerpApi Bing', 'q'),
    ('SerpApi DuckDuckGo', 'q'), ('SerpApi Yahoo', 'p'),
    ('SerpApi Yandex', 'text'), ('SerpApi Baidu', 'wd'),
    ('SerpApi Naver', 'query')])
def test_serpapi_buscas_documentadas(modules, monkeypatch, engine, key):
    _, deep, _ = modules
    prov = deep.sources_provider
    monkeypatch.setenv('MED_SERPAPI_API_KEY', 'FAKE-SERP')
    def fake_json(s, url, params=None, headers=None, **kw):
        assert url == 'https://serpapi.com/search.json'
        assert params[key] == '"Pessoa Exemplo"'
        assert params['api_key'] == 'FAKE-SERP'
        return {'organic_results': [{'link': 'https://example.test/perfil',
                                    'title': 'Pessoa Exemplo', 'snippet': 'Perfil publico'}]}
    monkeypatch.setattr(prov, '_json', fake_json)
    found, failed = prov.search(engine, None, '"Pessoa Exemplo"', 0)
    assert not failed and len(found) == 1


def test_google_legacy_so_com_credenciais_sem_habilitacao_automatica(modules, monkeypatch):
    _, deep, _ = modules
    prov = deep.sources_provider
    monkeypatch.setenv('MED_GOOGLE_API_KEY', 'FAKE')
    monkeypatch.setenv('MED_GOOGLE_CX', 'FAKE-CX')
    def fake_json(s, url, params=None, **kwargs):
        assert params['cx'] == 'FAKE-CX'
        return {'items': [{'link': 'https://site.example.test/perfil', 'title': 'Pessoa Exemplo',
                          'snippet': 'Pessoa Exemplo'}]}
    monkeypatch.setattr(prov, '_json', fake_json)
    found, failed = prov.search('Google Custom Search (legado)', None, '"Pessoa Exemplo"', 0)
    assert not failed and len(found) == 1


def test_multibuscador_max_requests_compartilhado_e_deduplicacao(modules, monkeypatch):
    _, deep, _ = modules
    prov = deep.sources_provider
    monkeypatch.setattr(prov, 'available_engines', lambda:
                        ['Bing RSS', 'Brave API', 'Mojeek API', 'Wikipedia PT'])
    monkeypatch.setattr(deep, 'variantes', lambda fields:
                        [('nome', '"Maria Exemplo"'), ('email', '"maria@example.test"')])
    sample = {'url': 'https://exemplo.test/perfil', 'title': 'Maria Exemplo',
              'snippet': 'maria@example.test'}
    monkeypatch.setattr(deep, '_bing_rss', lambda *a: ([sample], False))
    def fake_search(engine, session, q, offset):
        return ([sample], False)
    monkeypatch.setattr(prov, 'search', fake_search)
    sources, stats, warnings = deep.realizar_busca(
        None, {'nome': 'Maria Exemplo', 'email': 'maria@example.test'},
        'profunda', '', lambda _: True,
        lambda item, fields: ['email'] if item['snippet'] == 'maria@example.test' else [], max_requests=4)
    assert stats['requisicoes'] == 4
    assert len(sources) == 1
    assert set(sources[0]['buscadores']) == set(stats['mecanismos'])
    assert 'Maria Exemplo' not in repr(stats)
    assert any('Limite' in warning for warning in warnings)


def test_buscadores_especializados_ignoram_termos_sensiveis(modules, monkeypatch):
    _, deep, _ = modules
    prov = deep.sources_provider
    monkeypatch.setattr(prov, 'available_engines', lambda:
                        ['Google Noticias RSS', 'Wikipedia PT', 'GDELT Noticias'])
    monkeypatch.setattr(deep, 'variantes', lambda fields:
                        [('email', '"maria@example.test"'), ('cpf', '"12345678901"')])
    monkeypatch.setattr(prov, 'search', lambda *args: (_ for _ in ()).throw(
        AssertionError('nao deveria consultar esse identificador')))
    hits, stats, _ = deep.realizar_busca(None, {'email': 'maria@example.test',
         'cpf': '12345678901'}, 'profunda', '', lambda *_: True, lambda *a: [])
    assert not hits and stats['requisicoes'] == 0


# Regressao: Google Noticias e Wikipedia podem citar homonimos mesmo com busca entre aspas.
def test_nome_exato_sem_identificador_nunca_e_fonte_pessoal(modules, monkeypatch):
    audit, deep, _ = modules
    monkeypatch.setattr(deep, 'variantes', lambda _: [('nome', 'Pessoa Exemplo')])
    snippet = {'url': 'https://example.org/noticia',
               'title': 'Obituario de Pessoa Exemplo',
               'snippet': 'Uma pessoa famosa de nome igual.'}
    monkeypatch.setattr(deep, '_bing_rss', lambda *a: ([snippet], False))
    monkeypatch.setattr(deep, '_ddg', lambda *a: ([], False))
    result, stats, warning = deep.realizar_busca(None, {'nome': 'Pessoa Exemplo'},
        'rapida', '', lambda _: True, audit._matches_from_index, max_requests=2)
    assert result == []
    assert stats['homonimos_descartados'] == 1
    assert stats['fortes'] == 0
    assert any('nome igual' in w for w in warning)


def test_nome_mais_email_literal_pode_ser_candidato_nao_confirmado(modules, monkeypatch):
    audit, deep, _ = modules
    monkeypatch.setattr(deep, 'variantes', lambda _: [('nome', 'Pessoa Exemplo')])
    hit = {'url': 'https://example.org/perfil', 'title': 'Pessoa Exemplo',
           'snippet': 'Contato: pessoa@example.test'}
    monkeypatch.setattr(deep, '_bing_rss', lambda *a: ([hit], False))
    monkeypatch.setattr(deep, '_ddg', lambda *a: ([], False))
    result, stats, _ = deep.realizar_busca(None, {'nome': 'Pessoa Exemplo',
        'email': 'pessoa@example.test'}, 'rapida', '', lambda _: True,
        audit._matches_from_index, max_requests=2)
    assert len(result) == 1
    assert set(result[0]['coincidencias']) == {'nome', 'email'}
    assert result[0]['confianca'] == 'identificador_literal'
    assert stats['homonimos_descartados'] == 0


def test_imagens_de_nome_igual_nao_sao_baixadas(modules, monkeypatch, tmp_path):
    audit, _, _ = modules
    (tmp_path / 'imagens').mkdir()
    def should_not_fetch(*args):
        raise AssertionError('nao acessar imagem de um homonimo')
    monkeypatch.setattr(audit, '_safe_get', should_not_fetch)
    budget = {'remaining': 4, 'used': 0, 'deadline': float('inf')}
    photos = audit._detect_photos(None, tmp_path, 'a'*32,
         [{'tipo': 'nome', 'url': 'https://example.org/obituario',
           'title': 'Pessoa Exemplo', 'coincidencias': ['nome'],
           'confianca': 'nome_igual'}], 'Pessoa Exemplo', budget)
    assert photos == []
    assert budget['used'] == 0


def test_imagens_de_telefone_isolado_nao_sao_baixadas(modules, monkeypatch, tmp_path):
    audit, _, _ = modules
    (tmp_path / 'imagens').mkdir()
    monkeypatch.setattr(audit, '_safe_get',
        lambda *a: (_ for _ in ()).throw(AssertionError('imagem deve ser ignorada')))
    budget = {'remaining': 4, 'used': 0, 'deadline': float('inf')}
    photos = audit._detect_photos(None, tmp_path, 'a'*32,
        [{'tipo': 'nome', 'url': 'https://example.org/registro',
          'title': 'Pessoa Exemplo', 'coincidencias': ['nome', 'telefone'],
          'confianca': 'identificador_literal'}], 'Pessoa Exemplo', budget)
    assert photos == []
    assert budget['used'] == 0


def test_perfil_informado_pelo_usuario_e_preservado(modules, monkeypatch):
    audit, deep, _ = modules
    monkeypatch.setattr(deep, 'variantes', lambda _: [])
    rows, stats, _ = deep.realizar_busca(None, {'nome': 'Pessoa Exemplo'},
        'rapida', 'https://example.org/perfil',
        lambda url: url == 'https://example.org/perfil',
        audit._matches_from_index, max_requests=2)
    assert len(rows) == 1
    assert rows[0]['confianca'] == 'informado_por_voce'
    assert rows[0]['informado_por_voce'] is True
    assert stats['homonimos_descartados'] == 0

# Validacao adicional do corpo publico, sem trafego real.
def test_busca_lendo_pagina_encontra_email_que_resumo_omitiu(modules, monkeypatch):
    audit, deep, _ = modules
    monkeypatch.setattr(deep, 'variantes', lambda _: [('email', '"maria@example.test"')])
    monkeypatch.setattr(deep, '_bing_rss', lambda *args: ([
        {'url': 'https://example.org/perfil', 'title': 'Perfil profissional',
         'snippet': 'Resumo sem identificadores'}], False))
    monkeypatch.setattr(deep, '_ddg', lambda *args: ([], False))
    checked = []
    budget = {'remaining': 1, 'used': 0, 'deadline': float('inf')}

    def inspect(url, b):
        checked.append(url)
        b['remaining'] -= 1
        b['used'] += 1
        return 'Contato publicado: maria@example.test'

    rows, stats, _ = deep.realizar_busca(
        None, {'email': 'maria@example.test'}, 'rapida', '',
        lambda url: True, audit._matches_from_index, max_requests=2,
        verify_page=inspect, verification_budget=budget)
    assert checked == ['https://example.org/perfil']
    assert len(rows) == 1
    assert rows[0]['coincidencias'] == ['email']
    assert rows[0]['confianca'] == 'identificador_literal'
    assert rows[0]['evidencia'].startswith('Texto visivel')
    assert stats['paginas_verificadas'] == stats['paginas_com_identificador'] == 1
    assert budget['used'] == 1


def test_pagina_com_nome_sem_identificador_continua_excluida(modules, monkeypatch):
    audit, deep, _ = modules
    monkeypatch.setattr(deep, 'variantes', lambda _: [('email', '"maria@example.test"')])
    monkeypatch.setattr(deep, '_bing_rss', lambda *args: ([
        {'url': 'https://example.org/noticia', 'title': 'Maria Exemplo', 'snippet': ''}], False))
    monkeypatch.setattr(deep, '_ddg', lambda *args: ([], False))
    budget = {'remaining': 1, 'used': 0, 'deadline': float('inf')}

    def inspect(url, b):
        b['remaining'] -= 1
        b['used'] += 1
        return 'Maria Exemplo foi citada nesta noticia.'

    rows, stats, _ = deep.realizar_busca(
        None, {'email': 'maria@example.test', 'nome': 'Maria Exemplo'},
        'rapida', '', lambda url: True, audit._matches_from_index,
        max_requests=1, verify_page=inspect, verification_budget=budget)
    assert rows == []
    assert stats['homonimos_descartados'] == 1
    assert stats['paginas_com_identificador'] == 0


def test_pagina_privada_ou_interna_nao_e_lida(modules, monkeypatch):
    audit, deep, _ = modules
    monkeypatch.setattr(deep, 'variantes', lambda _: [('email', '"maria@example.test"')])
    monkeypatch.setattr(deep, '_bing_rss', lambda *args: ([
        {'url': 'https://127.0.0.1/segredo', 'title': 'Perfil', 'snippet': ''}], False))
    monkeypatch.setattr(deep, '_ddg', lambda *args: ([], False))
    budget = {'remaining': 2, 'used': 0, 'deadline': float('inf')}
    def must_not_run(*args):
        raise AssertionError('Nao consultar IP interno')
    rows, stats, _ = deep.realizar_busca(
        None, {'email': 'maria@example.test'}, 'rapida', '',
        lambda url: False, audit._matches_from_index, max_requests=1,
        verify_page=must_not_run, verification_budget=budget)
    assert not rows
    assert stats['paginas_verificadas'] == 0


def test_apenas_nome_nao_ativa_visitas_adicionais(modules, monkeypatch):
    audit, deep, _ = modules
    monkeypatch.setattr(deep, 'variantes', lambda _: [('nome', '"Maria Exemplo"')])
    monkeypatch.setattr(deep, '_bing_rss', lambda *args: ([
        {'url': 'https://example.org/nome', 'title': 'Maria Exemplo', 'snippet': ''}], False))
    monkeypatch.setattr(deep, '_ddg', lambda *args: ([], False))
    rows, stats, _ = deep.realizar_busca(
        None, {'nome': 'Maria Exemplo'}, 'rapida', '',
        lambda url: True, audit._matches_from_index, max_requests=1,
        verify_page=lambda *args: (_ for _ in ()).throw(AssertionError('Nao visitar')),
        verification_budget={'remaining': 2, 'used': 0, 'deadline': float('inf')})
    assert rows == []
    assert stats['paginas_verificadas'] == 0


def test_parser_exclui_identificador_escondido_em_scripts(modules):
    audit, _, _ = modules
    parser = audit._PublicPageText()
    parser.feed('<html><script>const email="maria@example.test"</script>'
                '<style>.x{content:"maria@example.test"}</style>'
                '<main>Pagina de Maria Exemplo</main></html>')
    assert 'maria@example.test' not in parser.text()
    assert 'Maria Exemplo' in parser.text()


def test_reserva_de_verificacao_nao_ultrapassa_limites(modules, monkeypatch):
    audit, deep, _ = modules
    monkeypatch.setattr(deep, 'variantes', lambda _: [('email', '"maria@example.test"')])
    monkeypatch.setattr(deep, '_bing_rss', lambda *args: ([
        {'url': 'https://one.example.org/a', 'title': 'A', 'snippet': ''},
        {'url': 'https://two.example.org/b', 'title': 'B', 'snippet': ''}], False))
    monkeypatch.setattr(deep, '_ddg', lambda *args: ([], False))
    calls=[]
    def inspect(url, b):
        calls.append(url)
        b['remaining'] -= 1
        b['used'] += 1
        return 'maria@example.test'
    budget = {'remaining': 1, 'used': 0, 'deadline': float('inf')}
    rows, stats, _ = deep.realizar_busca(
        None, {'email': 'maria@example.test'}, 'rapida', '',
        lambda url: True, audit._matches_from_index, max_requests=1,
        verify_page=inspect, verification_budget=budget)
    assert len(rows) == 1
    assert len(calls) == 1
    assert stats['requisicoes_verificacao'] == 1


def test_busca_prioriza_identificadores_antes_de_nomes(modules):
    _, deep, _ = modules
    queries = deep.variantes({'nome': 'Maria Exemplo',
                              'email': 'maria@example.test',
                              'usuario': 'maria_teste'})
    assert queries[0][0] != 'nome'
    name_positions = [i for i, (kind, _) in enumerate(queries) if kind == 'nome']
    assert name_positions and all(kind == 'nome' for kind, _ in queries[min(name_positions):])


def test_verificacao_publica_bloqueia_imagem_e_retorna_somente_html(modules, monkeypatch):
    audit, _, _ = modules
    mime_seen = []
    def mock_get(session, url, max_bytes, allowed_types, budget):
        mime_seen.append(tuple(allowed_types))
        return (b'<main>Conta de Maria Exemplo. Contato: maria@example.test</main>',
                'text/html', 'https://example.org/perfil')
    monkeypatch.setattr(audit, '_safe_get', mock_get)
    text = audit._verify_public_page(None, 'https://example.org/perfil',
                                     {'remaining': 1, 'used': 0, 'deadline': float('inf')})
    assert 'maria@example.test' in text
    assert mime_seen == [('text/html', 'application/xhtml+xml')]
