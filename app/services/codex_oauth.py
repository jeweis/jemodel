"""Codex OAuth device code flow 和 token 生命周期管理。

参考 router-for-me/CLIProxyAPI 的实现，OpenAI Codex 的 device code flow 是
OpenAI 专有协议（非标准 RFC 8628）：deviceauth/token 端点返回的不是 access_token
而是 authorization_code + PKCE（由服务端下发），客户端透传给 oauth/token 换 token。
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import Settings
from app.repositories.sqlalchemy import SqlRepositories


class CodexTokenError(Exception):
    """Codex OAuth token 不可用或刷新失败时抛出。"""


# 控制面使用的别名，保持语义一致
CodexOAuthError = CodexTokenError


# OpenAI 专有 device code flow 端点常量（来自 LiteLLM common_utils.py + CLIProxyAPI 验证）
DEVICE_USERCODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
TOKEN_URL = "https://auth.openai.com/oauth/token"
VERIFICATION_URI = "https://auth.openai.com/codex/device"
REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
POLL_TIMEOUT_SECONDS = 900  # 15 分钟
DEFAULT_POLL_INTERVAL_SECONDS = 5
SESSION_TTL_SECONDS = 30 * 60  # session 30 分钟后自动清理


@dataclass
class OAuthSession:
    """device code flow 的内存 session 状态。"""

    state: str
    provider_id: int
    status: str  # pending / completed / error
    error: str | None = None
    email: str | None = None
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    task: asyncio.Task[Any] | None = None


class CodexOAuthService:
    """封装 OpenAI Codex device code flow 和 token 刷新。

    设计为进程级单例（挂到 app.state），session store 和 refresh 锁跨请求共享；
    需要 DB 访问的方法显式接收 repos 参数，避免持有请求级 session。
    """

    def __init__(self, settings: Settings) -> None:
        """使用运行时配置初始化单例 service。"""
        self.settings = settings
        self._sessions: dict[str, OAuthSession] = {}
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self._cleanup_task: asyncio.Task[Any] | None = None

    async def start_device_flow(self, provider_id: int) -> dict[str, Any]:
        """请求 user code 并启动后台轮询，返回展示给前端的信息。"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                DEVICE_USERCODE_URL,
                json={"client_id": self.settings.codex_client_id},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

        device_auth_id = data.get("device_auth_id")
        user_code = data.get("user_code") or data.get("usercode")
        if not device_auth_id or not user_code:
            raise CodexTokenError("device_code_response_missing_fields")

        interval = self._parse_interval(data.get("interval"))
        state = str(uuid.uuid4())
        session = OAuthSession(state=state, provider_id=provider_id, status="pending")
        session.task = asyncio.create_task(
            self._poll_and_exchange(provider_id, state, device_auth_id, user_code, interval)
        )
        self._sessions[state] = session
        self._ensure_cleanup_task()
        return {
            "user_code": user_code,
            "verification_uri": VERIFICATION_URI,
            "state": state,
            "expires_in": POLL_TIMEOUT_SECONDS,
            "interval": interval,
        }

    async def _poll_and_exchange(
        self,
        provider_id: int,
        state: str,
        device_auth_id: str,
        user_code: str,
        interval: int,
    ) -> None:
        """后台 task：轮询 deviceauth/token → 换 token → 存表。

        使用独立的 DB session，不复用请求级 session（/start 请求结束后会被关闭）。
        """
        from app.db.session import SessionLocal

        session = self._sessions.get(state)
        if session is None:
            return
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        try:
            auth_code_data = await self._poll_device_token(
                device_auth_id, user_code, interval, deadline
            )
            token_data = await self._exchange_code_for_tokens(
                auth_code_data["authorization_code"],
                auth_code_data["code_verifier"],
            )
            claims = self._decode_id_token(token_data.get("id_token", ""))
            expires_at = datetime.now(UTC) + timedelta(seconds=token_data.get("expires_in", 0))
            db_session = SessionLocal()
            try:
                repos = SqlRepositories(db_session)
                repos.save_oauth_token(
                    {
                        "provider_id": provider_id,
                        "account_id": claims.get("account_id"),
                        "email": claims.get("email"),
                        "access_token": token_data["access_token"],
                        "refresh_token": token_data.get("refresh_token", ""),
                        "id_token": token_data.get("id_token"),
                        "expires_at": expires_at,
                        "last_refresh": datetime.now(UTC),
                        "status": "active",
                        "last_error": None,
                    }
                )
                db_session.commit()
            finally:
                db_session.close()
            session.status = "completed"
            session.email = claims.get("email")
            session.expires_at = expires_at
        except asyncio.CancelledError:
            session.status = "error"
            session.error = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001
            session.status = "error"
            session.error = str(exc)

    async def _poll_device_token(
        self,
        device_auth_id: str,
        user_code: str,
        interval: int,
        deadline: float,
    ) -> dict[str, Any]:
        """轮询 deviceauth/token 端点，返回 authorization_code + PKCE 数据。"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            while time.monotonic() < deadline:
                resp = await client.post(
                    DEVICE_TOKEN_URL,
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
                if resp.status_code in (403, 404):
                    await asyncio.sleep(max(interval, DEFAULT_POLL_INTERVAL_SECONDS))
                    continue
                if 200 <= resp.status_code < 300:
                    data = resp.json()
                    if all(
                        key in data
                        for key in ("authorization_code", "code_verifier", "code_challenge")
                    ):
                        return data
                    raise CodexTokenError("device_token_response_missing_fields")
                raise CodexTokenError(f"device_token_poll_failed_{resp.status_code}")
        raise CodexTokenError("device_authentication_timeout")

    async def _exchange_code_for_tokens(self, code: str, code_verifier: str) -> dict[str, Any]:
        """用 authorization_code + PKCE 换取 access/refresh/id token。"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.settings.codex_client_id,
                    "code": code,
                    "redirect_uri": REDIRECT_URI,
                    "code_verifier": code_verifier,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        if not all(key in data for key in ("access_token", "refresh_token", "id_token")):
            raise CodexTokenError("token_exchange_response_missing_fields")
        return data

    def get_status(self, state: str) -> dict[str, Any]:
        """前端轮询入口，返回当前 session 状态。"""
        session = self._sessions.get(state)
        if session is None:
            return {"status": "error", "error": "session_not_found"}
        if session.status == "completed":
            return {
                "status": "ok",
                "email": session.email,
                "expires_at": session.expires_at.isoformat() if session.expires_at else None,
            }
        if session.status == "error":
            return {"status": "error", "error": session.error or "unknown_error"}
        return {"status": "wait"}

    def cancel(self, state: str) -> dict[str, bool]:
        """取消正在进行的 device code flow 登录。"""
        session = self._sessions.pop(state, None)
        if session and session.task and not session.task.done():
            session.task.cancel()
        return {"cancelled": True}

    async def refresh_token(self, repos: SqlRepositories, provider_id: int) -> dict[str, Any]:
        """手动刷新 provider 的 OAuth token。"""
        record = repos.get_oauth_token(provider_id)
        if record is None:
            raise CodexTokenError("no_oauth_token")
        lock_key = record.refresh_token
        lock = self._refresh_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            record = repos.get_oauth_token(provider_id)
            if record is None:
                raise CodexTokenError("no_oauth_token")
            return await self._do_refresh(repos, record)

    async def _do_refresh(self, repos: SqlRepositories, record: Any) -> dict[str, Any]:
        """执行刷新请求并更新记录。"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "client_id": self.settings.codex_client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": record.refresh_token,
                    "scope": "openid profile email",
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
            if resp.status_code != 200:
                body = resp.text.lower()
                if "refresh_token_reused" in body:
                    repos.update_oauth_token(
                        record.id,
                        {"status": "revoked", "last_error": "refresh_token_reused"},
                    )
                    raise CodexTokenError("refresh_token_reused")
                raise CodexTokenError(f"refresh_failed_{resp.status_code}")
            data = resp.json()
        if not data.get("access_token") or not data.get("id_token"):
            raise CodexTokenError("refresh_response_missing_fields")
        claims = self._decode_id_token(data.get("id_token", ""))
        expires_at = datetime.now(UTC) + timedelta(seconds=data.get("expires_in", 0))
        repos.update_oauth_token(
            record.id,
            {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", record.refresh_token),
                "id_token": data.get("id_token"),
                "account_id": claims.get("account_id") or record.account_id,
                "email": claims.get("email") or record.email,
                "expires_at": expires_at,
                "last_refresh": datetime.now(UTC),
                "status": "active",
                "last_error": None,
            },
        )
        return {"status": "ok", "expires_at": expires_at.isoformat()}

    async def ensure_valid_token(
        self, repos: SqlRepositories, provider_id: int
    ) -> dict[str, Any]:
        """adapter 调用前检查，过期则自动刷新，返回 access_token + account_id。"""
        record = repos.get_oauth_token(provider_id)
        if record is None:
            raise CodexTokenError("no_oauth_token")
        if record.status == "revoked":
            raise CodexTokenError("token_revoked")
        if record.status != "active":
            raise CodexTokenError(f"token_not_active:{record.status}")
        if record.expires_at is not None:
            now = datetime.now(UTC)
            lead = self.settings.codex_token_refresh_lead_seconds
            if now + timedelta(seconds=lead) >= record.expires_at:
                await self.refresh_token(repos, provider_id)
                record = repos.get_oauth_token(provider_id)
                if record is None:
                    raise CodexTokenError("token_disappeared_after_refresh")
        return {
            "access_token": record.access_token,
            "account_id": record.account_id,
        }

    def revoke(self, repos: SqlRepositories, provider_id: int) -> dict[str, bool]:
        """撤销 provider 的 OAuth token（标记 revoked，不调 OpenAI revoke 端点）。"""
        record = repos.get_oauth_token(provider_id)
        if record is None:
            return {"revoked": False}
        repos.update_oauth_token(record.id, {"status": "revoked", "last_error": None})
        return {"revoked": True}

    @staticmethod
    def _parse_interval(raw: Any) -> int:
        """解析 device code 响应的 interval 字段，兼容字符串和数字。"""
        if isinstance(raw, int) and raw > 0:
            return raw
        if isinstance(raw, str) and raw.strip().isdigit():
            return int(raw.strip())
        return DEFAULT_POLL_INTERVAL_SECONDS

    @staticmethod
    def _decode_id_token(id_token: str) -> dict[str, Any]:
        """解析 id_token JWT payload（不验签），取 account_id / email / plan_type。"""
        if not id_token:
            return {}
        parts = id_token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        auth_claim = payload.get("https://api.openai.com/auth", {}) or {}
        return {
            "account_id": auth_claim.get("chatgpt_account_id"),
            "email": payload.get("email"),
            "plan_type": auth_claim.get("chatgpt_plan_type"),
        }

    def _ensure_cleanup_task(self) -> None:
        """启动后台 task 定时清理过期 session。"""
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_sessions_loop())

    async def _cleanup_sessions_loop(self) -> None:
        """每 5 分钟清理过期 session。"""
        while True:
            await asyncio.sleep(300)
            now = datetime.now(UTC)
            expired = [
                state
                for state, session in self._sessions.items()
                if (now - session.created_at).total_seconds() > SESSION_TTL_SECONDS
            ]
            for state in expired:
                session = self._sessions.pop(state, None)
                if session and session.task and not session.task.done():
                    session.task.cancel()