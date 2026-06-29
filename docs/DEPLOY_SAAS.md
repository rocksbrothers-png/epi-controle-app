# Deploy do SaaS — `EPI-CONTROLE-APP` (Liva Mobile)

Guia de deploy do **produto SaaS**, com infraestrutura **separada** do ambiente
corporativo/legado (`EPI-CONTROLE`). Complementa
`docs/AUDITORIA_SEPARACAO_REPOS.md`.

> ⚠️ Nunca use credenciais/serviços do ambiente corporativo aqui. Banco, deploy
> e Firebase do SaaS são independentes.

## 1. Mapa de ambientes

| Recurso | Corporativo/Legado (`EPI-CONTROLE`) | **SaaS (`EPI-CONTROLE-APP`)** |
|---|---|---|
| Supabase (Postgres) | `kkmskwmkhyssrxqbsrqv` | **`wlneevhqklboctghvdeo`** |
| Render — API/backend | serviço `epi-controle` (Docker, co-deploy) | **`epi-controle-app-livamobile-api`** (Docker) |
| Render — Web | embutido em `/app/` no mesmo container | **`epi-controle-app-livamobile-web`** (static site) |
| Identidade mobile | `com.rocksbrothers.epicontrole` | **`com.livamobile.epicontrole`** |
| URL da API | mesmo origin do web | `https://epi-controle-app-livamobile-api.onrender.com` |
| URL do Web | `/` (legado) + `/app/` (Flutter) | `https://epi-controle-app-livamobile-web.onrender.com` |

## 2. Diferença-chave de arquitetura: co-deploy × split deploy

- **Legado (co-deploy):** o Flutter Web é compilado com `--base-href /app/` e
  embutido no container Python, servido no **mesmo origin** da API. No app,
  `API_BASE_URL` fica **vazio** → URLs relativas.
- **SaaS (split deploy):** o Flutter Web é um **static site próprio** em outro
  origin. Por isso ele é compilado com:
  - `--base-href /` (raiz do próprio site), e
  - `--dart-define=API_BASE_URL=https://epi-controle-app-livamobile-api.onrender.com`.
  Como front e API ficam em **origens diferentes**, o backend **precisa** de
  `CORS_ALLOW_ORIGIN=https://epi-controle-app-livamobile-web.onrender.com`.

## 3. Serviço de API (`epi-controle-app-livamobile-api`)

Definido em `render.yaml` (type `web`, `env: docker`). Variáveis a preencher no
painel do Render (todas `sync:false` exceto as com valor fixo):

- `DATABASE_URL` → pooler do Supabase SaaS (`wlneevhqklboctghvdeo`).
- `SUPABASE_URL` = `https://wlneevhqklboctghvdeo.supabase.co` (fixo no blueprint).
- `SUPABASE_ANON_KEY`, `JWT_SECRET` → segredos do SaaS.
- `CORS_ALLOW_ORIGIN`, `WEB_BASE_URL`, `WEB_APP_URL` → URL do web SaaS (fixos).
- `MERCADO_PAGO_*` → credenciais de pagamento do SaaS.

## 4. Serviço de Web (`epi-controle-app-livamobile-web`)

Static site (type `web`, `runtime: static`) que **compila o Flutter Web** no
build (instala o SDK, `melos bootstrap` + geração de código), publica
`flutter/apps/epi_admin/build/web` e usa rewrite SPA para `/index.html`.
Variáveis `FIREBASE_WEB_*` como segredos.

## 5. Banco de dados (Supabase SaaS)

> ⚠️ **ORDEM OBRIGATÓRIA.** O schema-base do banco é criado pelo backend
> (`core/schema.py` → `init_db()`, idempotente, com `CREATE TABLE IF NOT EXISTS`
> e seu próprio runner `app_migrations`). As migrações em `supabase/migrations/`
> são **RLS hardening** que rodam **por cima** desse schema e são **guardadas**
> (`CONTINUE WHEN NOT EXISTS (tabela)`).
>
> Se as migrações de RLS forem aplicadas a um banco **vazio**, elas **pulam tudo
> silenciosamente** mas ficam **registradas como aplicadas** → as tabelas
> criadas depois ficariam **sem RLS**, com o histórico mentindo que estão
> protegidas. **Não aplique RLS antes do schema existir.**

Sequência correta no projeto SaaS (`wlneevhqklboctghvdeo`, us-east-1):

1. Definir `DATABASE_URL` (com a senha do Postgres SaaS) no serviço de API e
   **subir o backend uma vez** → `init_db()` cria todo o schema-base.
2. Confirmar via `list_tables` que as tabelas existem.
3. **Só então** aplicar as migrações `supabase/migrations/` e validar com
   `get_advisors` (security) — não pode sobrar tabela sem RLS.

Confirmar sempre que o `DATABASE_URL` aponta para o pooler do projeto SaaS
(us-east-1) — **nunca** o corporativo (`kkmskwmkhyssrxqbsrqv`, sa-east-1).

## 6. Mobile (Android/iOS) — identidade SaaS

- Application/Bundle id: **`com.livamobile.epicontrole`** (produto distinto do
  legado).
- Firebase: registrar apps Android/iOS/Web do SaaS com esse id e fornecer os
  `FIREBASE_*` correspondentes via `--dart-define`/secrets de CI.
- Builds: ver comandos em `docs/AUDITORIA_SEPARACAO_REPOS.md` (Parte 5).

## 7. Website comercial → app SaaS

A website comercial conecta ao SaaS apontando para:
- App web: `https://epi-controle-app-livamobile-web.onrender.com`
- API: `https://epi-controle-app-livamobile-api.onrender.com` (com a origem da
  website incluída em `CORS_ALLOW_ORIGIN`, separada por vírgula, se necessário).
