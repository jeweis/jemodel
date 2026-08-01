"""数据库 engine 和 session 生命周期工具。"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


def make_engine(settings: Settings):
    """根据运行时配置创建 SQLAlchemy engine。"""
    connect_args = (
        {"check_same_thread": False}
        if settings.database_url.startswith("sqlite")
        else {}
    )
    return create_engine(settings.database_url, connect_args=connect_args, future=True)


engine = make_engine(get_settings())
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False, future=True)


def get_session() -> Generator[Session, None, None]:
    """为 FastAPI 依赖注入提供数据库 session。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
