from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class OrganizationSummary(BaseModel):
    id: str
    name: str
    region_id: str
    latest_waiting: int
    risk: Literal["low", "medium", "high", "critical"]


class PeriodObservation(BaseModel):
    date: date
    referrals: int = Field(ge=0)
    waiting: int = Field(ge=0)
    refusals: int = Field(ge=0)
    treated_cases: int = Field(ge=0)


class Forecast(BaseModel):
    organization_id: str
    target: Literal["waiting"] = "waiting"
    generated_at: date
    horizon_days: int
    expected: float
    lower: float
    upper: float
    model: str
    contributors: list[str]
    synthetic: bool


class Anomaly(BaseModel):
    organization_id: str
    metric: str
    date: date
    observed: float
    expected: float
    z_score: float
    severity: Literal["medium", "high"]
    explanation: str
