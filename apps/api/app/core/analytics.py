from __future__ import annotations

import statistics
from datetime import date

from app.core.schemas import Anomaly, Forecast, PeriodObservation


def persistence_forecast(org_id: str, history: list[PeriodObservation], horizon_days: int = 7) -> Forecast:
    """Leakage-safe baseline: only the final observed target and historical residual spread."""
    values = [r.waiting for r in history]
    expected = float(values[-1])
    residuals = [values[i] - values[i - 1] for i in range(1, len(values))]
    spread = max(1.0, statistics.pstdev(residuals) * (horizon_days ** 0.5))
    trend = values[-1] - values[-8] if len(values) >= 8 else 0
    contributors = [f"waiting_7d_trend: {trend:+.0f}", f"last_observed_waiting: {values[-1]}"]
    return Forecast(organization_id=org_id, generated_at=date.today(), horizon_days=horizon_days,
                    expected=round(expected, 1), lower=round(max(0, expected - 1.96 * spread), 1),
                    upper=round(expected + 1.96 * spread, 1), model="naive_persistence_baseline",
                    contributors=contributors, synthetic=True)


def rolling_anomalies(org_id: str, history: list[PeriodObservation], window: int = 14) -> list[Anomaly]:
    output: list[Anomaly] = []
    values = [row.waiting for row in history]
    for index in range(window, len(values)):
        previous = values[index - window:index]
        mean, std = statistics.mean(previous), statistics.pstdev(previous)
        if std == 0:
            continue
        z = (values[index] - mean) / std
        if z >= 2.5:
            severity = "high" if z >= 3.5 else "medium"
            output.append(Anomaly(organization_id=org_id, metric="waiting", date=history[index].date,
                                  observed=values[index], expected=round(mean, 1), z_score=round(z, 2), severity=severity,
                                  explanation="Observed waiting count exceeds its trailing 14-period statistical reference; this is an analytical signal for review."))
    return output
