"""认证安全：pbkdf2 密码哈希 + HMAC-SHA256 签名 token（均标准库，零依赖）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from app import config

PBKDF2_ITER = 120_000


# ---------------------------------------------------------------- 密码
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITER)
    return f"pbkdf2${PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---------------------------------------------------------------- token
def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(payload_b64: str) -> str:
    return _b64e(hmac.new(config.AUTH_SECRET.encode(), payload_b64.encode(), hashlib.sha256).digest())


def create_token(user_id: int, role: str, ttl_hours: int | None = None) -> str:
    payload = {
        "uid": user_id, "role": role,
        "exp": int(time.time()) + (ttl_hours or config.AUTH_TOKEN_TTL_H) * 3600,
    }
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{payload_b64}.{_sign(payload_b64)}"


def parse_token(token: str) -> dict | None:
    """校验签名与过期，返回 payload 或 None。"""
    try:
        payload_b64, sig = token.split(".")
        if not hmac.compare_digest(_sign(payload_b64), sig):
            return None
        payload = json.loads(_b64d(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------- FastAPI 依赖
from fastapi import Header, HTTPException  # noqa: E402

import app.db as db  # noqa: E402


def _extract_token(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    payload = parse_token(authorization[7:])
    if not payload:
        raise HTTPException(401, "登录已过期，请重新登录")
    return payload


def current_user(authorization: str | None = Header(None)) -> dict:
    """要求已登录（返回 users 行）。"""
    if not db.db_ready():
        raise HTTPException(503, "用户系统未启用（数据库不可用）")
    payload = _extract_token(authorization)
    user = db.get_user_by_id(payload["uid"])
    if not user or not user.get("is_active", 1):
        raise HTTPException(401, "账户不存在或已禁用")
    return user


def require_admin(authorization: str | None = Header(None)) -> dict:
    """要求管理员。"""
    user = current_user(authorization)
    if user["role"] != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user
