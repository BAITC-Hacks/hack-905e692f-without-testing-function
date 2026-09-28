import csv
import json
import shutil
from pathlib import Path

import pytest
from app.core import auth
from app.core import data_pipeline as pipeline
from app.core.real_data import load_runtime
from app.core.reporting import csv_export
from app.core.schemas import DecisionActionCreate, ReportCreate
from app.main import action_create, app, health, report_create
from fastapi import HTTPException


def _source(path: Path, days: int = 30) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("region_origin_code", "mo_destination_code", "patient_seq_no", "registration_dt"))
        for day in range(1, days + 1):
            writer.writerow(("71", "ORG-1", str(day), f"2025-01-{day:02} 10:00:00"))


def test_incremental_ingestion_and_atomic_failure(tmp_path, monkeypatch):
    raw, interim, processed = tmp_path / "raw", tmp_path / "interim", tmp_path / "processed"
    raw.mkdir()
    source = raw / "Ожидающие плановую госпитализацию в стационары.csv"
    _source(source, 30)
    monkeypatch.setattr(pipeline, "RAW", raw)
    monkeypatch.setattr(pipeline, "INTERIM", interim)
    monkeypatch.setattr(pipeline, "PROCESSED", processed)
    monkeypatch.setattr(pipeline, "VERSIONS", processed / "mart_versions")
    monkeypatch.setattr(pipeline, "CURRENT", processed / "current_mart.json")
    monkeypatch.setattr(pipeline, "CHECKPOINT", interim / "ingestion_state.json")
    first = pipeline.ingest()
    pointer = pipeline.CURRENT.read_bytes()
    assert first["rows_processed"] == 30 and not first["skipped"]
    assert pipeline.ingest()["skipped"] is True
    source.write_text("bad,schema\n1,2\n", encoding="utf-8")
    with pytest.raises(pipeline.IngestionError):
        pipeline.ingest(force=True)
    assert pipeline.CURRENT.read_bytes() == pointer


def test_runtime_never_opens_raw(monkeypatch):
    original = Path.open

    def guarded(path, *args, **kwargs):
        if "data/raw" in str(path):
            raise AssertionError("HTTP runtime attempted to read raw data")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    assert load_runtime()["metadata"]["model_version"]


def test_preprocessor_fit_count_is_train_only():
    runtime = load_runtime()
    scaler = runtime["bundle"]["pipeline"].named_steps["preprocess"].named_transformers_["numeric"]
    assert int(scaler.n_samples_seen_) == runtime["metadata"]["metrics"]["train_rows"]


def test_corrupted_current_model_falls_back(tmp_path, monkeypatch):
    import app.core.real_data as models

    runtime = load_runtime()
    source_dir = models.ARTIFACTS / models._read_json(models.MODEL_CURRENT)["relative_path"]
    versions = tmp_path / "versions"
    good = versions / "001-good"
    bad = versions / "999-bad"
    shutil.copytree(source_dir, good)
    shutil.copytree(source_dir, bad)
    (bad / "model.joblib").write_bytes(b"corrupt")
    current = tmp_path / "current_model.json"
    current.write_text(json.dumps({"relative_path": "versions/999-bad"}), encoding="utf-8")
    monkeypatch.setattr(models, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(models, "MODEL_VERSIONS", versions)
    monkeypatch.setattr(models, "MODEL_CURRENT", current)
    monkeypatch.setattr(models, "MODEL_PATH", tmp_path / "legacy.joblib")
    monkeypatch.setattr(models, "METADATA_PATH", tmp_path / "legacy.json")
    recovered = models.load_runtime()
    assert recovered["metadata"]["model_version"] == runtime["metadata"]["model_version"]


def test_csv_formula_injection_is_neutralized():
    row = {"organization": "=HYPERLINK(\"bad\")", "region": "+cmd", "current_queue": 1, "daily_forecast": 2, "risk_level": "normal", "risk_score": 0}
    payload = csv_export([row]).decode("utf-8-sig")
    assert "'=HYPERLINK" in payload and "'+cmd" in payload


def test_rbac_and_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB", tmp_path / "state.sqlite3")
    viewer = auth.issue("viewer@example.org", role="viewer")
    with pytest.raises(HTTPException) as denied:
        auth.require_role("Bearer " + viewer["access_token"], {"analyst", "admin"})
    assert denied.value.status_code == 403
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.org")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", auth.password_hash("correct-password", "00" * 16))
    for _ in range(5):
        with pytest.raises(HTTPException):
            auth.login_user({"email": "admin@example.org", "password": "wrong-pass"}, "203.0.113.1")
    with pytest.raises(HTTPException) as limited:
        auth.login_user({"email": "admin@example.org", "password": "correct-password"}, "203.0.113.1")
    assert limited.value.status_code == 429


def test_action_report_rbac_health_and_cors(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB", tmp_path / "state.sqlite3")
    viewer = auth.issue("viewer@example.org", role="viewer")
    authorization = "Bearer " + viewer["access_token"]
    with pytest.raises(HTTPException) as action_denied:
        action_create(DecisionActionCreate(organization_id="x", action_type="add_control", model_version="v1"), authorization)
    with pytest.raises(HTTPException) as report_denied:
        report_create(ReportCreate(), authorization)
    assert action_denied.value.status_code == 403 and report_denied.value.status_code == 403
    assert "processed_data" in health()["components"]
    cors = next(item for item in app.user_middleware if item.cls.__name__ == "CORSMiddleware")
    assert "http://localhost:3000" in cors.kwargs["allow_origins"]
