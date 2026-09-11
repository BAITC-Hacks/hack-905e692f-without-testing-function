#!/usr/bin/env python3
"""Safe raw-data inventory; never alters input files or prints cell-level sensitive data."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
RAW = ROOT / "data/raw"
SENSITIVE = {"iin", "patient", "name", "fio", "phone", "email", "address"}
records = []
for path in sorted(p for p in RAW.rglob("*") if p.is_file() and p.name != ".gitkeep"):
    item = {"file": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "format": path.suffix.lower(), "status": "metadata_only"}
    if path.suffix.lower() in {".csv", ".tsv"}:
        try:
            with path.open(encoding="utf-8-sig", newline="") as source:
                rows = list(csv.DictReader(source, delimiter="\t" if path.suffix.lower() == ".tsv" else ","))
            columns = list(rows[0]) if rows else []
            item.update(rows=len(rows), columns=columns, sensitive_fields=[c for c in columns if any(t in c.lower() for t in SENSITIVE)])
        except (UnicodeDecodeError, csv.Error) as exc:
            item["error"] = str(exc)
    records.append(item)
(ROOT / "docs/data_profile.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(records, ensure_ascii=False, indent=2))
