"""Alembic env: URL built from app.config env vars (same as main app)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app import config
from app.db import Base

config_ = context.config
if config_.config_file_name is not None:
    fileConfig(config_.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    pwd = config.MYSQL_PASSWORD or ""
    return (f"mysql+pymysql://{config.MYSQL_USER}:{pwd}@"
            f"{config.MYSQL_HOST}:{config.MYSQL_PORT}/{config.MYSQL_DATABASE}?charset=utf8mb4")


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), pool_pre_ping=True, future=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
