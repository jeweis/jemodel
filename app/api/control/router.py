"""jemodel 控制面 API。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import (
    auth_service,
    codex_oauth_service,
    current_admin,
    current_control_auth,
    get_settings,
    provider_service,
    repositories,
    routing_service,
    usage_service,
)
from app.api.schemas import (
    ApiKeyCreate,
    ApiKeyUpdate,
    EnabledUpdate,
    LoginRequest,
    ProviderCreate,
    ProviderUpdate,
    RouteTargetCreate,
    RouteTargetUpdate,
    SetupOwnerRequest,
    UpstreamModelCreate,
    UpstreamModelUpdate,
    UserCreate,
    UserUpdate,
    VirtualModelCreate,
    VirtualModelUpdate,
)
from app.core.config import Settings
from app.domain.entities import AuthContext
from app.repositories.sqlalchemy import SqlRepositories
from app.services.auth import AuthError, AuthService
from app.services.codex_oauth import CodexOAuthError, CodexOAuthService
from app.services.providers import ProviderError, ProviderService
from app.services.routing import RoutingError, RoutingService
from app.services.usage import UsageFilters, UsageService

router = APIRouter(prefix="/api", tags=["control"])


@router.get("/health")
def api_health() -> dict:
    """返回控制面健康状态。"""
    return {"status": "ok"}


@router.get("/setup/status")
def setup_status(service: AuthService = Depends(auth_service)) -> dict:
    """返回首访初始化状态，供 Flutter 决定展示 setup 还是 login。"""
    return service.setup_status()


@router.post("/setup")
def setup_owner(
    payload: SetupOwnerRequest,
    service: AuthService = Depends(auth_service),
) -> dict:
    """首次创建 owner、控制台密码和第一把管理 API key。"""
    try:
        return service.setup_owner(payload.name, payload.email, payload.password)
    except AuthError as exc:
        status = 409 if str(exc) == "setup_already_completed" else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.post("/auth/login")
def login(payload: LoginRequest, service: AuthService = Depends(auth_service)) -> dict:
    """控制台账号密码登录。"""
    try:
        return service.login(payload.email, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/users")
def list_users(
    _admin: AuthContext = Depends(current_admin),
    repos: SqlRepositories = Depends(repositories),
) -> list[dict]:
    """列出所有用户。"""
    return [AuthService.public_user(user) for user in repos.list_users()]


@router.post("/users")
def create_user(
    payload: UserCreate,
    _admin: AuthContext = Depends(current_admin),
    service: AuthService = Depends(auth_service),
) -> dict:
    """创建用户。"""
    return service.create_user(payload.name, payload.email, payload.role)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    _admin: AuthContext = Depends(current_admin),
    service: AuthService = Depends(auth_service),
) -> dict:
    """更新用户。"""
    try:
        return service.update_user(user_id, payload.model_dump(exclude_unset=True))
    except AuthError as exc:
        status = 400 if str(exc) == "invalid_role" else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    _admin: AuthContext = Depends(current_admin),
    service: AuthService = Depends(auth_service),
) -> dict:
    """删除没有关联资源的用户。"""
    try:
        return service.delete_user(user_id)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api-keys")
def list_api_keys(
    _admin: AuthContext = Depends(current_admin),
    repos: SqlRepositories = Depends(repositories),
) -> list[dict]:
    """列出所有 API keys，但不返回明文。"""
    return [AuthService.public_api_key(record) for record in repos.list_api_keys()]


@router.post("/api-keys")
def create_api_key(
    payload: ApiKeyCreate,
    _admin: AuthContext = Depends(current_admin),
    service: AuthService = Depends(auth_service),
) -> dict:
    """创建 API key，并只在本次响应返回明文。"""
    return service.create_api_key(
        payload.user_id,
        payload.name,
        payload.scopes,
        payload.allowed_models,
    )


@router.patch("/api-keys/{api_key_id}")
def update_api_key(
    api_key_id: int,
    payload: ApiKeyUpdate,
    _admin: AuthContext = Depends(current_admin),
    service: AuthService = Depends(auth_service),
) -> dict:
    """更新 API key 生命周期和授权范围。"""
    try:
        return service.update_api_key(api_key_id, payload.model_dump(exclude_unset=True))
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/api-keys/{api_key_id}/enabled")
def set_api_key_enabled(
    api_key_id: int,
    payload: EnabledUpdate,
    _admin: AuthContext = Depends(current_admin),
    service: AuthService = Depends(auth_service),
) -> dict:
    """启用或禁用 API key。"""
    try:
        return service.set_api_key_enabled(api_key_id, payload.enabled)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api-keys/{api_key_id}/revoke")
def revoke_api_key(
    api_key_id: int,
    _admin: AuthContext = Depends(current_admin),
    service: AuthService = Depends(auth_service),
) -> dict:
    """撤销 API key。"""
    try:
        return service.revoke_api_key(api_key_id)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/providers")
def list_providers(
    _admin: AuthContext = Depends(current_admin),
    service: ProviderService = Depends(provider_service),
) -> list[dict]:
    """列出已脱敏 providers。"""
    return service.list_providers()


@router.post("/providers")
def create_provider(
    payload: ProviderCreate,
    _admin: AuthContext = Depends(current_admin),
    service: ProviderService = Depends(provider_service),
) -> dict:
    """创建 provider。"""
    try:
        return service.create_provider(payload.model_dump())
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/providers/{provider_id}/enabled")
def set_provider_enabled(
    provider_id: int,
    payload: EnabledUpdate,
    _admin: AuthContext = Depends(current_admin),
    service: ProviderService = Depends(provider_service),
) -> dict:
    """启用或禁用 provider。"""
    try:
        return service.set_provider_enabled(provider_id, payload.enabled)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/providers/{provider_id}")
def update_provider(
    provider_id: int,
    payload: ProviderUpdate,
    _admin: AuthContext = Depends(current_admin),
    service: ProviderService = Depends(provider_service),
) -> dict:
    """更新 provider。"""
    try:
        return service.update_provider(provider_id, payload.model_dump(exclude_unset=True))
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/providers/{provider_id}")
def delete_provider(
    provider_id: int,
    _admin: AuthContext = Depends(current_admin),
    service: ProviderService = Depends(provider_service),
) -> dict:
    """删除 provider。"""
    try:
        return service.delete_provider(provider_id)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/providers/{provider_id}/codex/oauth/start")
async def codex_oauth_start(
    provider_id: int,
    _admin: AuthContext = Depends(current_admin),
    service: CodexOAuthService = Depends(codex_oauth_service),
) -> dict:
    """启动 Codex OAuth device code flow。"""
    try:
        return await service.start_device_flow(provider_id)
    except CodexOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"oauth_start_failed: {exc}") from exc


@router.get("/providers/{provider_id}/codex/oauth/status")
def codex_oauth_status(
    provider_id: int,
    state: str,
    _admin: AuthContext = Depends(current_admin),
    service: CodexOAuthService = Depends(codex_oauth_service),
) -> dict:
    """查询 Codex OAuth 登录状态。"""
    return service.get_status(state)


@router.post("/providers/{provider_id}/codex/oauth/refresh")
async def codex_oauth_refresh(
    provider_id: int,
    _admin: AuthContext = Depends(current_admin),
    service: CodexOAuthService = Depends(codex_oauth_service),
    repos: SqlRepositories = Depends(repositories),
) -> dict:
    """手动刷新 Codex OAuth token。"""
    try:
        return await service.refresh_token(repos, provider_id)
    except CodexOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/providers/{provider_id}/codex/oauth")
def codex_oauth_revoke(
    provider_id: int,
    _admin: AuthContext = Depends(current_admin),
    service: CodexOAuthService = Depends(codex_oauth_service),
    repos: SqlRepositories = Depends(repositories),
) -> dict:
    """撤销 Codex OAuth token。"""
    return service.revoke(repos, provider_id)


@router.get("/upstream-models")
def list_upstream_models(
    _admin: AuthContext = Depends(current_admin),
    service: ProviderService = Depends(provider_service),
) -> list[dict]:
    """列出上游模型目标。"""
    return service.list_upstream_models()


@router.post("/upstream-models")
def create_upstream_model(
    payload: UpstreamModelCreate,
    _admin: AuthContext = Depends(current_admin),
    service: ProviderService = Depends(provider_service),
) -> dict:
    """创建上游模型目标。"""
    return service.create_upstream_model(payload.model_dump())


@router.patch("/upstream-models/{model_id}")
def update_upstream_model(
    model_id: int,
    payload: UpstreamModelUpdate,
    _admin: AuthContext = Depends(current_admin),
    service: ProviderService = Depends(provider_service),
) -> dict:
    """更新上游模型目标。"""
    try:
        return service.update_upstream_model(model_id, payload.model_dump(exclude_unset=True))
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/upstream-models/{model_id}")
def delete_upstream_model(
    model_id: int,
    _admin: AuthContext = Depends(current_admin),
    service: ProviderService = Depends(provider_service),
) -> dict:
    """删除上游模型目标。"""
    try:
        return service.delete_upstream_model(model_id)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/virtual-models")
def list_virtual_models(
    _admin: AuthContext = Depends(current_admin),
    service: RoutingService = Depends(routing_service),
) -> list[dict]:
    """列出虚拟模型。"""
    return service.list_virtual_models()


@router.post("/virtual-models")
def create_virtual_model(
    payload: VirtualModelCreate,
    _admin: AuthContext = Depends(current_admin),
    service: RoutingService = Depends(routing_service),
) -> dict:
    """创建虚拟模型。"""
    return service.create_virtual_model(payload.model_dump())


@router.patch("/virtual-models/{model_id}")
def update_virtual_model(
    model_id: int,
    payload: VirtualModelUpdate,
    _admin: AuthContext = Depends(current_admin),
    service: RoutingService = Depends(routing_service),
) -> dict:
    """更新 virtual model。"""
    try:
        return service.update_virtual_model(model_id, payload.model_dump(exclude_unset=True))
    except RoutingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/virtual-models/{model_id}/enabled")
def set_virtual_model_enabled(
    model_id: int,
    payload: EnabledUpdate,
    _admin: AuthContext = Depends(current_admin),
    service: RoutingService = Depends(routing_service),
) -> dict:
    """启用或禁用 virtual model。"""
    try:
        return service.set_virtual_model_enabled(model_id, payload.enabled)
    except RoutingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/virtual-models/{model_id}")
def delete_virtual_model(
    model_id: int,
    _admin: AuthContext = Depends(current_admin),
    service: RoutingService = Depends(routing_service),
) -> dict:
    """删除 virtual model。"""
    try:
        return service.delete_virtual_model(model_id)
    except RoutingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/route-targets")
def create_route_target(
    payload: RouteTargetCreate,
    _admin: AuthContext = Depends(current_admin),
    service: RoutingService = Depends(routing_service),
) -> dict:
    """创建路由目标。"""
    return service.create_route_target(payload.model_dump())


@router.patch("/route-targets/{target_id}")
def update_route_target(
    target_id: int,
    payload: RouteTargetUpdate,
    _admin: AuthContext = Depends(current_admin),
    service: RoutingService = Depends(routing_service),
) -> dict:
    """更新 route target。"""
    try:
        return service.update_route_target(target_id, payload.model_dump(exclude_unset=True))
    except RoutingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/route-targets/{target_id}/enabled")
def set_route_target_enabled(
    target_id: int,
    payload: EnabledUpdate,
    _admin: AuthContext = Depends(current_admin),
    service: RoutingService = Depends(routing_service),
) -> dict:
    """启用或禁用 route target。"""
    try:
        return service.set_route_target_enabled(target_id, payload.enabled)
    except RoutingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/route-targets/{target_id}")
def delete_route_target(
    target_id: int,
    _admin: AuthContext = Depends(current_admin),
    service: RoutingService = Depends(routing_service),
) -> dict:
    """删除 route target。"""
    try:
        return service.delete_route_target(target_id)
    except RoutingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/route-targets/{virtual_model}")
def list_route_targets(
    virtual_model: str,
    _admin: AuthContext = Depends(current_admin),
    repos: SqlRepositories = Depends(repositories),
) -> list[dict]:
    """按 virtual model 列出路由目标。"""
    return [
        RoutingService.public_route_target(record)
        for record in repos.list_route_targets(virtual_model)
    ]


@router.get("/usage")
def usage_summary(
    group_by: str = "user",
    user_id: int | None = None,
    api_key_id: int | None = None,
    virtual_model: str | None = None,
    provider: str | None = None,
    upstream_model: str | None = None,
    status: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    context: AuthContext = Depends(current_control_auth),
    service: UsageService = Depends(usage_service),
) -> list[dict]:
    """按指定维度返回 token 用量聚合。"""
    filters = UsageFilters(
        user_id=user_id,
        api_key_id=api_key_id,
        virtual_model=virtual_model,
        provider=provider,
        upstream_model=upstream_model,
        status=status,
        start_time=start_time,
        end_time=end_time,
    )
    return service.aggregate_usage(group_by, filters, context)


@router.get("/settings")
def settings_summary(
    _admin: AuthContext = Depends(current_admin),
    settings: Settings = Depends(get_settings),
) -> dict:
    """返回不含 secret 的 runtime settings 摘要。"""
    database_kind = settings.database_url.split(":", maxsplit=1)[0]
    return {
        "database": database_kind,
        "mock_upstreams": settings.mock_upstreams,
        "experimental_codex_oauth": settings.experimental_codex_oauth,
        "static_dir": str(settings.static_dir) if settings.static_dir else None,
        "bootstrap_admin_email": settings.bootstrap_admin_email,
    }


@router.get("/logs")
def request_logs(
    _admin: AuthContext = Depends(current_admin),
    repos: SqlRepositories = Depends(repositories),
) -> list[dict]:
    """返回脱敏请求日志。"""
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "api_key_id": row.api_key_id,
            "protocol_in": row.protocol_in,
            "protocol_out": row.protocol_out,
            "virtual_model": row.virtual_model,
            "provider_name": row.provider_name,
            "upstream_model": row.upstream_model,
            "status": row.status,
            "error_code": row.error_code,
            "latency_ms": row.latency_ms,
            "fallback_trace": row.fallback_trace,
            "created_at": row.created_at,
        }
        for row in repos.list_logs()
    ]
