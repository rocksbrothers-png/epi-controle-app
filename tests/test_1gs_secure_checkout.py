"""Fase 1G-S — contrato seguro do checkout público (#383 tenant/created_by,
#384 preço). Testes de MECANISMO: dirigem os handlers REAIS e verificam o
payload enviado ao Mercado Pago (mock), o estado do banco, a auditoria e a
ativação do tenant — não apenas o status HTTP.

SQLite em memória + mock do `mp_client`, no padrão de test_subscriptions_lifecycle
e test_onboarding_provisioning. O contrato HTTP mapeia ValueError → 400 e
CheckoutSessionError (subclasse de ValueError) → 400 (ver app.py::do_POST);
aqui asseguramos o mecanismo verificando o ValueError e o efeito no banco/MP.
"""

import json
import sqlite3

import pytest

from core import checkout_sessions
from modules.commercial.service import default_commercial_settings
from modules.companies import service as companies_service
from modules.onboarding import service as onboarding
from modules.payments import routes as payments_routes
from modules.payments import service, subscriptions_service

CNPJ_A = '11.222.333/0001-81'
CNPJ_B = '24.940.022/0001-08'


class NoCloseConn(sqlite3.Connection):
    """`closing(get_connection())` nos handlers não pode destruir o :memory:."""

    def close(self):  # noqa: D401 - no-op proposital
        pass


def _payment_db():
    conn = sqlite3.connect(':memory:', factory=NoCloseConn)
    conn.row_factory = sqlite3.Row
    service.ensure_payment_tables(conn)
    service.ensure_subscription_tables(conn)
    checkout_sessions.ensure_checkout_session_tables(conn)
    return conn


def _full_db():
    conn = _payment_db()
    conn.executescript(
        '''
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            legal_name TEXT NOT NULL DEFAULT '', cnpj TEXT NOT NULL DEFAULT '',
            logo_type TEXT NOT NULL DEFAULT '', plan_name TEXT NOT NULL DEFAULT 'start',
            user_limit INTEGER NOT NULL DEFAULT 10, license_status TEXT NOT NULL DEFAULT 'active',
            active INTEGER NOT NULL DEFAULT 1, commercial_notes TEXT NOT NULL DEFAULT '',
            contract_start TEXT NOT NULL DEFAULT '', contract_end TEXT NOT NULL DEFAULT '',
            monthly_value REAL NOT NULL DEFAULT 0, addendum_enabled INTEGER NOT NULL DEFAULT 0,
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
        );
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL, password TEXT NOT NULL DEFAULT '',
            full_name TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT '',
            company_id INTEGER, active INTEGER NOT NULL DEFAULT 1, linked_employee_id INTEGER
        );
        '''
    )
    return conn


class FakeHandler:
    command = 'POST'
    client_address = ('9.9.9.9',)
    headers = {}


def _mock_mp(monkeypatch):
    posts = []

    def fake_post(path, body):
        posts.append((path, json.loads(json.dumps(body))))
        if str(path).endswith('/preapproval'):
            ar = body.get('auto_recurring') or {}
            return {'id': 'PRE-1', 'status': 'authorized',
                    'auto_recurring': {'transaction_amount': ar.get('transaction_amount')},
                    'init_point': 'https://mp/ip'}
        return {'id': 'PAY-1', 'status': 'pending', 'currency_id': 'BRL',
                'point_of_interaction': {'transaction_data': {
                    'qr_code': 'Q', 'qr_code_base64': 'B', 'ticket_url': 'T'}}}

    monkeypatch.setattr(service.mp_client, 'post', fake_post)
    return posts


def _bind_conn(monkeypatch, conn):
    monkeypatch.setattr(payments_routes, 'get_connection', lambda: conn)
    captured = {}
    monkeypatch.setattr(payments_routes, 'send_json',
                        lambda h, status, body: captured.update(status=status, body=body) or {'status': status, 'body': body})
    return captured


def _token(conn, company_id, *, plan_key='start', ttl=3600):
    return checkout_sessions.create_checkout_session(
        conn, company_id=company_id, tenant_id='', owner_user_id=None,
        plan_key=plan_key, cycle='', ttl_seconds=ttl)


def _sub_rows(conn, company_id):
    return [dict(r) for r in conn.execute(
        'SELECT company_id, created_by, origin, amount, preapproval_id, status '
        'FROM subscriptions WHERE company_id = ? ORDER BY id', (company_id,)).fetchall()]


# ── T1: preço — amount do cliente é recusado; MP não recebe 1 ─────────────────

