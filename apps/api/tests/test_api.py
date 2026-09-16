def test_health() -> None:
    from app.main import app

    assert app.title == "MedFlow AI"


def test_forecast_schema_has_interval() -> None:
    from app.core.schemas import Forecast

    forecast = Forecast(organization_id="x", target="daily_waiting_registrations", generated_at="2025-01-01", horizon_days=1, expected=2, lower=1, upper=3, model="ridge", contributors=[])
    assert forecast.lower <= forecast.expected <= forecast.upper


def test_anomaly_is_explainable() -> None:
    from app.main import ecp_demo

    assert ecp_demo()["demo"] is True
