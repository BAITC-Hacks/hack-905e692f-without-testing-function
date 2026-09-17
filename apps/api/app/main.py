from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
from threading import Lock

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core import auth
from app.core.analytics import rolling_anomalies
from app.core.real_data import TARGET_NAME, DataUnavailable, local_contributors, train_or_load
from app.core.regions import region_name
from app.core.schemas import Forecast, OrganizationSummary, PeriodObservation

app = FastAPI(
    title="MedFlow AI", version="0.2.0", description="Real-data hospital-flow decision support"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def authenticate(request: Request, call_next):
    if (
        request.url.path.startswith("/api/v1/")
        and request.url.path
        not in {
            "/api/v1/health",
            "/api/v1/auth/register",
            "/api/v1/auth/login",
            "/api/v1/auth/ecp-demo",
        }
        and request.method != "OPTIONS"
    ):
        try:
            auth.session(request.headers.get("authorization"))
        except HTTPException as error:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": error.detail}, status_code=error.status_code)
    return await call_next(request)


@lru_cache(maxsize=1)
def _load_dataset() -> dict[str, object]:
    try:
        return train_or_load()
    except DataUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


_DATA_LOCK = Lock()


def dataset() -> dict[str, object]:
    with _DATA_LOCK:
        return _load_dataset()


dataset.cache_clear = _load_dataset.cache_clear


def known(org_id: str) -> None:
    if org_id not in _grouped():
        raise HTTPException(status_code=404, detail="Medical organization not found")


@lru_cache(maxsize=1)
def _grouped() -> dict[str, list]:
    output = defaultdict(list)
    for row in dataset()["rows"]:
        output[row.organization_id].append(row)
    return output


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    try:
        metadata = dataset()["metadata"]
        return {
            "status": "ok",
            "data_mode": "real_raw",
            "source_file": str(metadata["source_file"]),
        }
    except HTTPException as error:
        return {"status": "degraded", "data_mode": "unavailable", "detail": str(error.detail)}


@app.get("/api/v1/regions")
def regions() -> list[dict[str, object]]:
    grouped = _grouped()
    by_region = Counter(rows[-1].region_id for rows in grouped.values())
    return [
        {
            "id": key,
            "code": key,
            "name": region_name(key),
            "organization_count": by_region[key],
            "waiting": count,
            "synthetic": False,
        }
        for key, count in sorted(dataset()["metadata"]["region_counts"].items())
    ]


@lru_cache(maxsize=1)
def organization_summaries():
    result = []
    for org, rows in _grouped().items():
        region = rows[-1].region_id
        snapshot_total = dataset()["metadata"]["organization_counts"].get(
            org, sum(row.waiting for row in rows)
        )
        latest = rows[-1].waiting
        mean = sum(row.waiting for row in rows[-7:]) / min(7, len(rows))
        risk = (
            "high"
            if latest > mean * 1.8 and latest >= 5
            else "medium"
            if latest > mean * 1.25
            else "low"
        )
        result.append(
            OrganizationSummary(
                id=org,
                name=f"МО {org}",
                region_id=region,
                latest_waiting=snapshot_total,
                latest_daily_registrations=latest,
                risk=risk,
            )
        )
    return sorted(result, key=lambda item: item.latest_waiting, reverse=True)


def paginate(items, page, page_size):
    total = len(items)
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
        "items": items[(page - 1) * page_size : page * page_size],
    }


