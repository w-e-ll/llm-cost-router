"""Read-only projections from a completed live-run snapshot."""

import logging
import math
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from llm_cost_router.core.setup_logger import event

logger = logging.getLogger(__name__)


class PlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    run_id: UUID
    monthly_requests: int = Field(default=100000, ge=0, le=10000000, strict=True)
    days_per_month: int = Field(default=30, ge=1, le=31, strict=True)
    monthly_budget_usd: Decimal = Field(default=Decimal(100), ge=0, le=1000000)


class Attempt(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    model: str
    input_tokens: int = Field(ge=0, strict=True)
    output_tokens: int = Field(ge=0, strict=True)
    cached_tokens: int = Field(ge=0, strict=True)
    cost_usd: Decimal = Field(ge=0)


class Row(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    id: str
    policy: Literal["baseline", "routed"]
    complexity: Literal["simple", "complex"]
    correct: bool = Field(strict=True)
    error: str | None
    latency_ms: float = Field(ge=0)
    attempts: list[Attempt] = Field(min_length=1)


class SourceRun(BaseModel):
    mode: Literal["live"]
    status: Literal["completed"]
    run_id: UUID
    created_at: str
    dataset_version: str
    config: dict
    prices: dict
    price_date: str
    rows: list[Row]


def project_run(saved: dict, request: PlanningRequest) -> dict:
    run = SourceRun.model_validate(saved)
    if run.run_id != request.run_id:
        raise ValueError("Saved run ID does not match the requested ID.")
    count = run.config.get("task_count")
    if type(count) is not int or count <= 0:
        raise ValueError("Run has an invalid task count.")
    grouped = {p: [r for r in run.rows if r.policy == p] for p in ("baseline", "routed")}
    ids = [{r.id: r.complexity for r in grouped[p]} for p in grouped]
    if any(len(rows) != count for rows in grouped.values()) or any(len(i) != count for i in ids):
        raise ValueError("Both policies must have complete, unique task results.")
    if ids[0] != ids[1]:
        raise ValueError("Policies must use the same tasks and workload mix.")
    policies = []
    for policy, rows in grouped.items():
        calls = [a for row in rows for a in row.attempts]
        if any(a.cached_tokens > a.input_tokens for a in calls):
            raise ValueError("Cached tokens exceed input usage.")
        cost = sum((a.cost_usd for a in calls), Decimal(0))
        unit = cost / count
        month = unit * request.monthly_requests
        capacity = int(request.monthly_budget_usd // unit) if unit > 0 else None
        latencies = sorted(r.latency_ms for r in rows)
        policies.append(
            {
                "policy": policy,
                "models": sorted({a.model for a in calls}),
                "sample_tasks": count,
                "sample_calls": len(calls),
                "sample_cost_usd": str(cost),
                "sample_correct": sum(r.correct for r in rows),
                "sample_accuracy_percent": sum(r.correct for r in rows) / count * 100,
                "sample_errors": sum(r.error is not None for r in rows),
                "sample_fallbacks": len(calls) - count,
                "sample_p95_ms": latencies[math.ceil(0.95 * count) - 1],
                "avg_input_tokens": sum(a.input_tokens for a in calls) / count,
                "avg_output_tokens": sum(a.output_tokens for a in calls) / count,
                "avg_cached_tokens": sum(a.cached_tokens for a in calls) / count,
                "cost_per_request_usd": str(unit),
                "monthly_cost_usd": str(month),
                "daily_cost_usd": str(month / request.days_per_month),
                "within_budget": month <= request.monthly_budget_usd,
                "monthly_budget_gap_usd": str(max(Decimal(0), month - request.monthly_budget_usd)),
                "estimated_requests_affordable": capacity,
            }
        )
    baseline, routed = (Decimal(p["monthly_cost_usd"]) for p in policies)
    event(
        logger,
        "planning.projected",
        run_id=str(run.run_id),
        monthly_requests=request.monthly_requests,
        baseline_usd=str(baseline),
        routed_usd=str(routed),
        savings_usd=str(baseline - routed),
    )
    return {
        "source_run_id": str(run.run_id),
        "source_created_at": run.created_at,
        "dataset_version": run.dataset_version,
        "price_date": run.price_date,
        "source_prices": run.prices,
        "source_config": run.config,
        "simple_fraction": sum(r.complexity == "simple" for r in grouped["baseline"]) / count,
        "settings": request.model_dump(mode="json"),
        "policies": policies,
        "projected_savings_usd": str(baseline - routed),
        "projected_savings_percent": float((baseline - routed) / baseline * 100)
        if baseline
        else None,
        "assumptions": [
            "Projection scales the saved cost per task, including observed fallback attempts.",
            "Model selection, workload mix, token usage and cache hit pattern stay as observed.",
            "Costs use the source run's recorded rates, not today's model catalog.",
            "Accuracy, errors and p95 are historical sample measurements, not volume forecasts.",
            "Budget coverage uses average cost; it is not runtime admission or spending enforcement.",
            "A small sample cannot establish production cost, reliability or general model quality.",
            "Planning is read-only and makes no OpenAI calls.",
        ],
    }
