"""FastAPI 应用入口。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.control.router import router as control_router
from app.api.data.errors import data_plane_error
from app.api.data.router import router as data_router
from app.core.config import get_settings
from app.db.migrations import run_migrations
from app.db.session import SessionLocal, engine
from app.repositories.sqlalchemy import SqlRepositories
from app.services.auth import AuthService
from app.services.codex_oauth import CodexOAuthService


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。"""
    settings = get_settings()
    run_migrations(engine, settings)
    bootstrap_key = _bootstrap(settings)

    app = FastAPI(title="jemodel", version="0.1.0")
    app.state.codex_oauth_service = CodexOAuthService(settings)
    # 首次启动自动生成的引导 key，前端一次性展示后清空
    app.state.bootstrap_api_key = bootstrap_key
    if bootstrap_key:
        print("=" * 56, flush=True)
        print(f"  jemodel 首次启动，引导管理员 API key：{bootstrap_key}", flush=True)
        print("  请复制保存，登录管理控制台后此 key 不再展示。", flush=True)
        print("=" * 56, flush=True)
    _register_exception_handlers(app)
    app.include_router(control_router)
    app.include_router(data_router)

    @app.get("/health")
    def health() -> dict:
        """返回 runtime 健康状态。"""
        return {"status": "ok"}

    static_dir = settings.static_dir
    if static_dir and Path(static_dir).exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """注册数据面协议化错误 handler，控制面保持 FastAPI 默认形状。"""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        """将数据面 HTTPException 转为协议匹配错误。"""
        if request.url.path.startswith("/v1/"):
            return data_plane_error(request, str(exc.detail), exc.status_code)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """将数据面 validation error 转为协议匹配错误。"""
        if request.url.path.startswith("/v1/"):
            return data_plane_error(request, "validation_error", 422)
        return JSONResponse(status_code=422, content={"detail": exc.errors()})


def _bootstrap(settings) -> str | None:
    """启动时创建 bootstrap owner，返回自动生成的引导 key（若有）。"""
    session = SessionLocal()
    try:
        repos = SqlRepositories(session)
        raw_key = AuthService(repos, settings).bootstrap_owner()
        session.commit()
        return raw_key
    finally:
        session.close()
