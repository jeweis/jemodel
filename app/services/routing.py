"""Virtual model 路由选择和健康状态行为。"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from app.db.models import RouteTargetRecord
from app.domain.entities import RouteSelection
from app.repositories.sqlalchemy import SqlRepositories


def _as_aware_utc(value: datetime | None) -> datetime | None:
    """把 naive datetime 当作 UTC 并附加时区，避免与 aware datetime 比较报错。

    SQLite 不保留时区信息，SQLAlchemy 读回的 datetime 是 naive；写入时用的都是
    datetime.now(UTC)，因此这里统一补上 UTC 时区再比较。
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class RoutingError(Exception):
    """没有 route target 能满足请求时抛出。"""


class RoutingService:
    """为 virtual model 请求选择上游 target。"""

    def __init__(self, repos: SqlRepositories) -> None:
        """初始化 routing service。"""
        self.repos = repos

    def create_virtual_model(self, values: dict) -> dict:
        """创建对 client 可见的 virtual model。"""
        record = self.repos.create_virtual_model(values)
        return self.public_virtual_model(record)

    def set_virtual_model_enabled(self, model_id: int, enabled: bool) -> dict:
        """启用或禁用 virtual model。"""
        record = self.repos.get_virtual_model_by_id(model_id)
        if record is None:
            raise RoutingError("virtual_model_not_found")
        record.enabled = enabled
        return self.public_virtual_model(record)

    def update_virtual_model(self, model_id: int, values: dict) -> dict:
        """更新 virtual model 元数据和 enabled 状态。"""
        record = self.repos.get_virtual_model_by_id(model_id)
        if record is None:
            raise RoutingError("virtual_model_not_found")
        for field, value in values.items():
            if value is not None:
                setattr(record, field, value)
        return self.public_virtual_model(record)

    def delete_virtual_model(self, model_id: int) -> dict:
        """删除 virtual model。"""
        if not self.repos.delete_virtual_model(model_id):
            raise RoutingError("virtual_model_not_found")
        return {"deleted": True}

    def create_route_target(self, values: dict) -> dict:
        """为 virtual model 创建 route target。"""
        record = self.repos.create_route_target(values)
        return self.public_route_target(record)

    def set_route_target_enabled(self, target_id: int, enabled: bool) -> dict:
        """启用或禁用 route target。"""
        record = self.repos.get_route_target(target_id)
        if record is None:
            raise RoutingError("route_target_not_found")
        record.enabled = enabled
        return self.public_route_target(record)

    def update_route_target(self, target_id: int, values: dict) -> dict:
        """更新 route target 的路由权重、优先级和能力声明。"""
        record = self.repos.get_route_target(target_id)
        if record is None:
            raise RoutingError("route_target_not_found")
        for field, value in values.items():
            if value is not None:
                setattr(record, field, value)
        return self.public_route_target(record)

    def delete_route_target(self, target_id: int) -> dict:
        """删除 route target。"""
        if not self.repos.delete_route_target(target_id):
            raise RoutingError("route_target_not_found")
        return {"deleted": True}

    def list_virtual_models(self) -> list[dict]:
        """返回可用于 discovery 和管理的 virtual models。"""
        return [self.public_virtual_model(record) for record in self.repos.list_virtual_models()]

    def select(self, model: str, required: set[str]) -> RouteSelection:
        """按 priority、capability、cooldown 和 weight 选择 target。"""
        virtual = self.repos.get_virtual_model(model)
        if virtual is None or not virtual.enabled:
            raise RoutingError("unknown_model")
        candidates = self._eligible_targets(model, required)
        if not candidates:
            raise RoutingError("no_eligible_target")
        top_priority = min(target.priority for target in candidates)
        priority_targets = [target for target in candidates if target.priority == top_priority]
        chosen = self._weighted_choice(priority_targets)
        upstream = chosen.upstream_model
        provider = upstream.provider
        return RouteSelection(
            route_target_id=chosen.id,
            virtual_model=model,
            provider_name=provider.name,
            provider_protocol=provider.protocol,
            base_url=provider.base_url,
            auth_type=provider.auth_type,
            secret_value=provider.secret_value,
            upstream_model=upstream.model_name,
            capabilities={**upstream.capabilities, **chosen.capabilities},
            fallback_trace=self._trace(priority_targets, chosen.id),
            provider_id=provider.id,
        )

    def mark_failure(self, route_target_id: int, error_code: str) -> None:
        """记录 target 失败，并应用简单 cooldown 行为。"""
        target = self.repos.session.get(RouteTargetRecord, route_target_id)
        if target is None:
            return
        target.failure_count += 1
        target.cooldown_until = datetime.now(UTC) + timedelta(seconds=target.cooldown_seconds)
        target.upstream_model.provider.health_status = "degraded"
        target.upstream_model.provider.last_error = error_code

    def _eligible_targets(self, model: str, required: set[str]):
        """按健康状态、enabled 状态和 capabilities 过滤 route targets。"""
        now = datetime.now(UTC)
        eligible = []
        for target in self.repos.list_route_targets(model):
            provider = target.upstream_model.provider
            capabilities = {**target.upstream_model.capabilities, **target.capabilities}
            cooldown_until_aware = _as_aware_utc(target.cooldown_until)
            cooldown = cooldown_until_aware is not None and cooldown_until_aware > now
            if not target.enabled or not target.upstream_model.enabled or not provider.enabled:
                continue
            if cooldown or not required.issubset(self._supported_capabilities(capabilities)):
                continue
            eligible.append(target)
        return eligible

    @staticmethod
    def _supported_capabilities(capabilities: dict) -> set[str]:
        """返回 target 明确支持的 capability 名称集合。"""
        return {name for name, enabled in capabilities.items() if enabled is True}

    @staticmethod
    def _weighted_choice(targets):
        """按正整数权重选择 target。"""
        weighted = [(target, max(0, target.weight)) for target in targets]
        total = sum(weight for _, weight in weighted)
        if total <= 0:
            return targets[0]
        choices = [target for target, _ in weighted]
        weights = [weight for _, weight in weighted]
        return random.choices(choices, weights)[0]

    @staticmethod
    def _trace(targets, chosen_id: int) -> list[dict]:
        """为 logs 和 usage 构建已脱敏 fallback trace。"""
        return [
            {
                "route_target_id": target.id,
                "provider": target.upstream_model.provider.name,
                "model": target.upstream_model.model_name,
                "selected": target.id == chosen_id,
            }
            for target in targets
        ]

    @staticmethod
    def public_virtual_model(record) -> dict:
        """返回 virtual model 字段。"""
        return {
            "id": record.id,
            "name": record.name,
            "description": record.description,
            "enabled": record.enabled,
        }

    @staticmethod
    def public_route_target(record) -> dict:
        """返回 route target 字段。"""
        return {
            "id": record.id,
            "virtual_model_id": record.virtual_model_id,
            "upstream_model_id": record.upstream_model_id,
            "priority": record.priority,
            "weight": record.weight,
            "enabled": record.enabled,
            "cooldown_seconds": record.cooldown_seconds,
            "capabilities": record.capabilities,
        }
