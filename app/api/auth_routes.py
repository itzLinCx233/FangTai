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
    # 个人信息（首次登录引导填写）
    gender: str | None = None                        # 男/女/保密
    birthday: str | None = None                      # YYYY-MM-DD，推算年龄须 0-100
    taboo: str | None = None                         # 忌口自由文本（顿号/逗号分隔），空=无
    height_cm: int | None = None                     # 0-250
    weight_kg: int | None = None                     # 0-300
    city: str | None = None                          # 居住地自由文本，可空


def _validate_profile_fields(req: ProfileReq) -> dict:
    """校验并转换个人信息字段，返回可写入的 fields 片段。"""
    import datetime as _dt
    fields: dict = {}
    if req.gender is not None:
        if req.gender not in ("男", "女", "保密"):
            raise HTTPException(400, "性别只能是 男 / 女 / 保密")
        fields["gender"] = req.gender
    if req.birthday is not None:
        if req.birthday == "":
            fields["birthday"] = None
        else:
            try:
                d = _dt.date.fromisoformat(req.birthday)
            except ValueError:
                raise HTTPException(400, "生日格式应为 YYYY-MM-DD")
            today = _dt.date.today()
            age = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
            if not (0 <= age <= 100):
                raise HTTPException(400, f"由生日推算的年龄须在 0-100 之间（当前 {age}）")
            fields["birthday"] = d
    if req.taboo is not None:
        taboo = req.taboo.strip()
        if len(taboo) > 100:
            raise HTTPException(400, "忌口内容过长（最多 100 字）")
        fields["taboo"] = taboo or None
    if req.height_cm is not None:
        if not (0 < req.height_cm <= 250):
            raise HTTPException(400, "身高须在 1-250 cm 之间")
        fields["height_cm"] = req.height_cm
    if req.weight_kg is not None:
        if not (0 < req.weight_kg <= 300):
            raise HTTPException(400, "体重须在 1-300 kg 之间")
        fields["weight_kg"] = req.weight_kg
    if req.city is not None:
        city = req.city.strip()
        if len(city) > 32:
            raise HTTPException(400, "居住地过长（最多 32 字）")
        fields["city"] = city or None
    return fields


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
    fields.update(_validate_profile_fields(req))
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
    row = db.query_one("SELECT user_id FROM chat_sessions WHERE session_id=:sid", {"sid": sid})
    if not row or row["user_id"] != user["id"]:
        raise HTTPException(404, "会话不存在")
    return {"session_id": sid, "items": db.get_session_messages(sid)}
