"""jemodel 持久化使用的 SQLAlchemy 基础类型。"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM model 共享的声明式基类。"""
