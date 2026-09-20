"""Small real-model experiment. Prices are a dated snapshot, not a billing ledger."""

import json
import logging
import math
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from llm_cost_router.core.setup_logger import event

logger = logging.getLogger(__name__)

ModelId = Literal["gpt-4.1-nano", "gpt-4.1-mini"]
PRICES = {
    "gpt-4.1-nano": {"input": "0.10", "cached_input": "0.025", "output": "0.40"},
    "gpt-4.1-mini": {"input": "0.40", "cached_input": "0.10", "output": "1.60"},
}
PRICE_DATE = "2026-09-17"
MAX_OUTPUT = 128
# Large conservative allowance for the fixed, short prompts and JSON schema.
INPUT_ALLOWANCE = 4096
DATASET = [
    {
        "id": "sentiment-1",
        "complexity": "simple",
        "prompt": "Classify sentiment as positive, negative, or neutral: I love this product.",
        "expected": "positive",
    },
    {
        "id": "invoice-1",
        "complexity": "complex",
        "prompt": "An invoice has 3 items at 19 dollars each and 2 at 7 dollars each. Apply a 10 percent discount to the subtotal, then add 5 dollars shipping. Return the total with two decimals and no currency symbol.",
        "expected": "68.90",
    },
    {
        "id": "extract-1",
        "complexity": "simple",
        "prompt": "Extract the order ID only: Please refund order ORD-2048, placed yesterday.",
        "expected": "ORD-2048",
    },
    {
        "id": "inventory-1",
        "complexity": "complex",
        "prompt": "A store begins with 120 units, sells 37, receives 24, then reserves 15 of the remaining units. How many unreserved units remain? Return an integer only.",
        "expected": "92",
    },
    {
        "id": "sentiment-2",
        "complexity": "simple",
        "prompt": "Classify sentiment as positive, negative, or neutral: The package arrived on Tuesday.",
        "expected": "neutral",
    },
    {
        "id": "policy-1",
        "complexity": "complex",
        "prompt": "Policy: approve a refund only if purchase age is at most 30 days AND the item is unopened, unless defective items qualify regardless of age or opening. A 45-day-old opened item is defective. Return approve or reject.",
        "expected": "approve",
    },
    {
        "id": "extract-2",
        "complexity": "simple",
        "prompt": "Extract the email address only: Contact support at help@example.com for assistance.",
        "expected": "help@example.com",
    },
    {
        "id": "policy-2",
        "complexity": "complex",
        "prompt": "Policy: ship only if payment is confirmed AND stock is available AND either address is verified or a manager override exists. Payment confirmed, stock available, address unverified, no override. Return ship or hold.",
        "expected": "hold",
    },
]


class LiveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    baseline_model: ModelId = "gpt-4.1-mini"
    simple_model: ModelId = "gpt-4.1-nano"
    complex_model: ModelId = "gpt-4.1-mini"
    fallback_enabled: bool = True
    task_count: Literal[2, 4, 6, 8] = 8
    budget_usd: Decimal = Field(default=Decimal("0.05"), ge=0, le=1)
    sla_ms: float = Field(default=5000, gt=0, le=120000)
    monthly_requests: int = Field(default=100000, ge=1, le=10000000, strict=True)


class LiveRequest(LiveConfig):
    run_id: UUID
    confirm_paid: Literal[True]


def token_cost(model, input_tokens, output_tokens, cached_tokens=0):
    price = PRICES[model]
    return (
        Decimal(price["input"]) * (input_tokens - cached_tokens)
        + Decimal(price["cached_input"]) * cached_tokens
        + Decimal(price["output"]) * output_tokens
    ) / 1_000_000


def plan(config):
    calls = []
    for index, task in enumerate(DATASET[: config.task_count]):
        # Alternate which policy runs first to reduce consistent warm-cache/order bias.
        policies = ("baseline", "routed") if index % 2 == 0 else ("routed", "baseline")
        for policy in policies:
            model = (
                config.baseline_model
                if policy == "baseline"
                else (
                    config.simple_model if task["complexity"] == "simple" else config.complex_model
                )
            )
            calls.append((task, policy, model))
    return calls


