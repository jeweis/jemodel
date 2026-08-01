"""数据面协议相关错误响应工具。"""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.requests import Request
from fastapi.responses import JSONResponse


def openai_error(message: str, status_code: int = 400) -> JSONResponse:
    """返回 OpenAI-compatible error response。"""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": "jemodel_error", "code": message}},
    )


def anthropic_error(message: str, status_code: int = 400) -> JSONResponse:
    """返回 Anthropic-compatible error response。"""
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": "jemodel_error", "message": message}},
    )


def status_from_exception(exc: Exception) -> int:
    """根据异常类型粗略映射 HTTP 状态码。"""
    if isinstance(exc, HTTPException):
        return exc.status_code
    code = str(exc)
    if "api_key" in code or "admin_required" in code:
        return 401
    if "model_not_allowed" in code:
        return 403
    if "unknown_model" in code or "no_eligible_target" in code:
        return 404
    return 400


def data_plane_error(request: Request, message: str, status_code: int) -> JSONResponse:
    """按数据面入口路径选择 OpenAI 或 Anthropic error 形状。"""
    if _is_anthropic_path(request.url.path):
        return anthropic_error(message, status_code)
    return openai_error(message, status_code)


def _is_anthropic_path(path: str) -> bool:
    """判断请求路径是否属于 Anthropic-compatible 数据面。"""
    return path.startswith("/v1/messages") or path.startswith("/v1/anthropic/")
