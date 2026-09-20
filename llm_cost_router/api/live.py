import logging
import os
from threading import Lock
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query

from llm_cost_router.api.models import (
    EstimateResponse,
    LiveConfigResponse,
    RunPayloadResponse,
    RunSummaryResponse,
)
from llm_cost_router.core.live import (
    DATASET,
    PRICE_DATE,
    PRICES,
    LiveConfig,
    LiveRequest,
    estimate,
    execute,
)
from llm_cost_router.core.setup_logger import event
from llm_cost_router.core.setup_logger import run_id as log_run_id
from llm_cost_router.db.dependency import Repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/live", tags=["Live OpenAI experiments"])
RUN_LOCK = Lock()


@router.get("/config", response_model=LiveConfigResponse)
def live_config():
    return {
        "key_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "models": PRICES,
        "price_date": PRICE_DATE,
        "defaults": LiveConfig().model_dump(mode="json"),
        "dataset": DATASET,
    }


@router.post("/estimate", response_model=EstimateResponse)
def live_estimate(config: LiveConfig):
    result = estimate(config)
    event(
        logger,
        "experiment.estimated",
        reserved_usd=result["reserved_usd"],
        max_calls=result["max_calls"],
        within_budget=result["within_budget"],
    )
    return result


@router.get("/runs", response_model=list[RunSummaryResponse])
def list_runs(repository: Repo, limit: int = Query(50, ge=1, le=200)):
    return repository.list_runs(limit)


@router.get("/runs/{run_id}", response_model=RunPayloadResponse)
def get_run(run_id: UUID, repository: Repo):
    result = repository.get_run(run_id)
    if result is None:
        raise HTTPException(404, "Run not found in PostgreSQL. Import legacy JSON runs if needed.")
    return result


@router.post("/run", response_model=RunPayloadResponse)
def run_live(request: LiveRequest, repository: Repo):
    run_token = log_run_id.set(str(request.run_id))
    try:
        return _run_live(request, repository)
    finally:
        log_run_id.reset(run_token)


def _run_live(request, repository):
    event(logger, "experiment.requested", task_count=request.task_count)
    if not estimate(request)["within_budget"]:
        event(logger, "experiment.budget_rejected", level=logging.WARNING)
        raise HTTPException(422, "Preflight reserve exceeds the budget. Reduce task count.")
    if not RUN_LOCK.acquire(blocking=False):
        event(logger, "experiment.busy", level=logging.WARNING)
        raise HTTPException(409, "Another run is active. Wait, then retrieve your saved run.")
    try:
        config = request.model_dump(mode="json")
        existing = repository.get_run(request.run_id)
        if existing is not None:
            if existing.get("config") != config:
                raise HTTPException(409, "Run ID already belongs to different settings.")
            event(logger, "experiment.reused", status=existing.get("status"))
            return existing
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            event(logger, "experiment.key_missing", level=logging.WARNING)
            raise HTTPException(503, "Set OPENAI_API_KEY in the FastAPI process and restart it.")
        if not repository.claim_run(request.run_id, config):
            raise HTTPException(409, "This run was already submitted. Retrieve its saved result.")
        try:
            with httpx.Client(
                headers={"Authorization": f"Bearer {key}"}, timeout=20, trust_env=False
            ) as client:
                result = execute(request, client)
        except Exception:  # noqa: BLE001 -- Persist unexpected failures without exposing provider details.
            event(
                logger,
                "experiment.failed",
                level=logging.ERROR,
                reason="unexpected_execution_error",
            )
            repository.finish_run(
                request.run_id,
                {
                    "run_id": str(request.run_id),
                    "status": "failed",
                    "config": config,
                    "error": "Execution interrupted; inspect account usage before a new run.",
                },
            )
            raise HTTPException(
                502, "Execution interrupted. The run ID is reserved and will not be retried."
            ) from None
        result["run_id"] = str(request.run_id)
        repository.finish_run(request.run_id, result)
        return result
    finally:
        RUN_LOCK.release()
