"""Testes da página de cadastro self-service (GET /cadastro).

Cobre apenas o roteamento/serving da página estática (mesma origem da API,
sem CORS) — a lógica de provisionamento é testada em
``test_onboarding_provisioning.py``.
"""

import modules.onboarding.routes as routes


class _FakeWFile:
    def __init__(self):
        self.body = b''

    def write(self, data):
        self.body += data


class _FakeHandler:
    def __init__(self):
        self.path = '/cadastro'
        self.status = None
        self.sent_headers = {}
        self.end_headers_called = False
        self.wfile = _FakeWFile()

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.sent_headers[key] = value

    def end_headers(self):
        self.end_headers_called = True


def test_handle_get_signup_page_serves_html():
    handler = _FakeHandler()
    routes.handle_get_signup_page(handler, None, None, None)

    assert handler.status == 200
    assert handler.sent_headers['Content-Type'] == 'text/html; charset=utf-8'
    assert handler.end_headers_called
    assert b'<form' in handler.wfile.body
    assert b'/js/cadastro.js' in handler.wfile.body


def test_handle_get_signup_page_missing_file_returns_404(monkeypatch):
    monkeypatch.setattr(routes, '_SIGNUP_PAGE', routes._SIGNUP_PAGE.parent / 'nao-existe.html')
    handler = _FakeHandler()
    routes.handle_get_signup_page(handler, None, None, None)

    assert handler.status == 404
    assert handler.sent_headers['Content-Type'] == 'application/json; charset=utf-8'


def test_cadastro_route_is_registered():
    calls = []

    class _Router:
        def register(self, method, path, fn):
            calls.append((method, path, fn))

    routes.register_routes(_Router())
    assert ('GET', '/cadastro', routes.handle_get_signup_page) in calls


# ── Estrutura organizacional (Multi-CNPJ / Joint Venture) ────────────────────

def test_signup_page_offers_all_org_structure_options():
    """A etapa 'Como sua organização está estruturada?' precisa expor todas as
    opções previstas, senão o cliente não consegue declarar holding/JV/consórcio."""
    from pathlib import Path

    from epi_backend.config import BASE_DIR

    html = (Path(BASE_DIR) / 'cadastro.html').read_text(encoding='utf-8')
    assert 'org_structure_type' in html
    assert 'Como sua organização está estruturada?' in html
    for value in ('single_cnpj', 'multi_cnpj', 'holding', 'group',
                  'joint_venture', 'consortium', 'other'):
        assert f'value="{value}"' in html, value


def test_signup_page_sends_org_structure_in_payload():
    """O select não pode ficar órfão: o JS precisa enviá-lo no signup."""
    from pathlib import Path

    from epi_backend.config import BASE_DIR

    js = (Path(BASE_DIR) / 'js' / 'cadastro.js').read_text(encoding='utf-8')
    assert "org_structure_type: $('org_structure_type').value" in js
