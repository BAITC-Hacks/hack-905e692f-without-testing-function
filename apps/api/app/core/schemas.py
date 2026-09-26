from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class OrganizationSummary(BaseModel):
    id: str
    name: str
    region_id: str
    latest_waiting: int
    latest_daily_registrations: int | None = None
    risk: Literal["normal", "attention", "high", "critical", "low", "medium"]
    predicted_daily_registrations: float | None = None
    risk_score: int = 0
    risk_reasons: list[str] = []


class PeriodObservation(BaseModel):
    date: date
    referrals: int = Field(ge=0)
    waiting: int = Field(ge=0)
    refusals: int = Field(ge=0)
    treated_cases: int = Field(ge=0)


class Forecast(BaseModel):
    organization_id: str
    target: str
    generated_at: date
    horizon_days: int
    expected: float
    lower: float
    upper: float
    model: str
    contributors: list[str]
    synthetic: bool = False
    points: list[dict[str, float | str]] = []
    explanation: list[dict[str, float | str]] = []
    uncertainty_method: str | None = None
    baseline: float | None = None
    comparison_value: float | None = None
    error_mae: float | None = None
    model_version: str | None = None
    explanation_summary: str | None = None


class Anomaly(BaseModel):
    organization_id: str
    metric: str
    date: date
    observed: float
    expected: float
    z_score: float
    severity: Literal["medium", "high"]
    explanation: str


class DecisionActionCreate(BaseModel):
    organization_id: str = Field(min_length=1, max_length=64)
    action_type: Literal["prescription_draft", "flow_redirect", "notify_chief", "add_control", "report"]
    reason: str = Field(default="", max_length=2000)
    model_version: str = Field(min_length=1, max_length=128)
    forecast_snapshot: dict[str, object] = {}


class DecisionActionStatus(BaseModel):
    status: Literal["draft", "approved", "rejected", "completed"]


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False


class HealthComponent(BaseModel):
    status: Literal["online", "available", "ready", "degraded", "unavailable", "not_ready"]
    detail: str | None = None


class ReportCreate(BaseModel):
    region_id: str | None = None
    organization_id: str | None = None
    risk: str | None = None
    period_days: int = Field(default=30, ge=1, le=365)
