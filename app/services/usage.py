"""用量账本和请求日志 service。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain.entities import AuthContext, RequestUsage, RouteSelection
from app.repositories.sqlalchemy import SqlRepositories


class UsageService:
    """记录和聚合用量，但不记录 prompt body。"""

    def __init__(self, repos: SqlRepositories) -> None:
        """初始化 usage service。"""
        self.repos = repos

    def record_success(
        self,
        context: AuthContext,
        route: RouteSelection,
        usage: RequestUsage,
        latency_ms: int,
    ) -> None:
        """为成功请求写入 usage 和 request log。"""
        values = self._base_values(context, route, latency_ms, "success", None)
        self.repos.create_usage({**values, **usage.__dict__})
        self.repos.create_request_log(self._log_values(route, context, latency_ms, "success", None))

    def record_failure(
        self,
        context: AuthContext | None,
        model: str | None,
        protocol: str,
        latency_ms: int,
        error_code: str,
        route: RouteSelection | None = None,
    ) -> None:
        """鉴权成功后，为失败请求写入脱敏 log 和 usage。"""
        if context:
            values = self._base_values(context, route, latency_ms, "error", error_code)
            self.repos.create_usage(values)
        self.repos.create_request_log(
            {
                "user_id": context.user_id if context else None,
                "api_key_id": context.api_key_id if context else None,
                "protocol_in": protocol,
                "protocol_out": route.provider_protocol if route else None,
                "virtual_model": route.virtual_model if route else model,
                "provider_name": route.provider_name if route else None,
                "upstream_model": route.upstream_model if route else None,
                "status": "error",
                "error_code": error_code,
                "latency_ms": latency_ms,
                "fallback_trace": route.fallback_trace if route else [],
            }
        )

    def aggregate_usage(
        self,
        group_by: str,
        filters: UsageFilters | None = None,
        context: AuthContext | None = None,
    ) -> list[dict[str, Any]]:
        """按支持的维度聚合 token totals。"""
        key_map = {
            "user": "user_id",
            "api_key": "api_key_id",
            "virtual_model": "virtual_model",
            "provider": "provider_name",
            "upstream_model": "upstream_model",
            "status": "status",
        }
        attr = key_map.get(group_by, "user_id")
        totals: dict[Any, dict[str, int]] = defaultdict(
            lambda: {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
            }
        )
        for row in self.repos.list_usage():
            if not self._can_view_usage(row, context) or not self._matches_filters(row, filters):
                continue
            key = getattr(row, attr)
            totals[key]["input_tokens"] += row.input_tokens
            totals[key]["output_tokens"] += row.output_tokens
            totals[key]["cached_tokens"] += row.cached_tokens
            totals[key]["reasoning_tokens"] += row.reasoning_tokens
            totals[key]["total_tokens"] += row.total_tokens
        return [{"group": key, **values} for key, values in totals.items()]

    @staticmethod
    def _can_view_usage(row, context: AuthContext | None) -> bool:
        """限制普通用户只能看到自己产生的 usage。"""
        if context is None or context.user_role in {"owner", "admin"}:
            return True
        return row.user_id == context.user_id

    @staticmethod
    def _matches_filters(row, filters: UsageFilters | None) -> bool:
        """按查询参数过滤 usage row。"""
        if filters is None:
            return True
        checks = {
            "user_id": row.user_id,
            "api_key_id": row.api_key_id,
            "virtual_model": row.virtual_model,
            "provider": row.provider_name,
            "upstream_model": row.upstream_model,
            "status": row.status,
        }
        for field, actual in checks.items():
            expected = getattr(filters, field)
            if expected is not None and actual != expected:
                return False
        created_at = UsageService._comparable_time(row.created_at)
        if filters.start_time and created_at < UsageService._comparable_time(filters.start_time):
            return False
        return not (
            filters.end_time and created_at > UsageService._comparable_time(filters.end_time)
        )

    @staticmethod
    def _comparable_time(value: datetime) -> datetime:
        """统一时间为 UTC naive，兼容 SQLite 读取 timezone 丢失。"""
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    @staticmethod
    def _base_values(
        context: AuthContext,
        route: RouteSelection | None,
        latency_ms: int,
        status: str,
        error_code: str | None,
    ) -> dict:
        """根据请求上下文和路由构建共享 usage 字段。"""
        return {
            "user_id": context.user_id,
            "api_key_id": context.api_key_id,
            "virtual_model": route.virtual_model if route else None,
            "provider_name": route.provider_name if route else None,
            "upstream_model": route.upstream_model if route else None,
            "status": status,
            "error_code": error_code,
            "latency_ms": latency_ms,
            "fallback_trace": route.fallback_trace if route else [],
        }

    @staticmethod
    def _log_values(
        route: RouteSelection,
        context: AuthContext,
        latency_ms: int,
        status: str,
        error_code: str | None,
    ) -> dict:
        """构建 request log 字段，不保存 prompt 或 secret 内容。"""
        return {
            "user_id": context.user_id,
            "api_key_id": context.api_key_id,
            "protocol_in": "model",
            "protocol_out": route.provider_protocol,
            "virtual_model": route.virtual_model,
            "provider_name": route.provider_name,
            "upstream_model": route.upstream_model,
            "status": status,
            "error_code": error_code,
            "latency_ms": latency_ms,
            "fallback_trace": route.fallback_trace,
        }


@dataclass(frozen=True)
class UsageFilters:
    """控制面 usage 查询过滤条件。"""

    user_id: int | None = None
    api_key_id: int | None = None
    virtual_model: str | None = None
    provider: str | None = None
    upstream_model: str | None = None
    status: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
