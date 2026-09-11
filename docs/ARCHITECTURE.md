# Architecture

```mermaid
flowchart LR
  S[Ministry source files] --> I[Discovery & adapters]
  I --> V[Validation + privacy filter]
  V --> C[Canonical period facts]
  C --> F[Past-only feature pipeline]
  F --> T[Chronological training]
  T --> A[Model artifacts + metadata]
  A --> B[Forecast/risk/anomaly services]
  C --> B
  B --> API[FastAPI]
  API --> UI[Next.js dashboard]
  C -. optional .-> DB[(PostgreSQL)]
  C -. batch artifacts .-> P[(Parquet/DuckDB)]
```

Current MVP stores deterministic synthetic aggregates in code solely to keep the demo runnable. Production ingestion writes canonical aggregates and precomputed forecasts; inference does not train per request.
