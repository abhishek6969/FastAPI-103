"""
alembic/env.py — Alembic Migration Environment
================================================
This file is the "brain" of Alembic. It is executed every time you run any
Alembic CLI command (upgrade, downgrade, revision, current, history...).

Its two jobs:
  1. Tell Alembic WHERE the database is (load DATABASE_URL from our .env settings)
  2. Tell Alembic WHAT the schema should look like (import our SQLAlchemy models)

TWO MODES Alembic can run in:
  - Offline mode: Just generates SQL scripts to a file, no DB connection needed.
                  Use case: regulated environments where a DBA reviews SQL before
                  applying it manually.
  - Online mode:  Connects to the real DB and applies migrations directly.
                  This is what we use in Docker compose and CI/CD.

WHY asyncio?
  Our app uses async SQLAlchemy (asyncpg driver). Alembic itself is synchronous
  by default, so we use asyncio.run() + async_engine_from_config to bridge the gap.
  The `do_run_migrations` function runs synchronously inside an async context via
  `run_sync`, which is SQLAlchemy's way of running sync operations on an async conn.
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import our Base (the SQLAlchemy DeclarativeBase) and all ORM models.
# Alembic uses Base.metadata to know what the "target" schema should look like
# when running --autogenerate. Every model that inherits from Base must be
# imported here — otherwise Alembic won't know it exists and won't generate
# migrations for it.
from app.database import Base
from app.models.db_invoice import InvoiceRecord  # noqa: F401 — import registers model with Base.metadata
from app.models.db_preference import UserPreference  # noqa: F401 — registers user_preferences model
from app.config import get_settings

# `config` is the Alembic Config object — gives access to alembic.ini values.
config = context.config

# Set up Python logging using the [loggers] section in alembic.ini.
# This controls what gets printed when you run `alembic upgrade head` in the terminal.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DYNAMIC DATABASE URL — read from our app's .env via pydantic-settings.
# This overrides the blank `sqlalchemy.url =` in alembic.ini at runtime.
# Benefits:
#   - Single source of truth: DATABASE_URL only lives in .env
#   - No credentials in version-controlled alembic.ini
#   - Works seamlessly in Docker (db:5432) and locally (localhost:5432)
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# target_metadata tells --autogenerate what our DESIRED schema looks like.
# Alembic will compare this against the ACTUAL database schema and generate
# the difference as migration code (CREATE TABLE, ALTER TABLE, DROP INDEX, etc.)
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# OFFLINE MODE — generates SQL without connecting to the database
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """
    Generate migration SQL to stdout/file without a live DB connection.
    Useful for regulated environments where a DBA reviews SQL before applying.
    Run with: alembic upgrade head --sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,      # renders values inline in SQL (not as :param)
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# ONLINE MODE — connects to DB and applies migrations directly
# ---------------------------------------------------------------------------
def do_run_migrations(connection: Connection) -> None:
    """
    Inner function that runs the actual migration SQL on a live connection.
    Called by run_async_migrations() via connection.run_sync().
    `run_sync` allows synchronous Alembic code to execute inside async context.
    """
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Creates an async SQLAlchemy engine and runs migrations asynchronously.
    We use NullPool instead of the default connection pool because migrations
    are a one-shot operation — we don't need connections pooled after it runs.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # run_sync bridges the gap: Alembic's migration runner is synchronous,
        # but our engine is async. run_sync executes the sync function inside
        # the async connection.
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online mode. Wraps async code in asyncio.run()."""
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# ENTRYPOINT — Alembic calls this file directly; we choose mode based on context
# ---------------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
