"""数据面错误响应形状测试。"""

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


def _post_ok(client: TestClient, path: str, payload: dict) -> dict:
    """发送 POST 并返回成功 JSON。"""
    response = client.post(path, headers=ADMIN_HEADERS, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _create_routed_model(client: TestClient, name: str) -> None:
    """创建可供数据面调用的最小路由模型。"""
    provider = _post_ok(
        client,
        "/api/providers",
        {
            "name": f"{name}-provider",
            "protocol": "anthropic",
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
    _post_ok(
        client,
        "/api/route-targets",
        {"virtual_model_id": virtual["id"], "upstream_model_id": upstream["id"]},
    )


def _limited_key(client: TestClient, allowed_models: list[str]) -> str:
    """创建只允许指定模型的数据面 API key。"""
    user = _post_ok(
        client,
        "/api/users",
        {"name": "Limited", "email": "limited-errors@example.test", "role": "member"},
    )
    key = _post_ok(
        client,
        "/api/api-keys",
        {
            "user_id": user["id"],
            "name": "limited-key",
            "scopes": ["models"],
            "allowed_models": allowed_models,
        },
    )
    return key["api_key"]


def test_openai_auth_error_uses_openai_shape(tmp_path, monkeypatch) -> None:
    """验证 OpenAI-compatible 鉴权错误返回 OpenAI error object。"""
    client = _client(tmp_path, monkeypatch)

    response = client.get("/v1/models")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_api_key"


def test_anthropic_validation_error_uses_anthropic_shape(tmp_path, monkeypatch) -> None:
    """验证 Anthropic-compatible validation error 返回 Anthropic error shape。"""
    client = _client(tmp_path, monkeypatch)

    response = client.post("/v1/messages", headers=ADMIN_HEADERS, json={"messages": []})

    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["message"] == "validation_error"


def test_count_tokens_model_denied_uses_anthropic_shape(tmp_path, monkeypatch) -> None:
    """验证 count_tokens 的模型授权错误也使用 Anthropic error shape。"""
    client = _client(tmp_path, monkeypatch)
    _create_routed_model(client, "allowed-model")
    _create_routed_model(client, "blocked-model")
    api_key = _limited_key(client, ["allowed-model"])

    response = client.post(
        "/v1/messages/count_tokens",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": "blocked-model", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "model_not_allowed"
