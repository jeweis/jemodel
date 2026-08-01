"""Anthropic Messages 协议到 OpenAI Chat 协议的请求翻译。

Claude Code 等 Anthropic 客户端发送的 `system` 顶层参数、Anthropic 格式 tools
（`{name, description, input_schema}`）、`tool_use` / `tool_result` content blocks，
以及带 `cache_control` 的 blocks，在调用 OpenAI-compatible 上游前都需要归一化。
"""

from __future__ import annotations

import json
from typing import Any


def anthropic_system_to_openai(system: Any) -> str:
    """把 Anthropic 顶层 system 参数归一化为纯文本。

    Anthropic 允许 system 为字符串或 text block 数组（可能带 cache_control）。
    """
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts: list[str] = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "\n\n".join(parts)
    return ""


def anthropic_tools_to_openai(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """把 Anthropic tool 定义转换为 OpenAI function tool 定义。"""
    if not tools:
        return []
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not name:
            continue
        # 已是 OpenAI 格式时透传
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            converted.append(tool)
            continue
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description") or "",
                    "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return converted


def anthropic_tool_choice_to_openai(tool_choice: Any) -> Any:
    """把 Anthropic tool_choice 转换为 OpenAI tool_choice。"""
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        return tool_choice
    if not isinstance(tool_choice, dict):
        return None
    choice_type = tool_choice.get("type")
    if choice_type == "auto":
        return "auto"
    if choice_type == "any":
        return "required"
    if choice_type == "none":
        return "none"
    if choice_type == "tool":
        name = tool_choice.get("name")
        if name:
            return {"type": "function", "function": {"name": name}}
    return None


def anthropic_messages_to_openai(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 Anthropic messages 归一化为 OpenAI messages。

    - string content 透传；
    - text block 拼接为纯文本（丢弃 cache_control 等扩展字段）；
    - assistant 的 tool_use blocks 转为 OpenAI tool_calls；
    - user 的 tool_result blocks 拆分为独立的 OpenAI role=tool 消息；
    - image blocks 转为 OpenAI image_url 格式。
    """
    converted: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, str) or content is None:
            converted.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            converted.append({"role": role, "content": str(content)})
            continue
        if role == "assistant":
            converted.append(_assistant_message_from_blocks(content))
            continue
        converted.extend(_user_messages_from_blocks(role, content))
    return converted


def _assistant_message_from_blocks(blocks: list[Any]) -> dict[str, Any]:
    """把 assistant 消息的 blocks 转为 OpenAI 格式（tool_use → tool_calls）。"""
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                text_parts.append(text)
        elif block_type == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": block.get("name") or "",
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                }
            )
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(text_parts) if text_parts else None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _user_messages_from_blocks(role: Any, blocks: list[Any]) -> list[dict[str, Any]]:
    """把 user 消息的 blocks 归一化；tool_result 拆为独立 role=tool 消息。"""
    tool_results: list[dict[str, Any]] = []
    content_parts: list[Any] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "tool_result":
            tool_results.append(
                {
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id") or "",
                    "content": _tool_result_text(block.get("content")),
                }
            )
        elif block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                content_parts.append({"type": "text", "text": text})
        elif block_type == "image":
            image_part = _image_block_to_openai(block)
            if image_part is not None:
                content_parts.append(image_part)
    messages: list[dict[str, Any]] = []
    if content_parts:
        # 纯文本时降级为字符串，兼容性更好
        if all(part.get("type") == "text" for part in content_parts):
            merged = "\n".join(part["text"] for part in content_parts)
            messages.append({"role": role, "content": merged})
        else:
            messages.append({"role": role, "content": content_parts})
    messages.extend(tool_results)
    return messages


def _tool_result_text(content: Any) -> str:
    """把 tool_result 的 content 归一化为纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _image_block_to_openai(block: dict[str, Any]) -> dict[str, Any] | None:
    """把 Anthropic image block 转为 OpenAI image_url part。"""
    source = block.get("source")
    if not isinstance(source, dict):
        return None
    if source.get("type") == "base64":
        media_type = source.get("media_type") or "image/png"
        data = source.get("data") or ""
        return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}}
    if source.get("type") == "url":
        url = source.get("url")
        if url:
            return {"type": "image_url", "image_url": {"url": url}}
    return None