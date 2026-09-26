from __future__ import annotations

import math
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
from threading import Lock

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.core import auth, storage
from app.core.analytics import rolling_anomalies
from app.core.provenance import data_health
from app.core.provenance import data_sources as provenance_sources
from app.core.real_data import (
    TARGET_NAME,
    DataUnavailable,
    ModelNotReady,
    load_runtime,
    local_contributors,
)
from app.core.regions import REGIONS, region_name
from app.core.reporting import checksum, csv_export, pdf_export, printable_html, xlsx_export
from app.core.risk import assess_risk
from app.core.schemas import (
    DecisionActionCreate,
    DecisionActionStatus,
    Forecast,
    OrganizationSummary,
    PeriodObservation,
    ReportCreate,
)

app = FastAPI(title="MedFlow AI", version="1.0.0", description="GovTech hospital-flow decision support")
cors_origins = [value.strip() for value in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if value.strip()]
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["GET", "POST", "PATCH"], allow_headers=["*"])
PUBLIC = {"/api/v1/health", "/api/v1/auth/login", "/api/v1/auth/ecp-demo"}


@app.middleware("http")
async def authenticate(request: Request, call_next):
    public_verification = request.url.path.startswith("/api/v1/reports/verify/")
    if request.url.path.startswith("/api/v1/") and request.url.path not in PUBLIC and not public_verification and request.method != "OPTIONS":
        try:
            auth.session(request.headers.get("authorization"))
        except HTTPException as error:
            return JSONResponse({"error": {"code": "AUTH_REQUIRED", "message": str(error.detail), "retryable": False}}, status_code=error.status_code)
    return await call_next(request)


@app.exception_handler(DataUnavailable)
def unavailable_handler(_request: Request, error: DataUnavailable):
    return JSONResponse({"error": {"code": "DATA_UNAVAILABLE", "message": str(error), "retryable": True}}, status_code=503)


@app.exception_handler(ModelNotReady)
def model_handler(_request: Request, error: ModelNotReady):
    return JSONResponse({"error": {"code": "MODEL_NOT_READY", "message": str(error), "retryable": True}}, status_code=503)


@lru_cache(maxsize=1)
def _load_dataset() -> dict[str, object]:
    return load_runtime()


_DATA_LOCK = Lock()


def dataset() -> dict[str, object]:
    with _DATA_LOCK:
        return _load_dataset()


dataset.cache_clear = _load_dataset.cache_clear


@lru_cache(maxsize=1)
def _grouped() -> dict[str, list]:
    grouped = defaultdict(list)
    for row in dataset()["rows"]:
        grouped[row.organization_id].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: row.day)
    return grouped


def known(org_id: str):
    if org_id not in _grouped():
        raise HTTPException(404, "Медицинская организация не найдена")


def _user(authorization: str | None) -> str:
    return auth.session(authorization)["email"]


def _human_feature(name: str) -> str:
    normalized = name.replace("numeric__", "")
    if normalized.startswith(("organization__", "organization=")):
        return "Исторический профиль организации"
    labels = {"x1": "День недели", "dow": "День недели", "x2": "Регистрации за предыдущий день", "lag_1": "Регистрации за предыдущий день", "x3": "Регистрации 7 дней назад", "lag_7": "Регистрации 7 дней назад", "x4": "Среднее регистраций за 7 дней", "mean_7": "Среднее регистраций за 7 дней"}
    for raw, label in labels.items():
        if normalized == raw or normalized.startswith(raw + "_"):
            return label
    return "Фактор модели"