def test_t1_amount_from_client_is_rejected(monkeypatch):
    conn = _payment_db()
    posts = _mock_mp(monkeypatch)
    _bind_conn(monkeypatch, conn)
    tok = _token(conn, 1)
    with pytest.raises(ValueError):
        payments_routes.handle_post_subscription(
            FakeHandler(), None,
            {'checkout_token': tok, 'plan_key': 'start', 'cycle': 'monthly',
             'payer_email': 'a@x.com', 'card_token': 'tok', 'amount': 1}, None)
    assert posts == [], 'MP não pode ser chamado quando amount é informado'


# ── T2: preço server-side correto chega ao MP ─────────────────────────────────

def test_t2_server_price_reaches_mp(monkeypatch):
    conn = _payment_db()
    posts = _mock_mp(monkeypatch)
    cap = _bind_conn(monkeypatch, conn)
    tok = _token(conn, 1)
    payments_routes.handle_post_subscription(
        FakeHandler(), None,
        {'checkout_token': tok, 'plan_key': 'start', 'cycle': 'monthly',
         'payer_email': 'a@x.com', 'card_token': 'tok'}, None)
    assert cap['status'] == 201
    assert len(posts) == 1 and posts[0][0].endswith('/preapproval')
    body = posts[0][1]
    # Sem preapproval_plan configurado → auto_recurring com o preço do catálogo (297).
    assert body.get('auto_recurring', {}).get('transaction_amount') == 297.0
    assert 'preapproval_plan_id' not in body  # cliente não escolhe plano MP
    # DB grava o preço do servidor.
    assert _sub_rows(conn, 1)[0]['amount'] == 297.0


def test_t2b_configured_mp_plan_is_used_server_side(monkeypatch):
    conn = _payment_db()
    conn.execute(
        "INSERT INTO payment_plans (plan_key, mp_plan_id, frequency_type, created_at, updated_at) "
        "VALUES (?,?,?,'','')",
        ('start', 'MP-PLAN-START', 'months'))
    posts = _mock_mp(monkeypatch)
    _bind_conn(monkeypatch, conn)
    tok = _token(conn, 1)
    payments_routes.handle_post_subscription(
        FakeHandler(), None,
        {'checkout_token': tok, 'plan_key': 'start', 'cycle': 'monthly',
         'payer_email': 'a@x.com', 'card_token': 'tok'}, None)
    body = posts[0][1]
    assert body.get('preapproval_plan_id') == 'MP-PLAN-START'
    assert 'auto_recurring' not in body  # preço vive no plano do MP


# ── T3: enterprise/contact_only não é comprável; MP não é chamado ─────────────

def test_t3_enterprise_contact_only_rejected(monkeypatch):
    conn = _payment_db()
    posts = _mock_mp(monkeypatch)
    _bind_conn(monkeypatch, conn)
    # Isola a barreira `contact_only`: injeta um PREÇO no plano enterprise, de
    # modo que a única coisa que o torna não-comprável seja a flag contact_only
    # (sem isto, a ausência de preço no catálogo já o rejeitaria por outro
    # caminho e o teste não provaria a barreira certa).
    monkeypatch.setitem(service.SUBSCRIPTION_PLANS, 'enterprise', {
        'label': 'ENTERPRISE', 'max_users': None,
        'prices': {'monthly': 5000.0, 'annual': 50000.0}, 'contact_only': True,
    })
    tok = _token(conn, 1, plan_key='enterprise')
    with pytest.raises(ValueError):
        payments_routes.handle_post_subscription(
            FakeHandler(), None,
            {'checkout_token': tok, 'plan_key': 'enterprise', 'cycle': 'monthly',
             'payer_email': 'a@x.com', 'card_token': 'tok'}, None)
    assert posts == []


def test_t3b_unknown_cycle_rejected(monkeypatch):
    conn = _payment_db()
    posts = _mock_mp(monkeypatch)
    _bind_conn(monkeypatch, conn)
    tok = _token(conn, 1)
    with pytest.raises(ValueError):
        payments_routes.handle_post_subscription(
            FakeHandler(), None,
            {'checkout_token': tok, 'plan_key': 'start', 'cycle': 'weekly',
             'payer_email': 'a@x.com', 'card_token': 'tok'}, None)
    assert posts == []


# ── T4: company_id/tenant_id no corpo → 400; nenhuma assinatura da vítima ─────

def test_t4_company_id_in_body_rejected(monkeypatch):
    conn = _payment_db()
    posts = _mock_mp(monkeypatch)
    _bind_conn(monkeypatch, conn)
    tok = _token(conn, 1)
    for field, value in (('company_id', 999), ('tenant_id', 'victim')):
        with pytest.raises(ValueError):
            payments_routes.handle_post_subscription(
                FakeHandler(), None,
                {'checkout_token': tok, 'plan_key': 'start', 'cycle': 'monthly',
                 'payer_email': 'a@x.com', 'card_token': 'tok', field: value}, None)
    assert posts == []
    assert _sub_rows(conn, 999) == []


