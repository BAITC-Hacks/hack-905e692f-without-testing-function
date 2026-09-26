from datetime import date, timedelta

import joblib
import pytest
from app.core import auth, storage
from app.core.provenance import data_health, data_sources
from app.core.real_data import MODEL_PATH, QueueRow, _features, load_runtime, local_contributors
from app.core.reporting import checksum, csv_export, xlsx_export
from app.core.risk import assess_risk


def test_model_artifact_loads_with_required_metadata():
    runtime = load_runtime()
    assert {"pipeline", "feature_names", "coefficients", "intercept"} <= runtime["bundle"].keys()
    assert runtime["metadata"]["target"] == "daily_waiting_registrations"
    assert runtime["metadata"]["validation_period"][0] >= runtime["metadata"]["train_period"][1]


def test_missing_model_is_structured(monkeypatch, tmp_path):
    import app.core.real_data as module

    monkeypatch.setattr(module, "MODEL_PATH", tmp_path / "missing.joblib")
    monkeypatch.setattr(module, "METADATA_PATH", tmp_path / "missing.json")
    with pytest.raises(module.ModelNotReady):
        module.load_runtime()


def test_features_never_use_current_target():
    rows = [QueueRow(date(2025, 1, 1) + timedelta(days=i), "A", "71", i + 1) for i in range(8)]
    features, targets, aligned = _features(rows)
    assert targets == [8] and aligned[0].waiting == 8
    assert features[0]["lag_1"] == 7 and features[0]["lag_7"] == 1


def test_risk_is_rule_based_not_organization_based():
    risk = assess_risk(forecast=30, historical_mean=10, recent_trend_pct=60, current_queue=2000, anomaly_z=4, forecast_error=25, model_version="v1")
    assert risk.risk_level == "critical" and risk.risk_score >= 70
    assert risk.model_version == "v1" and len(risk.reasons) >= 3


def test_data_provenance_is_honest():
    sources = data_sources({"source_file": "Ожидающие плановую госпитализацию в стационары.csv", "raw_records_used": 765182, "trained_at": "2026-01-01", "date_range": ["2025-01-01", "2025-03-31"]})
    assert any("Пакетная выгрузка / real-time integration не подключена" in x["warnings"] for x in sources)
    assert data_health(sources)["real_time_integration"] is False


def test_decision_action_and_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB", tmp_path / "state.sqlite3")
    item = storage.create_action({"organization_id": "A", "action_type": "add_control", "reason": "check", "model_version": "v1", "forecast_snapshot": {"expected": 3}}, "admin")
    assert item["status"] == "draft"
    changed = storage.update_action(item["id"], "approved", "admin")
    assert changed["status"] == "approved" and storage.audit_rows()[0]["action"] == "status_change"


def test_report_exports_are_real_files():
    rows = [{"organization": "МО A", "region": "Астана", "current_queue": 5, "daily_forecast": 2.5, "risk_level": "normal", "risk_score": 0}]
    assert csv_export(rows).startswith(b"\xef\xbb\xbf")
    binary = xlsx_export(rows)
    assert binary.startswith(b"PK") and len(binary) > 1000
    assert checksum({"x": 1}) == checksum({"x": 1})


def test_ridge_local_contributions_reconstruct_prediction():
    bundle = joblib.load(MODEL_PATH)
    row = ["22ND", 1, 10, 8, 9.0]
    transformed = bundle["pipeline"].named_steps["preprocess"].transform([row])
    transformed = transformed.toarray()[0] if hasattr(transformed, "toarray") else transformed[0]
    reconstructed = bundle["intercept"] + sum(float(v) * float(c) for v, c in zip(transformed, bundle["coefficients"], strict=True))
    assert reconstructed == pytest.approx(float(bundle["pipeline"].predict([row])[0]))
    assert all("contribution" in item for item in local_contributors(bundle, row))
