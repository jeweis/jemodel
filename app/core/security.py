"""API key 创建、哈希和校验相关安全工具。"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any


def create_api_key() -> str:
    """创建面向用户展示的 jemodel API key。"""
    return f"jm_{secrets.token_urlsafe(32)}"


def hash_api_key(raw_key: str, secret_key: str) -> str:
    """使用应用 secret 对 API key 做哈希，避免存储明文。"""
    digest = hmac.new(secret_key.encode(), raw_key.encode(), hashlib.sha256).hexdigest()
    return f"sha256:{digest}"


def verify_api_key(raw_key: str, stored_hash: str, secret_key: str) -> bool:
    """用常量时间比较校验 API key，降低时序泄漏风险。"""
    return hmac.compare_digest(hash_api_key(raw_key, secret_key), stored_hash)


def key_prefix(raw_key: str) -> str:
    """返回短前缀，用于快速查找和审计展示。"""
    return raw_key[:12]


def hash_password(password: str) -> str:
    """使用 PBKDF2 哈希控制台密码，避免引入额外运行时依赖。"""
    salt = secrets.token_bytes(16)
    iterations = 220_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    salt_text = _b64encode(salt)
    digest_text = _b64encode(digest)
    return f"pbkdf2_sha256${iterations}${salt_text}${digest_text}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    """用常量时间比较校验控制台密码。"""
    if not stored_hash:
        return False
    try:
        algorithm, iterations_text, salt_text, digest_text = stored_hash.split("$", maxsplit=3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            _b64decode(salt_text),
            int(iterations_text),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(_b64encode(digest), digest_text)


def create_session_token(payload: dict[str, Any], secret_key: str, ttl_seconds: int) -> str:
    """创建 HMAC 签名的短期控制面会话 token。"""
    body = {**payload, "exp": int(time.time()) + ttl_seconds}
    body_text = _b64encode(json.dumps(body, separators=(",", ":")).encode())
    signature = _sign(body_text, secret_key)
    return f"jms_{body_text}.{signature}"


def verify_session_token(token: str, secret_key: str) -> dict[str, Any] | None:
    """校验控制面会话 token 并返回载荷，失败时返回 None。"""
    if not token.startswith("jms_") or "." not in token:
        return None
    body_text, signature = token.removeprefix("jms_").split(".", maxsplit=1)
    if not hmac.compare_digest(_sign(body_text, secret_key), signature):
        return None
    try:
        payload = json.loads(_b64decode(body_text))
    except (ValueError, TypeError):
        return None
    if int(payload.get("exp", 0)) <= int(time.time()):
        return None
    return payload


def _sign(value: str, secret_key: str) -> str:
    """为会话 token 生成 URL-safe HMAC 签名。"""
    digest = hmac.new(secret_key.encode(), value.encode(), hashlib.sha256).digest()
    return _b64encode(digest)


def _b64encode(value: bytes) -> str:
    """编码为不带补位的 URL-safe base64 文本。"""
    return urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    """解码不带补位的 URL-safe base64 文本。"""
    padded = value + ("=" * (-len(value) % 4))
    return urlsafe_b64decode(padded.encode())
