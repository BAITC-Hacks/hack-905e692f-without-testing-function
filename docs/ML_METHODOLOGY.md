# ML methodology

## Forecast semantics

The target is `daily_waiting_registrations`: the count of records registered on a day and still present in the supplied waiting-list snapshot. It is **not future total queue size** and it is not the complete historical arrival stream. The current queue snapshot is displayed independently and enters only the transparent risk layer.

## Model and validation

The current production artifact is Ridge Regression with destination-organization one-hot encoding plus weekday, lag-1, lag-7 and trailing seven-day mean. Every lag uses only observations strictly before the target day. The final 20% of dates is held out chronologically; random split is prohibited.

The persisted metadata contains model id/version, algorithm, target, features, train/validation periods, trained time, MAE, RMSE, WAPE, persistence baseline MAE, dataset fingerprint and residual spread. Metrics shown by the UI come only from the persisted validation metadata. MAPE is not a primary metric because targets often approach zero.

Ridge remains selected because it is interpretable and currently improves held-out MAE over lag-1 persistence. A tree model is not labelled production without a reproducible chronological comparison and a semantically valid treatment of the high-cardinality organization feature.

## Explainability

For one forecast, each local contribution is calculated as the transformed feature value multiplied by its fitted Ridge coefficient. The intercept is shown as the baseline and signed contributions reconcile to the raw linear prediction before non-negative clipping. One-hot organization features are grouped under the human label “historical organization profile”; raw transformed names are not displayed.

Global values are coefficient magnitudes and depend on feature scale. Local and global explanations describe statistical associations, not causality. Approximate forecast intervals use chronological holdout residual dispersion and are not guaranteed coverage intervals.

## Risk

Prediction and risk are separate. `config/risk_thresholds.yaml` controls points for forecast-to-history ratio, recent trend, latest anomaly z-score, current queue snapshot and forecast error. The result contains a 0–100 score, `normal/attention/high/critical` level, reasons, checks and model version. No organization identifier appears in a rule.

## Lifecycle

`data/raw → validation → persisted aggregates → explicit train → joblib artifact + JSON metadata → lazy runtime load → API → frontend`.

Run explicit training after authorized source changes:

```bash
PYTHONPATH=apps/api .venv/bin/python scripts/train_model.py
```

Only trusted locally produced joblib files may be loaded.
