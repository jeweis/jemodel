"""模型路由、权限和用量账本行为测试。"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

ADMIN_HEADERS = {"Authorization": "Bearer jm_test_admin"}


def _client(tmp_path, monkeypatch) -> TestClient:
    """用独立 SQLite 数据库创建隔离的 FastAPI client。"""
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


def _post_ok(client: TestClient, path: str, payload: dict, headers: dict | None = None) -> dict:
    """提交控制面对象并返回成功响应 JSON。"""
    response = client.post(path, headers=headers or ADMIN_HEADERS, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _create_virtual_model(client: TestClient, name: str) -> dict:
    """创建启用的 virtual model。"""
    return _post_ok(client, "/api/virtual-models", {"name": name, "description": name})


def _create_upstream_model(
    client: TestClient,
    provider_name: str,
    model_name: str,
    capabilities: dict | None = None,
) -> dict:
    """创建 provider 和对应 upstream model target。"""
    provider = _post_ok(
        client,
        "/api/providers",
        {
            "name": provider_name,
            "protocol": "openai",
            "base_url": "https://example.test/v1",
            "secret_value": f"sk-{provider_name}",
        },
    )
    return _post_ok(
        client,
        "/api/upstream-models",
        {
            "provider_id": provider["id"],
            "model_name": model_name,
            "display_name": model_name,
            "capabilities": capabilities or {},
        },
    )


def _create_route(
    client: TestClient,
    virtual_model_id: int,
    upstream_model_id: int,
    *,
    priority: int = 1,
    weight: int = 1,
    enabled: bool = True,
    capabilities: dict | None = None,
    cooldown_seconds: int = 60,
) -> dict:
    """创建 route target。"""
    return _post_ok(
        client,
        "/api/route-targets",
        {
            "virtual_model_id": virtual_model_id,
            "upstream_model_id": upstream_model_id,
            "priority": priority,
            "weight": weight,
            "enabled": enabled,
            "cooldown_seconds": cooldown_seconds,
            "capabilities": capabilities or {},
        },
    )


def _routing_service():
    """返回直接使用当前测试数据库的 routing service 和 session。"""
    session_module = importlib.import_module("app.db.session")
    repositories = importlib.import_module("app.repositories.sqlalchemy")
    routing = importlib.import_module("app.services.routing")
    db = session_module.SessionLocal()
    repos = repositories.SqlRepositories(db)
    return routing.RoutingService(repos), db


def test_selects_lowest_priority_target(tmp_path, monkeypatch) -> None:
    """优先选择 priority 值更低的 route target。"""
    client = _client(tmp_path, monkeypatch)
    virtual_model = _create_virtual_model(client, "team-coder")
    slow = _create_upstream_model(client, "slow-provider", "slow-model")
    fast = _create_upstream_model(client, "fast-provider", "fast-model")
    _create_route(client, virtual_model["id"], slow["id"], priority=20)
    _create_route(client, virtual_model["id"], fast["id"], priority=1)

    service, db = _routing_service()
    try:
        selected = service.select("team-coder", set())
    finally:
        db.close()

    assert selected.provider_name == "fast-provider"


def test_falls_back_when_primary_priority_target_is_disabled(tmp_path, monkeypatch) -> None:
    """最高优先级 target disabled 时选择下一 priority tier。"""
    client = _client(tmp_path, monkeypatch)
    virtual_model = _create_virtual_model(client, "team-coder")
    disabled = _create_upstream_model(client, "disabled-provider", "disabled-model")
    fallback = _create_upstream_model(client, "fallback-provider", "fallback-model")
    _create_route(client, virtual_model["id"], disabled["id"], priority=1, enabled=False)
    _create_route(client, virtual_model["id"], fallback["id"], priority=5)

    service, db = _routing_service()
    try:
        selected = service.select("team-coder", set())
    finally:
        db.close()

    assert selected.provider_name == "fallback-provider"


def test_uses_weights_within_same_priority_tier(tmp_path, monkeypatch) -> None:
    """同一 priority tier 内按配置权重调用 weighted choice。"""
    client = _client(tmp_path, monkeypatch)
    virtual_model = _create_virtual_model(client, "team-coder")
    small = _create_upstream_model(client, "small-provider", "small-model")
    large = _create_upstream_model(client, "large-provider", "large-model")
    _create_route(client, virtual_model["id"], small["id"], priority=1, weight=2)
    _create_route(client, virtual_model["id"], large["id"], priority=1, weight=7)
    captured: dict[str, list[int]] = {}

    def fake_choices(choices, weights):
        """捕获权重并选择第二个 target，避免随机性。"""
        captured["weights"] = weights
        return [choices[1]]

    monkeypatch.setattr("app.services.routing.random.choices", fake_choices)
    service, db = _routing_service()
    try:
        selected = service.select("team-coder", set())
    finally:
        db.close()

    assert captured["weights"] == [2, 7]
    assert selected.provider_name == "large-provider"


def test_skips_target_missing_required_capability(tmp_path, monkeypatch) -> None:
    """请求需要 tools 时跳过未声明 tools 能力的 target。"""
    client = _client(tmp_path, monkeypatch)
    virtual_model = _create_virtual_model(client, "team-coder")
    basic = _create_upstream_model(client, "basic-provider", "basic-model")
    tool = _create_upstream_model(client, "tool-provider", "tool-model", {"tools": True})
    _create_route(client, virtual_model["id"], basic["id"], priority=1)
    _create_route(client, virtual_model["id"], tool["id"], priority=5)

    service, db = _routing_service()
    try:
        selected = service.select("team-coder", {"tools"})
    finally:
        db.close()

    assert selected.provider_name == "tool-provider"


def test_cooldown_temporarily_removes_failed_target(tmp_path, monkeypatch) -> None:
    """失败 target 在 cooldown 到期前不参与选择，到期后重新纳入。"""
    client = _client(tmp_path, monkeypatch)
    virtual_model = _create_virtual_model(client, "team-coder")
    primary = _create_upstream_model(client, "primary-provider", "primary-model")
    fallback = _create_upstream_model(client, "fallback-provider", "fallback-model")
    _create_route(
        client,
        virtual_model["id"],
        primary["id"],
        priority=1,
        cooldown_seconds=30,
    )
    _create_route(client, virtual_model["id"], fallback["id"], priority=5)

    service, db = _routing_service()
    models = importlib.import_module("app.db.models")
    try:
        first = service.select("team-coder", set())
        service.mark_failure(first.route_target_id, "timeout")
        during_cooldown = service.select("team-coder", set())
        target = db.get(models.RouteTargetRecord, first.route_target_id)
        target.cooldown_until = datetime.now(UTC) - timedelta(seconds=1)
        after_cooldown = service.select("team-coder", set())
    finally:
        db.close()

    assert first.provider_name == "primary-provider"
    assert during_cooldown.provider_name == "fallback-provider"
    assert after_cooldown.provider_name == "primary-provider"


def test_model_discovery_filters_by_allowed_models(tmp_path, monkeypatch) -> None:
    """模型发现只返回当前 API key allowed_models 中的 virtual model。"""
    client = _client(tmp_path, monkeypatch)
    allowed = _create_virtual_model(client, "allowed-model")
    blocked = _create_virtual_model(client, "blocked-model")
    upstream = _create_upstream_model(client, "shared-provider", "shared-model")
    _create_route(client, allowed["id"], upstream["id"])
    _create_route(client, blocked["id"], upstream["id"])
    user = _post_ok(
        client,
        "/api/users",
        {"name": "Member", "email": "member@example.test", "role": "member"},
    )
    key = _post_ok(
        client,
        "/api/api-keys",
        {"user_id": user["id"], "name": "limited", "allowed_models": ["allowed-model"]},
    )

    response = client.get("/v1/models", headers={"Authorization": f"Bearer {key['api_key']}"})

    assert response.status_code == 200
    assert [model["id"] for model in response.json()["data"]] == ["allowed-model"]


def test_unknown_model_returns_openai_error_and_records_failure_usage(
    tmp_path,
    monkeypatch,
) -> None:
    """未知模型返回 OpenAI error，并为鉴权成功后的失败请求写 usage。"""
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/v1/chat/completions",
        headers=ADMIN_HEADERS,
        json={"model": "missing-model", "messages": [{"role": "user", "content": "hello"}]},
    )
    usage = client.get("/api/usage?group_by=status", headers=ADMIN_HEADERS)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_model"
    assert usage.status_code == 200
    assert usage.json() == [
        {
            "group": "error",
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }
    ]


def test_usage_aggregates_successful_requests_by_provider(tmp_path, monkeypatch) -> None:
    """usage 聚合按 provider 汇总成功请求 token totals。"""
    client = _client(tmp_path, monkeypatch)
    virtual_model = _create_virtual_model(client, "team-coder")
    upstream = _create_upstream_model(client, "usage-provider", "usage-model")
    _create_route(client, virtual_model["id"], upstream["id"])

    first = client.post(
        "/v1/chat/completions",
        headers=ADMIN_HEADERS,
        json={"model": "team-coder", "messages": [{"role": "user", "content": "hello"}]},
    )
    second = client.post(
        "/v1/messages",
        headers=ADMIN_HEADERS,
        json={"model": "team-coder", "messages": [{"role": "user", "content": "world"}]},
    )
    usage = client.get("/api/usage?group_by=provider", headers=ADMIN_HEADERS)

    assert first.status_code == 200
    assert second.status_code == 200
    assert usage.status_code == 200
    assert usage.json()[0]["group"] == "usage-provider"
    assert usage.json()[0]["total_tokens"] == (
        first.json()["usage"]["total_tokens"]
        + second.json()["usage"]["input_tokens"]
        + second.json()["usage"]["output_tokens"]
    )


def test_usage_filters_by_model_status_and_time_range(tmp_path, monkeypatch) -> None:
    """usage 聚合支持模型、状态和时间范围过滤。"""
    client = _client(tmp_path, monkeypatch)
    first_model = _create_virtual_model(client, "first-model")
    second_model = _create_virtual_model(client, "second-model")
    upstream = _create_upstream_model(client, "filter-provider", "filter-model")
    _create_route(client, first_model["id"], upstream["id"])
    _create_route(client, second_model["id"], upstream["id"])

    for model_name in ("first-model", "second-model"):
        response = client.post(
            "/v1/chat/completions",
            headers=ADMIN_HEADERS,
            json={"model": model_name, "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 200

    usage = client.get(
        "/api/usage",
        headers=ADMIN_HEADERS,
        params={
            "group_by": "virtual_model",
            "virtual_model": "first-model",
            "status": "success",
            "start_time": "2000-01-01T00:00:00+00:00",
            "end_time": "2100-01-01T00:00:00+00:00",
        },
    )

    assert usage.status_code == 200
    assert usage.json()[0]["group"] == "first-model"
    assert len(usage.json()) == 1


def test_usage_visibility_limits_member_key_to_own_rows(tmp_path, monkeypatch) -> None:
    """普通成员 API key 查询 usage 时只能看到自己的请求。"""
    client = _client(tmp_path, monkeypatch)
    virtual_model = _create_virtual_model(client, "visible-model")
    upstream = _create_upstream_model(client, "visible-provider", "visible-model-real")
    _create_route(client, virtual_model["id"], upstream["id"])
    user = _post_ok(
        client,
        "/api/users",
        {"name": "Usage Member", "email": "usage-member@example.test", "role": "member"},
    )
    key = _post_ok(
        client,
        "/api/api-keys",
        {"user_id": user["id"], "name": "usage-key", "scopes": ["models"]},
    )
    member_headers = {"Authorization": f"Bearer {key['api_key']}"}

    admin_response = client.post(
        "/v1/chat/completions",
        headers=ADMIN_HEADERS,
        json={"model": "visible-model", "messages": [{"role": "user", "content": "admin"}]},
    )
    member_response = client.post(
        "/v1/chat/completions",
        headers=member_headers,
        json={"model": "visible-model", "messages": [{"role": "user", "content": "member"}]},
    )
    admin_usage = client.get("/api/usage?group_by=user", headers=ADMIN_HEADERS)
    member_usage = client.get("/api/usage?group_by=user", headers=member_headers)

    assert admin_response.status_code == 200
    assert member_response.status_code == 200
    assert len(admin_usage.json()) == 2
    assert member_usage.status_code == 200
    assert member_usage.json() == [
        {
            "group": user["id"],
            "input_tokens": member_response.json()["usage"]["prompt_tokens"],
            "output_tokens": member_response.json()["usage"]["completion_tokens"],
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": member_response.json()["usage"]["total_tokens"],
        }
    ]
