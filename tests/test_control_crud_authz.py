"""控制面 CRUD、权限和 API key 边界测试。"""

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


def _client_with_codex_oauth(tmp_path, monkeypatch, enabled: bool) -> TestClient:
    """创建带 Codex OAuth feature flag 的隔离 TestClient。"""
    monkeypatch.setenv("JEMODEL_EXPERIMENTAL_CODEX_OAUTH", str(enabled).lower())
    return _client(tmp_path, monkeypatch)


def _post_ok(client: TestClient, path: str, payload: dict, headers: dict | None = None) -> dict:
    """发送 POST 并断言成功，减少测试样板。"""
    response = client.post(path, headers=headers or ADMIN_HEADERS, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _create_routed_model(client: TestClient, name: str = "team-coder") -> dict:
    """创建一组 provider/upstream/virtual/route，供权限测试复用。"""
    provider = _post_ok(
        client,
        "/api/providers",
        {
            "name": f"{name}-provider",
            "protocol": "openai",
            "base_url": "https://example.test/v1",
            "secret_value": "sk-test",
        },
    )
    upstream = _post_ok(
        client,
        "/api/upstream-models",
        {
            "provider_id": provider["id"],
            "model_name": f"{name}-real",
            "display_name": f"{name} real",
        },
    )
    virtual = _post_ok(client, "/api/virtual-models", {"name": name})
    route = _post_ok(
        client,
        "/api/route-targets",
        {"virtual_model_id": virtual["id"], "upstream_model_id": upstream["id"]},
    )
    return {"provider": provider, "upstream": upstream, "virtual": virtual, "route": route}


def test_control_resources_support_update_and_delete(tmp_path, monkeypatch) -> None:
    """验证 provider、upstream、virtual model 和 route target 的 CRUD 闭环。"""
    client = _client(tmp_path, monkeypatch)
    bundle = _create_routed_model(client, "crud-model")

    provider = client.patch(
        f"/api/providers/{bundle['provider']['id']}",
        headers=ADMIN_HEADERS,
        json={"base_url": "https://updated.example.test/v1", "metadata": {"tier": "test"}},
    )
    assert provider.status_code == 200
    assert provider.json()["base_url"] == "https://updated.example.test/v1"
    assert provider.json()["metadata"] == {"tier": "test"}
    assert "secret_value" not in provider.json()

    upstream = client.patch(
        f"/api/upstream-models/{bundle['upstream']['id']}",
        headers=ADMIN_HEADERS,
        json={"display_name": "Updated Real Model", "capabilities": {"tools": True}},
    )
    assert upstream.status_code == 200
    assert upstream.json()["display_name"] == "Updated Real Model"

    virtual = client.patch(
        f"/api/virtual-models/{bundle['virtual']['id']}",
        headers=ADMIN_HEADERS,
        json={"description": "更新后的虚拟模型"},
    )
    assert virtual.status_code == 200
    assert virtual.json()["description"] == "更新后的虚拟模型"

    route = client.patch(
        f"/api/route-targets/{bundle['route']['id']}",
        headers=ADMIN_HEADERS,
        json={"priority": 3, "weight": 20},
    )
    assert route.status_code == 200
    assert route.json()["priority"] == 3
    assert route.json()["weight"] == 20

    route_deleted = client.delete(
        f"/api/route-targets/{bundle['route']['id']}",
        headers=ADMIN_HEADERS,
    )
    virtual_deleted = client.delete(
        f"/api/virtual-models/{bundle['virtual']['id']}",
        headers=ADMIN_HEADERS,
    )
    upstream_deleted = client.delete(
        f"/api/upstream-models/{bundle['upstream']['id']}",
        headers=ADMIN_HEADERS,
    )
    assert route_deleted.json() == {"deleted": True}
    assert virtual_deleted.json() == {"deleted": True}
    assert upstream_deleted.json() == {"deleted": True}
    assert client.delete(f"/api/providers/{bundle['provider']['id']}", headers=ADMIN_HEADERS).json()


def test_member_model_key_cannot_access_control_plane(tmp_path, monkeypatch) -> None:
    """验证只有 owner/admin 能访问控制面。"""
    client = _client(tmp_path, monkeypatch)
    user = _post_ok(
        client,
        "/api/users",
        {"name": "Member", "email": "member@example.test", "role": "member"},
    )
    key = _post_ok(
        client,
        "/api/api-keys",
        {"user_id": user["id"], "name": "member-key", "scopes": ["models"]},
    )

    response = client.get("/api/users", headers={"Authorization": f"Bearer {key['api_key']}"})

    assert response.status_code == 403
    assert response.json()["detail"] == "admin_required"


def test_user_update_and_delete_for_unreferenced_user(tmp_path, monkeypatch) -> None:
    """验证 user update/delete 覆盖基础 CRUD 行为。"""
    client = _client(tmp_path, monkeypatch)
    user = _post_ok(
        client,
        "/api/users",
        {"name": "Temp", "email": "temp@example.test", "role": "viewer"},
    )

    updated = client.patch(
        f"/api/users/{user['id']}",
        headers=ADMIN_HEADERS,
        json={"name": "Temp Admin", "role": "admin", "enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Temp Admin"
    assert updated.json()["role"] == "admin"
    assert updated.json()["enabled"] is False

    deleted = client.delete(f"/api/users/{user['id']}", headers=ADMIN_HEADERS)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}


def test_codex_oauth_provider_blocked_when_feature_flag_disabled(tmp_path, monkeypatch) -> None:
    """验证显式禁用 Codex OAuth feature flag 时仍阻断创建。"""
    client = _client_with_codex_oauth(tmp_path, monkeypatch, enabled=False)

    response = client.post(
        "/api/providers",
        headers=ADMIN_HEADERS,
        json={
            "name": "codex-oauth",
            "protocol": "openai",
            "base_url": "https://chatgpt.example/v1",
            "auth_type": "codex_oauth",
            "secret_value": "oauth-refresh-token",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "experimental_codex_oauth_disabled"


def test_codex_oauth_provider_default_creatable(tmp_path, monkeypatch) -> None:
    """验证默认（feature flag 开启）可直接创建 Codex OAuth provider。"""
    client = _client(tmp_path, monkeypatch)

    provider = _post_ok(
        client,
        "/api/providers",
        {
            "name": "codex-oauth",
            "protocol": "openai",
            "base_url": "https://chatgpt.example/v1",
            "auth_type": "codex_oauth",
            "secret_value": "oauth-refresh-token",
        },
    )

    assert provider["auth_type"] == "codex_oauth"
    assert provider["experimental"] is True
    assert provider["health_status"] == "experimental"
    assert provider["metadata"]["credential_type"] == "codex_oauth"
    assert provider["oauth_status"] is None
    assert "secret_value" not in provider


def test_revoked_api_key_cannot_access_data_plane(tmp_path, monkeypatch) -> None:
    """验证撤销后的 API key 不能继续调用数据面。"""
    client = _client(tmp_path, monkeypatch)
    _create_routed_model(client, "revoked-model")
    user = _post_ok(
        client,
        "/api/users",
        {"name": "Member", "email": "revoked@example.test", "role": "member"},
    )
    key = _post_ok(
        client,
        "/api/api-keys",
        {"user_id": user["id"], "name": "revoked-key", "scopes": ["models"]},
    )
    headers = {"Authorization": f"Bearer {key['api_key']}"}

    before = client.get("/v1/models", headers=headers)
    assert before.status_code == 200

    revoke = client.post(f"/api/api-keys/{key['id']}/revoke", headers=ADMIN_HEADERS)
    assert revoke.status_code == 200

    after = client.get("/v1/models", headers=headers)
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "revoked_api_key"


def test_chat_completion_rejects_disallowed_model(tmp_path, monkeypatch) -> None:
    """验证实际模型调用会执行 allowed_models 限制。"""
    client = _client(tmp_path, monkeypatch)
    _create_routed_model(client, "allowed-model")
    _create_routed_model(client, "blocked-model")
    user = _post_ok(
        client,
        "/api/users",
        {"name": "Limited", "email": "limited@example.test", "role": "member"},
    )
    key = _post_ok(
        client,
        "/api/api-keys",
        {
            "user_id": user["id"],
            "name": "limited-key",
            "scopes": ["models"],
            "allowed_models": ["allowed-model"],
        },
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['api_key']}"},
        json={"model": "blocked-model", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "model_not_allowed"
