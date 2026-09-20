"""Deterministic, in-memory simulation; no model calls or persistence."""

import logging
import math
import random
from decimal import Decimal
from typing import Literal

from llm_cost_router.core.schemas import Comparison, ModelProfile, PolicyResult, Scenario
from llm_cost_router.core.setup_logger import event

logger = logging.getLogger(__name__)

ASSUMPTIONS = [
    "All model profiles are user-supplied assumptions; defaults are fictional.",
    "Same seeded workload and per-model failure draws are used for both policies.",
    "Task complexity is supplied, not inferred. Quality is not evaluated.",
    "Input/output token counts are constant across tasks and model attempts.",
    "Each attempt, including a failed attempt, is charged full token cost (conservative).",
    "One fallback on failure only; same-model fallback is skipped; no retries.",
    "Latency is fixed per model. Fallback latency is added to primary latency.",
    "p95 uses nearest rank over attempted requests, including failures; rejects are excluded.",
    "SLA violations count attempted requests with latency strictly above the SLA.",
    "Budget admission reserves primary plus possible fallback, then releases unused reserve.",
    "Savings compare unrestricted policies; budget rejections are not counted as savings.",
    "Daily cost is monthly cost divided by the chosen days; no infrastructure/router charges.",
]


def request_cost(model: ModelProfile, input_tokens: int, output_tokens: int) -> Decimal:
    return (
        model.input_usd_per_million * input_tokens + model.output_usd_per_million * output_tokens
    ) / Decimal(1_000_000)


def simulate(
    scenario: Scenario, policy: Literal["baseline", "routed"], *, enforce_budget: bool = False
) -> PolicyResult:
    rng = random.Random(scenario.seed)
    models = {m.name: m for m in scenario.models}
    costs = {
        m.name: request_cost(m, scenario.input_tokens, scenario.output_tokens)
        for m in scenario.models
    }
    # Exact proportion up to one request, shuffled to avoid simple-first budget bias.
    n_simple = round(scenario.requests_per_month * scenario.simple_fraction)
    workload = [True] * n_simple + [False] * (scenario.requests_per_month - n_simple)
    rng.shuffle(workload)
    spent = Decimal(0)
    completed = failed = rejected = fallback_attempts = sla_violations = 0
    latencies = []
    calls = dict.fromkeys(models, 0)
    for simple in workload:
        # Draw even for rejected requests so budget admission cannot alter future outcomes.
        failures = {name: rng.random() < model.failure_rate for name, model in models.items()}
        primary = (
            scenario.baseline_model
            if policy == "baseline"
            else (scenario.simple_model if simple else scenario.complex_model)
        )
        fallback = scenario.fallback_model
        can_fallback = scenario.fallback_enabled and fallback != primary
        reserve = costs[primary] + (costs[fallback] if can_fallback else Decimal(0))
        if enforce_budget and spent + reserve > scenario.monthly_budget_usd:
            rejected += 1
            continue
        spent += costs[primary]
        calls[primary] += 1
        latency = models[primary].latency_ms
        success = not failures[primary]
        if not success and can_fallback:
            fallback_attempts += 1
            spent += costs[fallback]
            calls[fallback] += 1
            latency += models[fallback].latency_ms
            success = not failures[fallback]
        completed += int(success)
        failed += int(not success)
        sla_violations += int(latency > scenario.sla_ms)
        latencies.append(latency)
    latencies.sort()
    n = scenario.requests_per_month
    return PolicyResult(
        policy=policy,
        budget_enforced=enforce_budget,
        requested=n,
        attempted=len(latencies),
        completed=completed,
        failed=failed,
        rejected_budget=rejected,
        fallback_attempts=fallback_attempts,
        sla_violations=sla_violations,
        simulated_p95_ms=latencies[math.ceil(0.95 * len(latencies)) - 1] if latencies else None,
        monthly_cost_usd=spent,
        daily_cost_usd=spent / scenario.days_per_month,
        cost_per_requested_usd=spent / n if n else Decimal(0),
        cost_per_completed_usd=spent / completed if completed else None,
        model_calls=calls,
    )


def compare(scenario: Scenario) -> Comparison:
    baseline = simulate(scenario, "baseline")
    routed = simulate(scenario, "routed")
    savings = baseline.monthly_cost_usd - routed.monthly_cost_usd
    event(
        logger,
        "simulation.compared",
        requests=scenario.requests_per_month,
        savings_usd=str(savings),
        mode="simulation",
    )
    return Comparison(
        scenario=scenario,
        baseline=baseline,
        routed=routed,
        budgeted_baseline=simulate(scenario, "baseline", enforce_budget=True),
        budgeted_routed=simulate(scenario, "routed", enforce_budget=True),
        projected_savings_usd=savings,
        projected_savings_percent=float(savings / baseline.monthly_cost_usd * 100)
        if baseline.monthly_cost_usd
        else None,
        assumptions=ASSUMPTIONS,
    )
