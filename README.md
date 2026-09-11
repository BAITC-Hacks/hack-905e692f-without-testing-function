# MedFlow AI

Early Warning & Forecasting System for aggregate hospital-flow analysis. It is a decision-support MVP for GovTech Camp: it does not diagnose patients, prescribe treatment, or replace human review.

## Status and data

No Ministry datasets were supplied in this repository. The end-to-end demo is intentionally marked **synthetic** and no ML quality metrics are asserted. Add source files to `data/raw/` (ignored by Git), run discovery, approve mappings, then train and validate before any operational use.

## Architecture

Raw files → profiling/adapters → validation/privacy filter → canonical aggregate periods → past-only features → chronological validation/model artifact → FastAPI → Next.js. See [architecture](docs/ARCHITECTURE.md), [data model](docs/DATA_MODEL.md), [privacy](docs/PRIVACY.md), and [ML methodology](docs/ML_METHODOLOGY.md).

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e . pytest ruff
PYTHONPATH=apps/api uvicorn app.main:app --reload --port 8000
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Dashboard: `http://localhost:3000`; API docs: `http://localhost:8000/docs`.

## Data ingestion and training

```bash
python scripts/profile_data.py
python scripts/generate_demo_data.py  # synthetic only
pytest
```

The profiler is an inventory/discovery tool and does not mutate raw files. Production mapping must be reviewed in `config/data_mappings/`; neither source columns nor linkage keys are invented.

## Validation, explainability and limitations

Baseline comparison is mandatory. Time-series splits preserve chronology and helper tests prevent current-target leakage. The live synthetic baseline exposes a residual-based interval and an interpretable rolling z-score anomaly. SHAP is deferred until a tree model has actually been trained. See [demo scenario](docs/DEMO_SCENARIO.md).

Known limitations: no real data, persistence-only demo forecast, no database/Alembic migration yet, and no authenticated production access. Priorities are source discovery, approved mappings, canonical ETL, model training/metrics and PostgreSQL persistence.

## Docker

```bash
docker compose up --build
```