def _forecast_payload(org_id: str, horizon_days: int = 7) -> dict[str, object]:
    known(org_id)
    state, rows = dataset(), _grouped()[org_id]
    bundle, metadata = state["bundle"], state["metadata"]
    values, current = [row.waiting for row in rows], rows[-1].day
    if len(values) < 7:
        raise HTTPException(422, "Недостаточно истории для прогноза")
    points, explanation = [], []
    for step in range(1, horizon_days + 1):
        next_day = current + timedelta(days=step)
        feature = [org_id, next_day.weekday(), values[-1], values[-7], sum(values[-7:]) / 7]
        value = max(0.0, float(bundle["pipeline"].predict([feature])[0]))
        if not explanation:
            explanation = local_contributors(bundle, feature)
        spread = float(metadata["metrics"]["residual_std"]) * 1.96 * math.sqrt(step)
        points.append({"date": str(next_day), "expected": round(value, 2), "lower": round(max(0, value - spread), 2), "upper": round(value + spread, 2)})
        values.append(value)
    top = [item for item in explanation if not str(item["feature"]).startswith("organization__")][:2] or explanation[:2]
    direction = "выше" if points[0]["expected"] >= statistics.mean([row.waiting for row in rows[-7:]]) else "ниже"
    factors = ", ".join(_human_feature(str(item["feature"])) for item in top)
    return {"organization_id": org_id, "target": TARGET_NAME, "generated_at": datetime.now().astimezone().date(), "horizon_days": horizon_days, "expected": points[0]["expected"], "lower": points[0]["lower"], "upper": points[0]["upper"], "model": "Ridge Regression", "contributors": [f"{_human_feature(str(x['feature']))}: {float(x['contribution']):+.2f}" for x in explanation], "points": points, "explanation": [{**x, "label": _human_feature(str(x["feature"]))} for x in explanation], "uncertainty_method": "Приближённый 95% интервал по остаткам хронологической validation", "synthetic": False, "baseline": round(float(bundle["intercept"]), 3), "comparison_value": rows[-1].waiting, "error_mae": metadata["metrics"].get("mae"), "model_version": metadata["model_version"], "explanation_summary": f"Прогноз {direction} среднего за 7 дней. Наибольший вклад: {factors or 'исторический профиль организации'}. Вклады отражают связь, а не причинность."}


def _latest_anomaly_z(values: list[int]) -> float:
    if len(values) < 15:
        return 0.0
    previous = values[-15:-1]
    std = statistics.pstdev(previous)
    return (values[-1] - statistics.mean(previous)) / std if std else 0.0


def _assessment(org_id: str, forecast_data: dict | None = None):
    rows, metadata = _grouped()[org_id], dataset()["metadata"]
    values = [row.waiting for row in rows]
    if forecast_data is None and len(values) >= 7:
        forecast_data = _forecast_payload(org_id, 1)
    current_week, previous_week = sum(values[-7:]), sum(values[-14:-7]) if len(values) >= 14 else sum(values[-7:])
    trend = (current_week - previous_week) / max(previous_week, 1) * 100
    return assess_risk(forecast=float(forecast_data["expected"]) if forecast_data else None, historical_mean=statistics.mean(values[-28:]), recent_trend_pct=trend, current_queue=int(metadata["organization_counts"].get(org_id, sum(values))), anomaly_z=_latest_anomaly_z(values), forecast_error=float(metadata["metrics"]["mae"]), model_version=str(metadata["model_version"]))


@lru_cache(maxsize=1)
def _batch_next_day_forecasts() -> dict[str, float]:
    features, ids = [], []
    for org_id, rows in _grouped().items():
        values = [row.waiting for row in rows]
        if len(values) < 7:
            continue
        next_day = rows[-1].day + timedelta(days=1)
        ids.append(org_id)
        features.append([org_id, next_day.weekday(), values[-1], values[-7], sum(values[-7:]) / 7])
    predictions = dataset()["bundle"]["pipeline"].predict(features)
    return {org_id: round(max(0.0, float(value)), 2) for org_id, value in zip(ids, predictions, strict=True)}


@lru_cache(maxsize=1)
def _organization_summaries() -> list[OrganizationSummary]:
    output, counts = [], dataset()["metadata"]["organization_counts"]
    predictions = _batch_next_day_forecasts()
    for org_id, rows in _grouped().items():
        value = predictions.get(org_id)
        prediction = {"expected": value} if value is not None else None
        risk = _assessment(org_id, prediction)
        output.append(OrganizationSummary(id=org_id, name=f"Медицинская организация {org_id}", region_id=rows[-1].region_id, latest_waiting=int(counts.get(org_id, sum(row.waiting for row in rows))), latest_daily_registrations=rows[-1].waiting, predicted_daily_registrations=value, risk=risk.risk_level, risk_score=risk.risk_score, risk_reasons=risk.reasons))
    return sorted(output, key=lambda item: (-item.risk_score, -item.latest_waiting, item.id))


