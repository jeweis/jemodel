"""MVP runtime 使用的轻量 schema 迁移入口。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.core.config import Settings
from app.db import models as _models  # noqa: F401
from app.db.base import Base


def prepare_data_dir(settings: Settings) -> None:
    """在 SQLite 打开数据库前创建配置的数据目录。"""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite:///"):
        db_path = Path(settings.database_url.removeprefix("sqlite:///"))
        if not str(db_path).startswith(":"):
            db_path.parent.mkdir(parents=True, exist_ok=True)


def run_migrations(engine: Engine, settings: Settings) -> None:
    """为 MVP schema 创建缺失数据表。

    工具链中保留 Alembic 以便后续版本化迁移；当前空项目的 MVP
    使用 SQLAlchemy metadata 创建表作为初始迁移。
    """
    prepare_data_dir(settings)
    Base.metadata.create_all(bind=engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine: Engine) -> None:
    """补齐早期开发数据库缺失的列，后续可迁移到 Alembic。"""
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "password_hash" in user_columns:
        return
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)"))
