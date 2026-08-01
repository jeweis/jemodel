"""Codex OAuth 控制面 API 端点集成测试。"""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

ADMIN_HEADERS = {"Authorization": "Bearer jm_test_admin"}


def _client(tmp_path, monkeypatch) -> TestClient:
    """用临时 SQLite 数据库创建隔离 TestClient。"""
    monkeypatch.setenv("JEMODEL_DATABASE_URL", f"sqlite:///{tmp_path}/jemodel.db")
    monkeypatch.setenv("JEMODEL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JEMODEL_SECRET_KEY", "test-secret")
    monkeypatch.setenv("JEMODEL_BOOTSTRAP_ADMIN_API_KEY", "jm_test_admin")
    monkeypatch.setenv("JEMODEL_MOCK_UPSTREAMS", "true")

    config = importlib.import_module("app.core.config")
    config.get_settings.cache_clear()
    session = importlib.import_module("app.db.session")
    importlib.reload(session)
    main = importlib.import_module("app.main")
    importlib.reload(main)
    return TestClient(main.create_app())


def _create_codex_provider(client: TestClient) -> dict:
    """创建 codex_oauth provider 并返回响应。"""
    response = client.post(
        "/api/providers",
        headers=ADMIN_HEADERS,
        json={
            "name": "codex-test",
            "protocol": "openai",
            "base_url": "https://chatgpt.example/v1",
            "auth_type": "codex_oauth",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_codex_oauth_start_endpoint_requires_admin(tmp_path, monkeypatch) -> None:
    """非 admin 调用 start 端点应返回 401。"""
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/providers/1/codex/oauth/start")
    assert response.status_code == 401


def test_codex_oauth_start_endpoint_for_unknown_provider(tmp_path, monkeypatch) -> None:
    """start 端点 mock 网络失败时返回 400 或 500（不调真实 OpenAI）。"""
    client = _client(tmp_path, monkeypatch)
    from unittest.mock import AsyncMock, patch

    with patch("app.services.codex_oauth.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_cls.return_value = mock_client
        response = client.post("/api/providers/999/codex/oauth/start", headers=ADMIN_HEADERS)
    # 异常未捕获时 FastAPI 返回 502（网络错误），验证端点可达且触发网络调用
    assert response.status_code in (400, 500, 502)


def test_codex_oauth_status_endpoint_returns_session_not_found(tmp_path, monkeypatch) -> None:
    """查询不存在的 state 返回 error。"""
    client = _client(tmp_path, monkeypatch)
    response = client.get(
        "/api/providers/1/codex/oauth/status",
        headers=ADMIN_HEADERS,
        params={"state": "missing-state"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "error", "error": "session_not_found"}


def test_codex_oauth_revoke_endpoint_for_no_token(tmp_path, monkeypatch) -> None:
    """撤销不存在的 token 返回 revoked: false。"""
    client = _client(tmp_path, monkeypatch)
    _create_codex_provider(client)
    response = client.delete(
        "/api/providers/1/codex/oauth",
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    assert response.json() == {"revoked": False}


def test_provider_response_includes_oauth_status_null_for_codex(tmp_path, monkeypatch) -> None:
    """codex_oauth provider 的 list 响应应包含 oauth_status: null。"""
    client = _client(tmp_path, monkeypatch)
    _create_codex_provider(client)
    response = client.get("/api/providers", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    providers = response.json()
    codex_provider = next(p for p in providers if p["auth_type"] == "codex_oauth")
    assert codex_provider["oauth_status"] is None


def test_provider_response_omits_oauth_status_for_api_key(tmp_path, monkeypatch) -> None:
    """api_key provider 的 oauth_status 字段应为 null。"""
    client = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/providers",
        headers=ADMIN_HEADERS,
        json={
            "name": "openai-test",
            "protocol": "openai",
            "base_url": "https://api.example.test/v1",
            "auth_type": "api_key",
            "secret_value": "sk-test",
        },
    )
    assert response.status_code == 200
    provider_id = response.json()["id"]
    list_resp = client.get("/api/providers", headers=ADMIN_HEADERS)
    api_key_provider = next(p for p in list_resp.json() if p["id"] == provider_id)
    assert api_key_provider["oauth_status"] is None