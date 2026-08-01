"""让 service 不依赖 SQLite 细节的 repository 协议。"""

from __future__ import annotations

from typing import Any, Protocol

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


class AuthRepository(Protocol):
    """鉴权 service 依赖的最小持久化契约。"""

    def list_users(self) -> list[Any]: ...

    def get_user(self, user_id: int) -> Any | None: ...

    def find_user_by_email(self, email: str) -> Any | None: ...

    def create_user(
        self,
        name: str,
        email: str,
        role: str,
        password_hash: str | None = None,
    ) -> Any: ...

    def delete_user(self, user_id: int) -> bool: ...

    def list_api_keys(self) -> list[Any]: ...

    def get_api_key(self, api_key_id: int) -> Any | None: ...

    def create_api_key(
        self,
        user_id: int,
        name: str,
        key_hash: str,
        prefix: str,
        scopes: list[str],
        allowed_models: list[str],
    ) -> Any: ...


class UserRepository(Protocol):
    """users 和 API keys 的持久化契约。"""

    def list_users(self) -> list[UserRecord]: ...

    def get_user(self, user_id: int) -> UserRecord | None: ...

    def find_user_by_email(self, email: str) -> UserRecord | None: ...

    def create_user(
        self,
        name: str,
        email: str,
        role: str,
        password_hash: str | None = None,
    ) -> UserRecord: ...

    def list_api_keys(self, user_id: int | None = None) -> list[ApiKeyRecord]: ...

    def get_api_key(self, api_key_id: int) -> ApiKeyRecord | None: ...

    def create_api_key(
        self,
        user_id: int,
        name: str,
        key_hash: str,
        prefix: str,
        scopes: list[str],
        allowed_models: list[str],
    ) -> ApiKeyRecord: ...


class ProviderRepository(Protocol):
    """providers 和 upstream models 的持久化契约。"""

    def list_providers(self) -> list[ProviderRecord]: ...

    def create_provider(self, values: dict) -> ProviderRecord: ...

    def list_upstream_models(self) -> list[UpstreamModelRecord]: ...

    def create_upstream_model(self, values: dict) -> UpstreamModelRecord: ...


class RoutingRepository(Protocol):
    """virtual models 和 route targets 的持久化契约。"""

    def list_virtual_models(self) -> list[VirtualModelRecord]: ...

    def create_virtual_model(self, values: dict) -> VirtualModelRecord: ...

    def list_route_targets(self, virtual_model: str) -> list[RouteTargetRecord]: ...

    def create_route_target(self, values: dict) -> RouteTargetRecord: ...


class UsageRepository(Protocol):
    """usage 和 request logs 的持久化契约。"""

    def create_usage(self, values: dict) -> UsageLedgerRecord: ...

    def create_request_log(self, values: dict) -> RequestLogRecord: ...

    def list_usage(self) -> list[UsageLedgerRecord]: ...

    def list_logs(self) -> list[RequestLogRecord]: ...


class OAuthTokenRepository(Protocol):
    """Codex OAuth 凭证的持久化契约。"""

    def get_oauth_token(self, provider_id: int) -> OAuthTokenRecord | None: ...

    def save_oauth_token(self, values: dict) -> OAuthTokenRecord: ...

    def update_oauth_token(self, token_id: int, values: dict) -> OAuthTokenRecord | None: ...

    def delete_oauth_token(self, provider_id: int) -> bool: ...
