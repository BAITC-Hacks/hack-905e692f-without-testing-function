"""Processed-mart runtime and offline leakage-safe model training."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import joblib
import numpy as np
from app.core.data_pipeline import IngestionError, current_mart
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[4]
ARTIFACTS = ROOT / "ml" / "artifacts"
MODEL_VERSIONS = ARTIFACTS / "versions"
MODEL_CURRENT = ARTIFACTS / "current_model.json"
MODEL_PATH = ARTIFACTS / "queue_arrivals_ridge.joblib"  # local-tool compatibility
METADATA_PATH = ARTIFACTS / "queue_arrivals_metadata.json"
TARGET_NAME = "daily_waiting_registrations"
FEATURE_NAMES = [
    "organization_id", "day_of_week", "month", "is_weekend", "lag_1", "lag_7",
    "lag_14", "lag_28", "rolling_mean_7", "rolling_std_7", "trend_7",
    "historical_anomaly_z",
]


class DataUnavailable(RuntimeError):
    pass


class ModelNotReady(RuntimeError):
    pass


@dataclass(frozen=True)
class QueueRow:
    day: date
    organization_id: str
    region_id: str
    waiting: int
    current_waiting_snapshot: int = 0
    features: tuple[float, ...] = ()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_processed_rows() -> tuple[list[QueueRow], dict[str, object]]:
    try:
        mart, metadata = current_mart()
    except IngestionError as error:
        raise DataUnavailable(str(error)) from error
    rows: list[QueueRow] = []
    try:
        with mart.open(encoding="utf-8", newline="") as source:
            for record in csv.DictReader(source):
                numeric = tuple(float(record[name]) for name in FEATURE_NAMES[1:])
                if not all(math.isfinite(value) for value in numeric):
                    raise ValueError("non-finite feature")
                waiting = int(record[TARGET_NAME])
                snapshot = int(record["current_waiting_snapshot"])
                if waiting < 0 or snapshot < 0:
                    raise ValueError("negative count")
                rows.append(QueueRow(date.fromisoformat(record["date"]), record["organization_id"], record["region_id"], waiting, snapshot, numeric))
    except (OSError, ValueError, KeyError) as error:
        raise DataUnavailable(f"Processed data mart is invalid: {type(error).__name__}") from error
    if not rows:
        raise DataUnavailable("Processed data mart is empty")
    return rows, metadata


def _features(rows: list[QueueRow]) -> tuple[list[dict[str, object]], list[int], list[QueueRow]]:
    """Build past-only features; fallback path keeps small unit fixtures useful."""
    if rows and rows[0].features:
        seen: dict[str, int] = {}
        aligned = []
        for row in rows:
            count = seen.get(row.organization_id, 0)
            if count >= 28:
                aligned.append(row)
            seen[row.organization_id] = count + 1
        result = [dict(zip(FEATURE_NAMES, (row.organization_id, *row.features), strict=True)) for row in aligned]
        return result, [row.waiting for row in aligned], aligned
    histories: dict[str, list[int]] = {}
    result: list[dict[str, object]] = []
    targets: list[int] = []
    aligned: list[QueueRow] = []
    for row in sorted(rows, key=lambda item: (item.day, item.organization_id)):
        history = histories.setdefault(row.organization_id, [])
        if len(history) >= 7:
            trailing = history[-7:]
            mean = float(np.mean(trailing))
            result.append({
                "organization_id": row.organization_id, "day_of_week": row.day.weekday(),
                "month": row.day.month, "is_weekend": int(row.day.weekday() >= 5),
                "lag_1": history[-1], "lag_7": history[-7],
                "lag_14": history[-14] if len(history) >= 14 else 0,
                "lag_28": history[-28] if len(history) >= 28 else 0,
                "rolling_mean_7": mean, "rolling_std_7": float(np.std(trailing)),
                "trend_7": mean - float(np.mean(history[-14:-7] or trailing)),
                "historical_anomaly_z": 0.0,
            })
            targets.append(row.waiting)
            aligned.append(row)
        history.append(row.waiting)
    return result, targets, aligned


def _matrix(features: list[dict[str, object]]) -> list[list[object]]:
    return [[feature[name] for name in FEATURE_NAMES] for feature in features]


def inference_feature(organization_id: str, day: date, values: list[float]) -> list[object]:
    """Build a future row from observations/predictions strictly before ``day``."""
    trailing = values[-7:]
    mean = float(np.mean(trailing))
    earlier = values[-14:-7] or trailing
    anomaly_history = values[-28:]
    std = float(np.std(anomaly_history)) if len(anomaly_history) > 1 else 0.0
    anomaly = (values[-1] - float(np.mean(anomaly_history))) / std if std else 0.0
    return [
        organization_id, day.weekday(), day.month, int(day.weekday() >= 5), values[-1],
        values[-7], values[-14] if len(values) >= 14 else 0,
        values[-28] if len(values) >= 28 else 0, mean, float(np.std(trailing)),
        mean - float(np.mean(earlier)), anomaly,
    ]


def training_periods(rows: list[QueueRow], cutoff: str) -> dict[str, list[str]]:
    train = [row.day for row in rows if str(row.day) < cutoff]
    validation = [row.day for row in rows if str(row.day) >= cutoff]
    return {"train_period": [str(min(train)), str(max(train))] if train else [], "validation_period": [str(min(validation)), str(max(validation))] if validation else []}


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _candidate_model_dirs() -> list[Path]:
    pointer = _read_json(MODEL_CURRENT)
    candidates: list[Path] = []
    if pointer.get("relative_path"):
        candidates.append((ARTIFACTS / str(pointer["relative_path"])).resolve())
    if MODEL_VERSIONS.exists():
        candidates.extend(path.resolve() for path in sorted(MODEL_VERSIONS.iterdir(), reverse=True) if path.is_dir())
    return list(dict.fromkeys(candidates))


def _load_version(directory: Path, dataset_fingerprint: str) -> tuple[dict[str, object], dict[str, object]]:
    if ARTIFACTS.resolve() not in directory.parents:
        raise ModelNotReady("Invalid model pointer")
    model_path, metadata_path = directory / "model.joblib", directory / "metadata.json"
    metadata = _read_json(metadata_path)
    if metadata.get("status") != "validated" or metadata.get("dataset_fingerprint") != dataset_fingerprint:
        raise ModelNotReady("Model does not match the published data mart")
    if not model_path.is_file() or metadata.get("artifact_sha256") != _sha256(model_path):
        raise ModelNotReady("Model artifact checksum mismatch")
    try:
        bundle = joblib.load(model_path)
    except Exception as error:
        raise ModelNotReady("Model artifact cannot be loaded") from error
    required = {"pipeline", "feature_names", "coefficients", "intercept", "feature_schema"}
    if not isinstance(bundle, dict) or not required.issubset(bundle):
        raise ModelNotReady("Model artifact schema is incompatible")
    return bundle, metadata


def load_runtime() -> dict[str, object]:
    """Load compact processed data and a trusted local artifact; never touch data/raw."""
    rows, source = load_processed_rows()
    errors: list[str] = []
    for directory in _candidate_model_dirs():
        try:
            bundle, metadata = _load_version(directory, str(source["source_fingerprint"]))
            counts = {row.organization_id: row.current_waiting_snapshot for row in rows}
            return {"bundle": bundle, "metadata": {**metadata, **source, "organization_counts": counts}, "rows": rows}
        except ModelNotReady as error:
            errors.append(str(error))
    # Existing deployments can be upgraded before their first retrain.
    if MODEL_PATH.exists() and METADATA_PATH.exists():
        metadata = _read_json(METADATA_PATH)
        if metadata.get("source_fingerprint") == source.get("source_fingerprint"):
            try:
                return {"bundle": joblib.load(MODEL_PATH), "metadata": {**metadata, **source}, "rows": rows}
            except (OSError, ValueError, KeyError, EOFError) as error:
                errors.append(f"Legacy artifact rejected: {type(error).__name__}")
    raise ModelNotReady("No valid model is available" + (f": {errors[0]}" if errors else ""))


def train_or_load(force_train: bool = False) -> dict[str, object]:
    """Offline train/validate/publish. A failure never changes the production pointer."""
    if not force_train:
        try:
            return load_runtime()
        except (DataUnavailable, ModelNotReady):
            pass
    rows, source = load_processed_rows()
    features, targets, aligned = _features(rows)
    days = sorted({row.day for row in aligned})
    if len(days) < 20 or len(features) < 50:
        raise DataUnavailable("Insufficient history for chronological training")
    cutoff = days[max(1, math.floor(len(days) * 0.8))]
    train_idx = [index for index, row in enumerate(aligned) if row.day < cutoff]
    validation_idx = [index for index, row in enumerate(aligned) if row.day >= cutoff]
    if not train_idx or not validation_idx or max(aligned[i].day for i in train_idx) >= min(aligned[i].day for i in validation_idx):
        raise DataUnavailable("Chronological split invariant failed")
    x, y = _matrix(features), np.asarray(targets, dtype=np.float64)
    preprocess = ColumnTransformer([
        ("organization", OneHotEncoder(handle_unknown="ignore"), [0]),
        ("numeric", StandardScaler(), list(range(1, len(FEATURE_NAMES)))),
    ])
    pipeline = Pipeline([("preprocess", preprocess), ("model", Ridge(alpha=3.0, solver="lsqr"))])
    pipeline.fit([x[i] for i in train_idx], y[train_idx])
    predicted = np.clip(pipeline.predict([x[i] for i in validation_idx]), 0, None)
    actual = y[validation_idx]
    baseline = np.asarray([float(x[i][4]) for i in validation_idx])
    if not np.isfinite(predicted).all():
        raise DataUnavailable("Model produced non-finite validation predictions")
    metrics = {
        "mae": round(float(mean_absolute_error(actual, predicted)), 3),
        "rmse": round(float(mean_squared_error(actual, predicted) ** 0.5), 3),
        "wape": round(float(np.sum(np.abs(actual - predicted)) / max(float(np.sum(actual)), 1.0) * 100), 3),
        "baseline_mae": round(float(mean_absolute_error(actual, baseline)), 3),
        "train_rows": len(train_idx), "test_rows": len(validation_idx), "split_date": str(cutoff),
        "residual_std": float(np.std(actual - predicted, ddof=1) if len(actual) > 1 else 0),
    }
    names = list(pipeline.named_steps["preprocess"].get_feature_names_out())
    coefficients = pipeline.named_steps["model"].coef_.tolist()
    importance = sorted(({"feature": name, "importance": round(abs(float(coef)), 6)} for name, coef in zip(names, coefficients, strict=True)), key=lambda item: item["importance"], reverse=True)[:12]
    trained_at = datetime.now(UTC)
    version = f"ridge-{str(source['source_fingerprint'])[:12]}-{trained_at.strftime('%Y%m%dT%H%M%SZ')}"
    metadata: dict[str, object] = {
        "status": "validated", "model_version": version, "model_id": "queue-arrivals-ridge",
        "algorithm": "Ridge Regression", "trained_at": trained_at.isoformat(), "target": TARGET_NAME,
        "target_description": "Daily waiting-list registrations at organization/day level",
        "features": FEATURE_NAMES, "feature_schema_version": "features-v2", "metrics": metrics,
        "baseline_metrics": {"mae": metrics["baseline_mae"]},
        "dataset_fingerprint": source["source_fingerprint"], "dataset_version": source["dataset_version"],
        "validation_strategy": "chronological holdout; preprocessing fit only on train",
        **training_periods(aligned, str(cutoff)), "global_importance": importance,
        "limitations": "Snapshot-derived registrations are not a future total queue. Intervals are residual-based and approximate.",
    }
    bundle = {"pipeline": pipeline, "feature_names": names, "coefficients": coefficients, "intercept": float(pipeline.named_steps["model"].intercept_), "feature_schema": FEATURE_NAMES}
    MODEL_VERSIONS.mkdir(parents=True, exist_ok=True)
    temporary = MODEL_VERSIONS / f".{version}.{os.getpid()}.tmp"
    final = MODEL_VERSIONS / version
    temporary.mkdir()
    try:
        model_file = temporary / "model.joblib"
        joblib.dump(bundle, model_file)
        metadata["artifact_sha256"] = _sha256(model_file)
        _atomic_json(temporary / "metadata.json", metadata)
        _load_version(temporary.resolve(), str(source["source_fingerprint"]))
        os.replace(temporary, final)
        _atomic_json(MODEL_CURRENT, {"model_version": version, "relative_path": f"versions/{version}", "published_at": datetime.now(UTC).isoformat()})
        joblib.dump(bundle, MODEL_PATH)
        _atomic_json(METADATA_PATH, {**metadata, "source_fingerprint": source["source_fingerprint"]})
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return {"bundle": bundle, "metadata": {**metadata, **source}, "rows": rows}


def local_contributors(bundle: dict[str, object], feature_row: list[object]) -> list[dict[str, object]]:
    pipeline: Pipeline = bundle["pipeline"]  # type: ignore[assignment]
    transformed = pipeline.named_steps["preprocess"].transform([feature_row])
    transformed = (transformed.toarray() if hasattr(transformed, "toarray") else transformed)[0]
    entries = []
    for name, value, coefficient in zip(bundle["feature_names"], transformed, bundle["coefficients"], strict=True):
        contribution = float(value * coefficient)
        if abs(contribution) > 0.0001:
            entries.append({"feature": str(name).replace("organization__organization_id_", "organization=").replace("numeric__", ""), "direction": "increase" if contribution >= 0 else "decrease", "contribution": round(contribution, 3)})
    return sorted(entries, key=lambda item: abs(float(item["contribution"])), reverse=True)[:10]
