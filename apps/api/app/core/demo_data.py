"""Clearly labelled, deterministic synthetic demo data. Never represents Ministry data."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.core.schemas import OrganizationSummary, PeriodObservation

ORGANIZATIONS = [
    ("org-almaty-1", "Synthetic City Hospital A", "region-almaty"),
    ("org-almaty-2", "Synthetic City Hospital B", "region-almaty"),
    ("org-astana-1", "Synthetic Regional Hospital C", "region-astana"),
]
REGIONS = {"region-almaty": "Synthetic Region Almaty", "region-astana": "Synthetic Region Astana"}


def observations(org_id: str, days: int = 84) -> list[PeriodObservation]:
    offset = {org[0]: index for index, org in enumerate(ORGANIZATIONS)}[org_id]
    start = datetime.now().astimezone().date() - timedelta(days=days - 1)
    rows: list[PeriodObservation] = []
    for i in range(days):
        referrals = round(43 + offset * 4 + 7 * math.sin(i / 7) + (i % 5))
        treated = round(38 + offset * 3 + 4 * math.cos(i / 9) + (i % 3))
        pressure = max(0, referrals - treated)
        waiting = round(72 + offset * 15 + i * (0.35 + offset * 0.12) + 8 * math.sin(i / 10) + pressure)
        if org_id == "org-almaty-1" and i == days - 3:
            waiting += 68  # controlled synthetic anomaly
        rows.append(PeriodObservation(date=start + timedelta(days=i), referrals=referrals, waiting=waiting,
                                      refusals=round(2 + (i + offset) % 4), treated_cases=treated))
    return rows


def summaries() -> list[OrganizationSummary]:
    result = []
    for org_id, name, region_id in ORGANIZATIONS:
        latest = observations(org_id)[-1].waiting
        risk = "high" if org_id == "org-almaty-1" else "medium" if org_id == "org-almaty-2" else "low"
        result.append(OrganizationSummary(id=org_id, name=name, region_id=region_id, latest_waiting=latest, risk=risk))
    return result
