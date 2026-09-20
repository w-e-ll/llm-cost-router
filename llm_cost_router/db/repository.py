import logging
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from llm_cost_router.core.setup_logger import event
from llm_cost_router.db.schema import plans, runs

logger = logging.getLogger(__name__)


class Repository:
    def __init__(self, engine):
        self.engine = engine

    def health(self):
        with self.engine.connect() as conn:
            conn.execute(sa.select(runs.c.id).limit(1))
            conn.execute(sa.select(plans.c.id).limit(1))

    def get_run(self, run_id):
        with self.engine.connect() as conn:
            return conn.execute(
                sa.select(runs.c.payload).where(runs.c.id == run_id)
            ).scalar_one_or_none()

    def claim_run(self, run_id, config):
        """Commit reservation BEFORE any provider call. Exactly one claimant wins."""
        payload = {"run_id": str(run_id), "status": "started", "config": config}
        with self.engine.begin() as conn:
            inserted = conn.execute(
                insert(runs)
                .values(
                    id=run_id,
                    status="started",
                    config=config,
                    payload=payload,
                )
                .on_conflict_do_nothing(index_elements=[runs.c.id])
                .returning(runs.c.id)
            ).scalar_one_or_none()
        event(
            logger,
            "db.run_claimed" if inserted is not None else "db.run_conflict",
            run_id=str(run_id),
        )
        return inserted is not None

    def finish_run(self, run_id, result):
        with self.engine.begin() as conn:
            updated = conn.execute(
                runs.update()
                .where(
                    runs.c.id == run_id,
                    runs.c.status == "started",
                )
                .values(status=result["status"], payload=result, updated_at=sa.func.now())
            )
            if updated.rowcount != 1:
                raise ValueError("Only a reserved run may be finalized.")

        event(logger, "db.run_saved", run_id=str(run_id), status=result["status"])

    def import_run(self, run_id, payload):
        """Insert-only import: existing IDs are never replaced."""
        with self.engine.begin() as conn:
            inserted = conn.execute(
                insert(runs)
                .values(
                    id=run_id,
                    status=payload["status"],
                    config=payload["config"],
                    payload=payload,
                )
                .on_conflict_do_nothing(index_elements=[runs.c.id])
                .returning(runs.c.id)
            ).scalar_one_or_none()
        return inserted is not None

    def list_runs(self, limit=50):
        with self.engine.connect() as conn:
            rows = (
                conn.execute(
                    sa.select(runs.c.id, runs.c.status, runs.c.created_at)
                    .order_by(runs.c.created_at.desc(), runs.c.id.desc())
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            return [
                {
                    "run_id": str(r["id"]),
                    "status": r["status"],
                    "created_at": r["created_at"].isoformat(),
                }
                for r in rows
            ]

    def save_plan(self, source_run_id, settings, result):
        plan_id = uuid4()
        payload = {**result, "plan_id": str(plan_id)}
        with self.engine.begin() as conn:
            conn.execute(
                plans.insert().values(
                    id=plan_id, source_run_id=source_run_id, settings=settings, payload=payload
                )
            )
        event(logger, "db.plan_saved", run_id=str(source_run_id), plan_id=str(plan_id))
        return payload

    def get_plan(self, plan_id):
        with self.engine.connect() as conn:
            return conn.execute(
                sa.select(plans.c.payload).where(plans.c.id == plan_id)
            ).scalar_one_or_none()
