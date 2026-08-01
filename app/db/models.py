"""用户、密钥、provider、路由和用量相关数据库模型。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON as JsonType
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    """返回带时区的 UTC 时间，用于默认时间列。"""
    return datetime.now(UTC)


class UserRecord(Base):
    """拥有 API key 和用量记录的成员或自动化 actor。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    api_keys: Mapped[list[ApiKeyRecord]] = relationship(back_populates="user")


class ApiKeyRecord(Base):
    """受保护的对外 jemodel API key 元数据。"""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    prefix: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JsonType, default=list)
    allowed_models: Mapped[list[str]] = mapped_column(JsonType, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[UserRecord] = relationship(back_populates="api_keys")


class ProviderRecord(Base):
    """上游 provider endpoint 和鉴权元数据。"""

    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="api_key")
    secret_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)
    health_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    models: Mapped[list[UpstreamModelRecord]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )
    oauth_tokens: Mapped[list[OAuthTokenRecord]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )


class UpstreamModelRecord(Base):
    """某个 provider 暴露的真实上游模型。"""

    __tablename__ = "upstream_models"
    __table_args__ = (UniqueConstraint("provider_id", "model_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    capabilities: Mapped[dict] = mapped_column(JsonType, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    provider: Mapped[ProviderRecord] = relationship(back_populates="models")
    route_targets: Mapped[list[RouteTargetRecord]] = relationship(
        back_populates="upstream_model", cascade="all, delete-orphan"
    )


class VirtualModelRecord(Base):
    """对 client 可见的稳定虚拟模型名。"""

    __tablename__ = "virtual_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    route_targets: Mapped[list[RouteTargetRecord]] = relationship(
        back_populates="virtual_model", cascade="all, delete-orphan"
    )


class RouteTargetRecord(Base):
    """将 virtual model 映射到 upstream model 的路由目标。"""

    __tablename__ = "route_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    virtual_model_id: Mapped[int] = mapped_column(ForeignKey("virtual_models.id"), nullable=False)
    upstream_model_id: Mapped[int] = mapped_column(ForeignKey("upstream_models.id"), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capabilities: Mapped[dict] = mapped_column(JsonType, default=dict)

    virtual_model: Mapped[VirtualModelRecord] = relationship(back_populates="route_targets")
    upstream_model: Mapped[UpstreamModelRecord] = relationship(back_populates="route_targets")


class UsageLedgerRecord(Base):
    """通过鉴权的模型请求对应的 append-only 用量账本行。"""

    __tablename__ = "usage_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id"), nullable=False)
    virtual_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    upstream_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usage_source: Mapped[str] = mapped_column(String(32), nullable=False, default="estimated")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback_trace: Mapped[list[dict]] = mapped_column(JsonType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RequestLogRecord(Base):
    """不包含原始 prompt 或 provider secret 的操作日志。"""

    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id"), nullable=True)
    protocol_in: Mapped[str] = mapped_column(String(32), nullable=False)
    protocol_out: Mapped[str | None] = mapped_column(String(32), nullable=True)
    virtual_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    upstream_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback_trace: Mapped[list[dict]] = mapped_column(JsonType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OAuthTokenRecord(Base):
    """Codex OAuth 凭证，MVP 阶段一个 provider 绑定一个 ChatGPT 账号。"""

    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    account_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    id_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_refresh: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    provider: Mapped[ProviderRecord] = relationship(back_populates="oauth_tokens")
