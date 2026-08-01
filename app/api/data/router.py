"""OpenAI-compatible 和 Anthropic-compatible 数据面 API。"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from app.adapters.litellm_adapter import UpstreamAdapter
from app.api.data.anthropic_compat import (
    anthropic_messages_to_openai,
    anthropic_system_to_openai,
    anthropic_tool_choice_to_openai,
    anthropic_tools_to_openai,
)
from app.api.data.errors import anthropic_error, openai_error, status_from_exception
from app.api.data.formatters import (
    _map_stop_reason,
    anthropic_message_response,
    anthropic_models,
    openai_chat_response,
    openai_models,
    openai_tool_calls_to_anthropic_blocks,
)
from app.api.deps import (
    codex_oauth_service,
    current_model_auth,
    repositories,
    routing_service,
    upstream_adapter,
    usage_service,
)
from app.api.schemas import AnthropicMessageRequest, OpenAIChatRequest
from app.domain.entities import AuthContext, RequestUsage, RouteSelection
from app.domain.protocol import AdapterResponse, NormalizedRequest
from app.repositories.sqlalchemy import SqlRepositories
from app.services.auth import AuthError
from app.services.codex_oauth import CodexOAuthService
from app.services.routing import RoutingError, RoutingService
from app.services.usage import UsageService

router = APIRouter(prefix="/v1", tags=["data-plane"])


@router.get("/models")
def list_models(
    context: AuthContext = Depends(current_model_auth),
    routing: RoutingService = Depends(routing_service),
    anthropic_version: str | None = Header(default=None, alias="anthropic-version"),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    """返回当前 API key 可见的 model list。

    Anthropic 客户端调 GET /v1/models（带 anthropic-version 或 x-api-key header），
    返回 Anthropic 格式（含 has_more/first_id/last_id）；
    其他返回 OpenAI 格式（object: list + data[]）。
    """
    visible = _visible_models(context, routing.list_virtual_models())
    if _is_anthropic_client(anthropic_version, x_api_key):
        return anthropic_models(visible)
    return openai_models(visible)


@router.get("/anthropic/models")
def list_anthropic_models(
    context: AuthContext = Depends(current_model_auth),
    routing: RoutingService = Depends(routing_service),
) -> dict:
    """返回 Anthropic 格式 model list（非标准路径，保留向后兼容）。"""
    visible = _visible_models(context, routing.list_virtual_models())
    return anthropic_models(visible)


@router.post("/chat/completions")
async def chat_completions(
    payload: OpenAIChatRequest,
    context: AuthContext = Depends(current_model_auth),
    routing: RoutingService = Depends(routing_service),
    adapter: UpstreamAdapter = Depends(upstream_adapter),
    usage: UsageService = Depends(usage_service),
    codex_oauth: CodexOAuthService = Depends(codex_oauth_service),
    repos: SqlRepositories = Depends(repositories),
):
    """处理 OpenAI-compatible chat completions 请求。"""
    started = time.perf_counter()
    try:
        _ensure_model_allowed(context, payload.model)
        request = _normalized_openai(payload)
        response, route = await _complete_with_failover(
            request,
            payload.model,
            _required_capabilities(payload),
            context,
            routing,
            adapter,
            usage,
            started,
            codex_oauth,
            repos,
        )
        request_usage = adapter.usage_from_response(response)
        usage.record_success(context, route, request_usage, _latency_ms(started))
        if payload.stream:
            return _openai_stream(response, payload.model, request_usage)
        return openai_chat_response(route, response, request_usage)
    except (AuthError, RoutingError) as exc:
        usage.record_failure(context, payload.model, "openai", _latency_ms(started), str(exc))
        return openai_error(str(exc), status_from_exception(exc))


@router.post("/messages")
async def anthropic_messages(
    payload: AnthropicMessageRequest,
    context: AuthContext = Depends(current_model_auth),
    routing: RoutingService = Depends(routing_service),
    adapter: UpstreamAdapter = Depends(upstream_adapter),
    usage: UsageService = Depends(usage_service),
    codex_oauth: CodexOAuthService = Depends(codex_oauth_service),
    repos: SqlRepositories = Depends(repositories),
):
    """处理 Anthropic-compatible messages 请求。"""
    started = time.perf_counter()
    try:
        _ensure_model_allowed(context, payload.model)
        request = _normalized_anthropic(payload)
        response, route = await _complete_with_failover(
            request,
            payload.model,
            _required_capabilities(payload),
            context,
            routing,
            adapter,
            usage,
            started,
            codex_oauth,
            repos,
        )
        request_usage = adapter.usage_from_response(response)
        usage.record_success(context, route, request_usage, _latency_ms(started))
        if payload.stream:
            return _anthropic_stream(response, payload.model, request_usage)
        return anthropic_message_response(route, response, request_usage)
    except (AuthError, RoutingError) as exc:
        usage.record_failure(context, payload.model, "anthropic", _latency_ms(started), str(exc))
        return anthropic_error(str(exc), status_from_exception(exc))


@router.post("/messages/count_tokens")
def count_tokens(
    payload: AnthropicMessageRequest,
    context: AuthContext = Depends(current_model_auth),
    adapter: UpstreamAdapter = Depends(upstream_adapter),
):
    """返回 Anthropic-compatible token count，默认使用明确标记的估算。"""
    try:
        _ensure_model_allowed(context, payload.model)
        estimated = adapter.estimate_tokens(_normalized_anthropic(payload))
        return {"input_tokens": estimated.input_tokens, "usage_source": estimated.usage_source}
    except AuthError as exc:
        return anthropic_error(str(exc), status_from_exception(exc))


def _visible_models(context: AuthContext, models: list[dict]) -> list[dict]:
    """按 API key allowed_models 过滤模型列表。"""
    if not context.allowed_models:
        return models
    return [model for model in models if model["name"] in context.allowed_models]


def _ensure_model_allowed(context: AuthContext, model: str) -> None:
    """确保 API key 可以访问指定 virtual model。"""
    if context.allowed_models and model not in context.allowed_models:
        raise AuthError("model_not_allowed")


def _is_anthropic_client(anthropic_version: str | None, x_api_key: str | None) -> bool:
    """通过请求 header 判断是否 Anthropic 客户端。

    Anthropic SDK 发送 anthropic-version 和/或 x-api-key header；
    OpenAI SDK 发送 Authorization Bearer，无上述 header。
    """
    return anthropic_version is not None or x_api_key is not None


def _required_capabilities(payload: OpenAIChatRequest | AnthropicMessageRequest) -> set[str]:
    """根据请求字段计算 route target 必需能力。"""
    required: set[str] = set()
    if payload.stream:
        required.add("streaming")
    if payload.tools:
        required.add("tools")
    if _message_has_vision(payload.messages):
        required.add("vision")
    response_format = getattr(payload, "response_format", None)
    if response_format and response_format.get("type") in {"json_object", "json_schema"}:
        required.add("json_mode")
    return required


async def _complete_with_failover(
    request: NormalizedRequest,
    model: str,
    required: set[str],
    context: AuthContext,
    routing: RoutingService,
    adapter: UpstreamAdapter,
    usage: UsageService,
    started: float,
    codex_oauth: CodexOAuthService | None = None,
    codex_repos: SqlRepositories | None = None,
) -> tuple[AdapterResponse, RouteSelection]:
    """在响应开始前完成上游调用，失败时标记 cooldown 并重试 fallback。"""
    attempts: set[int] = set()
    while True:
        route = routing.select(model, required)
        if route.route_target_id in attempts:
            raise RoutingError("no_eligible_target")
        attempts.add(route.route_target_id)
        try:
            return await adapter.complete(
                request, route, codex_oauth=codex_oauth, codex_repos=codex_repos
            ), route
        except Exception as exc:
            error_code = type(exc).__name__
            routing.mark_failure(route.route_target_id, error_code)
            usage.record_failure(
                context,
                model,
                request.protocol,
                _latency_ms(started),
                error_code,
                route,
            )


def _message_has_vision(messages: list[dict]) -> bool:
    """判断消息中是否包含图片或视觉内容块。"""
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            part_type = part.get("type") if isinstance(part, dict) else None
            if part_type in {"image", "image_url"}:
                return True
    return False


def _normalized_openai(payload: OpenAIChatRequest) -> NormalizedRequest:
    """将 OpenAI-compatible 请求转换为内部归一结构。"""
    stop = payload.stop
    if isinstance(stop, str):
        stop = [stop]
    max_tokens = payload.max_completion_tokens or payload.max_tokens
    return NormalizedRequest(
        protocol="openai",
        model=payload.model,
        messages=payload.messages,
        stream=payload.stream,
        max_tokens=max_tokens,
        temperature=payload.temperature,
        top_p=payload.top_p,
        stop=stop,
        tool_choice=payload.tool_choice,
        parallel_tool_calls=payload.parallel_tool_calls,
        presence_penalty=payload.presence_penalty,
        frequency_penalty=payload.frequency_penalty,
        seed=payload.seed,
        stream_include_usage=bool((payload.stream_options or {}).get("include_usage")),
        tools=payload.tools,
        raw=payload.model_dump(),
    )


def _normalized_anthropic(payload: AnthropicMessageRequest) -> NormalizedRequest:
    """将 Anthropic-compatible 请求归一化为 OpenAI 形状供 LiteLLM 调用。

    顶层 system 注入为第一条 system 消息；tool_use/tool_result/image blocks、
    Anthropic 格式 tools 和 tool_choice 全部翻译为 OpenAI 形状。
    """
    messages = anthropic_messages_to_openai(payload.messages)
    system_text = anthropic_system_to_openai(payload.system)
    if system_text:
        messages = [{"role": "system", "content": system_text}] + messages
    return NormalizedRequest(
        protocol="anthropic",
        model=payload.model,
        messages=messages,
        stream=payload.stream,
        max_tokens=payload.max_tokens,
        temperature=payload.temperature,
        tools=anthropic_tools_to_openai(payload.tools),
        tool_choice=anthropic_tool_choice_to_openai(payload.tool_choice),
        raw=payload.model_dump(),
    )


def _latency_ms(started: float) -> int:
    """计算从 started 到当前的毫秒耗时。"""
    return int((time.perf_counter() - started) * 1000)


def _openai_stream(
    response: AdapterResponse,
    model: str,
    usage: RequestUsage,
) -> StreamingResponse:
    """返回 OpenAI-compatible SSE stream。

    chunk 字段齐全；上游返回 tool_calls 时以单个 delta.tool_calls chunk 透传
    （合成流：上游非流式调用完成后一次性切片下发）。
    """

    async def events() -> AsyncIterator[str]:
        created = int(time.time())
        chunk_id = f"chatcmpl-jemodel-{created}"
        delta: dict[str, Any] = {"role": "assistant"}
        if response.content:
            delta["content"] = response.content
        if response.tool_calls:
            delta["tool_calls"] = [
                {"index": i, **tc} for i, tc in enumerate(response.tool_calls)
            ]
        chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        done_chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": response.finish_reason or "stop"}
            ],
        }
        yield f"data: {json.dumps(done_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


def _anthropic_stream(
    response: AdapterResponse,
    model: str,
    usage: RequestUsage,
) -> StreamingResponse:
    """返回 Anthropic-compatible SSE stream。

    Anthropic SDK（Claude Code）严格要求完整事件序列：
    message_start → content_block_start → content_block_delta →
    content_block_stop → message_delta → message_stop。
    上游返回 tool_calls 时转为 tool_use block 事件（input_json_delta 承载参数）。
    """

    async def events() -> AsyncIterator[str]:
        created = int(time.time())
        message_start = {
            "type": "message_start",
            "message": {
                "id": f"msg_jemodel_{created}",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": usage.input_tokens, "output_tokens": 0},
            },
        }
        yield f"event: message_start\ndata: {json.dumps(message_start)}\n\n"

        index = 0
        if response.content:
            start_event = {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": ""},
            }
            yield f"event: content_block_start\ndata: {json.dumps(start_event)}\n\n"
            delta_event = {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "text_delta", "text": response.content},
            }
            yield f"event: content_block_delta\ndata: {json.dumps(delta_event)}\n\n"
            stop_event = {"type": "content_block_stop", "index": index}
            yield f"event: content_block_stop\ndata: {json.dumps(stop_event)}\n\n"
            index += 1

        for block in openai_tool_calls_to_anthropic_blocks(response.tool_calls):
            start_event = {
                "type": "content_block_start",
                "index": index,
                "content_block": {
                    "type": "tool_use",
                    "id": block["id"],
                    "name": block["name"],
                    "input": {},
                },
            }
            yield f"event: content_block_start\ndata: {json.dumps(start_event)}\n\n"
            delta_event = {
                "type": "content_block_delta",
                "index": index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(block["input"], ensure_ascii=False),
                },
            }
            delta_data = json.dumps(delta_event, ensure_ascii=False)
            yield f"event: content_block_delta\ndata: {delta_data}\n\n"
            stop_event = {"type": "content_block_stop", "index": index}
            yield f"event: content_block_stop\ndata: {json.dumps(stop_event)}\n\n"
            index += 1

        message_delta = {
            "type": "message_delta",
            "delta": {
                "stop_reason": _map_stop_reason(response.finish_reason),
                "stop_sequence": None,
            },
            "usage": {"output_tokens": usage.output_tokens},
        }
        yield f"event: message_delta\ndata: {json.dumps(message_delta)}\n\n"

        yield 'event: message_stop\ndata: {"type":"message_stop"}\n\n'

    return StreamingResponse(events(), media_type="text/event-stream")
