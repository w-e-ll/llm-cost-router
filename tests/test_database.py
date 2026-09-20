import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient

from llm_cost_router.api import live
from llm_cost_router.api.main import app
from llm_cost_router.db.connection import ROOT
from llm_cost_router.db.dependency import get_repository
from llm_cost_router.db.import_runs import import_directory
from llm_cost_router.db.repository import Repository
from llm_cost_router.db.schema import metadata

pytestmark = pytest.mark.postgres


def test_migration_roundtrip_and_schema_match(repository):
    repository.health()
    config = Config(str(ROOT / "alembic.ini"))
    with repository.engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), metadata) == []
        conn.rollback()
        config.attributes["connection"] = conn
        command.downgrade(config, "base")
        assert "experiment_runs" not in sa.inspect(conn).get_table_names()
        conn.rollback()
        command.upgrade(config, "head")
    repository.health()


def test_concurrent_claim_and_durable_immutable_result(repository):
    run_id = uuid4()
    config = {"model": "test"}
    with ThreadPoolExecutor(max_workers=4) as workers:
        claims = list(workers.map(lambda _: repository.claim_run(run_id, config), range(4)))
    assert sum(claims) == 1
    result = {
        "run_id": str(run_id),
        "status": "completed",
        "config": config,
        "decimal_cost": "0.000000123456789",
    }
    repository.finish_run(run_id, result)
    assert Repository(repository.engine).get_run(run_id) == result
    with pytest.raises(ValueError):
        repository.finish_run(run_id, {**result, "decimal_cost": "999"})
    assert repository.get_run(run_id) == result


def test_import_is_insert_only_and_keeps_files(repository, tmp_path):
    run_id = uuid4()
    payload = {"run_id": str(run_id), "status": "aborted", "config": {"task_count": 2}}
    path = tmp_path / f"{run_id}.json"
    path.write_text(json.dumps(payload))
    assert import_directory(repository, tmp_path)["imported"] == 1
    path.write_text(json.dumps({**payload, "status": "failed"}))
    assert import_directory(repository, tmp_path)["existing"] == 1
    assert repository.get_run(run_id) == payload
    assert path.exists()


def test_db_error_never_starts_paid_call(repository, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "not-real")
    monkeypatch.setattr(live, "execute", lambda *a: pytest.fail("Provider must not be called"))

    def unavailable(*args):
        raise sa.exc.OperationalError("secret SQL", {}, Exception("secret password"))

    monkeypatch.setattr(repository, "get_run", unavailable)
    response = TestClient(app).post(
        "/api/v1/live/run", json={"run_id": str(uuid4()), "confirm_paid": True}
    )
    assert response.status_code == 503
    assert "secret" not in response.text


def test_failed_execution_is_not_retried(repository, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "not-real")
    count = []

    def fail(*args):
        count.append(1)
        raise RuntimeError("private-provider-error")

    monkeypatch.setattr(live, "execute", fail)
    request = {"run_id": str(uuid4()), "confirm_paid": True}
    client = TestClient(app)
    assert client.post("/api/v1/live/run", json=request).status_code == 502
    second = client.post("/api/v1/live/run", json=request)
    assert second.json()["status"] == "failed"
    assert "private-provider-error" not in second.text
    assert len(count) == 1


def test_health_history_and_missing_config(repository, monkeypatch):
    client = TestClient(app)
    assert client.get("/health/db").json()["storage"] == "postgresql"
    repository.claim_run(uuid4(), {})
    assert len(client.get("/api/v1/live/runs").json()) == 1
    assert client.get("/api/v1/live/runs?limit=0").status_code == 422
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app.dependency_overrides.pop(get_repository)
    from llm_cost_router.db.connection import get_engine

    get_engine.cache_clear()
    assert client.get("/health/db").status_code == 503
