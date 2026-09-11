from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health() -> None:
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_forecast_schema_has_interval() -> None:
    data = client.get("/api/v1/forecasts/org-almaty-1").json()
    assert data["lower"] <= data["expected"] <= data["upper"]


def test_anomaly_is_explainable() -> None:
    data = client.get("/api/v1/anomalies").json()
    assert data and data[0]["explanation"]
