"""基于 LiteLLM 的上游模型 adapter。"""

from __future__ import annotations

import contextvars
from typing import Any

import litellm
from litellm.llms.chatgpt.chat.transformation import ChatGPTConfig

from app.core.config import Settings
from app.domain.entities import RequestUsage, RouteSelection
from app.domain.protocol import AdapterResponse, NormalizedRequest
from app.services.codex_oauth import CodexOAuthService, CodexTokenError

# 请求级上下文，传递当前 codex provider 的 OAuth token 给 monkey-patched Authenticator
_current_codex_token: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "jemodel_codex_token", default=None
)


class _DBBackedAuthenticator:
    """替代 LiteLLM 默认 Authenticator，从 contextvar 读取 token。

    LiteLLM ChatGPTConfig._get_openai_compatible_provider_info 强制走
    self.authenticator，完全忽略调用方传入的 api_key/api_base。我们 monkey-patch
    __init__ 替换 authenticator 实例，通过 contextvar 注入数据库中的 token。
    """

    def get_api_base(self) -> str:
        """返回 ChatGPT backend API base。"""
        return "https://chatgpt.com/backend-api/codex"

    def get_access_token(self) -> str:
        """从 contextvar 读取 access_token，未设置时抛错。"""
        ctx = _current_codex_token.get()
        if not ctx or not ctx.get("access_token"):
            raise CodexTokenError("codex_token_not_in_context")
        return ctx["access_token"]

    def get_account_id(self) -> str | None:
        """从 contextvar 读取 account_id。"""
        ctx = _current_codex_token.get()
        if not ctx:
            return None
        return ctx.get("account_id")


# 模块加载时 monkey-patch ChatGPTConfig.__init__，注入 DB-backed Authenticator
_orig_chatgpt_init = ChatGPTConfig.__init__


def _patched_chatgpt_init(self: Any, *args: Any, **kwargs: Any) -> None:
    """替换 ChatGPTConfig 的 authenticator 为数据库驱动版本。"""
    _orig_chatgpt_init(self, *args, **kwargs)
    if hasattr(self, "authenticator"):
        self.authenticator = _DBBackedAuthenticator()


ChatGPTConfig.__init__ = _patched_chatgpt_init  # type: ignore[method-assign]


