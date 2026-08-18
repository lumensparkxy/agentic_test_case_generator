FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin appuser

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-compile -r backend/requirements.txt

COPY backend/execution_runtime/package.json backend/execution_runtime/package-lock.json backend/execution_runtime/
RUN cd backend/execution_runtime \
    && npm ci \
    && npx playwright install --with-deps msedge

COPY --chown=appuser:appuser backend ./backend
RUN mkdir -p .execution_artifacts && chown -R appuser:appuser .execution_artifacts

USER appuser

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8080}"]
