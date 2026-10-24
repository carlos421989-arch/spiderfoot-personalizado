"""Integração Flask REAL, isolada da aplicação e rede do SpiderFoot.

Roda somente onde Flask está instalado: pytest -q test/test_personal_audit_flask_real.py
Não inicia serviços, não usa banco e não consulta buscadores.
"""
import importlib
from pathlib import Path
import re
import sys
import types

import pytest

flask = pytest.importorskip('flask')
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader


def test_blueprint_real_flask_render_busca_exclusao(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    pkg = types.ModuleType('spiderfoot')
    pkg.__path__ = [str(repo / 'spiderfoot')]
    blues = types.ModuleType('spiderfoot.blueprints')
    blues.__path__ = [str(repo / 'spiderfoot' / 'blueprints')]
    ver = types.ModuleType('spiderfoot.__version__')
    ver.__version__ = '5.2.0'
    for key, obj in [('spiderfoot', pkg), ('spiderfoot.blueprints', blues),
                     ('spiderfoot.__version__', ver)]:
        monkeypatch.setitem(sys.modules, key, obj)
    for name in ('spiderfoot.blueprints.personal_audit',
                 'spiderfoot.blueprints.personal_audit_deep',
                 'spiderfoot.blueprints.personal_audit_matcher_usuario'):
        monkeypatch.delitem(sys.modules, name, raising=False)

    audit = importlib.import_module('spiderfoot.blueprints.personal_audit')
    deep = importlib.import_module('spiderfoot.blueprints.personal_audit_deep')
    app = flask.Flask('teste_isolado', template_folder=None)
    app.secret_key = 'somente-para-teste'
    app.jinja_loader = ChoiceLoader([
        DictLoader({'base.html': '{% block title %}{% endblock %}{% block content %}{% endblock %}'}),
        FileSystemLoader(str(repo / 'spiderfoot' / 'templates')),
    ])
    app.register_blueprint(audit.personal_audit_bp)
    monkeypatch.setenv('SPIDERFOOT_DATA', str(tmp_path))
    client = app.test_client()

    response = client.get('/minha-exposicao')
    assert response.status_code == 200
    assert 'Minha Exposição Digital' in response.get_data(as_text=True)
    assert response.headers['Cache-Control'] == 'no-store'
    token_match = re.search(r'const csrf = "([^"]+)";', response.get_data(as_text=True))
    assert token_match, 'CSRF da extensão não foi renderizado'
    csrf = token_match.group(1)

    denied = client.post('/minha-exposicao/pesquisar', json={'nome': 'Maria Exemplo'})
    assert denied.status_code == 403
    malformed = client.post('/minha-exposicao/pesquisar', json=['not', 'an', 'object'],
                            headers={'X-Audit-CSRF': csrf})
    assert malformed.status_code == 400

    def fake_busca(session, fields, modo, perfis, is_public_url,
                   match_index, max_requests=None, verify_page=None, verification_budget=None):
        return [], {'descartadas': 0, 'consultas': 1, 'requisicoes': 1}, []
    monkeypatch.setattr(deep, 'realizar_busca', fake_busca)
    searched = client.post('/minha-exposicao/pesquisar', json={
        'nome': 'Maria Exemplo', 'consentimento': True,
        'modo': 'rapida', 'coletar_fotos': False,
    }, headers={'X-Audit-CSRF': csrf})
    assert searched.status_code == 200
    route = searched.get_json()['url']
    shown = client.get(route)
    assert shown.status_code == 200
    assert 'Resultado da auditoria' in shown.get_data(as_text=True)
    assert 'Maria Exemplo' not in (tmp_path / 'auditoria_pessoal' / 'runs' /
                                    (route.split('/')[-1] + '.json')).read_text('utf-8')

    run_id = route.split('/')[-1]
    intruso = app.test_client()
    assert intruso.get('/minha-exposicao').status_code == 200
    assert intruso.get(route).status_code == 404
    assert intruso.get('/minha-exposicao/imagem/' + run_id + '/foto.png').status_code == 404
    assert 'Resultado da auditoria' not in intruso.get('/minha-exposicao').get_data(as_text=True)
    deleted = client.post('/minha-exposicao/excluir/' + run_id,
                          headers={'X-Audit-CSRF': csrf})
    assert deleted.status_code == 200
    assert not (tmp_path / 'auditoria_pessoal' / 'runs' / (run_id + '.json')).exists()
