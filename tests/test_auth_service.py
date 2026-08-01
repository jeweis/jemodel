"""鉴权 service 与 repository 边界测试。"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.services.auth import AuthService


class FakeRepos:
    """最小 fake repository，用于证明 AuthService 不依赖 SQLite。"""

    def __init__(self) -> None:
        """初始化内存集合。"""
        self.users: list[Any] = []
        self.keys: list[Any] = []

    def list_users(self) -> list[Any]:
        """返回内存用户列表。"""
        return self.users

    def get_user(self, user_id: int) -> Any | None:
        """按 id 返回内存用户。"""
        return next((user for user in self.users if user.id == user_id), None)

    def find_user_by_email(self, email: str) -> Any | None:
        """按 email 返回内存用户。"""
        return next((user for user in self.users if user.email == email), None)

    def create_user(
        self,
        name: str,
        email: str,
        role: str,
        password_hash: str | None = None,
    ) -> Any:
        """创建带属性的轻量用户对象。"""
        user = type(
            "User",
            (),
            {
                "id": len(self.users) + 1,
                "name": name,
                "email": email,
                "role": role,
                "password_hash": password_hash,
                "enabled": True,
            },
        )
        self.users.append(user)
        return user

    def delete_user(self, user_id: int) -> bool:
        """删除内存用户。"""
        before = len(self.users)
        self.users = [user for user in self.users if user.id != user_id]
        return len(self.users) != before

    def create_api_key(
        self,
        user_id: int,
        name: str,
        key_hash: str,
        prefix: str,
        scopes: list[str],
        allowed_models: list[str],
    ) -> Any:
        """创建带属性的轻量 API key 对象。"""
        key = type(
            "ApiKey",
            (),
            {
                "id": 1,
                "user_id": user_id,
                "name": name,
                "key_hash": key_hash,
                "prefix": prefix,
                "scopes": scopes,
                "allowed_models": allowed_models,
                "enabled": True,
                "revoked_at": None,
                "expires_at": None,
                "user": self.users[0],
            },
        )
        self.keys.append(key)
        return key

    def list_api_keys(self) -> list[Any]:
        """返回内存 API key 列表。"""
        return self.keys

    def get_api_key(self, api_key_id: int) -> Any | None:
        """按 id 返回内存 API key。"""
        return next((key for key in self.keys if key.id == api_key_id), None)


def test_auth_service_uses_repository_contract() -> None:
    """验证 AuthService 可运行在 fake repository 上。"""
    settings = Settings(secret_key="test-secret", bootstrap_admin_api_key="jm_fake")
    service = AuthService(FakeRepos(), settings)

    service.bootstrap_owner()
    context = service.authenticate("jm_fake")

    assert context.user_role == "owner"
    assert context.api_key_id == 1


def test_first_run_setup_creates_owner_session_and_one_time_key() -> None:
    """验证无 bootstrap key 时可通过首访 setup 初始化 owner。"""
    settings = Settings(secret_key="test-secret")
    service = AuthService(FakeRepos(), settings)

    assert service.setup_status() == {"needs_setup": True}
    result = service.setup_owner("Owner", "owner@example.test", "secret-pass")
    context = service.authenticate_session(result["session"]["access_token"])

    assert result["user"]["role"] == "owner"
    assert result["api_key"]["api_key"].startswith("jm_")
    assert context.user_role == "owner"
    assert context.api_key_id is None


def test_login_rejects_wrong_password() -> None:
    """验证控制台登录不会接受错误密码。"""
    settings = Settings(secret_key="test-secret")
    service = AuthService(FakeRepos(), settings)

    service.setup_owner("Owner", "owner@example.test", "secret-pass")

    try:
        service.login("owner@example.test", "wrong-pass")
    except Exception as exc:  # noqa: BLE001
        assert str(exc) == "invalid_credentials"
    else:
        raise AssertionError("login should reject wrong password")
