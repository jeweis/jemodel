"""数据面流式响应协议形状测试。"""

from __future__ import annotations

import importlib
import json

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


def _create_routed_model(client: TestClient, name: str, protocol: str = "openai") -> None:
    """创建可供数据面调用的最小路由模型。"""
    provider_resp = client.post(
        "/api/providers",
        headers=ADMIN_HEADERS,
        json={
            "name": f"{name}-provider",
            "protocol": protocol,
            "base_url": "https://example.test/v1",
            "secret_value": "sk-test",
        },
    )
    assert provider_resp.status_code == 200, provider_resp.text
    provider = provider_resp.json()
    upstream_resp = client.post(
        "/api/upstream-models",
        headers=ADMIN_HEADERS,
        json={
            "provider_id": provider["id"],
            "model_name": f"{name}-real",
            "display_name": f"{name} real",
        },
    )
    assert upstream_resp.status_code == 200, upstream_resp.text
    upstream = upstream_resp.json()
    virtual_resp = client.post(
        "/api/virtual-models",
        headers=ADMIN_HEADERS,
        json={"name": name},
    )
    assert virtual_resp.status_code == 200, virtual_resp.text
    route_resp = client.post(
        "/api/route-targets",
        headers=ADMIN_HEADERS,
        json={
            "virtual_model_id": virtual_resp.json()["id"],
            "upstream_model_id": upstream["id"],
            "priority": 1,
            "weight": 1,
            "capabilities": {"streaming": True, "tools": True},
        },
    )
    assert route_resp.status_code == 200, route_resp.text


def test_anthropic_stream_has_full_event_sequence(tmp_path, monkeypatch) -> None:
    """Anthropic 流式响应应包含完整 SSE 事件序列（Claude Code 兼容）。"""
    client = _client(tmp_path, monkeypatch)
    _create_routed_model(client, "stream-model")

    response = client.post(
        "/v1/messages",
        headers=ADMIN_HEADERS,
        json={
            "model": "stream-model",
            "max_tokens": 50,
            "stream": True,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        },
    )
    assert response.status_code == 200, response.text

    events = [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    # Claude Code 的 Anthropic SDK 要求 message_start 必须是第一个事件
    assert events[0] == "message_start"
    assert events == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]

    # message_start 的 message 字段形状校验
    lines = response.text.splitlines()
    message_start_data = next(
        line.removeprefix("data: ") for line in lines if line.startswith("data: ")
    )
    parsed = json.loads(message_start_data)
    assert parsed["type"] == "message_start"
    assert parsed["message"]["role"] == "assistant"
    assert parsed["message"]["type"] == "message"
    assert "usage" in parsed["message"]


def test_openai_stream_chunk_has_required_fields(tmp_path, monkeypatch) -> None:
    """OpenAI 流式 chunk 应包含 id/object/created/model/choices 字段。"""
    client = _client(tmp_path, monkeypatch)
    _create_routed_model(client, "openai-stream")

    response = client.post(
        "/v1/chat/completions",
        headers=ADMIN_HEADERS,
        json={
            "model": "openai-stream",
            "max_tokens": 50,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200, response.text

    data_lines = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ") and "[DONE]" not in line
    ]
    assert len(data_lines) >= 2
    chunk = json.loads(data_lines[0])
    assert chunk["object"] == "chat.completion.chunk"
    assert "id" in chunk
    assert "created" in chunk
    assert chunk["model"] == "openai-stream"
    assert chunk["choices"][0]["delta"]["role"] == "assistant"


def test_anthropic_non_stream_with_system_and_tools(tmp_path, monkeypatch) -> None:
    """带 system 和 Anthropic 格式 tools 的请求应正常处理（不报错）。"""
    client = _client(tmp_path, monkeypatch)
    _create_routed_model(client, "full-request")

    response = client.post(
        "/v1/messages",
        headers=ADMIN_HEADERS,
        json={
            "model": "full-request",
            "max_tokens": 50,
            "system": [
                {"type": "text", "text": "You are helpful", "cache_control": {"type": "ephemeral"}}
            ],
            "tools": [
                {
                    "name": "get_weather",
                    "description": "Get weather",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert isinstance(data["content"], list)
    assert data["content"][0]["type"] == "text"


def test_anthropic_multi_turn_tool_result_accepted(tmp_path, monkeypatch) -> None:
    """含 tool_use/tool_result 的多轮对话请求应被接受并归一化。"""
    client = _client(tmp_path, monkeypatch)
    _create_routed_model(client, "tool-loop")

    response = client.post(
        "/v1/messages",
        headers=ADMIN_HEADERS,
        json={
            "model": "tool-loop",
            "max_tokens": 50,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "weather?"}]},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "get_weather",
                            "input": {"city": "Beijing"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": [{"type": "text", "text": "sunny"}],
                        }
                    ],
                },
            ],
        },
    )
    assert response.status_code == 200, response.text


