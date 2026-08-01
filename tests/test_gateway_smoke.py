"""FastAPI 数据面和控制面冒烟测试。"""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


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


def test_control_and_data_plane_smoke(tmp_path, monkeypatch) -> None:
    """验证 provider、route、OpenAI、Anthropic 和 usage 形成闭环。"""
    client = _client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer jm_test_admin"}

    provider = client.post(
        "/api/providers",
        headers=headers,
        json={
            "name": "mock-openai",
            "protocol": "openai",
            "base_url": "https://example.test/v1",
            "secret_value": "sk-test",
        },
    )
    assert provider.status_code == 200
    assert provider.json()["secret_present"] is True
    assert "secret_value" not in provider.json()

    upstream = client.post(
        "/api/upstream-models",
        headers=headers,
        json={
            "provider_id": provider.json()["id"],
            "model_name": "gpt-test",
            "display_name": "GPT Test",
            "capabilities": {"streaming": True, "tools": True},
        },
    )
    assert upstream.status_code == 200

    virtual_model = client.post(
        "/api/virtual-models",
        headers=headers,
        json={"name": "team-coder", "description": "团队编码模型"},
    )
    assert virtual_model.status_code == 200

    route = client.post(
        "/api/route-targets",
        headers=headers,
        json={
            "virtual_model_id": virtual_model.json()["id"],
            "upstream_model_id": upstream.json()["id"],
            "priority": 1,
            "weight": 100,
            "capabilities": {"streaming": True},
        },
    )
    assert route.status_code == 200

    openai = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "team-coder", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert openai.status_code == 200
    assert openai.json()["model"] == "team-coder"
    assert "jemodel mock response" in openai.json()["choices"][0]["message"]["content"]

    anthropic = client.post(
        "/v1/messages",
        headers=headers,
        json={"model": "team-coder", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert anthropic.status_code == 200
    assert anthropic.json()["model"] == "team-coder"

    usage = client.get("/api/usage?group_by=virtual_model", headers=headers)
    assert usage.status_code == 200
    assert usage.json()[0]["group"] == "team-coder"
