import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException

from llm_cost_router.api.models import PlanPayloadResponse
from llm_cost_router.core.planning import PlanningRequest, project_run
from llm_cost_router.db.dependency import Repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/planning", tags=["Cost planning"])


@router.post("", response_model=PlanPayloadResponse)
def cost_plan(request: PlanningRequest, repository: Repo):
    saved = repository.get_run(request.run_id)
    if saved is None:
        raise HTTPException(404, "Run not found in PostgreSQL. Import legacy JSON runs if needed.")
    if saved.get("status") != "completed":
        raise HTTPException(
            409, "Cost planning needs a completed live run. This run is not complete."
        )
    try:
        result = project_run(saved, request)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(
            422, "Run cannot support planning: incomplete or invalid task/token/cost data."
        ) from None
    return repository.save_plan(request.run_id, request.model_dump(mode="json"), result)


@router.get("/{plan_id}", response_model=PlanPayloadResponse)
def get_plan(plan_id: UUID, repository: Repo):
    result = repository.get_plan(plan_id)
    if result is None:
        raise HTTPException(404, "Cost plan not found.")
    return result
