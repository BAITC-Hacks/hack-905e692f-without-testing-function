# Data quality controls

Качество проверяется при каждой offline ingestion, а результат записывается в versioned metadata и `data/interim/ingestion_state.json`.

Проверки:

- ровно один matching waiting-list CSV;
- обязательная schema;
- непустой безопасный organization identifier;
- parseable date в допустимом диапазоне;
- deterministic SHA-256 event key и deduplication;
- non-empty mart с точной schema;
- non-negative bounded counts и finite derived features;
- recorded source SHA-256, row counts, invalid/duplicate counts и date range;
- staged validation до atomic current-pointer publication.

```bash
python -m app.cli ingest
python -m app.cli status
cat data/interim/ingestion_state.json
cat data/processed/mart_versions/<VERSION>/metadata.json
```

Текущие цифры намеренно не копируются в документ: они меняются вместе со snapshot. `status=failed` не переключает рабочую витрину. Успешная техническая validation не заменяет semantic/privacy approval владельца данных.