class UpstreamAdapter:
    """将归一化请求分发到上游 providers。"""

    def __init__(self, settings: Settings, codex_oauth: CodexOAuthService | None = None) -> None:
        """使用运行时行为开关和可选的 Codex OAuth service 初始化 adapter。"""
        self.settings = settings
        self.codex_oauth = codex_oauth

    async def complete(
        self,
        request: NormalizedRequest,
        route: RouteSelection,
        codex_oauth: CodexOAuthService | None = None,
        codex_repos: Any | None = None,
    ) -> AdapterResponse:
        """通过 LiteLLM 或本地 mock adapter 执行模型请求。

        始终以非流式方式调用上游并返回完整 response；流式切片由 router 层基于
        完整内容完成。这样可以避免 LiteLLM async generator 在 model_dump 时抛错，
        也让 cooldown、usage 记账和 fallback 在响应开始前完成。
        """
        if self.settings.mock_upstreams:
            return self._mock_response(request, route)
        if route.auth_type == "codex_oauth":
            return await self._complete_codex(request, route, codex_oauth, codex_repos)
        params = self._litellm_params(request, route)
        params["stream"] = False
        return await self._invoke_litellm(params)

    async def _complete_codex(
        self,
        request: NormalizedRequest,
        route: RouteSelection,
        codex_oauth: CodexOAuthService | None,
        codex_repos: Any | None,
    ) -> AdapterResponse:
        """处理 codex_oauth provider：注入 DB token，401 时刷新重试一次。"""
        if route.provider_id is None:
            raise CodexTokenError("codex_provider_id_missing")
        oauth_service = codex_oauth or self.codex_oauth
        if oauth_service is None:
            raise CodexTokenError("codex_oauth_service_not_injected")
        if codex_repos is None:
            raise CodexTokenError("codex_repos_not_injected")
        token = await oauth_service.ensure_valid_token(codex_repos, route.provider_id)
        params = self._chatgpt_subscription_params(request, route)
        params["stream"] = False
        token_ctx = _current_codex_token.set(token)
        try:
            return await self._invoke_litellm(params)
        except litellm.AuthenticationError:
            # token 过期或失效，刷新一次重试
            await oauth_service.refresh_token(codex_repos, route.provider_id)
            new_token = await oauth_service.ensure_valid_token(codex_repos, route.provider_id)
            token_ctx_reset = _current_codex_token.set(new_token)
            try:
                return await self._invoke_litellm(params)
            finally:
                _current_codex_token.reset(token_ctx_reset)
        finally:
            _current_codex_token.reset(token_ctx)

    @staticmethod
    async def _invoke_litellm(params: dict[str, Any]) -> AdapterResponse:
        """调用 litellm.acompletion 并归一化响应（含 tool_calls 和 finish_reason）。"""
        response = await litellm.acompletion(**params)
        raw = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        return AdapterResponse(
            content=UpstreamAdapter._extract_content(raw),
            raw=raw,
            usage=raw.get("usage") or {},
            tool_calls=UpstreamAdapter._extract_tool_calls(raw),
            finish_reason=UpstreamAdapter._extract_finish_reason(raw),
        )

    def estimate_tokens(self, request: NormalizedRequest) -> RequestUsage:
        """上游无法计数时返回明确标记的 token 估算值。"""
        text = " ".join(str(message.get("content", "")) for message in request.messages)
        input_tokens = max(1, len(text) // 4)
        return RequestUsage(
            input_tokens=input_tokens,
            total_tokens=input_tokens,
            usage_source="estimated",
        )

    @staticmethod
    def usage_from_response(response: AdapterResponse) -> RequestUsage:
        """从 adapter response 中归一化 usage 字段，含缓存和推理 token。"""
        usage = response.usage or {}
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        cached_tokens = int(
            prompt_details.get("cached_tokens")
            or usage.get("cache_read_input_tokens")
            or 0
        )
        reasoning_tokens = int(completion_details.get("reasoning_tokens") or 0)
        return RequestUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=total_tokens,
            usage_source="litellm" if usage else "estimated",
        )

    def _litellm_params(self, request: NormalizedRequest, route: RouteSelection) -> dict[str, Any]:
        """将 jemodel route configuration 转换为 LiteLLM 调用参数。"""
        params: dict[str, Any] = {
            "model": self._litellm_model(route.provider_protocol, route.upstream_model),
            "messages": request.messages,
            "api_base": route.base_url,
        }
        if route.secret_value:
            params["api_key"] = route.secret_value
        self._apply_common_params(params, request)
        return params

    def _chatgpt_subscription_params(
        self,
        request: NormalizedRequest,
        route: RouteSelection,
    ) -> dict[str, Any]:
        """构造 LiteLLM ChatGPT Subscription 调用参数。

        不传 api_base/api_key，由 monkey-patched Authenticator 从 contextvar 注入。
        """
        model = route.upstream_model
        params: dict[str, Any] = {
            "model": model if model.startswith("chatgpt/") else f"chatgpt/{model}",
            "messages": request.messages,
            "stream": False,
        }
        self._apply_common_params(params, request)
        return params

    @staticmethod
    def _apply_common_params(params: dict[str, Any], request: NormalizedRequest) -> None:
        """把 OpenAI 兼容的可选采样/工具参数透传给上游。"""
        if request.temperature is not None:
            params["temperature"] = request.temperature
        if request.max_tokens is not None:
            params["max_tokens"] = request.max_tokens
        if request.top_p is not None:
            params["top_p"] = request.top_p
        if request.stop:
            params["stop"] = request.stop
        if request.presence_penalty is not None:
            params["presence_penalty"] = request.presence_penalty
        if request.frequency_penalty is not None:
            params["frequency_penalty"] = request.frequency_penalty
        if request.seed is not None:
            params["seed"] = request.seed
        if request.tools:
            params["tools"] = request.tools
        if request.tool_choice is not None:
            params["tool_choice"] = request.tool_choice
        if request.parallel_tool_calls is not None:
            params["parallel_tool_calls"] = request.parallel_tool_calls

    @staticmethod
    def _litellm_model(provider_protocol: str, upstream_model: str) -> str:
        """根据 provider protocol 为上游模型名补齐 LiteLLM 期望的 provider 前缀。

        LiteLLM 通过 model 名前缀路由到对应 adapter，裸模型名会抛 BadRequestError。
        jemodel 自管 base_url 和 api_key，前缀只用于选择 OpenAI/Anthropic 协议形状。
        """
        prefix_map = {"openai": "openai", "anthropic": "anthropic"}
        prefix = prefix_map.get(provider_protocol, "openai")
        if upstream_model.startswith(f"{prefix}/"):
            return upstream_model
        return f"{prefix}/{upstream_model}"

    @staticmethod
    def _extract_content(raw: dict[str, Any]) -> str:
        """从 OpenAI-shaped response 中提取 assistant 文本。

        兼容 content 为字符串、为 text block 数组（部分上游），以及 thinking
        类模型把文本放在 reasoning_content 而 content 为空的情况。
        """
        choices = raw.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
        return ""

    @staticmethod
    def _extract_tool_calls(raw: dict[str, Any]) -> list[dict[str, Any]]:
        """从 OpenAI-shaped response 中提取 tool_calls 列表。"""
        choices = raw.get("choices") or []
        if not choices:
            return []
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            return []
        return [tc for tc in tool_calls if isinstance(tc, dict)]

    @staticmethod
    def _extract_finish_reason(raw: dict[str, Any]) -> str | None:
        """从 OpenAI-shaped response 中提取 finish_reason。"""
        choices = raw.get("choices") or []
        if not choices:
            return None
        finish_reason = choices[0].get("finish_reason")
        return str(finish_reason) if finish_reason else None

    @staticmethod
    def _mock_response(request: NormalizedRequest, route: RouteSelection) -> AdapterResponse:
        """为 Docker 冒烟测试返回确定性的本地 mock response。"""
        content = f"jemodel mock response from {route.provider_name}/{route.upstream_model}"
        input_tokens = (
            sum(len(str(message.get("content", ""))) for message in request.messages) // 4
        )
        usage = {
            "prompt_tokens": max(1, input_tokens),
            "completion_tokens": max(1, len(content) // 4),
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return AdapterResponse(content=content, raw={"mock": True, "content": content}, usage=usage)