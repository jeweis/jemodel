"""service 和 adapter 之间共享的领域值对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AuthContext:
    """请求通过鉴权后的用户与 API key 上下文。"""

    user_id: int
    api_key_id: int | None
    user_role: str
    scopes: list[str] = field(default_factory=list)
    allowed_models: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RouteSelection:
    """已选择的上游路由目标及其可追踪元数据。"""

    route_target_id: int
    virtual_model: str
    provider_name: str
    provider_protocol: str
    base_url: str
    auth_type: str
    secret_value: str | None
    upstream_model: str
    capabilities: dict[str, Any]
    fallback_trace: list[dict[str, Any]]
    provider_id: int | None = None


@dataclass(frozen=True)
class RequestUsage:
    """从上游响应提取或估算的用量信息。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    usage_source: str = "estimated"


@dataclass(frozen=True)
class HealthEvent:
    """用于更新 provider 和 route target 健康状态的调用结果。"""

    route_target_id: int
    provider_id: int
    ok: bool
    error_code: str | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class OAuthToken:
    """Codex OAuth 凭证领域对象，用于 adapter 层注入。"""

    provider_id: int
    account_id: str | None
    email: str | None
    access_token: str
    refresh_token: str
    id_token: str | None
    expires_at: datetime | None
    status: str

    def is_valid(self, refresh_lead_seconds: int = 0) -> bool:
        """判断 token 是否有效且在 refresh_lead 之外不需要刷新。"""
        if self.status != "active":
            return False
        if self.expires_at is None:
            return True
        from datetime import UTC, timedelta
        from datetime import datetime as _dt

        return _dt.now(UTC) < (self.expires_at - timedelta(seconds=refresh_lead_seconds))
