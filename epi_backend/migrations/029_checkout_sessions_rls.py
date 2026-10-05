"""Migration 029: RLS versionada de `checkout_sessions` (#383, fase 1G-S).

A tabela guarda o binding server-side da capability de checkout público e o
hash do token; nenhum acesso direto via PostgREST (anon/authenticated) é
legítimo. A RLS (ligada + policy RESTRICTIVE negando tudo) é versionada aqui,
fora do código de aplicação (#309). Ver o `.sql` para o raciocínio e a
idempotência (bloco único `DO $$`, no-op quando a tabela ainda não existe).
"""

from __future__ import annotations

import pathlib

MIGRATION_ID = '029_checkout_sessions_rls'

_SQL_FILE = (
    pathlib.Path(__file__).parent.parent.parent
    / 'supabase' / 'migrations' / '20260828000000_checkout_sessions_rls.sql'
)


def run(connection) -> dict[str, str]:
    connection.execute(_SQL_FILE.read_text(encoding='utf-8'))
    connection.commit()
    return {'migration_id': MIGRATION_ID, 'status': 'applied'}
