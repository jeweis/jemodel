"""Anthropic → OpenAI 协议翻译测试。"""

from app.api.data.anthropic_compat import (
    anthropic_messages_to_openai,
    anthropic_system_to_openai,
    anthropic_tool_choice_to_openai,
    anthropic_tools_to_openai,
)


def test_system_string_passthrough() -> None:
    """字符串 system 直接透传。"""
    assert anthropic_system_to_openai("You are helpful") == "You are helpful"


def test_system_block_array_joined_and_cache_control_dropped() -> None:
    """block 数组拼接为纯文本，cache_control 被丢弃。"""
    system = [
        {"type": "text", "text": "Part 1", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "Part 2"},
    ]
    assert anthropic_system_to_openai(system) == "Part 1\n\nPart 2"


def test_system_none_returns_empty() -> None:
    assert anthropic_system_to_openai(None) == ""


def test_tools_converted_to_openai_function_format() -> None:
    """Anthropic tools 转为 OpenAI function 格式。"""
    tools = [
        {
            "name": "get_weather",
            "description": "Get weather",
            "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
        }
    ]
    converted = anthropic_tools_to_openai(tools)
    assert converted == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]


def test_tool_choice_variants() -> None:
    """tool_choice 各形态转换。"""
    assert anthropic_tool_choice_to_openai({"type": "auto"}) == "auto"
    assert anthropic_tool_choice_to_openai({"type": "any"}) == "required"
    assert anthropic_tool_choice_to_openai({"type": "tool", "name": "x"}) == {
        "type": "function",
        "function": {"name": "x"},
    }


def test_messages_string_content_passthrough() -> None:
    """字符串 content 直接透传。"""
    messages = [{"role": "user", "content": "hello"}]
    assert anthropic_messages_to_openai(messages) == [{"role": "user", "content": "hello"}]


def test_user_text_blocks_merged_to_string() -> None:
    """user 的 text blocks 合并为字符串，cache_control 丢弃。"""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "there"},
            ],
        }
    ]
    assert anthropic_messages_to_openai(messages) == [
        {"role": "user", "content": "hi\nthere"}
    ]


def test_assistant_tool_use_converted_to_tool_calls() -> None:
    """assistant 的 tool_use blocks 转为 OpenAI tool_calls。"""
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "get_weather",
                    "input": {"city": "Beijing"},
                },
            ],
        }
    ]
    converted = anthropic_messages_to_openai(messages)
    assert converted == [
        {
            "role": "assistant",
            "content": "Let me check.",
            "tool_calls": [
                {
                    "id": "toolu_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "Beijing"}'},
                }
            ],
        }
    ]


def test_user_tool_result_split_to_tool_messages() -> None:
    """user 的 tool_result blocks 拆分为独立 role=tool 消息。"""
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": [{"type": "text", "text": "sunny"}],
                }
            ],
        }
    ]
    converted = anthropic_messages_to_openai(messages)
    assert converted == [
        {"role": "tool", "tool_call_id": "toolu_1", "content": "sunny"}
    ]


def test_image_block_converted_to_image_url() -> None:
    """base64 image block 转为 OpenAI image_url 格式。"""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "iVBOR"},
                },
            ],
        }
    ]
    converted = anthropic_messages_to_openai(messages)
    assert converted == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR"}},
            ],
        }
    ]