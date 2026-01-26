import os
from typing import TYPE_CHECKING

from dbos import DBOS, DBOSConfig
import structlog

from backend.core.conf.settings import SETTINGS


if TYPE_CHECKING:
    from fastapi import FastAPI


logger = structlog.get_logger(__name__)


def initialize_dbos(app: "FastAPI") -> DBOS:
    """
    Initialize DBOS framework with FastAPI app.
    """
    # Extract database URL and convert to sync format for DBOS
    # DBOS uses psycopg (sync) while our app uses asyncpg
    _dbos_database_url = str(SETTINGS.DATABASE.DATABASE_URL).replace("+asyncpg", "")

    _dbos_config: DBOSConfig = {
        "name": "prb-billing",
        "database_url": _dbos_database_url,
        "conductor_key": os.environ.get("DBOS_CONDUCTOR_KEY", None),
    }

    # Initialize DBOS with FastAPI integration
    _dbos_instance = DBOS(fastapi=app, config=_dbos_config)

    logger.info(
        "DBOS initialized",
        app_name=_dbos_config["name"],
        features=[
            "durable_workflows",
            "exactly_once_execution",
            "crash_recovery",
            "scheduled_jobs",
        ],
    )

    return _dbos_instance
