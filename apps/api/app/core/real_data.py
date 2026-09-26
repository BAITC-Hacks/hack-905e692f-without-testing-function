"""Real-source queue-arrival modelling; never falls back to synthetic data."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

ROOT = Path(__file__).resolve().parents[4]
RAW, ARTIFACTS = ROOT / "data/raw", ROOT / "ml/artifacts"
MODEL_PATH, METADATA_PATH = (
    ARTIFACTS / "queue_arrivals_ridge.joblib",
    ARTIFACTS / "queue_arrivals_metadata.json",
)
TARGET_NAME = "daily_waiting_registrations"


class DataUnavailable(RuntimeError):
    pass


class ModelNotReady(RuntimeError):
    """The dataset may be available, but no compatible trusted model artifact exists."""



@dataclass
class QueueRow:
    day: date
    organization_id: str
    region_id: str
    waiting: int


def _waiting_file() -> Path:
    candidates = [p for p in RAW.glob("*.csv") if "Ожидающие плановую" in p.name]
    if not candidates:
        raise DataUnavailable("No real waiting-list extract found in data/raw.")
    return candidates[0]


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    return hashlib.sha256(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[
        :16
    ]


def _parse_day(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def load_real_rows() -> tuple[list[QueueRow], dict[str, object]]:
    path, counts, regions, invalid_dates = _waiting_file(), Counter(), defaultdict(Counter), 0
    region_counts, organization_counts = Counter(), Counter()
    row_limit = int(os.getenv("MEDFLOW_MAX_RAW_ROWS", "0"))
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {"mo_destination_code", "registration_dt", "region_origin_code"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise DataUnavailable(
                f"Waiting-list schema is incompatible; required columns: {sorted(required)}"
            )
        for scanned, record in enumerate(reader, start=1):
            if row_limit and scanned > row_limit:
                break
            region_counts[(record.get("region_origin_code") or "unknown").strip()] += 1
            organization_counts[(record.get("mo_destination_code") or "").strip()] += 1
            org, day = (
                (record.get("mo_destination_code") or "").strip(),
                _parse_day(record.get("registration_dt")),
            )
            if not org or day is None:
                invalid_dates += 1
                continue
            counts[day, org] += 1
            regions[org][(record.get("region_origin_code") or "unknown").strip() or "unknown"] += 1
    if not counts:
        raise DataUnavailable(
            "Waiting-list extract has no valid destination-code/registration-date rows."
        )
    by_org: dict[str, list[date]] = defaultdict(list)
    for day, org in counts:
        by_org[org].append(day)
    rows: list[QueueRow] = []
    for org, days in by_org.items():
        cursor, end, region = min(days), max(days), regions[org].most_common(1)[0][0]
        while cursor <= end:
            rows.append(QueueRow(cursor, org, region, counts[cursor, org]))
            cursor += timedelta(days=1)
    rows.sort(key=lambda row: (row.day, row.organization_id))
    return rows, {
        "region_counts": dict(region_counts),
        "organization_counts": dict(organization_counts),
        "source_file": path.name,
        "source_fingerprint": _fingerprint(path),
        "source_row_limit": row_limit or None,
        "invalid_or_missing_dates": invalid_dates,
        "raw_records_used": sum(counts.values()),
        "organizations": len(by_org),
        "date_range": [str(min(d for d, _ in counts)), str(max(d for d, _ in counts))],
    }


def _features(rows: list[QueueRow]) -> tuple[list[dict[str, object]], list[int], list[QueueRow]]:
    histories: dict[str, list[int]] = defaultdict(list)
    result, targets, aligned = [], [], []
    for row in rows:
        history = histories[row.organization_id]
        if len(history) >= 7:
            result.append(
                {
                    "organization_id": row.organization_id,
                    "dow": row.day.weekday(),
                    "lag_1": history[-1],
                    "lag_7": history[-7],
                    "mean_7": float(np.mean(history[-7:])),
                }
            )
            targets.append(row.waiting)
            aligned.append(row)
        history.append(row.waiting)
    return result, targets, aligned


def _matrix(features: list[dict[str, object]]) -> list[list[object]]:
    return [[f["organization_id"], f["dow"], f["lag_1"], f["lag_7"], f["mean_7"]] for f in features]


def training_periods(rows, cutoff):
    seen = Counter()
    periods = {"train_period": [], "validation_period": []}
    for row in rows:
        if seen[row.organization_id] >= 7:
            key = "train_period" if str(row.day) < cutoff else "validation_period"
            values = periods[key]
            day = str(row.day)
            if not values:
                values.extend([day, day])
            else:
                values[0], values[1] = min(values[0], day), max(values[1], day)
        seen[row.organization_id] += 1
    return periods


def _load_aggregates() -> tuple[list[QueueRow], dict[str, object]]:
    path = _waiting_file()
    fingerprint = _fingerprint(path)
    row_limit = int(os.getenv("MEDFLOW_MAX_RAW_ROWS", "0"))
    cache_path = ARTIFACTS / "queue_arrivals_aggregates.joblib"
    cached = joblib.load(cache_path) if cache_path.exists() else None
    if (
        cached
        and cached["fingerprint"] == fingerprint
        and cached["row_limit"] == row_limit
        and "region_counts" in cached["source"]
    ):
        rows, source = cached["rows"], cached["source"]
    else:
        rows, source = load_real_rows()
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(f".{os.getpid()}.tmp")
        joblib.dump(
            {"fingerprint": fingerprint, "row_limit": row_limit, "rows": rows, "source": source},
            temporary,
        )
        temporary.replace(cache_path)
    return rows, source


def load_runtime() -> dict[str, object]:
    """Load persisted aggregates and model only; HTTP requests never train a model."""
    rows, source = _load_aggregates()
    fingerprint = source["source_fingerprint"]
    if not MODEL_PATH.exists() or not METADATA_PATH.exists():
        raise ModelNotReady("Model artifact or metadata is missing. Run the training command.")
    try:
        metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        bundle = joblib.load(MODEL_PATH)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise ModelNotReady(f"Model artifact cannot be loaded: {type(error).__name__}") from error
    if metadata.get("source_fingerprint") != fingerprint:
        raise ModelNotReady("Model artifact does not match the current dataset fingerprint.")
    if metadata.get("source_row_limit") != source["source_row_limit"]:
        raise ModelNotReady("Model artifact was trained with a different row-limit configuration.")
    required = {"pipeline", "feature_names", "coefficients", "intercept"}
    if not required.issubset(bundle):
        raise ModelNotReady("Model artifact has an incompatible schema.")
    metrics = metadata.setdefault("metrics", {})
    metadata.setdefault("model_id", "queue-arrivals-ridge")
    metadata.setdefault("algorithm", "Ridge Regression")
    metadata.setdefault("dataset_fingerprint", fingerprint)
    metadata.setdefault("model_version", f"ridge-{fingerprint}")
    metadata.setdefault("baseline_metrics", {"mae": metrics.get("baseline_mae")})
    metadata.setdefault("validation_strategy", "chronological holdout (last 20% of dates)")
    return {
        "bundle": bundle,
        "metadata": {**metadata, **source, **training_periods(rows, metrics["split_date"])},
        "rows": rows,
    }


def train_or_load(force_train: bool = False) -> dict[str, object]:
    """Explicit training entrypoint used by CLI/tests, never implicitly by request handlers."""
    rows, source = _load_aggregates()
    fingerprint = source["source_fingerprint"]
    if not force_train:
        try:
            return load_runtime()
        except ModelNotReady:
            pass
    features, targets, aligned = _features(rows)
    distinct_days = sorted({row.day for row in aligned})
    if len(distinct_days) < 20 or len(features) < 50:
        raise DataUnavailable(
            "Insufficient dated history for chronological training (need >=20 dates and >=50 rows)."
        )
    cutoff = distinct_days[max(1, math.floor(len(distinct_days) * 0.8))]
    train_idx = [i for i, row in enumerate(aligned) if row.day < cutoff]
    test_idx = [i for i, row in enumerate(aligned) if row.day >= cutoff]
    if not train_idx or not test_idx:
        raise DataUnavailable("Chronological split produced an empty train or test partition.")
    x, y = _matrix(features), np.asarray(targets, dtype=float)
    preprocess = ColumnTransformer(
        [
            ("organization", OneHotEncoder(handle_unknown="ignore"), [0]),
            ("numeric", "passthrough", [1, 2, 3, 4]),
        ]
    )
    pipeline = Pipeline([("preprocess", preprocess), ("model", Ridge(alpha=3.0, solver="lsqr"))])
    pipeline.fit([x[i] for i in train_idx], y[train_idx])
    predicted, actual = np.clip(pipeline.predict([x[i] for i in test_idx]), 0, None), y[test_idx]
    baseline = np.asarray([float(x[i][2]) for i in test_idx])
    metrics = {
        "mae": round(float(mean_absolute_error(actual, predicted)), 3),
        "rmse": round(float(mean_squared_error(actual, predicted) ** 0.5), 3),
        "wape": round(float(np.sum(np.abs(actual - predicted)) / max(np.sum(actual), 1) * 100), 3),
        "baseline_mae": round(float(mean_absolute_error(actual, baseline)), 3),
        "test_rows": len(test_idx),
        "train_rows": len(train_idx),
        "split_date": str(cutoff),
        "residual_std": float(np.std(actual - predicted, ddof=1) if len(actual) > 1 else 0),
    }
    names, coefficients = (
        list(pipeline.named_steps["preprocess"].get_feature_names_out()),
        pipeline.named_steps["model"].coef_.tolist(),
    )
    importance = sorted(
        (
            {
                "feature": name.replace("organization__organization_id_", "organization=").replace(
                    "numeric__", ""
                ),
                "importance": round(abs(float(coef)), 4),
            }
            for name, coef in zip(names, coefficients)
        ),
        key=lambda item: item["importance"],
        reverse=True,
    )[:12]
    metadata = {
        **source,
        **training_periods(rows, str(cutoff)),
        "model_version": f"ridge-{fingerprint}",
        "model_id": "queue-arrivals-ridge",
        "algorithm": "Ridge Regression",
        "trained_at": datetime.now().astimezone().isoformat(),
        "target": TARGET_NAME,
        "target_description": "Daily count of records registered in the supplied waiting-list snapshot, grouped by mo_destination_code.",
        "features": ["organization_id", "dow", "lag_1", "lag_7", "mean_7"],
        "metrics": metrics,
        "baseline_metrics": {"mae": metrics["baseline_mae"]},
        "dataset_fingerprint": fingerprint,
        "validation_strategy": "chronological holdout (last 20% of dates)",
        "global_importance": importance,
        "limitations": "The source is one waiting-list snapshot, not daily historical backlog snapshots. The model forecasts daily new waiting-list registrations, not future total queue size. Origin region is used only for grouping and is not a destination-organization region.",
    }
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    bundle = {
        "pipeline": pipeline,
        "feature_names": names,
        "coefficients": coefficients,
        "intercept": float(pipeline.named_steps["model"].intercept_),
    }
    joblib.dump(bundle, MODEL_PATH)
    METADATA_PATH.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"bundle": bundle, "metadata": metadata, "rows": rows}


def local_contributors(
    bundle: dict[str, object], feature_row: list[object]
) -> list[dict[str, object]]:
    pipeline: Pipeline = bundle["pipeline"]  # type: ignore[assignment]
    transformed = pipeline.named_steps["preprocess"].transform([feature_row])
    transformed = (transformed.toarray() if hasattr(transformed, "toarray") else transformed)[0]
    entries = []
    for name, value, coef in zip(bundle["feature_names"], transformed, bundle["coefficients"]):
        contribution = float(value * coef)
        if abs(contribution) > 0.0001:
            entries.append(
                {
                    "feature": name.replace(
                        "organization__organization_id_", "organization="
                    ).replace("numeric__", ""),
                    "direction": "increase" if contribution >= 0 else "decrease",
                    "contribution": round(contribution, 3),
                }
            )
    return sorted(entries, key=lambda item: abs(float(item["contribution"])), reverse=True)[:10]
