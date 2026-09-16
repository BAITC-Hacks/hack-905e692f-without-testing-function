"""Streaming, schema-first inventory of local source extracts (no cell values emitted)."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parents[1]
RAW = ROOT / "data/raw"
OUT = ROOT / "docs/data_profile.json"
SENSITIVE = {"iin", "patient", "name", "fio", "phone", "email", "address", "patient_seq"}
SAMPLE_ROWS = 10_000

def profile_csv(path: Path) -> dict[str, object]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            with path.open(encoding=encoding, newline="") as source:
                reader = csv.DictReader(source)
                columns = reader.fieldnames or []
                nulls, distinct, rows = Counter(), {c: set() for c in columns}, 0
                for row in reader:
                    rows += 1
                    if rows <= SAMPLE_ROWS:
                        for column in columns:
                            value = (row.get(column) or "").strip()
                            if not value: nulls[column] += 1
                            elif len(distinct[column]) < 100: distinct[column].add(value)
                return {"encoding": encoding, "rows": rows, "columns": columns, "sample_rows": min(rows, SAMPLE_ROWS), "nulls_in_sample": dict(nulls), "distinct_in_sample_capped": {c: len(v) for c, v in distinct.items()}}
        except (UnicodeDecodeError, csv.Error) as error:
            last_error = error
    return {"error": str(last_error or "unreadable CSV")}

records = []
for path in sorted(p for p in RAW.rglob("*") if p.is_file() and p.name != ".gitkeep"):
    item: dict[str, object] = {"file": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "format": path.suffix.lower(), "status": "profiled"}
    if path.suffix.lower() in {".csv", ".tsv"}:
        item.update(profile_csv(path))
        columns = item.get("columns", [])
        item["sensitive_fields"] = [c for c in columns if any(t in c.lower() for t in SENSITIVE)]
    else: item["status"] = "unsupported_format"
    records.append(item)
OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(records, ensure_ascii=False, indent=2))
