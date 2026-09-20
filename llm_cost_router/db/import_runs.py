"""Insert legacy run JSON into PostgreSQL without replacing records or deleting files."""

import argparse
import json
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from llm_cost_router.core.planning import SourceRun
from llm_cost_router.db.connection import ROOT, get_engine
from llm_cost_router.db.repository import Repository

logger = logging.getLogger(__name__)


def import_directory(repository, directory):
    counts = {"imported": 0, "existing": 0, "invalid": 0}
    for path in sorted(Path(directory).glob("*.json")):
        try:
            run_id = UUID(path.stem)
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if payload.get("run_id") and UUID(payload["run_id"]) != run_id:
                raise ValueError("ID mismatch")
            payload["run_id"] = str(run_id)
            if payload.get("status") not in ("completed", "aborted", "started", "failed"):
                raise ValueError("Unknown status")
            if not isinstance(payload.get("config"), dict):
                raise TypeError("Missing config")
            if payload["status"] == "completed":
                SourceRun.model_validate(payload)
        except (ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "legacy_import.invalid_file file=%s error_type=%s", path.name, type(exc).__name__
            )
            counts["invalid"] += 1
            continue
        inserted = repository.import_run(run_id, payload)
        counts["imported" if inserted else "existing"] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "var" / "live-runs")
    args = parser.parse_args()
    try:
        counts = import_directory(Repository(get_engine()), args.directory)
        print(json.dumps(counts))
    except (ValueError, SQLAlchemyError, OSError) as exc:
        logger.exception("legacy_import.failed error_type=%s", type(exc).__name__)
        parser.exit(
            1,
            "Import failed. Check PostgreSQL configuration and migrations. No source files were deleted.\n",
        )


if __name__ == "__main__":
    main()
