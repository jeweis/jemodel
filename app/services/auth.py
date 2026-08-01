"""鉴权、授权和首次启动初始化 service。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import Settings
from app.core.security import (
    create_api_key,
    create_session_token,
    hash_api_key,
    hash_password,
    key_prefix,
    verify_api_key,
    verify_password,
    verify_session_token,
)
from app.domain.entities import AuthContext
from app.repositories.interfaces import AuthRepository


class AuthError(Exception):
    """请求鉴权或授权失败时抛出。"""


class AuthService:
    """独立于 HTTP handler 的用户与 API key 业务操作。"""

    def __init__(self, repos: AuthRepository, settings: Settings) -> None:
        """注入 repository 集合和运行时配置。"""
        self.repos = repos
        self.settings = settings

    def bootstrap_owner(self) -> None:
        """存在 bootstrap key 时创建首个 owner；否则等待 UI 首次设置。"""
        if self.repos.list_users():
            return
        if not self.settings.bootstrap_admin_api_key:
            return
        user = self.repos.create_user(
            self.settings.bootstrap_admin_name,
            self.settings.bootstrap_admin_email,
            "owner",
        )
        raw_key = self.settings.bootstrap_admin_api_key
        self.repos.create_api_key(
            user.id,
            "bootstrap-admin",
            hash_api_key(raw_key, self._required_secret_key()),
            key_prefix(raw_key),
            ["admin", "models"],
            [],
        )

    def setup_status(self) -> dict:
        """返回首次进入 UI 是否需要初始化 owner。"""
        return {"needs_setup": len(self.repos.list_users()) == 0}

    def setup_owner(self, name: str, email: str, password: str) -> dict:
        """首次初始化 owner、密码和第一把管理 API key。"""
        if self.repos.list_users():
            raise AuthError("setup_already_completed")
        self._validate_password(password)
        user = self.repos.create_user(
            name=name,
            email=email,
            role="owner",
            password_hash=hash_password(password),
        )
        api_key = self.create_api_key(user.id, "initial-admin", ["admin", "models"], [])
        session = self._create_session(user)
        return {"user": self.public_user(user), "api_key": api_key, "session": session}

    def login(self, email: str, password: str) -> dict:
        """用控制台账号密码登录并返回短期会话。"""
        user = self.repos.find_user_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            raise AuthError("invalid_credentials")
        if not user.enabled:
            raise AuthError("disabled_user")
        return {"user": self.public_user(user), "session": self._create_session(user)}

    def create_user(self, name: str, email: str, role: str) -> dict:
        """创建用户并返回可公开的元数据。"""
        self._validate_role(role)
        user = self.repos.create_user(name=name, email=email, role=role)
        return self.public_user(user)

    def update_user(self, user_id: int, values: dict) -> dict:
        """更新用户基础字段和角色。"""
        user = self.repos.get_user(user_id)
        if user is None:
            raise AuthError("user_not_found")
        if "role" in values and values["role"] is not None:
            self._validate_role(values["role"])
        for field in ("name", "email", "role", "enabled"):
            if field in values and values[field] is not None:
                setattr(user, field, values[field])
        return self.public_user(user)

    def delete_user(self, user_id: int) -> dict:
        """删除用户；有关联 API key 或 usage 时应先禁用用户。"""
        if not self.repos.delete_user(user_id):
            raise AuthError("user_not_found")
        return {"deleted": True}

    def create_api_key(
        self,
        user_id: int,
        name: str,
        scopes: list[str],
        allowed_models: list[str],
    ) -> dict:
        """创建 API key，并且只在本次返回原始明文 key。"""
        raw = create_api_key()
        record = self.repos.create_api_key(
            user_id,
            name,
            hash_api_key(raw, self._required_secret_key()),
            key_prefix(raw),
            scopes,
            allowed_models,
        )
        return {**self.public_api_key(record), "api_key": raw}

    def set_api_key_enabled(self, api_key_id: int, enabled: bool) -> dict:
        """启用或禁用 API key。"""
        record = self.repos.get_api_key(api_key_id)
        if record is None:
            raise AuthError("api_key_not_found")
        record.enabled = enabled
        return self.public_api_key(record)

    def update_api_key(self, api_key_id: int, values: dict) -> dict:
        """更新 API key 生命周期和授权范围。"""
        record = self.repos.get_api_key(api_key_id)
        if record is None:
            raise AuthError("api_key_not_found")
        for field in ("enabled", "expires_at", "scopes", "allowed_models"):
            if field in values and values[field] is not None:
                setattr(record, field, values[field])
        return self.public_api_key(record)

    def revoke_api_key(self, api_key_id: int) -> dict:
        """撤销 API key，后续请求不可再使用。"""
        record = self.repos.get_api_key(api_key_id)
        if record is None:
            raise AuthError("api_key_not_found")
        record.enabled = False
        record.revoked_at = datetime.now(UTC)
        return self.public_api_key(record)

    def authenticate(self, raw_key: str | None) -> AuthContext:
        """校验原始 API key 并返回请求上下文。"""
        if not raw_key:
            raise AuthError("missing_api_key")
        now = datetime.now(UTC)
        for record in self.repos.list_api_keys():
            if record.prefix != key_prefix(raw_key):
                continue
            if not verify_api_key(raw_key, record.key_hash, self._required_secret_key()):
                continue
            if not record.enabled or record.revoked_at is not None:
                raise AuthError("revoked_api_key")
            if record.expires_at and record.expires_at <= now:
                raise AuthError("expired_api_key")
            if not record.user.enabled:
                raise AuthError("disabled_user")
            return AuthContext(
                user_id=record.user_id,
                api_key_id=record.id,
                user_role=record.user.role,
                scopes=record.scopes or [],
                allowed_models=record.allowed_models or [],
            )
        raise AuthError("invalid_api_key")

    def authenticate_session(self, token: str | None) -> AuthContext:
        """校验控制面会话 token 并返回用户上下文。"""
        if not token:
            raise AuthError("missing_session")
        payload = verify_session_token(token, self._required_secret_key())
        if not payload:
            raise AuthError("invalid_session")
        user = self.repos.get_user(int(payload.get("sub", 0)))
        if user is None:
            raise AuthError("invalid_session")
        if not user.enabled:
            raise AuthError("disabled_user")
        return AuthContext(
            user_id=user.id,
            api_key_id=None,
            user_role=user.role,
            scopes=["admin"] if user.role in {"owner", "admin"} else [],
            allowed_models=[],
        )

    def require_admin(self, context: AuthContext) -> None:
        """管理操作必须要求 owner 或 admin 角色。"""
        if context.user_role not in {"owner", "admin"}:
            raise AuthError("admin_required")
        self.require_scope(context, "admin")

    @staticmethod
    def require_scope(context: AuthContext, scope: str) -> None:
        """确保当前 API key 具备指定 scope。"""
        if scope not in context.scopes:
            raise AuthError(f"{scope}_scope_required")

    def ensure_model_allowed(self, context: AuthContext, model: str) -> None:
        """确保数据面 key 有权限调用指定 virtual model。"""
        self.require_scope(context, "models")
        if context.allowed_models and model not in context.allowed_models:
            raise AuthError("model_not_allowed")

    def _required_secret_key(self) -> str:
        """读取必填 secret key，避免默认弱密钥进入运行时。"""
        if not self.settings.secret_key:
            raise RuntimeError("JEMODEL_SECRET_KEY is required")
        return self.settings.secret_key

    def _create_session(self, user) -> dict:
        """创建控制面会话响应，供 Flutter 控制台保存。"""
        token = create_session_token(
            {"sub": user.id, "role": user.role},
            self._required_secret_key(),
            self.settings.control_session_ttl_seconds,
        )
        return {"access_token": token, "token_type": "bearer"}

    @staticmethod
    def _validate_role(role: str) -> None:
        """限定用户角色，避免任意字符串进入授权逻辑。"""
        if role not in {"owner", "admin", "member", "viewer"}:
            raise AuthError("invalid_role")

    @staticmethod
    def _validate_password(password: str) -> None:
        """为首次设置保留最小密码强度门槛。"""
        if len(password) < 8:
            raise AuthError("weak_password")

    @staticmethod
    def public_user(user) -> dict:
        """返回可安全用于 API response 的 user 字段。"""
        return {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "enabled": user.enabled,
        }

    @staticmethod
    def public_api_key(record) -> dict:
        """返回可安全用于 API response 的 API key 字段。"""
        return {
            "id": record.id,
            "user_id": record.user_id,
            "name": record.name,
            "prefix": record.prefix,
            "scopes": record.scopes,
            "allowed_models": record.allowed_models,
            "enabled": record.enabled,
            "revoked_at": record.revoked_at,
            "expires_at": record.expires_at,
        }
