# Data inventory

At repository initialization `data/raw/` contains no Ministry source files. The implementation therefore runs only in `synthetic_demo` mode. Run `python scripts/profile_data.py` after files are placed in `data/raw/`; its machine-readable report is `docs/data_profile.json` and is intentionally not committed.

Supported discovery targets: CSV, TSV, XLS/XLSX, Parquet and JSON. CSV/TSV profiling is implemented without loading data into the API process; Excel/Parquet/JSON require an adapter added after inspecting their actual schemas.