_SUMMARY_LOCK = Lock()


def organization_summaries() -> list[OrganizationSummary]:
    with _SUMMARY_LOCK:
        return _organization_summaries()


organization_summaries.cache_clear = _organization_summaries.cache_clear


def paginate(items, page: int, page_size: int):
    total = len(items)
    return {"items": items[(page - 1) * page_size:page * page_size], "page": page, "page_size": page_size, "total": total, "total_pages": math.ceil(total / page_size) if total else 0}


@app.get("/api/v1/health")
def health():
    components = {"api": {"status": "online"}, "database": {"status": "available"}}
    try:
        state = dataset()
        components["dataset"] = {"status": "available", "detail": state["metadata"]["source_file"]}
        components["model"] = {"status": "ready", "detail": state["metadata"]["model_version"]}
        status = "ok"
    except ModelNotReady as error:
        components.update({"dataset": {"status": "available"}, "model": {"status": "not_ready", "detail": str(error)}})
        status = "degraded"
    except DataUnavailable as error:
        components.update({"dataset": {"status": "unavailable", "detail": str(error)}, "model": {"status": "not_ready"}})
        status = "degraded"
    return {"status": status, "components": components, "checked_at": datetime.now().astimezone().isoformat(timespec="seconds")}


@app.get("/api/v1/regions")
def regions():
    summaries, by_region = organization_summaries(), defaultdict(list)
    for item in summaries:
        by_region[item.region_id].append(item)
    anomaly_counts = Counter()
    for rows in _grouped().values():
        if _latest_anomaly_z([row.waiting for row in rows]) >= 2.5:
            anomaly_counts[rows[-1].region_id] += 1
    freshness = dataset()["metadata"]["date_range"][1]
    rank = {"normal": 0, "attention": 1, "high": 2, "critical": 3}
    return [{"id": code, "code": code, "name": region_name(code), "organization_count": len(by_region[code]), "current_waiting": sum(x.latest_waiting for x in by_region[code]), "predicted_daily_registrations": round(sum(x.predicted_daily_registrations or 0 for x in by_region[code]), 1), "high_risk_organizations": sum(x.risk in {"high", "critical"} for x in by_region[code]), "anomaly_count": anomaly_counts[code], "risk_level": max((x.risk for x in by_region[code]), key=rank.get, default="normal"), "data_freshness": freshness, "synthetic": False} for code in sorted(REGIONS)]


@app.get("/api/v1/regions/{region_id}/summary")
def region_summary(region_id: str):
    if region_id not in REGIONS:
        raise HTTPException(404, "Регион не найден в проверенном справочнике")
    rows = [item for item in organization_summaries() if item.region_id == region_id]
    return {"id": region_id, "name": region_name(region_id), "organizations": len(rows), "current_waiting": sum(x.latest_waiting for x in rows), "predicted_daily_registrations": round(sum(x.predicted_daily_registrations or 0 for x in rows), 1), "top_risk": rows[:10]}


