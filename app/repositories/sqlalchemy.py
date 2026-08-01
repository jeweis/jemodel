"""MVP 数据库使用的 SQLAlchemy repository 实现。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    ApiKeyRecord,
    OAuthTokenRecord,
    ProviderRecord,
    RequestLogRecord,
    RouteTargetRecord,
    UpstreamModelRecord,
    UsageLedgerRecord,
    UserRecord,
    VirtualModelRecord,
)


class SqlRepositories:
    """基于当前 SQLAlchemy session 的具体 repository 集合。"""

    def __init__(self, session: Session) -> None:
        """保存当前 unit-of-work session。"""
        self.session = session

    def list_users(self) -> list[UserRecord]:
        """按 id 返回所有用户。"""
        return list(self.session.scalars(select(UserRecord).order_by(UserRecord.id)))

    def get_user(self, user_id: int) -> UserRecord | None:
        """按 id 返回用户，不存在时返回 None。"""
        return self.session.get(UserRecord, user_id)

    def find_user_by_email(self, email: str) -> UserRecord | None:
        """按 email 返回用户，不存在时返回 None。"""
        stmt = select(UserRecord).where(UserRecord.email == email)
        return self.session.scalar(stmt)

    def create_user(
        self,
        name: str,
        email: str,
        role: str,
        password_hash: str | None = None,
    ) -> UserRecord:
        """创建并持久化用户。"""
        user = UserRecord(name=name, email=email, role=role, password_hash=password_hash)
        self.session.add(user)
        self.session.flush()
        return user

    def delete_user(self, user_id: int) -> bool:
        """删除没有关联数据的用户，成功时返回 True。"""
        record = self.get_user(user_id)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    def list_api_keys(self, user_id: int | None = None) -> list[ApiKeyRecord]:
        """返回 API keys，可按 owner user 限定。"""
        stmt = select(ApiKeyRecord).order_by(ApiKeyRecord.id)
        if user_id is not None:
            stmt = stmt.where(ApiKeyRecord.user_id == user_id)
        return list(self.session.scalars(stmt))

    def get_api_key(self, api_key_id: int) -> ApiKeyRecord | None:
        """按 id 返回 API key。"""
        return self.session.get(ApiKeyRecord, api_key_id)

    def create_api_key(
        self,
        user_id: int,
        name: str,
        key_hash: str,
        prefix: str,
        scopes: list[str],
        allowed_models: list[str],
    ) -> ApiKeyRecord:
        """创建并持久化 API key 元数据。"""
        record = ApiKeyRecord(
            user_id=user_id,
            name=name,
            key_hash=key_hash,
            prefix=prefix,
            scopes=scopes,
            allowed_models=allowed_models,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def list_providers(self) -> list[ProviderRecord]:
        """返回所有 providers。"""
        return list(self.session.scalars(select(ProviderRecord).order_by(ProviderRecord.id)))

    def get_provider(self, provider_id: int) -> ProviderRecord | None:
        """按 id 返回 provider。"""
        return self.session.get(ProviderRecord, provider_id)

    def create_provider(self, values: dict) -> ProviderRecord:
        """根据已验证字段创建并持久化 provider。"""
        record = ProviderRecord(**values)
        self.session.add(record)
        self.session.flush()
        return record

    def delete_provider(self, provider_id: int) -> bool:
        """删除 provider，成功时返回 True。"""
        record = self.get_provider(provider_id)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    def list_upstream_models(self) -> list[UpstreamModelRecord]:
        """返回 upstream models，并预加载 provider 元数据。"""
        stmt = select(UpstreamModelRecord).options(selectinload(UpstreamModelRecord.provider))
        return list(self.session.scalars(stmt.order_by(UpstreamModelRecord.id)))

    def create_upstream_model(self, values: dict) -> UpstreamModelRecord:
        """创建并持久化 upstream model target。"""
        record = UpstreamModelRecord(**values)
        self.session.add(record)
        self.session.flush()
        return record

    def get_upstream_model(self, model_id: int) -> UpstreamModelRecord | None:
        """按 id 返回 upstream model。"""
        return self.session.get(UpstreamModelRecord, model_id)

    def delete_upstream_model(self, model_id: int) -> bool:
        """删除 upstream model target，成功时返回 True。"""
        record = self.get_upstream_model(model_id)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    def list_virtual_models(self) -> list[VirtualModelRecord]:
        """返回所有 virtual models。"""
        stmt = select(VirtualModelRecord).order_by(VirtualModelRecord.id)
        return list(self.session.scalars(stmt))

    def get_virtual_model(self, name: str) -> VirtualModelRecord | None:
        """按对外名称返回 virtual model，不区分 enabled 状态。"""
        stmt = select(VirtualModelRecord).where(VirtualModelRecord.name == name)
        return self.session.scalar(stmt)

    def get_virtual_model_by_id(self, model_id: int) -> VirtualModelRecord | None:
        """按 id 返回 virtual model。"""
        return self.session.get(VirtualModelRecord, model_id)

    def create_virtual_model(self, values: dict) -> VirtualModelRecord:
        """创建并持久化 virtual model。"""
        record = VirtualModelRecord(**values)
        self.session.add(record)
        self.session.flush()
        return record

    def delete_virtual_model(self, model_id: int) -> bool:
        """删除 virtual model，成功时返回 True。"""
        record = self.get_virtual_model_by_id(model_id)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    def list_route_targets(self, virtual_model: str) -> list[RouteTargetRecord]:
        """返回某个 virtual model 的 route targets，并预加载 provider 信息。"""
        stmt = (
            select(RouteTargetRecord)
            .join(RouteTargetRecord.virtual_model)
            .where(VirtualModelRecord.name == virtual_model)
            .options(
                selectinload(RouteTargetRecord.upstream_model).selectinload(
                    UpstreamModelRecord.provider
                )
            )
            .order_by(RouteTargetRecord.priority, RouteTargetRecord.id)
        )
        return list(self.session.scalars(stmt))

    def create_route_target(self, values: dict) -> RouteTargetRecord:
        """创建并持久化 route target。"""
        record = RouteTargetRecord(**values)
        self.session.add(record)
        self.session.flush()
        return record

    def get_route_target(self, target_id: int) -> RouteTargetRecord | None:
        """按 id 返回 route target。"""
        return self.session.get(RouteTargetRecord, target_id)

    def delete_route_target(self, target_id: int) -> bool:
        """删除 route target，成功时返回 True。"""
        record = self.get_route_target(target_id)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    def create_usage(self, values: dict) -> UsageLedgerRecord:
        """创建 append-only usage ledger 行。"""
        record = UsageLedgerRecord(**values)
        self.session.add(record)
        self.session.flush()
        return record

    def create_request_log(self, values: dict) -> RequestLogRecord:
        """创建已脱敏的操作 request log 行。"""
        record = RequestLogRecord(**values)
        self.session.add(record)
        self.session.flush()
        return record

    def list_usage(self) -> list[UsageLedgerRecord]:
        """按最新优先返回 usage ledger rows。"""
        stmt = select(UsageLedgerRecord).order_by(UsageLedgerRecord.created_at.desc())
        return list(self.session.scalars(stmt))

    def list_logs(self) -> list[RequestLogRecord]:
        """按最新优先返回 request logs。"""
        stmt = select(RequestLogRecord).order_by(RequestLogRecord.created_at.desc())
        return list(self.session.scalars(stmt))

    def get_oauth_token(self, provider_id: int) -> OAuthTokenRecord | None:
        """按 provider_id 返回 OAuth token 记录，不存在时返回 None。"""
        stmt = select(OAuthTokenRecord).where(OAuthTokenRecord.provider_id == provider_id)
        return self.session.scalar(stmt)

    def save_oauth_token(self, values: dict) -> OAuthTokenRecord:
        """创建或替换 provider 的 OAuth token（upsert，保持 unique 约束）。"""
        existing = self.get_oauth_token(values["provider_id"])
        if existing is not None:
            for field, value in values.items():
                setattr(existing, field, value)
            self.session.flush()
            return existing
        record = OAuthTokenRecord(**values)
        self.session.add(record)
        self.session.flush()
        return record

    def update_oauth_token(self, token_id: int, values: dict) -> OAuthTokenRecord | None:
        """更新 OAuth token 字段。"""
        record = self.session.get(OAuthTokenRecord, token_id)
        if record is None:
            return None
        for field, value in values.items():
            setattr(record, field, value)
        self.session.flush()
        return record

    def delete_oauth_token(self, provider_id: int) -> bool:
        """删除 provider 的 OAuth token，成功时返回 True。"""
        record = self.get_oauth_token(provider_id)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True
