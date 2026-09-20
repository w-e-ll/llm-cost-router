import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from llm_cost_router.api.main import app
from llm_cost_router.db.connection import ROOT, build_engine
from llm_cost_router.db.dependency import get_repository
from llm_cost_router.db.repository import Repository


@pytest.fixture
def repository():
    """Real PostgreSQL in a fresh schema in the explicitly named test database."""
    raw_url = os.getenv("TEST_DATABASE_URL", "")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL-backed API tests.")
    url = make_url(raw_url)
    if not (url.database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a separate database ending in _test.")
    schema = "test_" + uuid4().hex
    admin = build_engine(raw_url)
    with admin.begin() as conn:
        conn.execute(sa.schema.CreateSchema(schema))
    isolated = url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = build_engine(isolated)
    try:
        config = Config(str(ROOT / "alembic.ini"))
        with engine.connect() as conn:
            config.attributes["connection"] = conn
            command.upgrade(config, "head")
        repo = Repository(engine)
        app.dependency_overrides[get_repository] = lambda: repo
        yield repo
    finally:
        app.dependency_overrides.pop(get_repository, None)
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(sa.schema.DropSchema(schema, cascade=True))
        admin.dispose()
