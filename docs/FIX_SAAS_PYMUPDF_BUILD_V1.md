# FIX-SAAS-PYMUPDF-BUILD-V1 — build Docker da API SaaS falhando na verificação da Biblioteca

Projeto: **SaaS** (`rocksbrothers-png/epi-controle-app`). Base: `main` `25b0170`. Prioridade P0: bloqueava qualquer
implantação da API.

## 1. Sintoma

- A API em produção roda o `b25147b3` (#425, 25/10). Os deploys de 26/10 (#436 `38d9446` e #437 `eac9fb3`) terminaram
  em `build_failed`.
- O passo que falha é `RUN python scripts/check_library_runtime.py` (Dockerfile, linha 48), com
  `ValueError: Pré-visualização excedeu o tempo permitido.` depois de 100 s.
- O mesmo check roda no `CMD`. Mesmo com o build passando, a API não subiria com o problema presente.

## 2. Causa

### Confirmado pelos logs do Render

| | Build de 25/10 (passou) | Builds de 26/10 (falharam) |
|---|---|---|
| Imagem base | `python:3.11-slim@sha256:b7a9e5f6…` | a mesma |
| Camada do apt (LibreOffice, UNO, Tesseract) | do cache | do cache (idêntica) |
| Camada do pip | do cache (PyMuPDF `>=1.28.2,<2`) | reinstalada: **PyMuPDF 2.0**, com `onnxruntime`, `pymupdf-layout`, `pymupdf4llm`, `networkx` |
| `check_library_runtime.py` | **2,2 s** | **> 100 s** |

A única mudança entre os dois builds é a camada de dependências Python. O motivo foi a atualização do Dependabot para
`PyMuPDF>=2.0,<3` (#429). O pandas 3.1 também entrou nessa camada, mas o worker da Biblioteca não o importa.

### Mecanismo (medido localmente)

O worker isolado (`modules/safety_library/render_worker.py`) faz `import pymupdf` a cada pré-visualização. O import
acontece dentro do sandbox, com `seccomp`, `RLIMIT_CPU` de 80 s, `RLIMIT_AS` de 3 GiB e `RLIMIT_NOFILE` de 256.

| Medida | PyMuPDF 1.28.2 | PyMuPDF 2.0 |
|---|---|---|
| Carrega `onnxruntime` no import | não | **sim**; o import executa o modelo de layout, com nós `Conv` |
| Memória virtual após o import | ~90 MB | **~0,93 a 1,05 GB** (≈ +41 MB por CPU visível) |
| Threads após o import | 1 | **11 a 14** (uma por CPU visível) |
| Import com 4 CPUs | 0,13 s | 0,68 s |
| Import com 0,1 CPU de cota (4 visíveis) | 1,4 s | **14,3 s** |
| `check_library_runtime.py` com 0,1 CPU, imagem completa | 20 a 21 s (4/4) | 32 a 37 s (4/4); falhou 1 vez em outra rodada |
| Import com `RLIMIT_AS` de 1.000 MB | ok | `std::bad_alloc` no `onnxruntime` |

O 2.0 tem custo e consumo de memória que crescem com o número de CPUs visíveis. Ele é muito sensível a cota de CPU, e o
contêiner de build do Render roda num host grande (AVX-512).

### Limite do diagnóstico

- Os 100 s exatos do Render não foram reproduzidos. Este ambiente tem 4 CPUs e não mostra a quantidade de CPUs nem a
  cota do builder do Render.
- O proxy deste ambiente bloqueia `deb.debian.org`. O build fiel, com a base Debian, não roda aqui. A validação usou
  o Dockerfile real **sem alterações** sobre uma base local Ubuntu 24.04 (Python 3.12, mesmo LibreOffice, UNO e
  Tesseract via apt), mais um contêiner `python:3.11-slim` para as dependências Python e o sandbox.
- A prova final é o build no próprio Render. Ela fica para a implantação autorizada.

## 3. Correção

- **`requirements.txt`:** `PyMuPDF>=1.28.2,<2`, com comentário apontando para este documento. É a mesma série do build
  que funcionava; a 1.28.2 é a última da série 1.x.
- **`.github/dependabot.yml`:** ignore **temporário** de `pymupdf` `>=2.0`, ao lado do ignore já existente do numpy. A série
  1.x continua recebendo atualizações. Nenhuma outra dependência foi afetada.
- **Fica igual:**
  - o sandbox: `seccomp`, `rlimits` e a lista de variáveis de ambiente permitidas;
  - o timeout de 100 s e o check do build e do `CMD`;
  - o Dockerfile e o código da Biblioteca.
- **Guarda:** `tests/test_fix_saas_pymupdf_build_v1.py` falha em três casos:
  - a faixa voltar a aceitar a 2.x;
  - o ignore do Dependabot sumir ou ficar mais amplo;
  - o PyMuPDF instalado ser 2.x ou carregar `onnxruntime` no import.

  Ele reprova a `main` e a imagem com o 2.0.

## 4. Validação (ambiente local descartável, dados fictícios)

| Teste | `main` (PyMuPDF 2.0) | Correção (1.28.2) |
|---|---|---|
| Build Docker com o Dockerfile real (base local Ubuntu) | passa neste host (check em 3,1 s) | passa (check em 2,3 s) |
| `check_library_runtime.py` na imagem, cota de 1 / 0,25 / 0,15 / 0,1 CPU | 5 / 15 / 25 / 32 a 37 s; uma falha a 0,1 | 2 / 9 / 13 / 20 a 21 s; sem falhas |
| Pacotes instalados | +`onnxruntime`, `pymupdf-layout`, `pymupdf4llm`, `networkx`, `flatbuffers`, `protobuf` | sem esses pacotes |
| Inicialização com o `CMD` real e PostgreSQL local | — | checks da Biblioteca e do OCR ok; `/health` 200 e `ready: true`; login ok |
| `tests/test_portal_docs_postgres.py` e `test_portal_docs_security.py` (HTTP real, PostgreSQL, pré-visualização Office/PDF, sandbox, RBAC, ataques entre empresas) | — | **65 passaram** |
| `scripts/test_portal_docs_mutations.py` | — | **8/8 mutações detectadas** |
| `pip-audit` | só Pillow 12.3.0 (39 avisos, correção no 13.x) | idem: sem diferença e sem aviso para o PyMuPDF |

## 5. Riscos residuais

- **Pillow 12.3.0 com vulnerabilidades conhecidas:** já existiam, iguais na `main` e na correção. A correção exige
  Pillow 13, que o `requirements.txt` limita a `<13`. Fica para demanda própria.
- **Fixação temporária na 1.x:** antes de remover o ignore e a faixa, validar a 2.x com build Docker real e cota de
  CPU. Alternativas, se a 2.x for necessária:
  - desligar o carregamento do layout no worker, se a biblioteca oferecer essa opção;
  - limitar as threads do `onnxruntime` no ambiente do worker;
  - medir o check no builder do Render.
- **O build fiel com Debian não foi executado aqui** (`deb.debian.org` bloqueado). O primeiro build no Render confirma.
