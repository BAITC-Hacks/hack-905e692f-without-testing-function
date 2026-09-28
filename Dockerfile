FROM python:3.12.7-slim-bookworm AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml requirements.lock ./
COPY apps/api ./apps/api
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --require-virtualenv -r requirements.lock && /opt/venv/bin/pip install --require-virtualenv --no-deps .

FROM python:3.12.7-slim-bookworm AS runtime
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/app/apps/api PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10001 medflow && useradd --system --uid 10001 --gid medflow --home /nonexistent --shell /usr/sbin/nologin medflow
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=medflow:medflow apps/api ./apps/api
COPY --chown=medflow:medflow config ./config
COPY --chown=medflow:medflow docs/data_profile.json ./docs/data_profile.json
RUN mkdir -p /app/data/interim /app/data/processed /app/ml/artifacts /app/state && chown -R medflow:medflow /app/data /app/ml /app/state
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips=*"]
