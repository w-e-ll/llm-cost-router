from alembic import context

from llm_cost_router.db.connection import build_engine, database_url
from llm_cost_router.db.schema import metadata

config = context.config


def run_migrations():
    if context.is_offline_mode():
        context.configure(url=database_url(), target_metadata=metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    # Supplied connection allows isolated PostgreSQL schema tests without global env mutation.
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    else:
        engine = build_engine(database_url())
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
        engine.dispose()


run_migrations()
