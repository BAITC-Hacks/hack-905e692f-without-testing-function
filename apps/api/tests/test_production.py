import sqlite3
from datetime import date, timedelta

import pytest
from app.core import auth
from app.core.real_data import QueueRow
from app.core.regions import region_name
from app.main import (
    _grouped,
    dataset,
    login,
    logout,
    organization_summaries,
    organizations,
    register,
)
from fastapi import HTTPException


def test_persistent_auth_and_revocation(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB", tmp_path / "auth.sqlite3")
    payload = {"email": "Analyst@example.org", "password": "secure-password-12", "name": "Аналитик"}
    result = register(payload)
    with sqlite3.connect(auth.DB) as connection:
        row = connection.execute("SELECT email,password_hash,name,created_at FROM users").fetchone()
    assert row[0] == "analyst@example.org" and row[2] == "Аналитик" and row[3]
    assert row[1].startswith("scrypt$") and payload["password"] not in row[1]
    with pytest.raises(HTTPException) as duplicate:
        register(payload)
    assert duplicate.value.status_code == 409
    logout("Bearer " + result["access_token"])
    with pytest.raises(HTTPException) as expired:
        auth.session("Bearer " + result["access_token"])
    assert expired.value.status_code == 401
    logged_in = login(payload)
    assert not auth.session("Bearer " + logged_in["access_token"])["demo"]
    with pytest.raises(HTTPException) as bad_password:
        login({**payload, "password": "wrong-password"})
    assert bad_password.value.status_code == 401
    with pytest.raises(HTTPException) as invalid:
        register({**payload, "email": "invalid"})
    assert invalid.value.status_code == 400


def test_region_names():
    assert region_name(" 71 ") == "Астана"
    assert region_name("59") == "Северо-Казахстанская область"
    assert region_name("999") == "Неизвестный регион (код 999)"


def test_filter_sort_before_page(monkeypatch):
    rows = [
        QueueRow(date(2025, 1, 1) + timedelta(days=day), f"{org:03}", "71" if org % 2 else "59", 2)
        for org in range(120)
        for day in range(8)
    ]
    monkeypatch.setattr(
        "app.main.dataset",
        lambda: {
            "rows": rows,
            "metadata": {"organization_counts": {f"{org:03}": 9000 for org in range(120)}},
        },
    )
    _grouped.cache_clear()
    organization_summaries.cache_clear()
    try:
        first = organizations(
            region_id="71", risk=None, search="", sort="name", page=1, page_size=25
        )
        second = organizations(
            region_id="71", risk=None, search="", sort="name", page=2, page_size=25
        )
        assert first["total"] == 60 and first["total_pages"] == 3
        assert len(first["items"]) == 25 and first["items"][-1].id < second["items"][0].id
        assert all(r.region_id == "71" for r in first["items"])
        assert first["items"][0].latest_waiting == 9000
        assert first["items"][0].latest_daily_registrations == 2
        assert first["items"][0].risk == "low"
    finally:
        _grouped.cache_clear()
        organization_summaries.cache_clear()
        dataset.cache_clear()
