"""OpenAI-compatible 和 Anthropic-compatible response formatter。"""

from __future__ import annotations

import json
import time
from typing import Any

from app.domain.entities import RequestUsage, RouteSelection
from app.domain.protocol import AdapterResponse


def openai_chat_response(
    route: RouteSelection,
    response: AdapterResponse,
    usage: RequestUsage,
) -> dict[str, Any]:
    """把 adapter response 格式化为 OpenAI chat completions response。

    上游返回 tool_calls 时透传，finish_reason 取上游真实值。
    """
    message: dict[str, Any] = {"role": "assistant", "content": response.content or None}
    if response.tool_calls:
        message["tool_calls"] = response.tool_calls
    return {
        "id": f"chatcmpl-jemodel-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": route.virtual_model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": response.finish_reason or "stop",
            }
        ],
        "usage": {
            "prompt_tokens": usage.input_tokens,
            "completion_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "prompt_tokens_details": {"cached_tokens": usage.cached_tokens},
            "completion_tokens_details": {"reasoning_tokens": usage.reasoning_tokens},
        },
    }


def anthropic_message_response(
    route: RouteSelection,
    response: AdapterResponse,
    usage: RequestUsage,
) -> dict[str, Any]:
    """把 adapter response 格式化为 Anthropic messages response。

    OpenAI tool_calls 转为 Anthropic tool_use content blocks；stop_reason 按
    上游 finish_reason 映射（tool_calls → tool_use，length → max_tokens）。
    """
    content: list[dict[str, Any]] = []
    if response.content:
        content.append({"type": "text", "text": response.content})
    content.extend(openai_tool_calls_to_anthropic_blocks(response.tool_calls))
    return {
        "id": f"msg_jemodel_{int(time.time())}",
        "type": "message",
        "role": "assistant",
        "model": route.virtual_model,
        "content": content,
        "stop_reason": _map_stop_reason(response.finish_reason),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_input_tokens": usage.cached_tokens,
        },
    }


def openai_tool_calls_to_anthropic_blocks(
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把 OpenAI tool_calls 转换为 Anthropic tool_use content blocks。"""
    blocks: list[dict[str, Any]] = []
    for call in tool_calls:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        try:
            input_value = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        except (ValueError, TypeError):
            input_value = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id") or "",
                "name": function.get("name") or "",
                "input": input_value,
            }
        )
    return blocks


def _map_stop_reason(finish_reason: str | None) -> str:
    """把 OpenAI finish_reason 映射为 Anthropic stop_reason。"""
    return {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
        "length": "max_tokens",
        "content_filter": "refusal",
    }.get(finish_reason or "", "end_turn")


def openai_models(models: list[dict[str, Any]]) -> dict[str, Any]:
    """格式化 OpenAI-compatible model list。"""
    return {
        "object": "list",
        "data": [
            {"id": model["name"], "object": "model", "created": 0, "owned_by": "jemodel"}
            for model in models
            if model["enabled"]
        ],
    }


def anthropic_models(models: list[dict[str, Any]]) -> dict[str, Any]:
    """格式化 Anthropic-compatible model list（带分页字段 has_more/first_id/last_id）。

    参考官方 GET /v1/models 规范：
    https://docs.anthropic.com/en/api/models-list
    """
    data = [
        {
            "id": model["name"],
            "type": "model",
            "display_name": model["name"],
            "created_at": "1970-01-01T00:00:00Z",
            "max_input_tokens": 200000,
            "max_tokens": 8192,
        }
        for model in models
        if model["enabled"]
    ]
    first_id = data[0]["id"] if data else None
    last_id = data[-1]["id"] if data else None
    return {
        "data": data,
        "first_id": first_id,
        "has_more": False,
        "last_id": last_id,
    }
