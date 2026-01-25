import asyncio
from logging.config import fileConfig
from pathlib import Path
import sys
from typing import Any

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config


# Add the project root directory to Python path
# Path: migrations/env.py -> backend/ -> kinonee/ (project root)
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from backend.core.conf.settings import SETTINGS
from backend.core.infrastructure.database.models import DbBaseModel
from backend.features.auth.models import *

# Import all feature-based models for Trainer.me marketplace
from backend.features.billing.models import *
from backend.features.events.models import *
from backend.features.prereg.models import *
from backend.features.profiles.models import *
from backend.features.tags.models import *


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = DbBaseModel.metadata

# Set database URL directly without interpolation issues
# Check if POSTGRES_HOST is overridden for local development
import os


postgres_host = os.getenv("POSTGRES_HOST", SETTINGS.DATABASE.POSTGRES_HOST)
if postgres_host == "localhost":
    # Build URL with localhost for local development
    database_url = f"postgresql+asyncpg://{SETTINGS.DATABASE.POSTGRES_USER}:{SETTINGS.DATABASE.POSTGRES_PASSWORD.get_secret_value()}@localhost:{SETTINGS.DATABASE.POSTGRES_PORT}/{SETTINGS.DATABASE.POSTGRES_DB}"
else:
    database_url = str(SETTINGS.DATABASE.DATABASE_URL)

# Replace any problematic characters that might cause interpolation issues
database_url = database_url.replace("%", "%%")
config.set_main_option("sqlalchemy.url", database_url)

def exclude_tables_from_config(config_: Any) -> list[str] | None:
    tables_ = config_.get("tables", None)
    if tables_ is not None:
        tables = tables_.split(",")
        return tables  # type: ignore[no-any-return]
    return None

alembic_exclude_section = config.get_section("alembic:exclude")
exclude_tables = (alembic_exclude_section.get("tables", "") if alembic_exclude_section else "").split(",")

def include_object(object: Any, name: str | None, type_: str, *args: Any, **kwargs: Any) -> bool:
    return not (type_ == "table" and name in exclude_tables)


def process_revision_directives(context: Any, revision: Any, directives: Any) -> None:
    """Generate sequential revision IDs like 001, 002, 003 instead of random hashes."""
    if directives:
        migration_script = directives[0]
        script_dir = ScriptDirectory.from_config(config)
        head_revision = script_dir.get_current_head()

        if head_revision is None:
            # First migration
            new_rev_id = 1
        else:
            # Get the current head revision ID and increment
            try:
                last_rev_id = int(head_revision.lstrip("0"))
                new_rev_id = last_rev_id + 1
            except (ValueError, AttributeError):
                # If the current head is not a number (e.g., legacy hash), start from 1
                new_rev_id = 1

        # Format with leading zeros: 1 -> 001
        migration_script.rev_id = f"{new_rev_id:03d}"

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
        process_revision_directives=process_revision_directives,
        compare_type=True,  # Enable type comparison to detect Enum changes
    )

    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        include_object=include_object,
        include_schemas=False,
        target_metadata=target_metadata,
        process_revision_directives=process_revision_directives,
        compare_type=True,  # Enable type comparison to detect Enum changes
    )

    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # await connection.execute(text("SET lock_timeout = '4s'"))
        # await connection.execute(text("SET statement_timeout = '8s'"))
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()

def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
