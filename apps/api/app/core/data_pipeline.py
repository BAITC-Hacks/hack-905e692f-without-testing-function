"""Offline, deterministic raw -> normalized -> analytical mart ingestion.

This module is deliberately not imported by HTTP request handlers.  The raw extract is a
snapshot, so a changed source is rebuilt offline and published by swapping one small pointer.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import statistics
import tempfile
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RAW = ROOT / "data" / "raw"
INTERIM = ROOT / "data" / "interim"
PROCESSED = ROOT / "data" / "processed"
VERSIONS = PROCESSED / "mart_versions"
CURRENT = PROCESSED / "current_mart.json"
CHECKPOINT = INTERIM / "ingestion_state.json"
SCHEMA_VERSION = "waiting-mart-v2"
MAX_COUNT = 9_223_372_036_854_775_807
MART_FIELDS = [
    "organization_id", "region_id", "date", "daily_waiting_registrations",
    "current_waiting_snapshot", "lag_1", "lag_7", "lag_14", "lag_28",
    "rolling_mean_7", "rolling_std_7", "trend_7", "day_of_week", "month",
    "is_weekend", "historical_anomaly_z",
]
PUBLIC_FIELDS = frozenset(MART_FIELDS)
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class IngestionError(RuntimeError):
    pass


def waiting_source() -> Path:
    matches = sorted(RAW.glob("*Ожидающие плановую*.csv"))
    if not matches:
        raise IngestionError("Waiting-list source is absent")
    if len(matches) != 1:
        raise IngestionError("Expected exactly one waiting-list snapshot")
    return matches[0]


def _stat_identity(path: Path) -> str:
    stat = path.stat()
    return f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _parse_day(value: str | None) -> date | None:
    try:
        parsed = date.fromisoformat((value or "")[:10])
    except ValueError:
        return None
    today = datetime.now(UTC).date()
    return parsed if date(2000, 1, 1) <= parsed <= today + timedelta(days=2) else None


def _validate_mart(path: Path) -> tuple[int, date, date]:
    rows = 0
    minimum: date | None = None
    maximum: date | None = None
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != MART_FIELDS:
            raise IngestionError("Published mart schema is incompatible")
        for record in reader:
            rows += 1
            day = date.fromisoformat(record["date"])
            minimum = day if minimum is None else min(minimum, day)
            maximum = day if maximum is None else max(maximum, day)
            for field in ("daily_waiting_registrations", "current_waiting_snapshot"):
                value = int(record[field])
                if value < 0 or value > MAX_COUNT:
                    raise IngestionError(f"Unsafe count in {field}")
            for field in MART_FIELDS[5:]:
                value = float(record[field])
                if not math.isfinite(value):
                    raise IngestionError(f"Non-finite feature in {field}")
    if not rows or minimum is None or maximum is None:
        raise IngestionError("Generated mart is empty")
    return rows, minimum, maximum


def _feature_row(org: str, region: str, day: date, count: int, snapshot: int, history: list[int]) -> dict[str, object]:
    def lag(period: int) -> int:
        return history[-period] if len(history) >= period else 0

    trailing = history[-7:]
    mean = statistics.fmean(trailing) if trailing else 0.0
    std = statistics.pstdev(trailing) if len(trailing) > 1 else 0.0
    earlier = history[-14:-7]
    prior_mean = statistics.fmean(earlier) if earlier else mean
    anomaly_history = history[-28:]
    anomaly_std = statistics.pstdev(anomaly_history) if len(anomaly_history) > 1 else 0.0
    anomaly = (count - statistics.fmean(anomaly_history)) / anomaly_std if anomaly_std else 0.0
    return {
        "organization_id": org,
        "region_id": region,
        "date": day.isoformat(),
        "daily_waiting_registrations": count,
        "current_waiting_snapshot": snapshot,
        "lag_1": lag(1), "lag_7": lag(7), "lag_14": lag(14), "lag_28": lag(28),
        "rolling_mean_7": round(mean, 8),
        "rolling_std_7": round(std, 8),
        "trend_7": round(mean - prior_mean, 8),
        "day_of_week": day.weekday(), "month": day.month,
        "is_weekend": int(day.weekday() >= 5),
        "historical_anomaly_z": round(anomaly, 8),
    }


def ingest(force: bool = False) -> dict[str, object]:
    """Rebuild a snapshot source offline and atomically publish a validated version."""
    source = waiting_source()
    identity = _stat_identity(source)
    checkpoint = _read_json(CHECKPOINT, {})
    if not force and isinstance(checkpoint, dict) and checkpoint.get("stat_identity") == identity and checkpoint.get("status") == "success" and CURRENT.exists():
        return {**checkpoint, "skipped": True}

    started = datetime.now(UTC)
    INTERIM.mkdir(parents=True, exist_ok=True)
    VERSIONS.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ingest-", dir=INTERIM))
    database = work / "events.sqlite3"
    normalized = work / "waiting_normalized.csv.gz"
    scanned = invalid = duplicates = 0
    try:
        db = sqlite3.connect(database)
        db.execute("PRAGMA journal_mode=OFF")
        db.execute("PRAGMA synchronous=OFF")
        db.execute("CREATE TABLE events(org TEXT NOT NULL, region TEXT NOT NULL, day TEXT NOT NULL, event_key TEXT NOT NULL UNIQUE)")
        batch: list[tuple[str, str, str, str]] = []

        def flush_batch() -> None:
            nonlocal duplicates
            if not batch:
                return
            before = db.total_changes
            db.executemany("INSERT OR IGNORE INTO events VALUES(?,?,?,?)", batch)
            duplicates += len(batch) - (db.total_changes - before)
            batch.clear()

        with source.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"mo_destination_code", "registration_dt", "region_origin_code"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise IngestionError(f"Missing required columns: {sorted(required)}")
            for scanned, record in enumerate(reader, 1):
                org = (record.get("mo_destination_code") or "").strip()
                region = (record.get("region_origin_code") or "unknown").strip() or "unknown"
                day = _parse_day(record.get("registration_dt"))
                if not _SAFE_ID.fullmatch(org) or len(region) > 64 or day is None:
                    invalid += 1
                    continue
                patient = (record.get("patient_seq_no") or "").strip()
                key_material = "\x1f".join((org, region, record.get("registration_dt") or "", patient))
                event_key = hashlib.sha256(key_material.encode()).hexdigest()
                batch.append((org, region, day.isoformat(), event_key))
                if len(batch) >= 10_000:
                    flush_batch()
                    db.commit()
        flush_batch()
        db.commit()
        valid_rows = int(db.execute("SELECT count(*) FROM events").fetchone()[0])
        if valid_rows == 0:
            raise IngestionError("Source contains no valid records")

        with gzip.open(normalized, "wt", encoding="utf-8", newline="") as target:
            writer = csv.writer(target)
            writer.writerow(("organization_id", "region_id", "event_date", "source_id"))
            for row in db.execute("SELECT org,region,day,? FROM events ORDER BY day,org", (source.name,)):
                writer.writerow(row)

        daily: dict[str, dict[date, int]] = defaultdict(dict)
        for org, day_text, count in db.execute("SELECT org,day,count(*) FROM events GROUP BY org,day ORDER BY org,day"):
            daily[org][date.fromisoformat(day_text)] = int(count)
        snapshots = {org: int(count) for org, count in db.execute("SELECT org,count(*) FROM events GROUP BY org")}
        regions = {org: region for org, region in db.execute("SELECT org,region FROM (SELECT org,region,count(*) n,row_number() OVER(PARTITION BY org ORDER BY count(*) DESC,region) r FROM events GROUP BY org,region) WHERE r=1")}
        db.close()

        source_hash = _sha256(source)
        version = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{source_hash[:12]}"
        staged = work / version
        staged.mkdir()
        mart = staged / "daily_organization_mart.csv"
        with mart.open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=MART_FIELDS)
            writer.writeheader()
            for org in sorted(daily):
                first, last = min(daily[org]), max(daily[org])
                history: list[int] = []
                cursor = first
                while cursor <= last:
                    count = daily[org].get(cursor, 0)
                    writer.writerow(_feature_row(org, regions[org], cursor, count, snapshots[org], history))
                    history.append(count)
                    cursor += timedelta(days=1)

        mart_rows, minimum, maximum = _validate_mart(mart)
        metadata = {
            "schema_version": SCHEMA_VERSION, "dataset_version": version,
            "created_at": started.isoformat(), "source_id": source.name,
            "source_file": source.name,
            "source_fingerprint": source_hash, "stat_identity": identity,
            "rows_processed": valid_rows, "rows_scanned": scanned,
            "invalid_rows": invalid, "duplicate_rows": duplicates,
            "mart_rows": mart_rows, "organizations": len(daily),
            "date_range": [minimum.isoformat(), maximum.isoformat()],
            "max_event_date": maximum.isoformat(), "status": "success",
            "source_provenance": {"kind": "immutable_snapshot", "filename": source.name},
            "public_field_allowlist": sorted(PUBLIC_FIELDS),
        }
        _atomic_json(staged / "metadata.json", metadata)
        published = VERSIONS / version
        os.replace(staged, published)
        interim_target = INTERIM / f"waiting_normalized-{version}.csv.gz"
        os.replace(normalized, interim_target)
        pointer = {"dataset_version": version, "relative_path": f"mart_versions/{version}", "published_at": datetime.now(UTC).isoformat()}
        _atomic_json(CURRENT, pointer)
        result = {**metadata, "last_processed_at": pointer["published_at"], "skipped": False}
        _atomic_json(CHECKPOINT, result)
        return result
    except Exception as error:
        failure = {"source_id": source.name, "stat_identity": identity, "last_processed_at": datetime.now(UTC).isoformat(), "status": "failed", "schema_version": SCHEMA_VERSION, "error": type(error).__name__}
        _atomic_json(CHECKPOINT, failure)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def current_mart() -> tuple[Path, dict[str, object]]:
    pointer = _read_json(CURRENT, {})
    if not isinstance(pointer, dict) or not pointer.get("relative_path"):
        raise IngestionError("No validated processed data mart is published; run ingest")
    directory = (PROCESSED / str(pointer["relative_path"])).resolve()
    if PROCESSED.resolve() not in directory.parents:
        raise IngestionError("Invalid data mart pointer")
    metadata = _read_json(directory / "metadata.json", {})
    mart = directory / "daily_organization_mart.csv"
    if not isinstance(metadata, dict) or metadata.get("status") != "success" or not mart.is_file():
        raise IngestionError("Published data mart is invalid")
    return mart, metadata
