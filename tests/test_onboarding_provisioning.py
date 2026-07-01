"""Testes do provisionamento self-service de empresa (onboarding).

Usam SQLite em memória e monkeypatch do envio de e-mail (SMTP) e das
configurações comerciais, para não tocar rede nem depender do banco de settings.
Cobrem: criação da empresa pendente + dono inativo, ativação idempotente com
geração de senha, e a ativação disparada pelo webhook de assinatura.
"""

import sqlite3

import pytest

from core.security import verify_password
from modules.commercial.service import default_commercial_settings
from modules.companies import service as companies_service
from modules.onboarding import service as onboarding

# CNPJs reais válidos (dígitos verificadores corretos) usados nos testes.
CNPJ_A = '11.222.333/0001-81'
CNPJ_B = '24.940.022/0001-08'


def make_connection():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute(
        '''
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            legal_name TEXT NOT NULL DEFAULT '',
            cnpj TEXT NOT NULL DEFAULT '',
            logo_type TEXT NOT NULL DEFAULT '',
            plan_name TEXT NOT NULL DEFAULT 'start',
            user_limit INTEGER NOT NULL DEFAULT 10,
            license_status TEXT NOT NULL DEFAULT 'active',
            active INTEGER NOT NULL DEFAULT 1,
            commercial_notes TEXT NOT NULL DEFAULT '',
            contract_start TEXT NOT NULL DEFAULT '',
            contract_end TEXT NOT NULL DEFAULT '',
            monthly_value REAL NOT NULL DEFAULT 0,
            addendum_enabled INTEGER NOT NULL DEFAULT 0,
            slug TEXT, subdomain TEXT, custom_domain TEXT,
            login_logo_type TEXT NOT NULL DEFAULT '',
            primary_color TEXT NOT NULL DEFAULT '#1565C0',
            secondary_color TEXT NOT NULL DEFAULT '#42A5F5',
            accent_color TEXT NOT NULL DEFAULT '#FF6F00',
            default_language TEXT NOT NULL DEFAULT 'pt-BR',
            favicon_type TEXT NOT NULL DEFAULT '',
            institutional_message TEXT NOT NULL DEFAULT '',
            contact_email TEXT NOT NULL DEFAULT '',
            contact_phone TEXT NOT NULL DEFAULT '',
            website TEXT NOT NULL DEFAULT '',
            theme_json TEXT NOT NULL DEFAULT '{}'
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            password TEXT NOT NULL DEFAULT '',
            full_name TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT '',
            company_id INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            linked_employee_id INTEGER
        )
        '''
    )
    return conn


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    # validate_company_payload lê as configurações comerciais do banco; nos
    # testes devolvemos os defaults para não depender da tabela de settings.
    monkeypatch.setattr(
        companies_service, 'get_commercial_settings',
        lambda _conn: default_commercial_settings(),
    )


def _signup_payload(**over):
    base = {
        'name': 'Liva Mobile',
        'legal_name': 'Liva Mobile LTDA',
        'cnpj': CNPJ_A,
        'plan_name': 'start',
        'user_limit': 5,
        'owner_name': 'Ana Responsável',
        'owner_email': 'ana@liva.com',
    }
    base.update(over)
    return base


def test_provision_creates_pending_company_and_inactive_owner():
    conn = make_connection()
    result = onboarding.provision_pending_tenant(conn, _signup_payload())

    company = dict(conn.execute(
        'SELECT * FROM companies WHERE id = ?', (result['company_id'],)).fetchone())
    assert company['active'] == 0
    assert company['license_status'] == 'pending'
    assert company['plan_name'] == 'start'

    owner = dict(conn.execute(
        'SELECT * FROM users WHERE id = ?', (result['owner_user_id'],)).fetchone())
    assert owner['role'] == 'general_admin'
    assert owner['active'] == 0
    assert owner['username'] == 'ana@liva.com'
    assert owner['company_id'] == result['company_id']


def test_provision_rejects_invalid_email():
    conn = make_connection()
    with pytest.raises(ValueError):
        onboarding.provision_pending_tenant(conn, _signup_payload(owner_email='nao-eh-email'))


