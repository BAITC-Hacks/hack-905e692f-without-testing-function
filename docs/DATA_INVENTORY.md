# Data inventory

Runtime source of truth — metadata опубликованной витрины, а не список файлов в README.

## Runtime inputs

`app.core.data_pipeline.waiting_source()` принимает ровно один CSV `data/raw/*Ожидающие плановую*.csv`. Обязательные columns:

- `mo_destination_code`;
- `registration_dt`;
- `region_origin_code`;
- `patient_seq_no` используется только для deduplication key и не публикуется.

Остальные raw extracts могут присутствовать для отдельного исследования, но текущий ingestion/training/API их не использует. `data/raw/synthetic_hospital_period.csv` также не входит в runtime path.

## Published inventory

```bash
python -m app.cli status
cat data/processed/current_mart.json
cat data/processed/mart_versions/<VERSION>/metadata.json
cat ml/artifacts/current_model.json
```

Mart metadata содержит source filename/fingerprint, rows scanned/processed, invalid/duplicate counts, organizations и date range. Model metadata содержит matching dataset fingerprint/version. `scripts/profile_data.py` — отдельный discovery tool; `docs/data_profile.json` может быть историческим snapshot профилирования и не определяет runtime readiness.

Raw/interim/processed/model файлы игнорируются Git. Передача и retention выполняются по политике владельца данных.
