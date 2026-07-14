"""/api/report-requests — regressão: master_admin (company_id NULL) recebia 500.

`int(actor['company_id'])` estourava TypeError quando o ator era o admin
master (sem empresa vinculada), derrubando a tela de Relatórios no Flutter
Web com "Sem conexão com a internet". Master admin deve listar as
solicitações de todas as empresas; os demais perfis continuam escopados.
"""

import io
import json
from urllib.parse import urlparse

import pytest

import modules.reports.routes as routes
import modules.reports.service as service


class _FakeHandler:
    def __init__(self, path='/api/report-requests'):
        self.path = path
        self.command = 'GET'
        self.status = None
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, *_a, **_k):
        pass

    def end_headers(self):
        pass

    def json(self):
        return json.loads(self.wfile.getvalue().decode('utf-8'))


def _parsed(query='actor_user_id=1'):
    return urlparse(f'/api/report-requests?{query}')


def _patch(monkeypatch, actor, *, captured=None):
    monkeypatch.setattr(routes, 'get_connection', lambda: type('C', (), {'close': lambda self: None})())
    monkeypatch.setattr(routes, 'resolve_actor_user_id', lambda *a, **k: actor['id'])
    monkeypatch.setattr(routes, 'authorize_action', lambda *a, **k: actor)
    monkeypatch.setattr(routes, 'actor_operational_unit_id', lambda *a, **k: None)
    monkeypatch.setattr(routes, 'get_actor_purchase_unit_scope', lambda *a, **k: None)
    monkeypatch.setattr(routes, 'canary_evaluate_visibility_dataset', lambda *_a, **k: k.get('legacy_items'))

    def _fetch(_conn, clauses, params):
        if captured is not None:
            captured['clauses'] = list(clauses)
            captured['params'] = list(params)
        return [{'id': 1, 'company_id': 1, 'status': 'pending'}]

    monkeypatch.setattr(routes, 'fetch_report_requests', _fetch)


def test_get_report_requests_master_admin_without_company(monkeypatch):
    actor = {'id': 1, 'role': 'master_admin', 'company_id': None}
    cap = {}
    _patch(monkeypatch, actor, captured=cap)
    h = _FakeHandler()
    routes.handle_get_report_requests(h, _parsed(), None, None)
    assert h.status == 200
    assert h.json()['items']
    assert cap['clauses'] == []          # master enxerga todas as empresas
    assert cap['params'] == []


def test_get_report_requests_scoped_by_company(monkeypatch):
    actor = {'id': 2, 'role': 'general_admin', 'company_id': 7}
    cap = {}
    _patch(monkeypatch, actor, captured=cap)
    h = _FakeHandler()
    routes.handle_get_report_requests(h, _parsed('actor_user_id=2'), None, None)
    assert h.status == 200
    assert cap['clauses'] == ['rr.company_id = %s']
    assert cap['params'] == [7]


def test_get_report_requests_non_master_without_company_forbidden(monkeypatch):
    actor = {'id': 3, 'role': 'general_admin', 'company_id': None}
    _patch(monkeypatch, actor)
    h = _FakeHandler()
    with pytest.raises(PermissionError):
        routes.handle_get_report_requests(h, _parsed('actor_user_id=3'), None, None)


def test_fetch_report_requests_accepts_empty_clauses():
    captured = {}

    class _Conn:
        def execute(self, sql, params=()):
            captured['sql'] = sql
            captured['params'] = params

            class _Cur:
                def fetchall(self):
                    return []

            return _Cur()

    service.fetch_report_requests(_Conn(), [], [])
    assert 'WHERE' not in captured['sql']


def test_mark_report_request_done_without_company_updates_any():
    captured = {}

    class _Conn:
        def execute(self, sql, params=()):
            captured['sql'] = sql
            captured['params'] = params

    service.mark_report_request_done(_Conn(), 9, None, 1, 'Admin', '2026-07-14T00:00:00')
    assert 'company_id = ?' not in captured['sql']
    assert captured['params'][-1] == 9


def test_post_report_requests_master_admin_requires_unit(monkeypatch):
    actor = {'id': 1, 'role': 'master_admin', 'company_id': None, 'full_name': 'Admin'}
    _patch(monkeypatch, actor)
    h = _FakeHandler()
    h.command = 'POST'
    with pytest.raises(ValueError):
        routes.handle_post_report_requests(h, _parsed(), {'actor_user_id': 1}, None)
