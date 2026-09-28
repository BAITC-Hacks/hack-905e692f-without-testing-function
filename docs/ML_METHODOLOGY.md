# ML methodology

## Target semantics

`daily_waiting_registrations` — число регистраций на organization/day среди записей, сохранившихся в предоставленном waiting-list snapshot. Это не прогноз полного размера очереди и не полный historical arrival stream. `current_waiting_snapshot` используется отдельно в прозрачном risk layer.

## Features and preprocessing

После заполнения отсутствующих дней нулями для каждой организации строятся: `day_of_week`, `month`, `is_weekend`, lag 1/7/14/28, rolling mean/std 7, разность последних двух семидневных средних и historical anomaly z-score. Все lag/window используют только прошлые значения. Первые 28 точек каждой организации исключаются.

`organization_id` проходит `OneHotEncoder(handle_unknown="ignore")`; все числовые признаки — `StandardScaler`. `ColumnTransformer` fit только на train partition.

## Model and validation

Production pipeline фиксированно обучает `Ridge(alpha=3.0, solver="lsqr")`. Последние 20% уникальных календарных дат — chronological holdout; random split и TimeSeriesSplit не используются. Persistence baseline предсказывает `lag_1` на тех же validation rows.

Метрики: MAE, RMSE, WAPE и baseline MAE. Model metadata также содержит train/validation periods, row counts, residual standard deviation, source fingerprint и feature schema. Текущие значения смотрите через UI, `/api/v1/model/info` или versioned metadata. Код не выполняет автоматический перебор tree/boosting candidates.

## Selection and publication

Ridge — единственная production candidate в текущем коде; metadata сохраняет её метрики и baseline для сравнения. Artifact сначала пишется во временную version directory, затем проверяется на bundle schema/fingerprint/SHA-256 и только после этого атомарно публикуется. Failed training не меняет current pointer.

```bash
python -m app.cli ingest
python -m app.cli train
python -m app.cli status
```

## Inference and explainability

Multi-step forecast рекурсивно использует предыдущие predictions только для будущих шагов и clips отрицательный output к нулю. Interval приблизительно основан на chronological holdout residual dispersion.

Local contribution = transformed feature value × Ridge coefficient; intercept показывается как baseline. Global importance — абсолютный коэффициент в transformed space. Значения описывают ассоциацию и не доказывают причинность.

## Risk

Risk не является output Ridge. `app.core.risk.assess_risk` отдельно оценивает forecast/history ratio, recent trend, anomaly z-score, current snapshot queue и uncertainty по [risk_thresholds.yaml](../config/risk_thresholds.yaml). Пороги и баллы описаны в основном README.
