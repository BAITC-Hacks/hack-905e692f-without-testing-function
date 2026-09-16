from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.core.analytics import rolling_anomalies
from app.core.real_data import TARGET_NAME, DataUnavailable, local_contributors, train_or_load
from app.core.schemas import Anomaly, Forecast, OrganizationSummary, PeriodObservation

app = FastAPI(title="MedFlow AI", version="0.2.0", description="Real-data hospital-flow decision support")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["GET"], allow_headers=["*"])


DEMO_USERS: dict[str, dict[str, str]] = {}

@lru_cache(maxsize=1)
def dataset() -> dict[str, object]:
    try: return train_or_load()
    except DataUnavailable as error: raise HTTPException(status_code=503, detail=str(error)) from error

def known(org_id: str) -> None:
    if org_id not in {row.organization_id for row in dataset()["rows"]}: raise HTTPException(status_code=404, detail="Medical organization not found")

def _grouped() -> dict[str, list]:
    output = defaultdict(list)
    for row in dataset()["rows"]: output[row.organization_id].append(row)
    return output


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    try:
        metadata = dataset()["metadata"]
        return {"status": "ok", "data_mode": "real_raw", "source_file": str(metadata["source_file"])}
    except HTTPException as error: return {"status": "degraded", "data_mode": "unavailable", "detail": str(error.detail)}


@app.get("/api/v1/regions")
def regions() -> list[dict[str, object]]:
    grouped = _grouped(); by_region = Counter(rows[-1].region_id for rows in grouped.values())
    return [{"id": key, "name": f"Код региона происхождения {key}", "organization_count": count, "synthetic": False} for key, count in sorted(by_region.items())]


@app.get("/api/v1/organizations", response_model=list[OrganizationSummary])
def organizations(region_id: str | None = None) -> list[OrganizationSummary]:
    result = []
    for org, rows in _grouped().items():
        region = rows[-1].region_id
        if region_id and region != region_id: continue
        snapshot_total = sum(row.waiting for row in rows)
        latest = rows[-1].waiting
        mean = sum(row.waiting for row in rows[-7:]) / min(7, len(rows))
        risk = "high" if latest > mean * 1.8 and latest >= 5 else "medium" if latest > mean * 1.25 else "low"
        result.append(OrganizationSummary(id=org, name=f"МО {org}", region_id=region, latest_waiting=snapshot_total, latest_daily_registrations=latest, risk=risk))
    return sorted(result, key=lambda item: item.latest_waiting, reverse=True)


@app.get("/api/v1/organizations/{org_id}/history", response_model=list[PeriodObservation])
def history(org_id: str, days: int = Query(30, ge=7, le=84)) -> list[PeriodObservation]:
    known(org_id)
    rows = _grouped()[org_id][-days:]
    return [PeriodObservation(date=row.day, referrals=0, waiting=row.waiting, refusals=0, treated_cases=0) for row in rows]


@app.get("/api/v1/forecasts/{org_id}", response_model=Forecast)
def forecast(org_id: str, horizon_days: int = Query(7, ge=1, le=30)) -> Forecast:
    known(org_id)
    state = dataset(); bundle, metadata = state["bundle"], state["metadata"]
    rows = _grouped()[org_id]
    values, current = [row.waiting for row in rows], rows[-1].day
    points = []; first_explanation = []
    for step in range(1, horizon_days + 1):
        next_day = current + timedelta(days=step)
        feature = [org_id, next_day.weekday(), values[-1], values[-7], sum(values[-7:]) / 7]
        value = max(0.0, float(bundle["pipeline"].predict([feature])[0]))
        if not first_explanation: first_explanation = local_contributors(bundle, feature)
        spread = float(metadata["metrics"]["residual_std"]) * 1.96 * (step ** .5)
        points.append({"date": str(next_day), "expected": round(value, 2), "lower": round(max(0, value - spread), 2), "upper": round(value + spread, 2)})
        values.append(value)
    first = points[0]
    contributors = [f"{item['feature']}: {float(item['contribution']):+.2f}" for item in first_explanation]
    return Forecast(organization_id=org_id, target=TARGET_NAME, generated_at=datetime.now().astimezone().date(), horizon_days=horizon_days, expected=first["expected"], lower=first["lower"], upper=first["upper"], model="ridge_chronological", contributors=contributors, points=points, explanation=first_explanation, uncertainty_method="95% normal interval from chronological holdout residual standard deviation", synthetic=False)


@app.get("/api/v1/anomalies", response_model=list[Anomaly])
def anomalies(organization_id: str | None = None) -> list[Anomaly]:
    ids = [organization_id] if organization_id else list(_grouped())
    for org_id in ids:
        known(org_id)
    return [a for org_id in ids for a in rolling_anomalies(org_id, history(org_id, 84))]


@app.get("/api/v1/metrics")
def metrics() -> dict[str, object]:
    metadata = dataset()["metadata"]
    return {"status": "trained", "synthetic": False, **metadata}

@app.post("/api/v1/auth/register")
def register(payload: dict[str, str]) -> dict[str, object]:
    email, password = (payload.get("email") or "").strip().lower(), payload.get("password") or ""
    if "@" not in email or len(password) < 6: raise HTTPException(status_code=422, detail="Use a valid email and a password of at least 6 characters.")
    if email in DEMO_USERS: raise HTTPException(status_code=409, detail="This demo account already exists.")
    DEMO_USERS[email] = {"password": password, "role": "analyst"}
    return {"access_token": f"demo-{uuid4()}", "email": email, "role": "analyst", "demo": True}

@app.post("/api/v1/auth/login")
def login(payload: dict[str, str]) -> dict[str, object]:
    email, password = (payload.get("email") or "").strip().lower(), payload.get("password") or ""
    user = DEMO_USERS.get(email)
    if not user or user["password"] != password: raise HTTPException(status_code=401, detail="Invalid demo email or password.")
    return {"access_token": f"demo-{uuid4()}", "email": email, "role": user["role"], "demo": True}

@app.post("/api/v1/auth/ecp-demo")
def ecp_demo() -> dict[str, object]:
    return {"access_token": f"demo-ecp-{uuid4()}", "email": "ecp-demo@local", "role": "analyst", "demo": True, "notice": "Demo confirmation only: no certificate, signature, or NCA RK integration is used."}
