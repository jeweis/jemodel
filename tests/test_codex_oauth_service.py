"""Codex OAuth service device code flow 和 token 刷新测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from app.core.config import Settings
from app.services.codex_oauth import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    CodexOAuthService,
    CodexTokenError,
)


def _settings() -> Settings:
    """测试用 settings。"""
    return Settings(
        secret_key="test-secret",
        codex_client_id="app_test_client",
        codex_token_refresh_lead_seconds=86400,
    )


def _make_repos() -> MagicMock:
    """构造 mock repository。"""
    repos = MagicMock()
    repos.get_oauth_token.return_value = None
    repos.save_oauth_token.return_value = MagicMock(id=1)
    repos.update_oauth_token.return_value = MagicMock(id=1)
    return repos


def _mock_response(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    """构造 mock httpx.Response。"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=resp
        )
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


def _make_id_token() -> str:
    """构造测试用 id_token JWT。"""
    import base64

    payload = {
        "email": "user@example.com",
        "https://api.openai.com/auth": {
            "chatgpt_account_id": "acc-123",
            "chatgpt_plan_type": "chatgpt-plus",
        },
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{payload_b64}.signature"


def test_decode_id_token_extracts_account_id_and_email() -> None:
    """_decode_id_token 应从 JWT payload 解析 chatgpt_account_id 和 email。"""
    id_token = _make_id_token()
    result = CodexOAuthService._decode_id_token(id_token)

    assert result == {
        "account_id": "acc-123",
        "email": "user@example.com",
        "plan_type": "chatgpt-plus",
    }


def test_decode_id_token_empty_returns_empty() -> None:
    """空 id_token 返回空字典。"""
    assert CodexOAuthService._decode_id_token("") == {}


def test_parse_interval_string() -> None:
    """interval 字段兼容字符串。"""
    assert CodexOAuthService._parse_interval("5") == 5
    assert CodexOAuthService._parse_interval(10) == 10
    assert CodexOAuthService._parse_interval(None) == DEFAULT_POLL_INTERVAL_SECONDS
    assert CodexOAuthService._parse_interval("abc") == DEFAULT_POLL_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_start_device_flow_returns_user_code_and_starts_task() -> None:
    """start_device_flow 应返回 user_code 并启动后台轮询 task。"""
    service = CodexOAuthService(_settings())
    mock_resp = _mock_response(
        200,
        {"device_auth_id": "dev-1", "user_code": "ABC123", "interval": 5},
    )
    with patch("app.services.codex_oauth.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = await service.start_device_flow(provider_id=1)

    assert result["user_code"] == "ABC123"
    assert result["verification_uri"] == "https://auth.openai.com/codex/device"
    assert result["expires_in"] == 900
    assert result["interval"] == 5
    assert "state" in result
    # session 已注册
    session = service._sessions.get(result["state"])
    assert session is not None
    assert session.status == "pending"
    # 清理后台 task
    session.task.cancel()
    import contextlib

    with contextlib.suppress(asyncio.CancelledError):
        await session.task


@pytest.mark.asyncio
async def test_get_status_pending_returns_wait() -> None:
    """pending session 返回 wait。"""
    service = CodexOAuthService(_settings())
    service._sessions["state-1"] = MagicMock(
        state="state-1",
        provider_id=1,
        status="pending",
        email=None,
        expires_at=None,
    )
    assert service.get_status("state-1") == {"status": "wait"}


def test_get_status_completed_returns_ok() -> None:
    """completed session 返回 ok + email + expires_at。"""
    service = CodexOAuthService(_settings())
    expires_at = datetime(2026, 7, 21, 5, 30, tzinfo=UTC)
    service._sessions["state-2"] = MagicMock(
        state="state-2",
        provider_id=1,
        status="completed",
        email="user@example.com",
        expires_at=expires_at,
    )
    result = service.get_status("state-2")
    assert result["status"] == "ok"
    assert result["email"] == "user@example.com"
    assert result["expires_at"] == expires_at.isoformat()


def test_get_status_not_found_returns_error() -> None:
    """不存在的 state 返回 error。"""
    service = CodexOAuthService(_settings())
    result = service.get_status("missing")
    assert result == {"status": "error", "error": "session_not_found"}


@pytest.mark.asyncio
async def test_refresh_token_success() -> None:
    """refresh_token 成功时更新记录。"""
    record = MagicMock(
        id=1,
        refresh_token="old-refresh",
        account_id="acc-1",
        email="user@example.com",
    )
    repos = _make_repos()
    repos.get_oauth_token.return_value = record
    service = CodexOAuthService(_settings())
    mock_resp = _mock_response(
        200,
        {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "id_token": _make_id_token(),
            "expires_in": 3600,
        },
    )
    with patch("app.services.codex_oauth.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = await service.refresh_token(repos, provider_id=1)

    assert result["status"] == "ok"
    repos.update_oauth_token.assert_called_once()
    call_args = repos.update_oauth_token.call_args
    assert call_args.args[0] == 1
    values = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["values"]
    assert values["access_token"] == "new-access"
    assert values["refresh_token"] == "new-refresh"
    assert values["status"] == "active"


@pytest.mark.asyncio
async def test_refresh_token_reused_marks_revoked() -> None:
    """refresh_token_reused 错误应标记 status=revoked。"""
    record = MagicMock(id=1, refresh_token="old-refresh")
    repos = _make_repos()
    repos.get_oauth_token.return_value = record
    service = CodexOAuthService(_settings())
    mock_resp = _mock_response(400, text="refresh_token_reused error")
    with patch("app.services.codex_oauth.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        with pytest.raises(CodexTokenError, match="refresh_token_reused"):
            await service.refresh_token(repos, provider_id=1)

    repos.update_oauth_token.assert_called_once_with(
        1, {"status": "revoked", "last_error": "refresh_token_reused"}
    )


@pytest.mark.asyncio
async def test_ensure_valid_token_refreshes_when_near_expiry() -> None:
    """token 即将过期时 ensure_valid_token 自动刷新。"""
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    record = MagicMock(
        id=1,
        provider_id=1,
        account_id="acc-1",
        email="user@example.com",
        access_token="old-access",
        refresh_token="refresh",
        id_token=None,
        expires_at=expires_at,
        status="active",
        last_error=None,
    )
    repos = _make_repos()
    repos.get_oauth_token.return_value = record
    service = CodexOAuthService(_settings())
    service.refresh_token = AsyncMock(return_value={"status": "ok"})

    result = await service.ensure_valid_token(repos, provider_id=1)

    # refresh_lead 86400 秒（1天），expires_at 1小时后 → 应触发刷新
    service.refresh_token.assert_awaited_once_with(repos, 1)
    assert result["access_token"] == "old-access"
    assert result["account_id"] == "acc-1"


@pytest.mark.asyncio
async def test_ensure_valid_token_raises_when_no_token() -> None:
    """无 token 时抛 CodexTokenError。"""
    repos = _make_repos()
    repos.get_oauth_token.return_value = None
    service = CodexOAuthService(_settings())
    with pytest.raises(CodexTokenError, match="no_oauth_token"):
        await service.ensure_valid_token(repos, provider_id=1)


def test_revoke_marks_status_revoked() -> None:
    """revoke 标记 status=revoked。"""
    record = MagicMock(id=1)
    repos = _make_repos()
    repos.get_oauth_token.return_value = record
    service = CodexOAuthService(_settings())
    result = service.revoke(repos, provider_id=1)
    assert result == {"revoked": True}
    repos.update_oauth_token.assert_called_once_with(1, {"status": "revoked", "last_error": None})


def test_revoke_no_token_returns_false() -> None:
    """无 token 时 revoke 返回 False。"""
    repos = _make_repos()
    repos.get_oauth_token.return_value = None
    service = CodexOAuthService(_settings())
    result = service.revoke(repos, provider_id=1)
    assert result == {"revoked": False}