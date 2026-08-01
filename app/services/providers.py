"""Provider 和 upstream model 管理 service。"""

from __future__ import annotations

from app.core.config import Settings
from app.repositories.sqlalchemy import SqlRepositories


class ProviderError(Exception):
    """Provider 配置无效时抛出。"""


class ProviderService:
    """控制面 provider 操作，所有响应都进行 secret 脱敏。"""

    def __init__(self, repos: SqlRepositories, settings: Settings) -> None:
        """初始化 provider service。"""
        self.repos = repos
        self.settings = settings

    def create_provider(self, values: dict) -> dict:
        """在检查实验性 credential 策略后创建 provider。"""
        values = self._normalized_provider_values(values)
        record = self.repos.create_provider(values)
        return self.public_provider(record)

    def set_provider_enabled(self, provider_id: int, enabled: bool) -> dict:
        """启用或禁用 provider。"""
        record = self.repos.get_provider(provider_id)
        if record is None:
            raise ProviderError("provider_not_found")
        record.enabled = enabled
        return self.public_provider(record)

    def update_provider(self, provider_id: int, values: dict) -> dict:
        """更新 provider 配置，secret 仅在显式传入时覆盖。"""
        record = self.repos.get_provider(provider_id)
        if record is None:
            raise ProviderError("provider_not_found")
        values = self._normalized_provider_values(values, existing_auth_type=record.auth_type)
        for field, value in values.items():
            if value is not None:
                setattr(record, field, value)
        return self.public_provider(record)

    def delete_provider(self, provider_id: int) -> dict:
        """删除 provider；有关联资源时由数据库约束阻止。"""
        if not self.repos.delete_provider(provider_id):
            raise ProviderError("provider_not_found")
        return {"deleted": True}

    def create_upstream_model(self, values: dict) -> dict:
        """创建 upstream model target。"""
        record = self.repos.create_upstream_model(values)
        return self.public_upstream_model(record)

    def update_upstream_model(self, model_id: int, values: dict) -> dict:
        """更新 upstream model target。"""
        record = self.repos.get_upstream_model(model_id)
        if record is None:
            raise ProviderError("upstream_model_not_found")
        for field, value in values.items():
            if value is not None:
                setattr(record, field, value)
        return self.public_upstream_model(record)

    def delete_upstream_model(self, model_id: int) -> dict:
        """删除 upstream model target。"""
        if not self.repos.delete_upstream_model(model_id):
            raise ProviderError("upstream_model_not_found")
        return {"deleted": True}

    def list_providers(self) -> list[dict]:
        """返回已脱敏的 providers。"""
        return [self.public_provider(record) for record in self.repos.list_providers()]

    def list_upstream_models(self) -> list[dict]:
        """返回 upstream model target 元数据。"""
        return [self.public_upstream_model(record) for record in self.repos.list_upstream_models()]

    def _normalized_provider_values(
        self,
        values: dict,
        existing_auth_type: str | None = None,
    ) -> dict:
        """规范化 provider 字段，并隔离实验性 credential 类型。"""
        auth_type = values.get("auth_type") or existing_auth_type or "api_key"
        if "metadata" in values:
            values["metadata_json"] = values.pop("metadata") or {}
        metadata = values.setdefault("metadata_json", {})
        if auth_type != "codex_oauth":
            return values
        if not self.settings.experimental_codex_oauth:
            raise ProviderError("experimental_codex_oauth_disabled")
        values["auth_type"] = "codex_oauth"
        values.setdefault("health_status", "experimental")
        metadata["credential_type"] = "codex_oauth"
        metadata["experimental"] = True
        return values

    def public_provider(self, record) -> dict:
        """返回 provider 字段，但不暴露原始 secret；codex_oauth 附带 oauth_status。"""
        metadata = record.metadata_json or {}
        oauth_status = None
        if record.auth_type == "codex_oauth":
            token = self.repos.get_oauth_token(record.id)
            if token is not None:
                oauth_status = {
                    "status": token.status,
                    "email": token.email,
                    "expires_at": token.expires_at.isoformat() if token.expires_at else None,
                    "last_error": token.last_error,
                }
        return {
            "id": record.id,
            "name": record.name,
            "protocol": record.protocol,
            "base_url": record.base_url,
            "auth_type": record.auth_type,
            "enabled": record.enabled,
            "metadata": metadata,
            "health_status": record.health_status,
            "last_error": record.last_error,
            "secret_present": bool(record.secret_value),
            "experimental": bool(metadata.get("experimental")),
            "oauth_status": oauth_status,
        }

    @staticmethod
    def public_upstream_model(record) -> dict:
        """返回适合管理页面展示的 upstream model 字段。"""
        return {
            "id": record.id,
            "provider_id": record.provider_id,
            "model_name": record.model_name,
            "display_name": record.display_name,
            "capabilities": record.capabilities,
            "enabled": record.enabled,
        }
