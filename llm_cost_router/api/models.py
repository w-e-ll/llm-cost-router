"""Validated API response envelopes."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(extra="allow", validate_assignment=True)


class HealthResponse(BaseModel):
    status: str = Field(min_length=1)
    mode: str | None = None
    storage: str | None = None


class LiveConfigResponse(APIModel):
    key_configured: bool
    models: dict[str, dict[str, str]]
    price_date: str
    defaults: dict[str, Any]
    dataset: list[dict[str, Any]]


class EstimateResponse(APIModel):
    reserved_usd: str
    max_calls: int = Field(ge=0)
    within_budget: bool
    input_allowance_per_call: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)
    price_date: str


class RunSummaryResponse(BaseModel):
    run_id: UUID
    status: str
    created_at: str


class RunPayloadResponse(APIModel):
    run_id: UUID
    status: str
    mode: str | None = None


class PlanPayloadResponse(APIModel):
    plan_id: UUID
    source_run_id: UUID
