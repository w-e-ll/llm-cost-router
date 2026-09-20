import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from llm_cost_router.api.live import router as live_router
from llm_cost_router.api.models import HealthResponse
from llm_cost_router.api.planning import router as planning_router
from llm_cost_router.core.engine import compare
from llm_cost_router.core.schemas import Comparison, Scenario
from llm_cost_router.core.setup_logger import event, request_id, setup_logger
from llm_cost_router.db.dependency import Repo

setup_logger()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="LLM Cost Router",
    version="0.2.0",
    description="LLM FinOps calculator and optional paid OpenAI experiments.",
)
app.include_router(live_router)
app.include_router(planning_router)


@app.middleware("http")
async def request_logging(request, call_next):
    correlation = uuid4().hex
    token = request_id.set(correlation)
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = correlation
        return response
    finally:
        route = request.scope.get("route")
        event(
            logger,
            "http.request",
            level=logging.ERROR if status >= 500 else logging.INFO,
            method=request.method,
            route=getattr(route, "path", "unmatched"),
            status=status,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        request_id.reset(token)


@app.exception_handler(SQLAlchemyError)
async def database_error(request, exc):
    event(logger, "db.unavailable", level=logging.ERROR, error_type=type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={
            "detail": "PostgreSQL is unavailable or migrations are missing. Check DATABASE_URL, start PostgreSQL and run alembic upgrade head. Retrieve the same run ID before starting a new paid run."
        },
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error(request: Request, exc: RequestValidationError):
    event(
        logger,
        "http.validation_error",
        level=logging.WARNING,
        method=request.method,
        route=request.scope.get("path", "unknown"),
        error_count=len(exc.errors()),
    )
    return JSONResponse(status_code=422, content={"detail": "Request validation failed."})


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    event(
        logger,
        "http.unhandled_error",
        level=logging.ERROR,
        method=request.method,
        route=request.scope.get("path", "unknown"),
        error_type=type(exc).__name__,
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


@app.get("/health/db", response_model=HealthResponse, response_model_exclude_none=True)
def database_health(repository: Repo):
    repository.health()
    return {"status": "ok", "storage": "postgresql"}


@app.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "live-and-planning"}


@app.get("/api/v1/defaults", response_model=Scenario)
def defaults() -> Scenario:
    return Scenario()


@app.post("/api/v1/compare", response_model=Comparison)
def compare_scenario(scenario: Scenario) -> Comparison:
    return compare(scenario)
