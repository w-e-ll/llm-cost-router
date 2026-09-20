from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = Annotated[Decimal, Field(ge=0, le=1_000_000, allow_inf_nan=False)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ModelProfile(StrictModel):
    name: str = Field(min_length=1, max_length=80, pattern=r".*\S.*")
    input_usd_per_million: Money
    output_usd_per_million: Money
    latency_ms: float = Field(gt=0, le=600_000)
    failure_rate: float = Field(default=0, ge=0, le=1)


def demo_models() -> list[ModelProfile]:
    """Fictional profiles, not provider prices or measured performance."""
    return [
        ModelProfile(
            name="Small",
            input_usd_per_million="0.20",
            output_usd_per_million="0.80",
            latency_ms=400,
            failure_rate=0.02,
        ),
        ModelProfile(
            name="Medium",
            input_usd_per_million="1.00",
            output_usd_per_million="4.00",
            latency_ms=1000,
            failure_rate=0.01,
        ),
        ModelProfile(
            name="Frontier",
            input_usd_per_million="3.00",
            output_usd_per_million="12.00",
            latency_ms=2200,
            failure_rate=0.01,
        ),
    ]


class Scenario(StrictModel):
    models: list[ModelProfile] = Field(default_factory=demo_models, min_length=1, max_length=10)
    baseline_model: str = "Frontier"
    simple_model: str = "Small"
    complex_model: str = "Frontier"
    fallback_model: str = "Medium"
    fallback_enabled: bool = True
    input_tokens: int = Field(default=1000, ge=0, le=1_000_000, strict=True)
    output_tokens: int = Field(default=300, ge=0, le=1_000_000, strict=True)
    requests_per_month: int = Field(default=100_000, ge=0, le=200_000, strict=True)
    days_per_month: int = Field(default=30, ge=1, le=31, strict=True)
    simple_fraction: float = Field(default=0.8, ge=0, le=1)
    sla_ms: float = Field(default=3000, gt=0, le=1_200_000)
    monthly_budget_usd: Money = Decimal(500)
    seed: int = Field(default=42, ge=0, le=2**32 - 1, strict=True)

    @model_validator(mode="after")
    def check_references(self):
        names = [m.name for m in self.models]
        if len(names) != len(set(names)):
            raise ValueError("Model names must be unique")
        for field in ("baseline_model", "simple_model", "complex_model", "fallback_model"):
            if getattr(self, field) not in names:
                raise ValueError(f"{field} must reference an existing model")
        return self


class PolicyResult(StrictModel):
    policy: Literal["baseline", "routed"]
    budget_enforced: bool
    requested: int
    attempted: int
    completed: int
    failed: int
    rejected_budget: int
    fallback_attempts: int
    sla_violations: int
    simulated_p95_ms: float | None
    monthly_cost_usd: Decimal
    daily_cost_usd: Decimal
    cost_per_requested_usd: Decimal
    cost_per_completed_usd: Decimal | None
    model_calls: dict[str, int]


class Comparison(StrictModel):
    mode: Literal["simulation"] = "simulation"
    scenario: Scenario
    baseline: PolicyResult
    routed: PolicyResult
    budgeted_baseline: PolicyResult
    budgeted_routed: PolicyResult
    projected_savings_usd: Decimal
    projected_savings_percent: float | None
    assumptions: list[str]
