"""Small local audit/action/report store. Secrets are never written to these tables."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime

from app.core import auth


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    auth.DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(auth.DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS decision_actions (
          id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, action_type TEXT NOT NULL,
          status TEXT NOT NULL, reason TEXT NOT NULL, model_version TEXT NOT NULL,
          forecast_snapshot TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, user TEXT NOT NULL,
          action TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT, details TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reports (
          id TEXT PRIMARY KEY, checksum TEXT NOT NULL, filters TEXT NOT NULL,
          snapshot TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL
        );
        """
    )
    return db


def audit(user: str, action: str, entity: str, entity_id: str | None, details: dict | None = None):
    with connect() as db:
        db.execute(
            "INSERT INTO audit_log(timestamp,user,action,entity,entity_id,details) VALUES(?,?,?,?,?,?)",
            (_now(), user, action, entity, entity_id, json.dumps(details or {}, ensure_ascii=False)),
        )


def create_action(payload: dict, user: str) -> dict:
    item = {
        "id": str(uuid.uuid4()),
        "organization_id": payload["organization_id"],
        "action_type": payload["action_type"],
        "status": "draft",
        "reason": payload.get("reason", ""),
        "model_version": payload["model_version"],
        "forecast_snapshot": payload.get("forecast_snapshot", {}),
        "created_at": _now(),
        "created_by": user,
    }
    with connect() as db:
        db.execute(
            "INSERT INTO decision_actions VALUES(?,?,?,?,?,?,?,?,?)",
            (*[item[k] for k in ("id", "organization_id", "action_type", "status", "reason", "model_version")], json.dumps(item["forecast_snapshot"], ensure_ascii=False), item["created_at"], item["created_by"]),
        )
    audit(user, "create", "DecisionAction", item["id"], {"action_type": item["action_type"]})
    return item


def list_actions(status: str | None = None) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM decision_actions" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC",
            (status,) if status else (),
        ).fetchall()
    return [{**dict(row), "forecast_snapshot": json.loads(row["forecast_snapshot"])} for row in rows]


def update_action(action_id: str, status: str, user: str) -> dict | None:
    with connect() as db:
        db.execute("UPDATE decision_actions SET status=? WHERE id=?", (status, action_id))
        row = db.execute("SELECT * FROM decision_actions WHERE id=?", (action_id,)).fetchone()
    if row:
        audit(user, "status_change", "DecisionAction", action_id, {"status": status})
        return {**dict(row), "forecast_snapshot": json.loads(row["forecast_snapshot"])}
    return None


def create_report(checksum: str, filters: dict, snapshot: dict, user: str) -> dict:
    report_id, created_at = f"RPT-{uuid.uuid4().hex[:12].upper()}", _now()
    with connect() as db:
        db.execute(
            "INSERT INTO reports VALUES(?,?,?,?,?,?)",
            (report_id, checksum, json.dumps(filters, ensure_ascii=False), json.dumps(snapshot, ensure_ascii=False), created_at, user),
        )
    audit(user, "create", "ManagementReport", report_id, {"checksum": checksum})
    return {"id": report_id, "checksum": checksum, "filters": filters, "snapshot": snapshot, "created_at": created_at, "created_by": user}


def get_report(report_id: str) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    return ({**dict(row), "filters": json.loads(row["filters"]), "snapshot": json.loads(row["snapshot"])} if row else None)


def audit_rows(limit: int = 100) -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(row), "details": json.loads(row["details"])} for row in rows]
