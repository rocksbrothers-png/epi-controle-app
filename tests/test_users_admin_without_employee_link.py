"""POST/PUT /api/users — regressão: perfis administrativos sem colaborador.

O create_user/update_user chamava resolve_user_employee_link para QUALQUER
perfil; sem linked_employee_id o caminho de criação manual exigia os campos
employee_* e o endpoint devolvia 400 ("Campo obrigatório: employee_id_code")
para todo payload dos apps (Flutter Web/Android/iOS), independentemente do
modo de senha (manual ou gerada). Perfis administrativos devem poder existir
sem colaborador vinculado; o vínculo segue obrigatório para admin/user
(ensure_operational_role_link) e para o perfil employee.
"""

import pytest

import modules.users.service as svc


def _delegate_marker(*_a, **_k):
    raise AssertionError('resolve_user_employee_link não deveria ser chamado')


def test_general_admin_without_link_skips_employee_resolution(monkeypatch):
    monkeypatch.setattr(svc, 'resolve_user_employee_link', _delegate_marker)
    actor = {'id': 1, 'role': 'master_admin', 'company_id': None}
    linked, company = svc._resolve_optional_employee_link(
        None, actor, {'username': 'carlos.braga'}, 2, 'general_admin', True
    )
    assert linked is None
    assert company == 2


@pytest.mark.parametrize('role', ['registry_admin', 'buyer', 'approver'])
def test_non_operational_roles_without_link_skip_resolution(monkeypatch, role):
    monkeypatch.setattr(svc, 'resolve_user_employee_link', _delegate_marker)
    actor = {'id': 1, 'role': 'general_admin', 'company_id': 2}
    linked, company = svc._resolve_optional_employee_link(
        None, actor, {}, 2, role, True
    )
    assert linked is None
    assert company == 2


def test_provided_link_still_delegates(monkeypatch):
    captured = {}

    def _fake(connection, actor, payload, company_id, allow_manual_create=False):
        captured['allow_manual_create'] = allow_manual_create
        return 42, 2

    monkeypatch.setattr(svc, 'resolve_user_employee_link', _fake)
    actor = {'id': 1, 'role': 'master_admin', 'company_id': None}
    linked, company = svc._resolve_optional_employee_link(
        None, actor, {'linked_employee_id': '42'}, 2, 'admin', True
    )
    assert (linked, company) == (42, 2)
    assert captured['allow_manual_create'] is False  # link explícito, sem criação manual


def test_employee_role_still_requires_resolution(monkeypatch):
    called = {}

    def _fake(connection, actor, payload, company_id, allow_manual_create=False):
        called['allow_manual_create'] = allow_manual_create
        return 7, 2

    monkeypatch.setattr(svc, 'resolve_user_employee_link', _fake)
    actor = {'id': 1, 'role': 'general_admin', 'company_id': 2}
    linked, _ = svc._resolve_optional_employee_link(
        None, actor, {}, 2, 'employee', True
    )
    assert linked == 7
    assert called['allow_manual_create'] is True


def test_manual_employee_fields_still_delegate(monkeypatch):
    called = {}

    def _fake(connection, actor, payload, company_id, allow_manual_create=False):
        called['ok'] = True
        return 9, 2

    monkeypatch.setattr(svc, 'resolve_user_employee_link', _fake)
    actor = {'id': 1, 'role': 'master_admin', 'company_id': None}
    linked, _ = svc._resolve_optional_employee_link(
        None, actor, {'employee_id_code': 'EMP-1'}, 2, 'general_admin', True
    )
    assert called.get('ok') is True
    assert linked == 9


def test_operational_role_without_link_raises_clear_error():
    with pytest.raises(ValueError, match='vinculados a um colaborador'):
        svc.ensure_operational_role_link(None, 'admin', None, 2)
