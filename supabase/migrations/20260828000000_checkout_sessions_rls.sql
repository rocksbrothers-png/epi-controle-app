-- Migration: RLS versionada da tabela `checkout_sessions` (#383 / fase 1G-S).
--
-- `checkout_sessions` guarda o binding server-side (company_id / tenant_id /
-- owner_user_id) da capability de checkout público e o HASH (SHA-256) do token.
-- Nenhum acesso via PostgREST (anon / authenticated) é legítimo — apenas o
-- backend (service role, que ignora RLS) a lê/escreve. RLS ligada + policy
-- RESTRICTIVE negando tudo fecha o acesso direto à API, no mesmo padrão das
-- tabelas de billing (ver 20260827000000_billing_rls.sql).
--
-- Idempotente por construção (bloco único DO $$, IF EXISTS / IF NOT EXISTS):
-- `ensure_checkout_session_tables` cria a tabela no bootstrap ANTES de
-- `run_pending_migrations`; o `IF EXISTS` torna a migration no-op caso a tabela
-- ainda não exista, em vez de erro. O bloco único torna inalcançável o estado
-- "RLS ligada sem policy".

DO $$
DECLARE
  tbl text := 'checkout_sessions';
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = tbl
  ) THEN
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);

    IF NOT EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname = 'public'
        AND tablename = tbl
        AND policyname = 'block_direct_api_access'
    ) THEN
      EXECUTE format(
        'CREATE POLICY block_direct_api_access ON public.%I AS RESTRICTIVE FOR ALL TO anon, authenticated USING (false)',
        tbl
      );
    END IF;
  END IF;
END $$;
