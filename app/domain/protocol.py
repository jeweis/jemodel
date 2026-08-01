"""API handler 和 adapter 之间使用的协议归一对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NormalizedRequest:
    """供路由和 adapter 使用的协议无关模型请求。"""

    protocol: str
    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    top_p: float | None = None
    stop: list[str] | None = None
    tool_choice: Any = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    parallel_tool_calls: bool | None = None
    stream_include_usage: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterResponse:
    """上游模型 adapter 返回的协议无关响应。"""

    content: str
    raw: dict[str, Any]
    usage: dict[str, Any]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
