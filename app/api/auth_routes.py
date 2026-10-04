"""认证与用户中心 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import app.db as db
from app.core import security
from app.core.profiles import load_profiles

router = APIRouter(prefix="/api/auth")


def _require_db():
    if not db.db_ready():
        raise HTTPException(503, f"用户系统未启用（{db.db_error() or '数据库不可用'}）")


class RegisterReq(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_\u4e00-\u9fa5]+$")
    password: str = Field(min_length=6, max_length=64)
    nickname: str = Field(default="", max_length=32)
    health_profile_id: int | None = None


class LoginReq(BaseModel):
    username: str
    password: str


class ProfileReq(BaseModel):
    nickname: str | None = None
    health_profile_id: int | None = None
    phone: str | None = None
    old_password: str | None = None
    new_password: str | None = None


def _validate_profile(pid: int | None) -> int | None:
    if pid is None:
        return None
    if pid not in load_profiles():
        raise HTTPException(400, f"健康档案 {pid} 不存在（有效范围 1-50）")
    return pid


@router.post("/register")
async def register(req: RegisterReq):
    _require_db()
    pid = _validate_profile(req.health_profile_id)
    user = db.create_user(
        req.username, req.password, req.nickname, role="user", health_profile_id=pid)
    if not user:
        raise HTTPException(409, "用户名已存在")
    token = security.create_token(user["id"], user["role"])
    return {"token": token, "user": db.user_public(user)}


@router.post("/login")
async def login(req: LoginReq):
    _require_db()
    user = db.authenticate(req.username, req.password)
    if not user:
        raise HTTPException(401, "用户名或密码错误")
    token = security.create_token(user["id"], user["role"])
    return {"token": token, "user": db.user_public(user)}


@router.get("/me")
async def me(user: dict = Depends(security.current_user)):
    return db.user_public(user)


@router.put("/profile")
async def update_profile(req: ProfileReq, user: dict = Depends(security.current_user)):
    _require_db()
    fields: dict = {}
    if req.nickname is not None:
        fields["nickname"] = req.nickname.strip()[:32]
    if req.health_profile_id is not None:
        fields["health_profile_id"] = _validate_profile(req.health_profile_id)
    if req.phone is not None:
        fields["phone"] = req.phone.strip()[:32] or None
    if req.new_password:
        if not req.old_password or not security.verify_password(req.old_password, user["password_hash"]):
            raise HTTPException(400, "原密码不正确")
        if len(req.new_password) < 6:
            raise HTTPException(400, "新密码至少 6 位")
        fields["password_hash"] = security.hash_password(req.new_password)
    if not fields:
        raise HTTPException(400, "没有可更新的字段")
    db.update_user(user["id"], fields)
    return db.user_public(db.get_user_by_id(user["id"]))


@router.get("/sessions")
async def my_sessions(user: dict = Depends(security.current_user)):
    """我的历史会话列表。"""
    _require_db()
    return {"items": db.list_user_sessions(user["id"])}


@router.get("/sessions/{sid}/messages")
async def my_session_messages(sid: str, user: dict = Depends(security.current_user)):
    """查看自己某次会话的聊天记录。"""
    _require_db()
    row = db.query_one(
        "SELECT user_id FROM chat_sessions WHERE session_id=%s", (sid,))
    if not row or row["user_id"] != user["id"]:
        raise HTTPException(404, "会话不存在")
    return {"session_id": sid, "items": db.get_session_messages(sid)}
