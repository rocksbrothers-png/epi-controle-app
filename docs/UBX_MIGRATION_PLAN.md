# Plano de Migração UBX — Fatoração de `app.js` e `index.html`

Status: **plano aprovado para execução incremental**. Atualizado em 2026-06-19.

## Por que (motivação)

- `static/app.js` é um monólito de **~12.369 linhas** com **556 funções** (111 `async` + 445 `sync`)
  dentro de **um único closure** (`if (!globalThis.__EPI_APP_RUNTIME_LOADED__) { ... }`).
- Um único defeito nesse arquivo degrada o sistema inteiro. Exemplos reais já corrigidos:
  - guard tornou `async function` block-scoped → loaders de Compras viraram no-op (#620);
  - cache-buster manual dessincronizado → JS obsoleto em produção (#619).
- Os módulos de view (`static/js/views/*.js`) hoje são **cascas finas** que delegam ao `app.js`
  via `globalThis.<fn>()` — acoplamento implícito e frágil.

## Estado atual (o que já existe)

`index.html` **já é modular** via `scripts/build_index.py` (montado de `static/views/_layout.html`
+ fragmentos `_head/_login/_sidebar/_topbar/_modals/_scripts` + views). O CI valida com
`tests/test_index_html_build.py`.

Estrutura JS parcial já presente:

```
static/js/
  core/      config.js  constants.js  feature-flags.js  permissions.js
  modules/   api-client.js  auth.js  router.js  feature-flags-rt.js  permissions-rt.js
  utils/     dom.js  storage.js  debug.js  perf.js
  views/     dashboard.js  epis.js  estoque.js  fichas.js  purchases.js  devolution.js
             employee-portal.js  feedback.js  feedback-detail.js  profile.js
             ui-helpers.js  view-helpers.js
  test/      run-tests.js   (harness Node em sandbox vm — permite testar módulos extraídos)
```

**Conclusão:** a arquitetura-alvo já existe. A migração = **mover a lógica do `app.js` para os
módulos** e **inverter a dependência** (módulos passam a ser donos da lógica; `app.js` vira um
bootstrap fino). Não é reescrever do zero.

## Princípios (inegociáveis)

1. **Aditivo e reversível.** Cada fase mantém a API pública (`globalThis.<fn>`) compatível via
   *shim* temporário, então nada quebra durante a transição.
2. **Uma fase = um PR pequeno e revisável**, validado em navegador antes do merge.
3. **Sem big-bang.** Nunca mover milhares de linhas de uma vez.
4. **Testável.** Toda lógica extraída ganha teste no harness `static/js/test/run-tests.js`.
5. **Versão no build.** Trocar o cache-buster manual por versão derivada no `build_index.py`
   (hash/commit) — elimina a causa-raiz do desync de cache (Fase 0).

## Validação por fase (obrigatória)

- `pytest` (inclui `test_js_unit.py`, `test_index_html_build.py`, `test_static_assets.py`).
- `node --check` em cada arquivo novo/alterado.
- **Smoke em navegador** (responsabilidade conjunta, pois o SPA não roda em CI): Login →
  Bootstrap → a área migrada (ex.: cada sub-aba de Compras) → DevTools sem erros.

## Fases

### Fase 0 — De-risk (fundação) — *menor risco, faça primeiro*
- `build_index.py`: injetar `ASSET_VERSION` (git short SHA ou hash do conteúdo) nos `?v=` ao montar
  o `index.html`; trocar os `?v=` hardcoded dos fragmentos por `?v=__ASSET_VERSION__`.
- Ajustar `test_index_html_build.py`/`test_static_assets.py` ao novo contrato.
- Remover arquivos mortos (ex.: `static/app.v20260326.js` — **já removido**).
- **Resultado:** nunca mais JS obsoleto por `?v=` esquecido.

### Fase 1 — Núcleo HTTP/estado (consolidação)
- `app.js` possui cópias de `api`/`apiOptional`/`apiWithBootstrapRetry` que **já usam** os helpers
  globais de `modules/api-client.js`. Fazer `app.js` **delegar** ao canônico
  (`apiFetch`/`apiFetchOptional`/`apiFetchWithRetry`), removendo a duplicação.
- Expor `state`/`refs` por um acessor estável (`__EPI_APP_STATE__` já existe) e parar de depender de
  closure para o que os módulos precisam.
- Testes no harness para a camada de API.

### Fase 2 — Módulo Compras (alto valor)
Extrair de `app.js` para `static/js/modules/purchases/`:
- `purchases.api.js` — endpoints: requisições, fornecedores autorizados, aprovações, POs, demandas,
  criar requisição, atualizar status, aprovar, gerar PO.
- `purchases.state.js` — `_authorizedSuppliers`, listas, aba ativa, filtros.
- `purchases.view.js` — render de tabelas/dropdowns/estados (vazio/erro/loading).
- `purchases.events.js` — binds (parte já em `views/purchases.js`).
- `purchases.controller.js` — orquestra api→state→view + permissões; expõe `initPurchaseModule()`.
- Durante a transição, `globalThis.loadAuthorizedSuppliers` etc. apontam para o controller (shim),
  até remover as versões do `app.js`.

### Fase 3..N — Demais módulos (um por PR)
Ordem sugerida por isolamento/risco: `delivery` → `stock` → `reports` → `employees` → `dashboard`.
Cada um no mesmo padrão (api/state/view/events/controller).

### Fase final — Limpeza
- Remover funções migradas do `app.js` e os shims.
- `app.js` reduzido a bootstrap: carregar config, montar estado, registrar módulos, iniciar router.
- Atualizar `index.html`/`_scripts.html` (via build) para a lista final de módulos.

## Riscos e mitigação
- **Acoplamento por closure** (causa dos bugs recentes): mitigado pelo contrato explícito
  `globalThis`/`__EPI_APP_STATE__` e shims durante a transição.
- **Sem execução de browser no CI:** mitigado por smoke manual por fase + testes de harness.
- **Ordem de carga dos `<script>`:** manter `core` → `modules` → `views` → `app.js` (bootstrap)
  no `_scripts.html`; `app.js` por último.

## Etapa A/B UBX (2026-10-22) — auditoria, guarda e primeira extração Business

Detalhes, números e ordem de extração em [`UBX_APPJS_AUDITORIA.md`](UBX_APPJS_AUDITORIA.md).

- Camadas UBX dentro do layout existente: `static/js/modules/<domínio>/{ui,business,integrations}/`
  (mapeiam `view/events`, `state/controller` e `api` da Fase 2 acima).
- Extraído: `modules/purchases/business/purchase-import-matching.js` (conciliação da importação de
  cotação). Removidas do `app.js` as cópias idênticas de `utils/debug.js`.
- Guarda `static/js/test/app-js-boundary.js`: o `app.js` não ganha declarações nem endpoints; o
  baseline é catraca. Funcionalidade nova vai para o módulo do domínio.
- Não cria cliente HTTP novo — o gate de unificação dos adaptadores HTTP continua valendo antes de
  extrair `integrations/`.

## UBX-V2 (2026-10-25) — auditoria de integração, guarda ampliada e formatação compartilhada

Detalhes em [`UBX_APPJS_AUDITORIA.md` §10](UBX_APPJS_AUDITORIA.md#10-ubx-v2-2026-10-25--auditoria-de-integração-e-primeira-extração).

- Extraído: `modules/shared/business/value-format.js` (14 funções puras de formatação/leitura de
  valores). `app.js` 20.281 → 20.200 linhas; 950 → 936 declarações.
- Guarda: catraca de endpoints na camada UI, ciclos entre scripts, `business` sem dependência da UI
  nem leitura de globais, limite de 500 linhas por módulo.

## UBX-V3 (2026-10-26) — seis extrações incrementais

Detalhes em [`UBX_APPJS_AUDITORIA.md` §11](UBX_APPJS_AUDITORIA.md#11-ubx-v3-2026-10-26--seis-extrações-incrementais).

- Etapas: helpers de DOM, rótulos com `tr`, HTTP das views pelo cliente único, Relatórios, EPIs e
  Usuários — cada uma com caracterização gerada das funções originais e comparação no navegador.
- `app.js` 20.205 → 19.227 linhas (base Corporate `3e6fe8c5`; o `app.js` do SaaS em `b25147b3` é byte-idêntico); 936 → 878 declarações; 121 → 117 endpoints; `fetch` cru na UI 9 → 4.
- O gate de unificação HTTP foi cumprido para as 5 views com `api` local; `procurement.js` fica
  para decisão separada (não desembrulha `{ ok, data }`).

## UBX-V4 (2026-10-26) — diálogos, Colaboradores/Terceirizados e regras `can*`

Detalhes em [`UBX_APPJS_AUDITORIA.md` §12](UBX_APPJS_AUDITORIA.md#12-ubx-v4-2026-10-26--diálogos-colaboradoresterceirizados-e-regras-can).

- `app.js` 19.227 → 18.106 linhas; 878 → 798 declarações; 117 → 112 endpoints.
- Regras de acesso da interface como negócio puro (`modules/auth/business/access-rules.js`), com
  23.320 decisões comparadas antes × depois (0 divergências). O backend continua sendo quem autoriza.

## UBX-V5, Etapa 1 (2026-10-27) — arquivamento genérico

Mesmo commit do Corporate. Detalhes em [`UBX_APPJS_AUDITORIA.md` §13](UBX_APPJS_AUDITORIA.md#13-ubx-v5-etapa-1-2026-10-27--arquivamento-genérico).

- `app.js` 18.167 → 17.639 linhas; 798 → 778 declarações; 112 → 108 endpoints.
- `modules/archival/{business,integrations,ui}`: 209 cenários comparados com as funções originais
  (0 divergências) e 14 sabotagens detectadas; contrato HTTP de arquivamento agora verificado contra o router.

## Próximo passo
UBX-V5, Etapa 2 (Unidades e CNPJ → Empresas → Minha Empresa → Comercial, este após a I18N-FULL-V1) e,
por último, a Etapa 3 (navegação/bootstrap) — sempre em paridade com o Corporate.
As Fases 0–2 acima seguem valendo para Compras.