def estimate(config):
    reserve = Decimal(0)
    count = 0
    for _, _, model in plan(config):
        reserve += token_cost(model, INPUT_ALLOWANCE, MAX_OUTPUT)
        count += 1
        if config.fallback_enabled and model != config.baseline_model:
            reserve += token_cost(config.baseline_model, INPUT_ALLOWANCE, MAX_OUTPUT)
            count += 1
    return {
        "reserved_usd": str(reserve),
        "max_calls": count,
        "within_budget": reserve <= config.budget_usd,
        "input_allowance_per_call": INPUT_ALLOWANCE,
        "max_output_tokens": MAX_OUTPUT,
        "price_date": PRICE_DATE,
    }


def attempt(client, model, task):
    payload = {
        "model": model,
        "store": False,
        "max_output_tokens": MAX_OUTPUT,
        "instructions": "Solve the task. Return only the requested answer in the JSON answer field.",
        "input": task["prompt"],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "task_answer",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                    "additionalProperties": False,
                },
            }
        },
    }
    started = time.perf_counter()
    result = {
        "model": model,
        "answer": None,
        "error": None,
        "fatal": False,
        "input_tokens": None,
        "output_tokens": None,
        "cached_tokens": None,
        "cost_usd": None,
        "reserved_usd": str(token_cost(model, INPUT_ALLOWANCE, MAX_OUTPUT)),
    }
    http_status = None
    try:
        response = client.post("https://api.openai.com/v1/responses", json=payload)
        http_status = response.status_code
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage") or {}
        i, o = usage.get("input_tokens"), usage.get("output_tokens")
        cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
        if not all(type(v) is int and v >= 0 for v in (i, o, cached)) or cached > i:
            result.update(error="Missing or invalid token usage; run stopped.", fatal=True)
        else:
            result.update(
                input_tokens=i,
                output_tokens=o,
                cached_tokens=cached,
                cost_usd=str(token_cost(model, i, o, cached)),
                response_model=data.get("model", model),
            )
            if i > INPUT_ALLOWANCE or o > MAX_OUTPUT:
                result.update(
                    error="Usage exceeded the preflight allowance; run stopped.", fatal=True
                )
            elif data.get("status") != "completed":
                result["error"] = "Response incomplete or refused."
            else:
                texts = [
                    part["text"]
                    for item in data.get("output", [])
                    if item.get("type") == "message"
                    for part in item.get("content", [])
                    if part.get("type") == "output_text"
                ]
                try:
                    answer = json.loads("".join(texts))["answer"]
                    if not isinstance(answer, str):
                        raise TypeError("Invalid answer type")
                    result["answer"] = answer
                except (ValueError, KeyError, TypeError):
                    result["error"] = "Response did not contain a valid answer."
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        # Never return provider body/headers: they can contain sensitive details.
        result.update(
            error=f"OpenAI HTTP {code}. Check API access, billing and model availability.",
            fatal=True,
        )
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        result.update(error="Connection or response error; usage unknown. Run stopped.", fatal=True)
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    event(
        logger,
        "model.attempt",
        level=logging.WARNING if result["error"] else logging.INFO,
        model=model,
        task_id=task["id"],
        latency_ms=result["latency_ms"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cached_tokens=result["cached_tokens"],
        cost_usd=result["cost_usd"],
        failed=bool(result["error"]),
        fatal=result["fatal"],
        http_status=http_status,
    )
    return result


def execute(config, client):
    preflight = estimate(config)
    if not preflight["within_budget"]:
        raise ValueError("Preflight reserve exceeds the selected budget. Reduce task count.")
    rows = []
    aborted = False
    for task, policy, model in plan(config):
        if aborted:
            break
        event(logger, "routing.selected", task_id=task["id"], policy=policy, model=model)
        attempts = [attempt(client, model, task)]
        first = attempts[0]
        # Fallback uses operational validity, never the hidden expected answer.
        if (
            first["error"]
            and not first["fatal"]
            and config.fallback_enabled
            and model != config.baseline_model
        ):
            event(
                logger,
                "routing.fallback",
                task_id=task["id"],
                policy=policy,
                primary=model,
                fallback=config.baseline_model,
            )
            attempts.append(attempt(client, config.baseline_model, task))
        final = attempts[-1]
        aborted = any(a["fatal"] for a in attempts)
        rows.append(
            {
                **task,
                "policy": policy,
                "answer": final["answer"],
                "correct": final["answer"] is not None
                and final["answer"].strip().casefold() == task["expected"].casefold(),
                "latency_ms": sum(a["latency_ms"] for a in attempts),
                "attempts": attempts,
                "error": final["error"],
            }
        )
    summaries = {}
    for policy in ("baseline", "routed"):
        subset = [row for row in rows if row["policy"] == policy]
        calls = [a for row in subset for a in row["attempts"]]
        cost = sum((Decimal(a["cost_usd"]) for a in calls if a["cost_usd"] is not None), Decimal(0))
        unknown = sum(a["cost_usd"] is None for a in calls)
        held = sum((Decimal(a["reserved_usd"]) for a in calls if a["cost_usd"] is None), Decimal(0))
        latency = sorted(row["latency_ms"] for row in subset)
        complete = len(subset) == config.task_count and unknown == 0
        summaries[policy] = {
            "policy": policy,
            "tasks_attempted": len(subset),
            "tasks_planned": config.task_count,
            "correct": sum(row["correct"] for row in subset),
            "accuracy_percent": sum(row["correct"] for row in subset) / len(subset) * 100
            if subset
            else None,
            "errors": sum(row["error"] is not None for row in subset),
            "calls": len(calls),
            "fallback_calls": len(calls) - len(subset),
            "input_tokens": sum(a["input_tokens"] or 0 for a in calls),
            "output_tokens": sum(a["output_tokens"] or 0 for a in calls),
            "cached_tokens": sum(a["cached_tokens"] or 0 for a in calls),
            "known_cost_usd": str(cost),
            "unknown_usage_calls": unknown,
            "unknown_usage_reserve_usd": str(held),
            "measured_p95_ms": latency[math.ceil(len(latency) * 0.95) - 1] if latency else None,
            "sla_violations": sum(value > config.sla_ms for value in latency),
            "projected_monthly_usd": str(cost / config.task_count * config.monthly_requests)
            if complete
            else None,
        }
    comparable = not aborted and len(rows) == config.task_count * 2
    savings = (
        (
            Decimal(summaries["baseline"]["known_cost_usd"])
            - Decimal(summaries["routed"]["known_cost_usd"])
        )
        if comparable
        else None
    )
    event(
        logger,
        "experiment.executed",
        status="aborted" if aborted else "completed",
        tasks=len(rows),
        baseline_cost_usd=summaries["baseline"]["known_cost_usd"],
        routed_cost_usd=summaries["routed"]["known_cost_usd"],
        unknown_usage_calls=sum(s["unknown_usage_calls"] for s in summaries.values()),
    )
    return {
        "mode": "live",
        "status": "aborted" if aborted else "completed",
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_version": "demo-v1",
        "config": config.model_dump(mode="json"),
        "preflight": preflight,
        "prices": PRICES,
        "price_date": PRICE_DATE,
        "summaries": summaries,
        "rows": rows,
        "savings_usd": str(savings) if savings is not None else None,
        "notes": [
            "Actual OpenAI calls; costs calculated from reported usage and a dated price snapshot.",
            "Eight toy tasks do not establish general quality or reliable production p95.",
            "Complexity labels are predefined; no paid classifier and no reference answers in prompts.",
            "Monthly figures extrapolate this workload mix, not an invoice forecast.",
            "No automatic HTTP retries. Unknown usage stops the run; its reserve is retained.",
            "Budget is a conservative application guard, not a provider-enforced billing cap.",
        ],
    }
