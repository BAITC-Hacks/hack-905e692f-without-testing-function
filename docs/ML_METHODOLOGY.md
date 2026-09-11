# ML methodology

## Current state

No real dataset is available, therefore no trained ML artifact or quality metric is claimed. The running demo uses a transparent persistence baseline on synthetic aggregates only. `GET /metrics` explicitly reports `not_trained`.

## Proposed controlled pipeline

The target is observed aggregate `waiting` per verified organization-period. Observation unit and horizon follow source granularity (daily: 7/14/30 days; weekly: 1/2/4 weeks; monthly: 1/2/3 months) and must be shortened for inadequate history. Candidate models: seasonal naive, persistence, Ridge, Extra Trees, then CatBoost only after baseline comparison.

Features include only data available at forecast cutoff: lags, trailing rolling statistics, calendar fields, and semantically valid aggregate ratios. Validation is chronological expanding-window or holdout; random splitting is prohibited. MAE, RMSE and sMAPE are calculated only on held-out periods. Intervals use held-out residual/conformal quantiles. SHAP is applied only to a fitted tree model and surfaced as actual signed contributions.

Limitations: a forecast is association, not causation; it cannot measure bed occupancy without validated bed data; source revisions, sparse series, changed reporting practice and unobserved operational factors may invalidate estimates.
