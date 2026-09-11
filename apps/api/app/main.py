from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.core.analytics import persistence_forecast, rolling_anomalies
from app.core.demo_data import ORGANIZATIONS, REGIONS, observations, summaries
from app.core.schemas import Anomaly, Forecast, OrganizationSummary, PeriodObservation

app = FastAPI(title="MedFlow AI", version="0.1.0", description="Synthetic-data decision-support MVP")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["GET"], allow_headers=["*"])


def known(org_id: str) -> None:
    if org_id not in {item[0] for item in ORGANIZATIONS}:
        raise HTTPException(status_code=404, detail="Medical organization not found")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "data_mode": "synthetic_demo"}


@app.get("/api/v1/regions")
def regions() -> list[dict[str, object]]:
    orgs = summaries()
    return [{"id": key, "name": name, "organization_count": sum(o.region_id == key for o in orgs), "synthetic": True} for key, name in REGIONS.items()]


@app.get("/api/v1/organizations", response_model=list[OrganizationSummary])
def organizations(region_id: str | None = None) -> list[OrganizationSummary]:
    return [o for o in summaries() if not region_id or o.region_id == region_id]


@app.get("/api/v1/organizations/{org_id}/history", response_model=list[PeriodObservation])
def history(org_id: str, days: int = Query(30, ge=7, le=84)) -> list[PeriodObservation]:
    known(org_id)
    return observations(org_id)[-days:]


@app.get("/api/v1/forecasts/{org_id}", response_model=Forecast)
def forecast(org_id: str, horizon_days: int = Query(7, ge=1, le=30)) -> Forecast:
    known(org_id)
    return persistence_forecast(org_id, observations(org_id), horizon_days)


@app.get("/api/v1/anomalies", response_model=list[Anomaly])
def anomalies(organization_id: str | None = None) -> list[Anomaly]:
    ids = [organization_id] if organization_id else [item[0] for item in ORGANIZATIONS]
    for org_id in ids:
        known(org_id)
    return [a for org_id in ids for a in rolling_anomalies(org_id, observations(org_id))]


@app.get("/api/v1/metrics")
def metrics() -> dict[str, object]:
    return {"status": "not_trained", "reason": "No real data is present. Metrics are deliberately withheld rather than fabricated.", "synthetic": True}
