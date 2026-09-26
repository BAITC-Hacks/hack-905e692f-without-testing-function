"""Transparent operational risk assessment, separate from the ML prediction."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
CONFIG_PATH = ROOT / "config/risk_thresholds.yaml"


@dataclass(frozen=True)
class RiskAssessment:
    risk_level: str
    risk_score: int
    reasons: list[str]
    recommended_checks: list[str]
    model_version: str

    def model_dump(self) -> dict[str, object]:
        return asdict(self)


def load_thresholds(path: Path = CONFIG_PATH) -> dict[str, float]:
    values = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(key): float(value) for key, value in values.items()}


def assess_risk(
    *,
    forecast: float | None,
    historical_mean: float,
    recent_trend_pct: float,
    current_queue: int,
    anomaly_z: float,
    forecast_error: float,
    model_version: str,
    thresholds: dict[str, float] | None = None,
) -> RiskAssessment:
    cfg = thresholds or load_thresholds()
    score, reasons, checks = 0, [], []
    if forecast is not None:
        ratio = forecast / max(historical_mean, 1.0)
        if ratio >= cfg["forecast_ratio_critical"]:
            score += 35
            reasons.append("Прогноз дневных регистраций существенно выше исторического среднего")
        elif ratio >= cfg["forecast_ratio_high"]:
            score += 25
            reasons.append("Прогноз дневных регистраций выше исторического среднего")
        elif ratio >= cfg["forecast_ratio_attention"]:
            score += 12
            reasons.append("Ожидается умеренный рост дневных регистраций")

    if recent_trend_pct >= cfg["trend_critical_pct"]:
        score += 25
        reasons.append("Выраженный рост регистраций за последнюю неделю")
    elif recent_trend_pct >= cfg["trend_high_pct"]:
        score += 16
        reasons.append("Регистрации растут относительно предыдущей недели")

    if anomaly_z >= cfg["anomaly_critical_z"]:
        score += 25
        reasons.append("Текущая динамика статистически крайне необычна")
    elif anomaly_z >= cfg["anomaly_high_z"]:
        score += 15
        reasons.append("Обнаружено статистически необычное значение")

    if current_queue >= cfg["queue_critical"]:
        score += 20
        reasons.append("Большой текущий снимок очереди")
    elif current_queue >= cfg["queue_high"]:
        score += 10
        reasons.append("Повышенный текущий снимок очереди")

    if forecast_error >= cfg["uncertainty_high"]:
        score += 5
        reasons.append("Высокая неопределённость прогноза требует осторожной интерпретации")

    score = min(score, 100)
    level = "critical" if score >= 70 else "high" if score >= 45 else "attention" if score >= 20 else "normal"
    if score >= 20:
        checks.extend(
            [
                "Сверить сигнал с первичными данными медицинской организации",
                "Проверить доступную мощность и изменения маршрутизации пациентов",
            ]
        )
    if anomaly_z >= cfg["anomaly_high_z"]:
        checks.append("Проверить полноту и своевременность загрузки данных")
    if not reasons:
        reasons.append("Значимых отклонений по настроенным правилам не обнаружено")
    return RiskAssessment(level, score, reasons, checks, model_version)
