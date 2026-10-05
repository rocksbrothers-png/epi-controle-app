"""Capability de checkout público (binding server-side onboarding → pagamento).

Um `checkout_token` opaco, emitido pelo servidor no signup (`provision_pending_tenant`),
que liga UMA requisição de checkout público à empresa/tenant PENDENTE já
provisionada. É a ÚNICA autoridade de `company_id`/`tenant_id` do checkout
público — o corpo da requisição nunca pode escolher a empresa (F-01, #383).

Propriedades:
- token forte (`secrets.token_urlsafe`, >= 32 bytes de entropia), não sequencial,
  não derivável de `company_id`;
- apenas o HASH (SHA-256) é persistido — o token bruto volta ao cliente UMA vez
  (no signup) e nunca é gravado em log/auditoria/banco;
- TTL explícito; uso único para criar a assinatura;
- consumo atômico e seguro sob concorrência: o `UPDATE ... WHERE consumed_at IS
  NULL` só é aplicado por um; no PostgreSQL ele também trava a linha pela duração
  da transação, serializando requisições concorrentes com o mesmo token. Se um
  passo posterior falhar e a transação for revertida, o consumo é desfeito
  (permite retry legítimo), sem nunca permitir duas assinaturas simultâneas.
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta

from epi_backend.config import UTC

# TTL padrão de 24h; configurável por ambiente sem tocar código.
CHECKOUT_TOKEN_TTL_SECONDS = int(os.environ.get('CHECKOUT_TOKEN_TTL_SECONDS', str(24 * 3600)))
_TOKEN_NBYTES = 32  # >= 32 bytes de entropia (token_urlsafe usa os.urandom)

# Origens válidas de binding de uma assinatura (ver subscriptions_service).
ORIGIN_PUBLIC_CHECKOUT = 'public_checkout'
ORIGIN_AUTHENTICATED = 'authenticated'
VALID_SUBSCRIPTION_ORIGINS = (ORIGIN_PUBLIC_CHECKOUT, ORIGIN_AUTHENTICATED)


class CheckoutSessionError(ValueError):
    """Token de checkout ausente/inválido/expirado/já consumido → HTTP 400."""


def _now():
    return datetime.now(UTC)


def _iso(dt):
    return dt.isoformat().replace('+00:00', 'Z')


def hash_token(raw):
    """SHA-256 hex do token bruto. Só o hash é persistido/consultado."""
    return hashlib.sha256(str(raw or '').encode('utf-8')).hexdigest()


def ensure_checkout_session_tables(connection):
    """Cria a tabela de sessões de checkout (idempotente; SQLite e PostgreSQL)."""
    connection.executescript(
        '''
        CREATE TABLE IF NOT EXISTS checkout_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL DEFAULT '',
            company_id INTEGER,
            tenant_id TEXT NOT NULL DEFAULT '',
            owner_user_id INTEGER,
            plan_key TEXT NOT NULL DEFAULT '',
            cycle TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL DEFAULT '',
            consumed_at TEXT
        );
        '''
    )
    # Índice único no hash do token (idempotente em ambos os bancos).
    try:
        connection.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_checkout_sessions_token '
            'ON checkout_sessions(token_hash)'
        )
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
    try:
        connection.execute(
            'CREATE INDEX IF NOT EXISTS idx_checkout_sessions_company '
            'ON checkout_sessions(company_id)'
        )
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass


def create_checkout_session(connection, *, company_id, tenant_id='', owner_user_id=None,
                            plan_key='', cycle='', ttl_seconds=None):
    """Cria uma sessão de checkout e devolve o token BRUTO (uma única vez).

    O chamador (signup) devolve o token ao cliente e NÃO o persiste em outro
    lugar. Apenas o hash fica no banco.
    """
    raw = secrets.token_urlsafe(_TOKEN_NBYTES)
    now = _now()
    expires = now + timedelta(seconds=int(ttl_seconds or CHECKOUT_TOKEN_TTL_SECONDS))
    connection.execute(
        '''
        INSERT INTO checkout_sessions
            (token_hash, company_id, tenant_id, owner_user_id, plan_key, cycle,
             created_at, expires_at, consumed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ''',
        (
            hash_token(raw),
            int(company_id) if company_id not in (None, '') else None,
            str(tenant_id or ''),
            int(owner_user_id) if owner_user_id not in (None, '') else None,
            str(plan_key or ''), str(cycle or ''),
            _iso(now), _iso(expires),
        ),
    )
    return raw


def claim_checkout_session(connection, raw_token):
    """Valida e CONSOME o token atomicamente; devolve o binding server-side.

    Retorna {id, company_id, tenant_id, owner_user_id, plan_key, cycle} ou
    levanta `CheckoutSessionError` (ausente/inválido/expirado/já consumido).

    O consumo é um `UPDATE ... WHERE consumed_at IS NULL`: no PostgreSQL ele
    trava a linha até o commit/rollback da transação do handler, de modo que
    dois requests concorrentes com o mesmo token NÃO criam duas assinaturas —
    o segundo encontra `rowcount == 0` e é recusado. Se o handler reverter a
    transação após uma falha legítima (ex.: erro do Mercado Pago), o consumo é
    desfeito e um retry é possível.
    """
    token = str(raw_token or '').strip()
    if not token:
        raise CheckoutSessionError('checkout_token é obrigatório.')
    token_hash = hash_token(token)
    row = connection.execute(
        'SELECT id, company_id, tenant_id, owner_user_id, plan_key, cycle, '
        'expires_at, consumed_at FROM checkout_sessions WHERE token_hash = ?',
        (token_hash,),
    ).fetchone()
    if not row:
        raise CheckoutSessionError('Checkout inválido ou expirado.')
    # Acesso posicional: compatível com sqlite3.Row e psycopg2 DictRow.
    session_id, company_id, tenant_id, owner_user_id, plan_key, cycle, expires_at, consumed_at = (
        row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]
    )
    if consumed_at:
        raise CheckoutSessionError('Checkout já utilizado.')
    now_iso = _iso(_now())
    if str(expires_at or '') and str(expires_at) < now_iso:
        raise CheckoutSessionError('Checkout inválido ou expirado.')
    cursor = connection.execute(
        'UPDATE checkout_sessions SET consumed_at = ? '
        'WHERE token_hash = ? AND consumed_at IS NULL',
        (now_iso, token_hash),
    )
    if getattr(cursor, 'rowcount', 0) != 1:
        # Corrida perdida: outro request consumiu primeiro.
        raise CheckoutSessionError('Checkout já utilizado.')
    return {
        'id': session_id,
        'company_id': company_id,
        'tenant_id': str(tenant_id or ''),
        'owner_user_id': owner_user_id,
        'plan_key': str(plan_key or ''),
        'cycle': str(cycle or ''),
    }
