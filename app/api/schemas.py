"""控制面和数据面共享的 Pydantic schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    """创建用户请求。"""

    name: str
    email: str
    role: str = "member"


class UserUpdate(BaseModel):
    """更新用户请求。"""

    name: str | None = None
    email: str | None = None
    role: str | None = None
    enabled: bool | None = None


class SetupOwnerRequest(BaseModel):
    """首次进入控制台时创建 owner 的请求。"""

    name: str
    email: str
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    """控制台账号密码登录请求。"""

    email: str
    password: str


class ApiKeyCreate(BaseModel):
    """创建 API key 请求。"""

    user_id: int
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["models"])
    allowed_models: list[str] = Field(default_factory=list)


class ApiKeyUpdate(BaseModel):
    """更新 API key 生命周期与授权范围请求。"""

    enabled: bool | None = None
    expires_at: datetime | None = None
    scopes: list[str] | None = None
    allowed_models: list[str] | None = None


class EnabledUpdate(BaseModel):
    """启用或禁用资源的通用请求。"""

    enabled: bool


class ProviderCreate(BaseModel):
    """创建 provider 请求。"""

    name: str
    protocol: str
    base_url: str
    auth_type: str = "api_key"
    secret_value: str | None = None
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderUpdate(BaseModel):
    """更新 provider 请求，未传字段保持不变。"""

    name: str | None = None
    protocol: str | None = None
    base_url: str | None = None
    auth_type: str | None = None
    secret_value: str | None = None
    enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class UpstreamModelCreate(BaseModel):
    """创建上游模型请求。"""

    provider_id: int
    model_name: str
    display_name: str
    capabilities: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class UpstreamModelUpdate(BaseModel):
    """更新上游模型请求。"""

    provider_id: int | None = None
    model_name: str | None = None
    display_name: str | None = None
    capabilities: dict[str, Any] | None = None
    enabled: bool | None = None


class VirtualModelCreate(BaseModel):
    """创建虚拟模型请求。"""

    name: str
    description: str = ""
    enabled: bool = True


class VirtualModelUpdate(BaseModel):
    """更新虚拟模型请求。"""

    name: str | None = None
    description: str | None = None
    enabled: bool | None = None


class RouteTargetCreate(BaseModel):
    """创建路由目标请求。"""

    virtual_model_id: int
    upstream_model_id: int
    priority: int = 100
    weight: int = 1
    enabled: bool = True
    cooldown_seconds: int = 60
    capabilities: dict[str, Any] = Field(default_factory=dict)


class RouteTargetUpdate(BaseModel):
    """更新路由目标请求。"""

    virtual_model_id: int | None = None
    upstream_model_id: int | None = None
    priority: int | None = None
    weight: int | None = None
    enabled: bool | None = None
    cooldown_seconds: int | None = None
    capabilities: dict[str, Any] | None = None


class OpenAIChatRequest(BaseModel):
    """OpenAI-compatible chat completions 请求。

    覆盖 OpenAI SDK 常用字段；未建模字段（logprobs/n/user 等）由 Pydantic 忽略。
    """

    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: Any = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: Any = None
    parallel_tool_calls: bool | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    stream_options: dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None


class AnthropicMessageRequest(BaseModel):
    """Anthropic-compatible messages 请求。

    Claude Code 等客户端会发送顶层 system、stop_sequences、tool_choice、
    metadata、thinking 等扩展字段；未建模字段由 Pydantic 默认忽略。
    """

    model: str
    messages: list[dict[str, Any]]
    max_tokens: int | None = None
    stream: bool = False
    temperature: float | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    system: Any = None
    stop_sequences: list[str] | None = None
    tool_choice: Any = None


class CodexOAuthStartResponse(BaseModel):
    """启动 device code flow 的响应。"""

    user_code: str
    verification_uri: str
    state: str
    expires_in: int
    interval: int


class CodexOAuthStatusResponse(BaseModel):
    """查询 device code flow 状态的响应。"""

    status: str  # wait / ok / error
    error: str | None = None
    email: str | None = None
    expires_at: str | None = None


class CodexOAuthRefreshResponse(BaseModel):
    """手动刷新 token 的响应。"""

    status: str
    expires_at: str | None = None


class CodexOAuthRevokeResponse(BaseModel):
    """撤销 token 的响应。"""

    revoked: bool
