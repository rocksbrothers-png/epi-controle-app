"""Rotas de onboarding self-service (cadastro público + ativação).

Endpoints:
  POST /api/onboarding/signup    → público: cria empresa PENDENTE + dono inativo
  POST /api/onboarding/activate  → master: ativa a empresa e (re)envia credenciais

O ``signup`` é público (sem autenticação) por design — é o cadastro do cliente
antes de existir qualquer usuário. A proteção contra abuso vem da validação de
CNPJ real e da unicidade de CNPJ/e-mail em ``provision_pending_tenant``.
"""

from contextlib import closing

from core.database import get_connection
from core.repository import require_master_actor
from core.security import resolve_actor_user_id
from epi_backend.http_utils import require_fields, send_json
from modules.onboarding import service


def handle_post_signup(handler, parsed, payload, match):
    payload = payload or {}
    require_fields(payload, [
        'name', 'legal_name', 'cnpj', 'plan_name', 'user_limit',
        'owner_name', 'owner_email',
    ])
    with closing(get_connection()) as connection:
        result = service.provision_pending_tenant(connection, payload)
        connection.commit()
        return send_json(handler, 201, {'ok': True, 'company': result})


def handle_post_activate(handler, parsed, payload, match):
    payload = payload or {}
    require_fields(payload, ['actor_user_id', 'company_id'])
    with closing(get_connection()) as connection:
        require_master_actor(connection, resolve_actor_user_id(handler, parsed, payload))
        result = service.activate_tenant_and_notify(
            connection, int(payload['company_id']),
            force_resend=bool(payload.get('force_resend')),
        )
        connection.commit()
        return send_json(handler, 200, {'ok': True, 'activation': result})


def register_routes(router):
    router.register('POST', '/api/onboarding/signup', handle_post_signup)
    router.register('POST', '/api/onboarding/activate', handle_post_activate)
