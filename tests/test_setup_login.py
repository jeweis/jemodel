"""首次初始化和控制台登录 API 测试。"""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch, bootstrap_key: str | None = None) -> TestClient:
    """创建隔离 TestClient，可选择是否启用 bootstrap API key。"""
    monkeypatch.setenv("JEMODEL_DATABASE_URL", f"sqlite:///{tmp_path}/jemodel.db")
    monkeypatch.setenv("JEMODEL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JEMODEL_SECRET_KEY", "test-secret")
    monkeypatch.setenv("JEMODEL_MOCK_UPSTREAMS", "true")
    if bootstrap_key is None:
        monkeypatch.delenv("JEMODEL_BOOTSTRAP_ADMIN_API_KEY", raising=False)
    else:
        monkeypatch.setenv("JEMODEL_BOOTSTRAP_ADMIN_API_KEY", bootstrap_key)

    config = importlib.import_module("app.core.config")
    config.get_settings.cache_clear()
    session = importlib.import_module("app.db.session")
    importlib.reload(session)
    main = importlib.import_module("app.main")
    importlib.reload(main)
    return TestClient(main.create_app())


def test_first_visit_setup_initializes_owner_and_login(tmp_path, monkeypatch) -> None:
    """验证空库可从 UI 首访路径创建 owner 并登录控制面。"""
    client = _client(tmp_path, monkeypatch)

    status = client.get("/api/setup/status")
    assert status.status_code == 200
    assert status.json() == {"needs_setup": True}

    setup = client.post(
        "/api/setup",
        json={
            "name": "Owner",
            "email": "owner@example.test",
            "password": "secret-pass",
        },
    )
    assert setup.status_code == 200
    assert setup.json()["user"]["role"] == "owner"
    assert setup.json()["api_key"]["api_key"].startswith("jm_")

    duplicate = client.post(
        "/api/setup",
        json={
            "name": "Other",
            "email": "other@example.test",
            "password": "secret-pass",
        },
    )
    assert duplicate.status_code == 409

    login = client.post(
        "/api/auth/login",
        json={"email": "owner@example.test", "password": "secret-pass"},
    )
    assert login.status_code == 200
    session_headers = {"Authorization": f"Bearer {login.json()['session']['access_token']}"}

    users = client.get("/api/users", headers=session_headers)
    assert users.status_code == 200
    assert users.json()[0]["email"] == "owner@example.test"

    models = client.get("/v1/models", headers=session_headers)
    assert models.status_code == 401


def test_bootstrap_key_still_initializes_headless_deployments(tmp_path, monkeypatch) -> None:
    """验证 Docker/CI 仍可通过 bootstrap key 直接进入控制面。"""
    client = _client(tmp_path, monkeypatch, bootstrap_key="jm_bootstrap")
    headers = {"Authorization": "Bearer jm_bootstrap"}

    status = client.get("/api/setup/status")
    assert status.status_code == 200
    assert status.json() == {"needs_setup": False}

    users = client.get("/api/users", headers=headers)
    assert users.status_code == 200
    assert users.json()[0]["role"] == "owner"