def test_openai_request_accepts_sdk_common_fields(tmp_path, monkeypatch) -> None:
    """OpenAI SDK 常用字段（top_p/stop/tool_choice/penalties/seed）不应报错。"""
    client = _client(tmp_path, monkeypatch)
    _create_routed_model(client, "sdk-fields")

    response = client.post(
        "/v1/chat/completions",
        headers=ADMIN_HEADERS,
        json={
            "model": "sdk-fields",
            "max_tokens": 50,
            "temperature": 0.7,
            "top_p": 0.9,
            "stop": ["END"],
            "tool_choice": "auto",
            "presence_penalty": 0.1,
            "frequency_penalty": 0.1,
            "seed": 42,
            "parallel_tool_calls": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200, response.text


def test_openai_stream_tool_calls_delta_chunk(tmp_path, monkeypatch) -> None:
    """上游返回 tool_calls 时，OpenAI 流式应包含 tool_calls delta chunk。"""
    client = _client(tmp_path, monkeypatch)
    _create_routed_model(client, "tool-stream")

    # mock adapter 返回带 tool_calls 的响应
    from app.adapters.litellm_adapter import UpstreamAdapter
    from app.domain.protocol import AdapterResponse

    original = UpstreamAdapter.complete

    async def fake_complete(self, request, route, codex_oauth=None, codex_repos=None):
        return AdapterResponse(
            content="",
            raw={"mock": True},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Beijing"}'},
                }
            ],
            finish_reason="tool_calls",
        )

    UpstreamAdapter.complete = fake_complete
    try:
        response = client.post(
            "/v1/chat/completions",
            headers=ADMIN_HEADERS,
            json={
                "model": "tool-stream",
                "max_tokens": 50,
                "stream": True,
                "messages": [{"role": "user", "content": "weather?"}],
            },
        )
    finally:
        UpstreamAdapter.complete = original

    assert response.status_code == 200, response.text
    text = response.text
    assert '"tool_calls"' in text
    assert '"finish_reason": "tool_calls"' in text


def test_anthropic_stream_tool_use_blocks(tmp_path, monkeypatch) -> None:
    """上游返回 tool_calls 时，Anthropic 流式应发 tool_use block 事件序列。"""
    client = _client(tmp_path, monkeypatch)
    _create_routed_model(client, "tool-use-stream")

    from app.adapters.litellm_adapter import UpstreamAdapter
    from app.domain.protocol import AdapterResponse

    original = UpstreamAdapter.complete

    async def fake_complete(self, request, route, codex_oauth=None, codex_repos=None):
        return AdapterResponse(
            content="Let me check.",
            raw={"mock": True},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Beijing"}'},
                }
            ],
            finish_reason="tool_calls",
        )

    UpstreamAdapter.complete = fake_complete
    try:
        response = client.post(
            "/v1/messages",
            headers=ADMIN_HEADERS,
            json={
                "model": "tool-use-stream",
                "max_tokens": 50,
                "stream": True,
                "messages": [{"role": "user", "content": [{"type": "text", "text": "weather?"}]}],
            },
        )
    finally:
        UpstreamAdapter.complete = original

    assert response.status_code == 200, response.text
    text = response.text
    assert '"type": "tool_use"' in text
    assert '"input_json_delta"' in text
    assert '"stop_reason": "tool_use"' in text


def test_openai_non_stream_tool_calls_response(tmp_path, monkeypatch) -> None:
    """上游返回 tool_calls 时，OpenAI 非流式响应应带 tool_calls 和 finish_reason。"""
    client = _client(tmp_path, monkeypatch)
    _create_routed_model(client, "tool-nonstream")

    from app.adapters.litellm_adapter import UpstreamAdapter
    from app.domain.protocol import AdapterResponse

    original = UpstreamAdapter.complete

    async def fake_complete(self, request, route, codex_oauth=None, codex_repos=None):
        return AdapterResponse(
            content="",
            raw={"mock": True},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Beijing"}'},
                }
            ],
            finish_reason="tool_calls",
        )

    UpstreamAdapter.complete = fake_complete
    try:
        response = client.post(
            "/v1/chat/completions",
            headers=ADMIN_HEADERS,
            json={
                "model": "tool-nonstream",
                "max_tokens": 50,
                "messages": [{"role": "user", "content": "weather?"}],
            },
        )
    finally:
        UpstreamAdapter.complete = original

    assert response.status_code == 200, response.text
    data = response.json()
    message = data["choices"][0]["message"]
    assert message["tool_calls"][0]["function"]["name"] == "get_weather"
    assert data["choices"][0]["finish_reason"] == "tool_calls"


def test_anthropic_non_stream_tool_use_content_blocks(tmp_path, monkeypatch) -> None:
    """上游返回 tool_calls 时，Anthropic 非流式响应应转 tool_use blocks。"""
    client = _client(tmp_path, monkeypatch)
    _create_routed_model(client, "tool-block")

    from app.adapters.litellm_adapter import UpstreamAdapter
    from app.domain.protocol import AdapterResponse

    original = UpstreamAdapter.complete

    async def fake_complete(self, request, route, codex_oauth=None, codex_repos=None):
        return AdapterResponse(
            content="",
            raw={"mock": True},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Beijing"}'},
                }
            ],
            finish_reason="tool_calls",
        )

    UpstreamAdapter.complete = fake_complete
    try:
        response = client.post(
            "/v1/messages",
            headers=ADMIN_HEADERS,
            json={
                "model": "tool-block",
                "max_tokens": 50,
                "messages": [{"role": "user", "content": [{"type": "text", "text": "weather?"}]}],
            },
        )
    finally:
        UpstreamAdapter.complete = original

    assert response.status_code == 200, response.text
    data = response.json()
    tool_use = next(b for b in data["content"] if b["type"] == "tool_use")
    assert tool_use["name"] == "get_weather"
    assert tool_use["input"] == {"city": "Beijing"}
    assert data["stop_reason"] == "tool_use"