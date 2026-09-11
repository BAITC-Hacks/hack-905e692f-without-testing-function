#!/usr/bin/env python3
"""Write a replaceable, clearly marked synthetic aggregate input for local demos."""
import csv
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "apps/api"))
from app.core.demo_data import ORGANIZATIONS, observations  # noqa: E402

out = Path(__file__).parents[1] / "data/raw/synthetic_hospital_period.csv"
with out.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=["synthetic", "date", "organization_id", "referrals", "waiting", "refusals", "treated_cases"])
    writer.writeheader()
    for org_id, _, _ in ORGANIZATIONS:
        for row in observations(org_id):
            writer.writerow({"synthetic": True, "date": row.date.isoformat(), "organization_id": org_id, **row.model_dump()})
print(f"Created {out}; data are synthetic and must not be reported as Ministry data.")
