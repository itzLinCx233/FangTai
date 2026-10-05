"""管理员后台 API（需 admin 角色）：统计 / 用户增删改查 / 会话记录 / LLM 运行时配置。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import app.db as db
from app.core import security
from app.core.profiles import load_profiles

router = APIRouter(prefix="/api/admin", dependencies=[])


def _validate_profile(pid: int | None) -> int | None:
    if pid is None:
        return None
    if pid not in load_profiles():
        raise HTTPException(400, f"健康档案 {pid} 不存在（有效范围 1-50）")
    return pid


@router.get("/stats")
async def stats(admin: dict = Depends(security.require_admin)):
    return db.admin_stats()


@router.get("/users")
async def users(
    admin: dict = Depends(security.require_admin),
    search: str = Query(default="", max_length=64),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
):
    rows, total = db.list_users(search, page, size)
    return {"total": total, "page": page, "size": size,
            "items": [db.user_public(u) for u in rows]}


class AdminCreateUser(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_\u4e00-\u9fa5]+$")
    password: str = Field(min_length=6, max_length=64)
    nickname: str = Field(default="", max_length=32)
    role: str = Field(default="user", pattern=r"^(user|admin)$")
    health_profile_id: int | None = None


@router.post("/users")
async def create_user(req: AdminCreateUser, admin: dict = Depends(security.require_admin)):
    user = db.create_user(
        req.username, req.password, req.nickname,
        role=req.role, health_profile_id=_validate_profile(req.health_profile_id))
    if not user:
        raise HTTPException(409, "用户名已存在")
    return db.user_public(user)


class AdminUpdateUser(BaseModel):
    nickname: str | None = None
    role: str | None = Field(default=None, pattern=r"^(user|admin)$")
    health_profile_id: int | None = None
    phone: str | None = None
    is_active: bool | None = None
    new_password: str | None = Field(default=None, min_length=6, max_length=64)


@router.put("/users/{uid}")
async def update_user(uid: int, req: AdminUpdateUser, admin: dict = Depends(security.require_admin)):
    target = db.get_user_by_id(uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    fields: dict = {}
    if req.nickname is not None:
        fields["nickname"] = req.nickname.strip()[:32]
    if req.role is not None:
        if target["id"] == admin["id"] and req.role != "admin":
            raise HTTPException(400, "不能取消自己的管理员角色")
        fields["role"] = req.role
    if req.health_profile_id is not None:
        fields["health_profile_id"] = _validate_profile(req.health_profile_id)
    if req.phone is not None:
        fields["phone"] = req.phone.strip()[:32] or None
    if req.is_active is not None:
        if target["id"] == admin["id"] and not req.is_active:
            raise HTTPException(400, "不能禁用自己")
        fields["is_active"] = 1 if req.is_active else 0
    if req.new_password:
        fields["password_hash"] = security.hash_password(req.new_password)
    if not fields:
        raise HTTPException(400, "没有可更新的字段")
    db.update_user(uid, fields)
    return db.user_public(db.get_user_by_id(uid))


@router.delete("/users/{uid}")
async def delete_user(uid: int, admin: dict = Depends(security.require_admin)):
    if uid == admin["id"]:
        raise HTTPException(400, "不能删除自己")
    target = db.get_user_by_id(uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    if target["role"] == "admin":
        raise HTTPException(400, "管理员账户不允许删除（如需删除，请先将其角色改为普通用户）")
    db.delete_user(uid)
    return {"deleted": uid}


@router.get("/sessions")
async def sessions(
    admin: dict = Depends(security.require_admin),
    search: str = Query(default="", max_length=64),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
):
    rows, total = db.admin_recent_sessions(search, page, size)
    return {"total": total, "page": page, "size": size, "items": rows}


@router.get("/sessions/{sid}/messages")
async def session_messages(sid: str, admin: dict = Depends(security.require_admin)):
    row = db.query_one("SELECT session_id FROM chat_sessions WHERE session_id=%s", (sid,))
    if not row:
        raise HTTPException(404, "会话不存在")
    return {"session_id": sid, "items": db.get_session_messages(sid)}


# ---------------------------------------------------------------- LLM 运行时配置
def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "****"
    return f"{key[:5]}****{key[-4:]}"


def _config_view(cfg: dict) -> dict:
    """对外视图：api_key 打码 + 是否已持久化。"""
    return {**cfg, "api_key": _mask_key(cfg.get("api_key", "")),
            "persisted": db.db_ready()}


@router.get("/llm-config")
async def get_llm_config(admin: dict = Depends(security.require_admin)):
    from app.core import runtime_settings
    return _config_view(runtime_settings.get())


class LLMConfigUpdate(BaseModel):
    base_url: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=100)
    fast_model: str | None = Field(default=None, max_length=100)  # 空串=回退主模型
    thinking: str | None = Field(default=None,
                                 pattern=r"^(disabled|enabled|low|high|max|auto)$")
    temperature: float | None = Field(default=None, ge=0, le=2)


@router.put("/llm-config")
async def update_llm_config(req: LLMConfigUpdate, admin: dict = Depends(security.require_admin)):
    from app.core import runtime_settings
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    # 前端把打码后的 key 原样传回 = 未修改，跳过
    if fields.get("api_key") and "****" in fields["api_key"]:
        fields.pop("api_key")
    if not fields:
        raise HTTPException(400, "没有可更新的字段")
    cfg = runtime_settings.update(fields)
    return _config_view(cfg)


@router.post("/llm-config/test")
async def test_llm_config(admin: dict = Depends(security.require_admin)):
    """用当前生效配置发一条 8-token 测试请求，返回连通性与延迟。"""
    from app.core.llm import get_llm
    llm = get_llm()
    t0 = time.perf_counter()
    try:
        out = await llm.chat([llm.system("只输出两个字：正常")], max_tokens=8,
                             thinking="disabled")
        return {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000),
                "model": llm.model, "reply": out.strip()[:20]}
    except Exception as e:
        return {"ok": False, "latency_ms": round((time.perf_counter() - t0) * 1000),
                "model": llm.model, "error": str(e)[:200]}
