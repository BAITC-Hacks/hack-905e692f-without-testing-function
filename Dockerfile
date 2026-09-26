FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY apps/api ./apps/api
RUN pip install --no-cache-dir .
COPY config ./config
COPY docs/data_profile.json ./docs/data_profile.json
RUN mkdir -p /app/data/raw /app/data/processed /app/ml/artifacts
ENV PYTHONPATH=/app/apps/api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