@app.get("/api/v1/organizations")
def organizations(region_id: str | None = None, risk: str | None = None, search: str = "", sort: str = Query("risk", pattern="^(risk|name|waiting)$"), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    rows = [x for x in organization_summaries() if (not region_id or x.region_id == region_id) and (not risk or x.risk == risk) and search.lower() in (x.name + " " + region_name(x.region_id)).lower()]
    if sort == "name": rows.sort(key=lambda x: (x.name, x.id))
    elif sort == "waiting": rows.sort(key=lambda x: (-x.latest_waiting, x.id))
    else: rows.sort(key=lambda x: (-x.risk_score, -x.latest_waiting, x.id))
    return paginate(rows, page, page_size)


@app.get("/api/v1/organizations/{org_id}")
def organization(org_id: str):
    known(org_id)
    item = next(x for x in organization_summaries() if x.id == org_id)
    return {**item.model_dump(), "region_name": region_name(item.region_id), "risk_assessment": _assessment(org_id).model_dump()}


@app.get("/api/v1/organizations/{org_id}/history", response_model=list[PeriodObservation])
def history(org_id: str, days: int = Query(30, ge=7, le=84)):
    known(org_id)
    return [PeriodObservation(date=row.day, referrals=0, waiting=row.waiting, refusals=0, treated_cases=0) for row in _grouped()[org_id][-days:]]


@app.get("/api/v1/organizations/{org_id}/forecast", response_model=Forecast)
@app.get("/api/v1/forecasts/{org_id}", response_model=Forecast, deprecated=True)
def forecast(org_id: str, horizon_days: int = Query(7, ge=1, le=30)):
    return Forecast(**_forecast_payload(org_id, horizon_days))


@app.get("/api/v1/model/explain/{org_id}")
def explain(org_id: str):
    item = _forecast_payload(org_id, 1)
    risk = _assessment(org_id, item)
    return {"what": "Новые регистрации в очередь за сутки (не общий размер очереди)", "period": "Следующий календарный день", "prediction": item["expected"], "interval": [item["lower"], item["upper"]], "mae": item["error_mae"], "comparison": item["comparison_value"], "baseline": item["baseline"], "contributions": item["explanation"], "unusualness": round(_latest_anomaly_z([r.waiting for r in _grouped()[org_id]]), 2), "summary": item["explanation_summary"], "limitations": dataset()["metadata"]["limitations"], "human_checks": risk.recommended_checks or ["Сверить входные данные и локальный операционный контекст"], "model_version": item["model_version"]}


@app.get("/api/v1/model/info")
@app.get("/api/v1/metrics", deprecated=True)
def model_info():
    metadata = {k: v for k, v in dataset()["metadata"].items() if k not in {"region_counts", "organization_counts"}}
    grouped_importance: dict[str, float] = {}
    for item in metadata.get("global_importance", []):
        label = _human_feature(str(item["feature"]))
        grouped_importance[label] = max(grouped_importance.get(label, 0), float(item["importance"]))
    metadata["global_importance"] = [{"feature": name, "importance": value} for name, value in sorted(grouped_importance.items(), key=lambda x: -x[1])]
    return {"status": "trained", "synthetic": False, **metadata}


@app.get("/api/v1/anomalies")
def anomalies(organization_id: str | None = None, region_id: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    ids = [organization_id] if organization_id else [org for org, rows in _grouped().items() if not region_id or rows[-1].region_id == region_id]
    for org_id in ids: known(org_id)
    return paginate([a for org_id in ids for a in rolling_anomalies(org_id, history(org_id, 84))], page, page_size)


@app.get("/api/v1/risks")
def risks(region_id: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    items = [{"organization_id": x.id, "organization_name": x.name, "region_id": x.region_id, **_assessment(x.id).model_dump()} for x in organization_summaries() if not region_id or x.region_id == region_id]
    return paginate(items, page, page_size)


@app.get("/api/v1/summary")
def summary():
    rows = organization_summaries()
    return {"waiting": sum(x.latest_waiting for x in rows), "predicted_daily_registrations": round(sum(x.predicted_daily_registrations or 0 for x in rows), 1), "organizations": len(rows), "attention": sum(x.risk in {"high", "critical"} for x in rows), "anomalies": sum(_latest_anomaly_z([r.waiting for r in history_rows]) >= 2.5 for history_rows in _grouped().values()), "data_as_of": dataset()["metadata"]["date_range"][1]}


@app.get("/api/v1/data-sources")
def data_sources_endpoint():
    sources = provenance_sources(dataset()["metadata"])
    return {"health": data_health(sources), "items": sources}


@app.get("/api/v1/actions")
def actions(status: str | None = None): return {"items": storage.list_actions(status)}


@app.post("/api/v1/actions", status_code=201)
def action_create(payload: DecisionActionCreate, authorization: str | None = Header(None)):
    known(payload.organization_id)
    return storage.create_action(payload.model_dump(), _user(authorization))


@app.patch("/api/v1/actions/{action_id}")
def action_update(action_id: str, payload: DecisionActionStatus, authorization: str | None = Header(None)):
    item = storage.update_action(action_id, payload.status, _user(authorization))
    if not item: raise HTTPException(404, "Решение не найдено")
    return item


def _report_rows(payload: ReportCreate) -> list[dict]:
    rows = [x for x in organization_summaries() if (not payload.region_id or x.region_id == payload.region_id) and (not payload.organization_id or x.id == payload.organization_id) and (not payload.risk or x.risk == payload.risk)]
    return [{"organization": x.name, "region": region_name(x.region_id), "current_queue": x.latest_waiting, "daily_forecast": x.predicted_daily_registrations, "risk_level": x.risk, "risk_score": x.risk_score} for x in rows]


@app.post("/api/v1/reports", status_code=201)
def report_create(payload: ReportCreate, authorization: str | None = Header(None)):
    rows, metadata = _report_rows(payload), dataset()["metadata"]
    snapshot = {"waiting": sum(x["current_queue"] for x in rows), "high_risk": sum(x["risk_level"] in {"high", "critical"} for x in rows), "anomalies": summary()["anomalies"], "organizations": rows[:100], "model_version": metadata["model_version"], "data_freshness": metadata["date_range"][1], "actions_summary": f"Записей в журнале решений: {len(storage.list_actions())}"}
    return storage.create_report(checksum(snapshot), payload.model_dump(), snapshot, _user(authorization))


@app.get("/api/v1/reports/verify/{report_id}")
def report_verify(report_id: str, checksum_value: str | None = Query(None, alias="checksum")):
    report = storage.get_report(report_id)
    if not report: raise HTTPException(404, "Отчёт не найден")
    valid = report["checksum"] == checksum(report["snapshot"]) and (not checksum_value or checksum_value == report["checksum"])
    return {"report_id": report_id, "valid": valid, "verification": "DEMO verification — не является юридически значимой ЭЦП", "created_at": report["created_at"]}


@app.get("/api/v1/reports/{report_id}/export/{format}")
def report_export(report_id: str, format: str, authorization: str | None = Header(None)):
    report = storage.get_report(report_id)
    if not report: raise HTTPException(404, "Отчёт не найден")
    storage.audit(_user(authorization), "export", "ManagementReport", report_id, {"format": format})
    rows = report["snapshot"]["organizations"]
    if format == "csv": return Response(csv_export(rows), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{report_id}.csv"'})
    if format == "xlsx": return Response(xlsx_export(rows), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{report_id}.xlsx"'})
    if format == "print": return HTMLResponse(printable_html(report), headers={"Content-Disposition": "inline"})
    if format == "pdf":
        try:
            binary = pdf_export(report)
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error
        return Response(binary, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{report_id}.pdf"'})
    raise HTTPException(422, "Поддерживаются csv, xlsx и print/pdf")


@app.get("/api/v1/audit")
def audit_log(limit: int = Query(100, ge=1, le=500)): return {"items": storage.audit_rows(limit)}


@app.post("/api/v1/auth/login")
def login(payload: dict):
    result = auth.login_user(payload)
    storage.audit(result["email"], "login", "Session", None, {"demo": False})
    return result


@app.get("/api/v1/auth/me")
def me(authorization: str | None = Header(None)): return auth.session(authorization)


@app.post("/api/v1/auth/logout")
def logout(authorization: str | None = Header(None)):
    auth.revoke(authorization)
    return {"ok": True}


@app.post("/api/v1/auth/ecp-demo")
def ecp_demo():
    result = auth.issue("ecp-demo@local", demo=True)
    storage.audit(result["email"], "login", "Session", None, {"demo": True})
    return {**result, "notice": "ДЕМОНСТРАЦИОННЫЙ ВХОД: без сертификата, подписи и интеграции НУЦ РК"}
