import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from dotenv import load_dotenv

from alembic import context

# ---------------------------------------------------------------------------
# Ensure the backend/ directory is on sys.path so "from app.xxx" imports work.
# env.py lives in backend/alembic/env.py, so go two levels up to reach
# the backend/ directory.
# ---------------------------------------------------------------------------
_backend_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# ---------------------------------------------------------------------------
# Load DATABASE_URL from the same .env file the FastAPI application uses.
# ---------------------------------------------------------------------------
load_dotenv(os.path.join(_backend_dir, ".env"))

# ---------------------------------------------------------------------------
# Alembic Config object -- provides access to values in alembic.ini.
# ---------------------------------------------------------------------------
config = context.config

# Set the database URL from the environment so we do NOT hard-code it in
# alembic.ini.
config.set_main_option("sqlalchemy.url", os.getenv("DATABASE_URL"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Import the project's SQLAlchemy Base and every model so that
# `alembic revision --autogenerate` sees all tables.
# ---------------------------------------------------------------------------
from app.database import Base  # noqa: E402
import app.models  # noqa: E402,F401 - required for autogenerate table detection

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine.
    By skipping the Engine creation we don't need a DBAPI to be available.

    Calls to context.execute() emit the given string to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine and associate a connection
    with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