@app.get("/api/v1/organizations")
def organizations(
    region_id: str | None = None,
    risk: str | None = None,
    search: str = "",
    sort: str = Query("risk", pattern="^(risk|name|waiting)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    rows = [
        r
        for r in organization_summaries()
        if (not region_id or r.region_id == region_id)
        and (not risk or r.risk == risk)
        and search.lower() in (r.name + " " + region_name(r.region_id)).lower()
    ]
    ranks = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    rows.sort(
        key=lambda r: (
            (r.name, r.id)
            if sort == "name"
            else (-r.latest_waiting, r.id)
            if sort == "waiting"
            else (-ranks[r.risk], r.id)
        )
    )
    return paginate(rows, page, page_size)


@app.get("/api/v1/summary")
def summary():
    rows = organization_summaries()
    return {
        "waiting": sum(r.latest_waiting for r in rows),
        "organizations": len(rows),
        "attention": sum(r.risk in {"high", "critical"} for r in rows),
    }


@app.get("/api/v1/organizations/{org_id}/history", response_model=list[PeriodObservation])
def history(org_id: str, days: int = Query(30, ge=7, le=84)) -> list[PeriodObservation]:
    known(org_id)
    rows = _grouped()[org_id][-days:]
    return [
        PeriodObservation(
            date=row.day, referrals=0, waiting=row.waiting, refusals=0, treated_cases=0
        )
        for row in rows
    ]


@app.get("/api/v1/forecasts/{org_id}", response_model=Forecast)
def forecast(org_id: str, horizon_days: int = Query(7, ge=1, le=30)) -> Forecast:
    known(org_id)
    state = dataset()
    bundle, metadata = state["bundle"], state["metadata"]
    rows = _grouped()[org_id]
    values, current = [row.waiting for row in rows], rows[-1].day
    if len(values) < 7:
        raise HTTPException(422, "Недостаточно истории для прогноза")
    points = []
    first_explanation = []
    for step in range(1, horizon_days + 1):
        next_day = current + timedelta(days=step)
        feature = [org_id, next_day.weekday(), values[-1], values[-7], sum(values[-7:]) / 7]
        value = max(0.0, float(bundle["pipeline"].predict([feature])[0]))
        if not first_explanation:
            first_explanation = local_contributors(bundle, feature)
        spread = float(metadata["metrics"]["residual_std"]) * 1.96 * (step**0.5)
        points.append(
            {
                "date": str(next_day),
                "expected": round(value, 2),
                "lower": round(max(0, value - spread), 2),
                "upper": round(value + spread, 2),
            }
        )
        values.append(value)
    first = points[0]
    contributors = [
        f"{item['feature']}: {float(item['contribution']):+.2f}" for item in first_explanation
    ]
    return Forecast(
        organization_id=org_id,
        target=TARGET_NAME,
        generated_at=datetime.now().astimezone().date(),
        horizon_days=horizon_days,
        expected=first["expected"],
        lower=first["lower"],
        upper=first["upper"],
        model="ridge_chronological",
        contributors=contributors,
        points=points,
        explanation=first_explanation,
        uncertainty_method="95% normal interval from chronological holdout residual standard deviation",
        synthetic=False,
    )


@app.get("/api/v1/anomalies")
def anomalies(
    organization_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    ids = [organization_id] if organization_id else list(_grouped())
    for org_id in ids:
        known(org_id)
    return paginate(
        [a for org_id in ids for a in rolling_anomalies(org_id, history(org_id, 84))],
        page,
        page_size,
    )


@app.get("/api/v1/metrics")
def metrics() -> dict[str, object]:
    metadata = {
        k: v
        for k, v in dataset()["metadata"].items()
        if k not in {"region_counts", "organization_counts"}
    }
    return {"status": "trained", "synthetic": False, **metadata}


@app.post("/api/v1/auth/register")
def register(payload: dict) -> dict:
    return auth.register_user(payload)


@app.post("/api/v1/auth/login")
def login(payload: dict) -> dict:
    return auth.login_user(payload)


@app.get("/api/v1/auth/me")
def me(authorization: str | None = Header(None)):
    return auth.session(authorization)


@app.post("/api/v1/auth/logout")
def logout(authorization: str | None = Header(None)):
    auth.revoke(authorization)
    return {"ok": True}


@app.post("/api/v1/auth/ecp-demo")
def ecp_demo() -> dict:
    return {**auth.issue("ecp-demo@local", demo=True), "notice": "DEMO: без интеграции НУЦ РК"}
