"""Política de senha temporária + resolução de empresa da Ficha (master_admin).

Cobre:
  • login sinaliza must_change_password e bloqueia senha temporária expirada;
  • troca/recuperação encerram a política;
  • usuários existentes (sem as colunas / flag 0) nunca são bloqueados;
  • ficha-config exige empresa para master_admin e isola por tenant.
"""

from datetime import datetime, timedelta

import pytest

import modules.auth.service as auth_svc
import modules.settings.routes as settings_routes
from epi_backend.config import UTC


# ── Fakes de conexão ─────────────────────────────────────────────────────────

class _FakeConn:
    """Conexão mínima: guarda linhas de users por id para as políticas."""

    def __init__(self, rows):
        self._rows = rows  # {user_id: {col: value}}
        self.updates = []

    def execute(self, sql, params=()):
        low = sql.lower().strip()
        if low.startswith('select must_change_password'):
            uid = int(params[0])
            row = self._rows.get(uid)
            return _Cur([row] if row else [])
        if low.startswith('update users set must_change_password = 1'):
            self.updates.append(('mark', params))
            return _Cur([])
        if low.startswith("update users set must_change_password = 0"):
            self.updates.append(('clear', params))
            return _Cur([])
        if low.startswith('update users set password'):
            self.updates.append(('password', params))
            return _Cur([])
        raise AssertionError(f'SQL inesperado: {sql}')

    def rollback(self):
        pass


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


# ── Política de senha ────────────────────────────────────────────────────────

def test_policy_active_and_not_expired():
    future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    conn = _FakeConn({5: {'must_change_password': 1, 'password_expires_at': future}})
    policy = auth_svc.get_user_password_policy(conn, 5)
    assert policy == {'must_change': True, 'expired': False}


def test_policy_expired():
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    conn = _FakeConn({5: {'must_change_password': 1, 'password_expires_at': past}})
    policy = auth_svc.get_user_password_policy(conn, 5)
    assert policy['must_change'] is True
    assert policy['expired'] is True


def test_existing_user_without_flag_is_never_blocked():
    conn = _FakeConn({9: {'must_change_password': 0, 'password_expires_at': ''}})
    policy = auth_svc.get_user_password_policy(conn, 9)
    assert policy == {'must_change': False, 'expired': False}


def test_policy_tolerates_missing_columns():
    class _Broken:
        def execute(self, *a, **k):
            raise Exception('column "must_change_password" does not exist')

        def rollback(self):
            pass

    policy = auth_svc.get_user_password_policy(_Broken(), 1)
    assert policy == {'must_change': False, 'expired': False}  # login preservado


def test_update_password_clears_policy():
    conn = _FakeConn({7: {'must_change_password': 1, 'password_expires_at': 'x'}})
    auth_svc.update_user_password(conn, 7, 'newhash')
    kinds = [u[0] for u in conn.updates]
    assert 'password' in kinds and 'clear' in kinds


# ── Resolução de empresa da Ficha ────────────────────────────────────────────

def test_ficha_company_master_requires_selection():
    actor = {'role': 'master_admin', 'company_id': None}
    with pytest.raises(ValueError, match='Selecione uma empresa'):
        settings_routes._resolve_settings_company_id(None, actor, '')


def test_ficha_company_master_with_selection(monkeypatch):
    import modules.companies.service as comp_svc
    monkeypatch.setattr(comp_svc, 'get_company_by_id', lambda c, cid: {'id': cid})
    actor = {'role': 'master_admin', 'company_id': None}
    assert settings_routes._resolve_settings_company_id(None, actor, '2') == 2


def test_ficha_company_general_admin_forced_to_own():
    actor = {'role': 'general_admin', 'company_id': 4}
    # ignora company_id ausente e usa a própria empresa
    assert settings_routes._resolve_settings_company_id(None, actor, '') == 4


def test_ficha_company_general_admin_cross_tenant_blocked():
    actor = {'role': 'general_admin', 'company_id': 4}
    with pytest.raises(PermissionError):
        settings_routes._resolve_settings_company_id(None, actor, '99')


def test_ficha_company_master_read_mode_returns_none():
    # GET (require=False): master sem seleção vê os padrões, não quebra o load.
    actor = {'role': 'master_admin', 'company_id': None}
    assert settings_routes._resolve_settings_company_id(None, actor, '', require=False) is None
