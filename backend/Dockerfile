FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --upgrade pip poetry

# Dependencies first so code changes do not invalidate this layer.
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root

COPY . .

EXPOSE 8000

# ASGI (HTTP + node WebSockets). entrypoint-app.sh migrates, collects static, then execs this.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "core.asgi:app"]
