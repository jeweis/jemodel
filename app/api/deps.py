"""FastAPI 依赖注入工具。"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.adapters.litellm_adapter import UpstreamAdapter
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.domain.entities import AuthContext
from app.repositories.sqlalchemy import SqlRepositories
from app.services.auth import AuthError, AuthService
from app.services.codex_oauth import CodexOAuthService
from app.services.providers import ProviderService
from app.services.routing import RoutingService
from app.services.usage import UsageService


def repositories(session: Session = Depends(get_session)) -> SqlRepositories:
    """为当前请求创建 repository 集合。"""
    return SqlRepositories(session)


def auth_service(
    repos: SqlRepositories = Depends(repositories),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    """创建鉴权 service。"""
    return AuthService(repos, settings)


def provider_service(
    repos: SqlRepositories = Depends(repositories),
    settings: Settings = Depends(get_settings),
) -> ProviderService:
    """创建 provider 管理 service。"""
    return ProviderService(repos, settings)


def routing_service(repos: SqlRepositories = Depends(repositories)) -> RoutingService:
    """创建路由 service。"""
    return RoutingService(repos)


def usage_service(repos: SqlRepositories = Depends(repositories)) -> UsageService:
    """创建用量 service。"""
    return UsageService(repos)


def upstream_adapter(settings: Settings = Depends(get_settings)) -> UpstreamAdapter:
    """创建上游模型 adapter。"""
    return UpstreamAdapter(settings)


def codex_oauth_service(request: Request) -> CodexOAuthService:
    """返回 app.state 上的进程级 Codex OAuth 单例（跨请求共享 session store）。"""
    return request.app.state.codex_oauth_service


def bearer_token(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> str | None:
    """从 Authorization bearer 或 x-api-key header 中提取 key。"""
    if x_api_key:
        return x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return None


def current_auth(
    token: str | None = Depends(bearer_token),
    service: AuthService = Depends(auth_service),
) -> AuthContext:
    """鉴权当前请求并返回 AuthContext。"""
    try:
        return service.authenticate(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def current_control_auth(
    token: str | None = Depends(bearer_token),
    service: AuthService = Depends(auth_service),
) -> AuthContext:
    """控制面支持账号会话 token，也兼容 bootstrap/admin API key。"""
    try:
        if token and token.startswith("jms_"):
            return service.authenticate_session(token)
        return service.authenticate(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def current_model_auth(
    context: AuthContext = Depends(current_auth),
    service: AuthService = Depends(auth_service),
) -> AuthContext:
    """要求当前 API key 具备模型调用 scope。"""
    try:
        service.require_scope(context, "models")
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return context


def current_admin(
    context: AuthContext = Depends(current_control_auth),
    service: AuthService = Depends(auth_service),
) -> AuthContext:
    """要求当前请求来自 owner/admin。"""
    try:
        service.require_admin(context)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return context
