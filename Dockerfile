FROM python:3.12-slim

WORKDIR /

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=2.4.1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && pip install --no-cache-dir "poetry==${POETRY_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock* ./

RUN poetry install --without dev --no-root

COPY app/ ./app

CMD ["sh", "-c", "poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
