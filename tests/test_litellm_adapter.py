"""LiteLLM adapter 参数构造和 codex_oauth 注入测试。"""

from unittest.mock import AsyncMock, MagicMock

from app.adapters.litellm_adapter import UpstreamAdapter, _current_codex_token
from app.core.config import Settings
from app.domain.entities import RouteSelection
from app.domain.protocol import NormalizedRequest
from app.services.codex_oauth import CodexOAuthService, CodexTokenError


def _codex_route(provider_id: int = 1) -> RouteSelection:
    """构造 codex_oauth 类型的 RouteSelection。"""
    return RouteSelection(
        route_target_id=1,
        virtual_model="team-coder",
        provider_name="codex",
        provider_protocol="openai",
        base_url="https://should-not-be-used.example",
        auth_type="codex_oauth",
        secret_value=None,
        upstream_model="codex-mini-latest",
        capabilities={},
        fallback_trace=[],
        provider_id=provider_id,
    )


def test_codex_oauth_uses_chatgpt_subscription_params() -> None:
    """Codex OAuth provider 走 _chatgpt_subscription_params，不传 api_base/api_key。"""
    adapter = UpstreamAdapter(Settings(secret_key="test-secret"))
    request = NormalizedRequest(
        protocol="openai",
        model="codex-main",
        messages=[{"role": "user", "content": "hi"}],
    )
    route = _codex_route()

    params = adapter._chatgpt_subscription_params(request, route)

    assert params["model"] == "chatgpt/codex-mini-latest"
    assert "api_base" not in params
    assert "api_key" not in params
    assert params["stream"] is False


def test_codex_oauth_complete_without_service_raises() -> None:
    """没有注入 CodexOAuthService 时调用 codex_oauth provider 应抛 CodexTokenError。"""
    adapter = UpstreamAdapter(Settings(secret_key="test-secret"))
    request = NormalizedRequest(
        protocol="openai",
        model="codex-main",
        messages=[{"role": "user", "content": "hi"}],
    )
    route = _codex_route()

    import pytest

    with pytest.raises(CodexTokenError):
        import asyncio

        asyncio.run(adapter.complete(request, route, codex_oauth=None, codex_repos=None))


def test_codex_oauth_complete_injects_token_via_contextvar() -> None:
    """complete() 应将 DB token 注入 contextvar 后调用 litellm。"""
    settings = Settings(secret_key="test-secret")
    oauth_service = MagicMock(spec=CodexOAuthService)
    oauth_service.ensure_valid_token = AsyncMock(
        return_value={"access_token": "test-access-token", "account_id": "acc-123"}
    )
    oauth_service.refresh_token = AsyncMock()
    repos = MagicMock()
    adapter = UpstreamAdapter(settings)
    request = NormalizedRequest(
        protocol="openai",
        model="codex-main",
        messages=[{"role": "user", "content": "hi"}],
    )
    route = _codex_route(provider_id=42)

    # 验证 contextvar 注入
    captured_token = {}

    async def fake_acompletion(**kwargs):
        ctx = _current_codex_token.get()
        captured_token["value"] = ctx
        return MagicMock(model_dump=lambda: {"choices": [{"message": {"content": "ok"}}]})

    import litellm

    orig = litellm.acompletion
    litellm.acompletion = fake_acompletion
    try:
        import asyncio

        response = asyncio.run(
            adapter.complete(
                request, route, codex_oauth=oauth_service, codex_repos=repos
            )
        )
    finally:
        litellm.acompletion = orig

    assert response.content == "ok"
    assert captured_token["value"] == {"access_token": "test-access-token", "account_id": "acc-123"}
    oauth_service.ensure_valid_token.assert_awaited_once_with(repos, 42)


def test_api_key_provider_keeps_compatible_endpoint_params() -> None:
    """普通 API key provider 继续传入兼容协议 endpoint 参数。"""
    adapter = UpstreamAdapter(Settings(secret_key="test-secret"))
    request = NormalizedRequest(
        protocol="openai",
        model="team-coder",
        messages=[{"role": "user", "content": "hi"}],
    )
    route = RouteSelection(
        route_target_id=1,
        virtual_model="team-coder",
        provider_name="third-party",
        provider_protocol="openai",
        base_url="https://example.test/v1",
        auth_type="api_key",
        secret_value="sk-test",
        upstream_model="gpt-compatible",
        capabilities={},
        fallback_trace=[],
    )

    params = adapter._litellm_params(request, route)

    assert params["model"] == "openai/gpt-compatible"
    assert params["api_base"] == "https://example.test/v1"
    assert params["api_key"] == "sk-test"


def test_anthropic_provider_gets_anthropic_prefix() -> None:
    """Anthropic 协议 provider 的 upstream model 应补齐 anthropic/ 前缀。"""
    adapter = UpstreamAdapter(Settings(secret_key="test-secret"))
    request = NormalizedRequest(
        protocol="anthropic",
        model="team-claude",
        messages=[{"role": "user", "content": "hi"}],
    )
    route = RouteSelection(
        route_target_id=1,
        virtual_model="team-claude",
        provider_name="anthropic-third-party",
        provider_protocol="anthropic",
        base_url="https://example.test",
        auth_type="api_key",
        secret_value="sk-ant-test",
        upstream_model="claude-3-5-haiku-20241022",
        capabilities={},
        fallback_trace=[],
    )

    params = adapter._litellm_params(request, route)

    assert params["model"] == "anthropic/claude-3-5-haiku-20241022"
    assert params["api_base"] == "https://example.test"
    assert params["api_key"] == "sk-ant-test"