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


def test_first_visit_auto_generates_bootstrap_key(tmp_path, monkeypatch) -> None:
    """验证空库首次启动自动生成引导 key，一次性端点可获取。"""
    client = _client(tmp_path, monkeypatch)

    # 空库自动 bootstrap 后已有 owner
    status = client.get("/api/setup/status")
    assert status.status_code == 200
    assert status.json() == {"needs_setup": False}

    # 引导 key 一次性可获取
    key_resp = client.get("/api/setup/bootstrap-key")
    assert key_resp.status_code == 200
    bootstrap_key = key_resp.json()["api_key"]
    assert bootstrap_key.startswith("jm_")

    # 用引导 key 可访问控制面
    headers = {"Authorization": f"Bearer {bootstrap_key}"}
    users = client.get("/api/users", headers=headers)
    assert users.status_code == 200
    assert users.json()[0]["role"] == "owner"

    # 第二次获取引导 key 返回 null（已取走）
    again = client.get("/api/setup/bootstrap-key")
    assert again.json() == {"api_key": None}


def test_bootstrap_key_still_initializes_headless_deployments(tmp_path, monkeypatch) -> None:
    """验证配置 bootstrap key 时直接用该 key，不再自动生成。"""
    client = _client(tmp_path, monkeypatch, bootstrap_key="jm_bootstrap")
    headers = {"Authorization": "Bearer jm_bootstrap"}

    status = client.get("/api/setup/status")
    assert status.status_code == 200
    assert status.json() == {"needs_setup": False}

    # 配置了 bootstrap key，自动生成端点返回 null
    key_resp = client.get("/api/setup/bootstrap-key")
    assert key_resp.json() == {"api_key": None}

    users = client.get("/api/users", headers=headers)
    assert users.status_code == 200
    assert users.json()[0]["role"] == "owner"


def test_bootstrap_generated_key_can_login_control_plane(tmp_path, monkeypatch) -> None:
    """验证自动生成的引导 key 可登录数据面。"""
    client = _client(tmp_path, monkeypatch)
    bootstrap_key = client.get("/api/setup/bootstrap-key").json()["api_key"]

    models = client.get("/v1/models", headers={"Authorization": f"Bearer {bootstrap_key}"})
    assert models.status_code == 200
