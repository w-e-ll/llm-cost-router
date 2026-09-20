from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from llm_cost_router.db.connection import get_engine
from llm_cost_router.db.repository import Repository


def get_repository():
    try:
        return Repository(get_engine())
    except (ValueError, SQLAlchemyError):
        raise HTTPException(
            503, "PostgreSQL is not configured. Set DATABASE_URL and run migrations."
        ) from None


Repo = Annotated[Repository, Depends(get_repository)]
