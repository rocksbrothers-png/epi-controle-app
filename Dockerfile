# ── API / Backend (Python) — SaaS split-deploy ───────────────────────────────
# NÃO compila Flutter Web. No SaaS o front é um static site SEPARADO
# (epi-controle-app-livamobile-web), então a API (backend) não precisa do Flutter.
# Isso também evita as falhas de build do Flutter no sandbox do Render
# (tar/precache e conflito de versão do intl/flutter_localizations).
#
# O Dockerfile multi-stage antigo (co-deploy Flutter Web + Python) foi preservado
# em Dockerfile.fullstack, caso se queira voltar ao modelo embutido.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TESSERACT_CMD=/usr/bin/tesseract \
    OCR_REQUIRED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-por \
    tesseract-ocr-spa \
    tesseract-ocr-nor \
    tesseract-ocr-fra \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt

COPY . .

RUN echo "[render][docker] API-only build (sem Flutter Web; o web é static site separado no SaaS)."

# Gera static/index.html a partir dos fragmentos (UI legada servida em / como
# fallback). O front do SaaS é o Flutter Web no static site separado.
RUN python scripts/build_index.py build

# Validação de runtime OCR no build (evita deploy quebrado em produção).
RUN python -m py_compile epi_backend/manufacture_date_ocr.py server_postgres.py app.py
RUN python -c "from epi_backend.manufacture_date_ocr import detect_manufacture_date, get_ocr_runtime_status; print('ocr_import_ok', callable(detect_manufacture_date), callable(get_ocr_runtime_status))"
RUN tesseract --version
RUN python -c "import pytesseract; print(pytesseract.get_tesseract_version())"
RUN python scripts/check_ocr_runtime.py --require

EXPOSE 8000

CMD ["sh", "-c", "python scripts/check_ocr_runtime.py --require && exec python app.py"]