# ── T5: actor_user_id/created_by forjado → 400; auditoria intacta ─────────────

def test_t5_forged_actor_rejected(monkeypatch):
    conn = _payment_db()
    _mock_mp(monkeypatch)
    _bind_conn(monkeypatch, conn)
    tok = _token(conn, 1)
    for field in ('actor_user_id', 'created_by'):
        with pytest.raises(ValueError):
            payments_routes.handle_post_subscription(
                FakeHandler(), None,
                {'checkout_token': tok, 'plan_key': 'start', 'cycle': 'monthly',
                 'payer_email': 'a@x.com', 'card_token': 'tok', field: 1}, None)
    audit = conn.execute('SELECT count(*) FROM subscription_audit_logs').fetchone()[0]
    assert audit == 0


# ── T6: a capability liga a assinatura à SUA empresa; corpo não sobrepõe ──────

def test_t6_binding_is_from_token_not_body(monkeypatch):
    conn = _payment_db()
    _mock_mp(monkeypatch)
    _bind_conn(monkeypatch, conn)
    tok = _token(conn, 42)  # token ligado à empresa 42
    payments_routes.handle_post_subscription(
        FakeHandler(), None,
        {'checkout_token': tok, 'plan_key': 'start', 'cycle': 'monthly',
         'payer_email': 'a@x.com', 'card_token': 'tok'}, None)
    rows = _sub_rows(conn, 42)
    assert len(rows) == 1
    assert rows[0]['company_id'] == 42
    assert rows[0]['created_by'] is None            # público → NULL
    assert rows[0]['origin'] == 'public_checkout'


# ── T7: sem token válido → nenhuma assinatura; replay/expirado/adulterado ─────

def test_t7_missing_token_rejected(monkeypatch):
    conn = _payment_db()
    posts = _mock_mp(monkeypatch)
    _bind_conn(monkeypatch, conn)
    with pytest.raises(ValueError):
        payments_routes.handle_post_subscription(
            FakeHandler(), None,
            {'plan_key': 'start', 'cycle': 'monthly',
             'payer_email': 'a@x.com', 'card_token': 'tok'}, None)
    assert posts == []


def test_t7_invalid_and_tampered_token_rejected(monkeypatch):
    conn = _payment_db()
    posts = _mock_mp(monkeypatch)
    _bind_conn(monkeypatch, conn)
    for bad in ('inexistente', _token(conn, 1) + 'xx'):
        with pytest.raises(checkout_sessions.CheckoutSessionError):
            payments_routes.handle_post_subscription(
                FakeHandler(), None,
                {'checkout_token': bad, 'plan_key': 'start', 'cycle': 'monthly',
                 'payer_email': 'a@x.com', 'card_token': 'tok'}, None)
    assert posts == []
    assert conn.execute('SELECT count(*) FROM subscriptions').fetchone()[0] == 0


def test_t7_expired_token_rejected(monkeypatch):
    conn = _payment_db()
    _mock_mp(monkeypatch)
    _bind_conn(monkeypatch, conn)
    tok = _token(conn, 1, ttl=-10)  # já expirado
    with pytest.raises(checkout_sessions.CheckoutSessionError):
        payments_routes.handle_post_subscription(
            FakeHandler(), None,
            {'checkout_token': tok, 'plan_key': 'start', 'cycle': 'monthly',
             'payer_email': 'a@x.com', 'card_token': 'tok'}, None)


def test_t7_replay_consumed_token_rejected(monkeypatch):
    conn = _payment_db()
    _mock_mp(monkeypatch)
    _bind_conn(monkeypatch, conn)
    tok = _token(conn, 1)
    body = {'checkout_token': tok, 'plan_key': 'start', 'cycle': 'monthly',
            'payer_email': 'a@x.com', 'card_token': 'tok'}
    payments_routes.handle_post_subscription(FakeHandler(), None, dict(body), None)
    with pytest.raises(checkout_sessions.CheckoutSessionError):
        payments_routes.handle_post_subscription(FakeHandler(), None, dict(body), None)
    assert conn.execute('SELECT count(*) FROM subscriptions').fetchone()[0] == 1


# ── T8: linha sem binding não sombreia assinatura válida ──────────────────────

def test_t8_unbound_row_does_not_shadow_bound(monkeypatch):
    conn = _payment_db()
    subscriptions_service.record_subscription(
        conn, company_id=7, plan_key='start', cycle='monthly', payment_method='card',
        preapproval_id='PRE-BOUND', status='authorized', amount=297.0,
        created_by=None, origin='public_checkout')
    # Linha "injetada" mais recente, sem binding server-side (origin vazio).
    conn.execute(
        "INSERT INTO subscriptions (company_id, subscription_id, preapproval_id, status, "
        "origin, created_at, updated_at) VALUES (7, 'inj', 'PRE-INJECTED', 'active', '', '', '')")
    conn.commit()
    current = subscriptions_service.get_current_subscription(conn, 7)
    assert current['preapproval_id'] == 'PRE-BOUND'