def test_provision_rejects_duplicate_email():
    conn = make_connection()
    onboarding.provision_pending_tenant(conn, _signup_payload())
    # Mesmo e-mail, CNPJ diferente → ainda deve barrar (login é global).
    with pytest.raises(ValueError):
        onboarding.provision_pending_tenant(conn, _signup_payload(cnpj=CNPJ_B))


def test_provision_rejects_duplicate_cnpj():
    conn = make_connection()
    onboarding.provision_pending_tenant(conn, _signup_payload())
    with pytest.raises(ValueError):
        onboarding.provision_pending_tenant(
            conn, _signup_payload(owner_email='outro@liva.com'))


def test_activate_sets_active_generates_password_and_emails(monkeypatch):
    conn = make_connection()
    sent = {}

    def _fake_email(to_email, password, company_name):
        sent['to'] = to_email
        sent['password'] = password
        sent['company'] = company_name

    monkeypatch.setattr(onboarding, 'send_credentials_email', _fake_email)

    result = onboarding.provision_pending_tenant(conn, _signup_payload())
    company_id = result['company_id']
    placeholder_hash = dict(conn.execute(
        'SELECT password FROM users WHERE id = ?',
        (result['owner_user_id'],)).fetchone())['password']

    activation = onboarding.activate_tenant_and_notify(conn, company_id)
    assert activation['activated'] is True
    assert activation['email_sent'] is True

    company = dict(conn.execute(
        'SELECT active, license_status FROM companies WHERE id = ?', (company_id,)).fetchone())
    assert company['active'] == 1
    assert company['license_status'] == 'active'

    owner = dict(conn.execute(
        'SELECT password, active FROM users WHERE id = ?',
        (result['owner_user_id'],)).fetchone())
    assert owner['active'] == 1
    assert owner['password'] != placeholder_hash
    # A senha enviada por e-mail é a que passa a valer no login.
    assert sent['to'] == 'ana@liva.com'
    assert verify_password(owner['password'], sent['password'])


def test_activate_is_idempotent(monkeypatch):
    conn = make_connection()
    calls = {'n': 0}
    monkeypatch.setattr(
        onboarding, 'send_credentials_email',
        lambda *a, **k: calls.__setitem__('n', calls['n'] + 1))

    result = onboarding.provision_pending_tenant(conn, _signup_payload())
    onboarding.activate_tenant_and_notify(conn, result['company_id'])
    second = onboarding.activate_tenant_and_notify(conn, result['company_id'])

    assert second['activated'] is False
    assert second['reason'] == 'already_active'
    assert calls['n'] == 1  # não reenvia e-mail na segunda chamada


def test_activate_email_failure_still_activates(monkeypatch):
    conn = make_connection()

    def _boom(*a, **k):
        raise ValueError('SMTP não configurado')

    monkeypatch.setattr(onboarding, 'send_credentials_email', _boom)

    result = onboarding.provision_pending_tenant(conn, _signup_payload())
    activation = onboarding.activate_tenant_and_notify(conn, result['company_id'])

    assert activation['activated'] is True
    assert activation['email_sent'] is False
    company = dict(conn.execute(
        'SELECT active FROM companies WHERE id = ?', (result['company_id'],)).fetchone())
    assert company['active'] == 1


def test_webhook_active_status_activates_pending_company(monkeypatch):
    from modules.payments import service as payments_service
    from modules.payments import subscriptions_service

    conn = make_connection()
    payments_service.ensure_subscription_tables(conn)

    activated = {}
    monkeypatch.setattr(
        onboarding, 'send_credentials_email',
        lambda to_email, password, company_name: activated.setdefault('to', to_email))

    result = onboarding.provision_pending_tenant(conn, _signup_payload())
    company_id = result['company_id']

    subscriptions_service.record_subscription(
        conn, company_id=company_id, plan_key='start', cycle='monthly',
        payment_method='card', preapproval_id='PRE-123', status='pending', amount=297.0)

    synced = subscriptions_service.sync_subscription_status(conn, 'PRE-123', 'authorized')
    assert synced is True

    company = dict(conn.execute(
        'SELECT active FROM companies WHERE id = ?', (company_id,)).fetchone())
    assert company['active'] == 1
    assert activated.get('to') == 'ana@liva.com'
