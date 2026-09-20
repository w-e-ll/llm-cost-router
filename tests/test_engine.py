from decimal import Decimal

import pytest
from pydantic import ValidationError

from llm_cost_router.core.engine import compare, request_cost, simulate
from llm_cost_router.core.schemas import Scenario


def scenario(**changes):
    s = Scenario(requests_per_month=100, **changes)
    for model in s.models:
        model.failure_rate = 0
    return s


def test_known_token_cost():
    s = scenario()
    assert request_cost(s.models[0], 1000, 300) == Decimal("0.00044")


def test_hand_calculated_routing_savings():
    result = compare(scenario())
    assert result.baseline.monthly_cost_usd == Decimal("0.66")
    assert result.routed.monthly_cost_usd == Decimal("0.1672")
    assert result.projected_savings_usd == Decimal("0.4928")
    assert result.routed.daily_cost_usd * 30 == result.routed.monthly_cost_usd
    assert result.routed.model_calls == {"Small": 80, "Medium": 0, "Frontier": 20}


def test_fallback_charges_both_attempts_and_adds_latency():
    s = scenario(simple_fraction=1)
    s.models[0].failure_rate = 1
    r = simulate(s, "routed")
    assert r.completed == r.fallback_attempts == 100
    assert r.monthly_cost_usd == Decimal("0.264")
    assert r.simulated_p95_ms == 1400


def test_fallback_can_fail():
    s = scenario(simple_fraction=1)
    s.models[0].failure_rate = s.models[1].failure_rate = 1
    r = simulate(s, "routed")
    assert r.failed == 100
    assert r.completed == 0
    assert r.cost_per_completed_usd is None


@pytest.mark.parametrize("fallback_enabled,fallback_model", [(False, "Medium"), (True, "Small")])
def test_no_retry_when_disabled_or_same_model(fallback_enabled, fallback_model):
    s = scenario(
        simple_fraction=1, fallback_enabled=fallback_enabled, fallback_model=fallback_model
    )
    s.models[0].failure_rate = 1
    r = simulate(s, "routed")
    assert r.failed == 100
    assert r.fallback_attempts == 0
    assert r.monthly_cost_usd == Decimal("0.044")


def test_exact_budget_boundary_and_rejections():
    s = scenario(simple_fraction=1, fallback_enabled=False, monthly_budget_usd="0.0044")
    r = simulate(s, "routed", enforce_budget=True)
    assert r.completed == 10
    assert r.rejected_budget == 90
    assert r.monthly_cost_usd == s.monthly_budget_usd


def test_budget_reserves_possible_fallback_before_admission():
    s = scenario(simple_fraction=1, monthly_budget_usd="0.00044")
    r = simulate(s, "routed", enforce_budget=True)
    assert r.attempted == 0
    assert r.monthly_cost_usd == 0


def test_zero_requests_and_zero_budget():
    empty = compare(Scenario(requests_per_month=0))
    assert empty.projected_savings_percent is None
    assert empty.routed.simulated_p95_ms is None
    r = compare(scenario(monthly_budget_usd="0"))
    assert r.budgeted_routed.rejected_budget == 100
    assert r.projected_savings_usd > 0  # Still based on unrestricted comparison.


def test_seed_reproducibility_and_identical_policy_parity():
    s = Scenario(requests_per_month=400, simple_model="Frontier")
    a, b = compare(s), compare(s)
    assert a == b
    assert a.baseline.monthly_cost_usd == a.routed.monthly_cost_usd
    assert a.baseline.failed == a.routed.failed
    assert a.projected_savings_usd == 0


def test_sla_boundary_and_p95():
    s = scenario(simple_fraction=1, sla_ms=400)
    assert simulate(s, "routed").sla_violations == 0
    s.sla_ms = 399
    assert simulate(s, "routed").sla_violations == 100


@pytest.mark.parametrize("seed", range(5))
def test_budget_and_accounting_invariants(seed):
    r = compare(Scenario(requests_per_month=1000, monthly_budget_usd="0.25", seed=seed))
    for result in (r.budgeted_baseline, r.budgeted_routed):
        assert result.monthly_cost_usd <= Decimal("0.25")
        assert result.requested == result.completed + result.failed + result.rejected_budget
        assert sum(result.model_calls.values()) == result.attempted + result.fallback_attempts


@pytest.mark.parametrize(
    "changes",
    [
        {"input_tokens": -1},
        {"requests_per_month": 200001},
        {"simple_fraction": 1.1},
        {"baseline_model": "missing"},
        {"monthly_budget_usd": "NaN"},
        {"sla_ms": float("inf")},
        {"requests_per_month": 1.5},
        {"unknown": 1},
    ],
)
def test_invalid_scenario(changes):
    with pytest.raises(ValidationError):
        Scenario(**changes)