# ── T9: webhook só ativa o tenant com binding válido ──────────────────────────

def _seed_pending_company(conn, company_id, cnpj):
    conn.execute(
        "INSERT INTO companies (id, name, cnpj, active, license_status) VALUES (?,?,?,0,'pending')",
        (company_id, f'C{company_id}', cnpj))
    conn.execute(
        "INSERT INTO users (id, username, full_name, role, company_id, active) "
        "VALUES (?,?,?,?,?,0)",
        (company_id * 10, f'owner{company_id}@x.com', 'Owner', 'general_admin', company_id))
    conn.commit()


def test_t9_webhook_activates_only_bound(monkeypatch):
    conn = _full_db()
    monkeypatch.setattr(onboarding, 'send_credentials_email', lambda *a, **k: True)
    _seed_pending_company(conn, 1, CNPJ_A)
    _seed_pending_company(conn, 2, CNPJ_B)
    # Empresa 1: assinatura com binding; empresa 2: linha sem binding (injetada).
    subscriptions_service.record_subscription(
        conn, company_id=1, plan_key='start', cycle='monthly', payment_method='card',
        preapproval_id='PRE-BOUND', status='pending', amount=297.0,
        created_by=None, origin='public_checkout')
    conn.execute(
        "INSERT INTO subscriptions (company_id, subscription_id, preapproval_id, status, "
        "origin, created_at, updated_at) VALUES (2, 'inj', 'PRE-UNBOUND', 'pending', '', '', '')")
    conn.commit()

    subscriptions_service.sync_subscription_status(conn, 'PRE-BOUND', 'authorized')
    subscriptions_service.sync_subscription_status(conn, 'PRE-UNBOUND', 'authorized')

    assert conn.execute('SELECT active FROM companies WHERE id=1').fetchone()[0] == 1
    assert conn.execute('SELECT active FROM companies WHERE id=2').fetchone()[0] == 0


# ── T10: cenário composto — vítima no corpo + preço manipulado → sem efeito ───

def test_t10_composite_victim_and_price_no_effect(monkeypatch):
    conn = _full_db()
    _seed_pending_company(conn, 5, CNPJ_A)  # vítima pendente
    posts = _mock_mp(monkeypatch)
    _bind_conn(monkeypatch, conn)
    tok = _token(conn, 1)  # token do atacante (empresa 1)
    with pytest.raises(ValueError):
        payments_routes.handle_post_subscription(
            FakeHandler(), None,
            {'checkout_token': tok, 'plan_key': 'start', 'cycle': 'monthly',
             'payer_email': 'atk@x.com', 'card_token': 'tok',
             'company_id': 5, 'amount': 1}, None)
    assert posts == []
    assert _sub_rows(conn, 5) == []
    assert conn.execute('SELECT active FROM companies WHERE id=5').fetchone()[0] == 0


# ── T11: fluxo legítimo ponta a ponta ─────────────────────────────────────────

def test_t11_legitimate_end_to_end(monkeypatch):
    conn = _full_db()
    monkeypatch.setattr(companies_service, 'get_commercial_settings',
                        lambda _c: default_commercial_settings())
    monkeypatch.setattr(onboarding, 'send_credentials_email', lambda *a, **k: True)
    _mock_mp(monkeypatch)
    cap = _bind_conn(monkeypatch, conn)

    signup = onboarding.provision_pending_tenant(conn, {
        'name': 'Liva', 'legal_name': 'Liva LTDA', 'cnpj': CNPJ_A,
        'plan_name': 'start', 'user_limit': 5,
        'owner_name': 'Dona', 'owner_email': 'dona@liva.com',
    })
    conn.commit()
    company_id = signup['company_id']
    token = signup['checkout_token']
    assert token and 'checkout_token' in signup

    payments_routes.handle_post_subscription(
        FakeHandler(), None,
        {'checkout_token': token, 'plan_key': 'start', 'cycle': 'monthly',
         'payer_email': 'dona@liva.com', 'card_token': 'tok'}, None)
    assert cap['status'] == 201

    rows = _sub_rows(conn, company_id)
    assert len(rows) == 1 and rows[0]['amount'] == 297.0 and rows[0]['origin'] == 'public_checkout'
    assert conn.execute('SELECT active FROM companies WHERE id=?', (company_id,)).fetchone()[0] == 0

    # Webhook aprova → empresa correta é ativada.
    subscriptions_service.sync_subscription_status(conn, rows[0]['preapproval_id'], 'authorized')
    assert conn.execute('SELECT active FROM companies WHERE id=?', (company_id,)).fetchone()[0] == 1
