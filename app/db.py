"""MySQL 数据层（SQLAlchemy 2.0 ORM + Alembic 迁移）。

对外函数与旧版手写 DAO 完全同名同签名（auth/admin/routes 零改动）：
  db_ready / db_error / init_db / query / query_one / execute / execute_returning_id
  user_public / create_user / get_user_by_id / get_user_by_name / authenticate /
  update_user / delete_user / list_users / save_session / save_message /
  list_user_sessions / get_session_messages / admin_stats / admin_recent_sessions /
  get_setting / set_setting

- MYSQL_ENABLED=false 或连接失败时进入"无DB模式"：用户系统端点 503，评测核心端点不受影响
- schema 演进用 Alembic：`alembic revision --autogenerate` + `alembic upgrade head`；
  init_db 对全新库直接建表并 stamp head（幂等）
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text,
    create_engine, func, select, text, update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, Session, mapped_column, sessionmaker,
)

from app import config
from app.core.security import hash_password, verify_password


# ---------------------------------------------------------------- 模型
class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    health_profile_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # 个人信息（注册后首次登录引导填写；未填为 NULL，前端显示为 保密/无）
    gender: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)          # 男/女/保密
    birthday: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)        # 出生日期（推算年龄 0-100）
    taboo: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)         # 忌口（逗号分隔，空=NULL 即"无"）
    height_cm: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)         # 身高 cm（可空=保密）
    weight_kg: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)         # 体重 kg（可空=保密）
    city: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)           # 居住地（可空=保密）
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                 onupdate=func.now())
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    __table_args__ = (Index("idx_role", "role"), Index("idx_created", "created_at"))


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    __table_args__ = (Index("idx_user", "user_id"),)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(40), ForeignKey("chat_sessions.session_id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # MySQL utf8mb4 下 VARCHAR 上限 16383 字符，长对话正文用 MEDIUMTEXT（16MB）
    content: Mapped[str] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    __table_args__ = (Index("idx_session", "session_id"),)


class AppSetting(Base):
    """通用 KV 设置表（运行时可变配置，如管理后台切换的 LLM 参数）。"""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False,
                                                 server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------- 引擎
_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None
_db_ready = False
_db_error: str = ""
_init_lock = threading.Lock()


def _mysql_url() -> str:
    pwd = config.MYSQL_PASSWORD or ""
    return (f"mysql+pymysql://{config.MYSQL_USER}:{pwd}@"
            f"{config.MYSQL_HOST}:{config.MYSQL_PORT}/{config.MYSQL_DATABASE}?charset=utf8mb4")


def _get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(
            _mysql_url(), pool_pre_ping=True, pool_recycle=3600,
            pool_size=config.MYSQL_POOL_SIZE, max_overflow=4, future=True,
        )
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def _session() -> Session:
    _get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def db_ready() -> bool:
    return _db_ready


def db_error() -> str:
    return _db_error


# ---------------------------------------------------------------- 兼容层：原生 SQL 小工具
def query(sql: str, args: tuple = ()) -> list[dict]:
    """原生 SQL 查询（少量存量调用使用；新代码请用 ORM DAO）。"""
    with _get_engine().connect() as conn:
        rows = conn.execute(text(sql), args).mappings().all()
        return [dict(r) for r in rows]


def query_one(sql: str, args: tuple = ()) -> Optional[dict]:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args: tuple = ()) -> int:
    with _get_engine().begin() as conn:
        return conn.execute(text(sql), args).rowcount


def execute_returning_id(sql: str, args: tuple = ()) -> int:
    with _get_engine().begin() as conn:
        result = conn.execute(text(sql), args)
        return result.lastrowid


# ---------------------------------------------------------------- 初始化
def init_db() -> bool:
    """建库（若缺）→ 建表（幂等）→ 管理员账户 → Alembic stamp。返回是否就绪。"""
    global _db_ready, _db_error
    if not config.MYSQL_ENABLED:
        _db_error = "MYSQL_ENABLED=false"
        print("[db] MySQL 未启用（评测/无DB模式）")
        return False
    with _init_lock:
        if _db_ready:
            return True
        try:
            # 1) 建库（连 server 不连 db）
            import pymysql
            server = pymysql.connect(
                host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                charset="utf8mb4", autocommit=True, connect_timeout=5,
            )
            with server.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{config.MYSQL_DATABASE}` "
                    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            server.close()
            # 2) schema 版本化迁移（空库跑全部；已 stamp 的旧库增量升级）
            try:
                from alembic.config import Config
                from alembic import command
                cfg = Config(str(config.BASE_DIR / "alembic.ini"))
                cfg.set_main_option("script_location", str(config.BASE_DIR / "alembic"))
                command.upgrade(cfg, "head")
            except Exception as mig_err:
                print(f"[db] alembic 迁移失败，回退 create_all: {mig_err}")
                Base.metadata.create_all(_get_engine())
            # 3) 管理员（已存在则跳过）
            with _session() as s:
                admin = s.execute(select(User).where(User.username == config.ADMIN_USERNAME)).scalar_one_or_none()
                if admin is None:
                    s.add(User(username=config.ADMIN_USERNAME,
                               password_hash=hash_password(config.ADMIN_PASSWORD),
                               nickname="管理员", role="admin"))
                    s.commit()
                    print(f"[db] 已创建管理员账户 {config.ADMIN_USERNAME}")
                else:
                    print(f"[db] 管理员账户 {config.ADMIN_USERNAME} 已存在，跳过创建")
            _db_ready = True
            print(f"[db] MySQL 就绪(SQLAlchemy): {config.MYSQL_HOST}:{config.MYSQL_PORT}/{config.MYSQL_DATABASE}")
            return True
        except Exception as e:
            _db_error = str(e)
            print(f"[db] MySQL 初始化失败（用户系统不可用，评测核心不受影响）: {e}")
            return False


# ---------------------------------------------------------------- 用户 DAO
def _user_dict(u: User) -> dict:
    return {
        "id": u.id, "username": u.username, "nickname": u.nickname,
        "role": u.role, "health_profile_id": u.health_profile_id,
        "phone": u.phone, "is_active": bool(u.is_active),
        "gender": u.gender, "birthday": str(u.birthday) if u.birthday else None,
        "taboo": u.taboo, "height_cm": u.height_cm, "weight_kg": u.weight_kg,
        "city": u.city,
        "created_at": str(u.created_at or ""), "last_login_at": str(u.last_login_at or ""),
        "password_hash": u.password_hash,
    }


def user_public(u: dict) -> dict:
    d = dict(u)
    d.pop("password_hash", None)
    return d


def create_user(username: str, password: str, nickname: str = "",
                role: str = "user", health_profile_id: Optional[int] = None) -> Optional[dict]:
    with _session() as s:
        exists = s.execute(select(User.id).where(User.username == username)).scalar_one_or_none()
        if exists:
            return None
        u = User(username=username, password_hash=hash_password(password),
                 nickname=nickname or username, role=role, health_profile_id=health_profile_id)
        s.add(u)
        s.commit()
        return user_public(_user_dict(u))


def get_user_by_id(uid: int) -> Optional[dict]:
    with _session() as s:
        u = s.get(User, uid)
        return _user_dict(u) if u else None


def get_user_by_name(username: str) -> Optional[dict]:
    with _session() as s:
        u = s.execute(select(User).where(User.username == username)).scalar_one_or_none()
        return _user_dict(u) if u else None


def authenticate(username: str, password: str) -> Optional[dict]:
    u = get_user_by_name(username)
    if not u or not u.get("is_active", True):
        return None
    if not verify_password(password, u["password_hash"]):
        return None
    with _session() as s:
        s.execute(update(User).where(User.id == u["id"]).values(last_login_at=func.now()))
        s.commit()
    return u


def update_user(uid: int, fields: dict) -> bool:
    allow = {"nickname", "role", "health_profile_id", "phone", "is_active", "password_hash",
             "gender", "birthday", "taboo", "height_cm", "weight_kg", "city"}
    vals = {k: v for k, v in fields.items() if k in allow}
    if not vals:
        return False
    with _session() as s:
        result = s.execute(update(User).where(User.id == uid).values(**vals))
        s.commit()
        return result.rowcount > 0


def delete_user(uid: int) -> bool:
    with _session() as s:
        u = s.get(User, uid)
        if not u:
            return False
        s.delete(u)
        s.commit()
        return True


def list_users(search: str = "", page: int = 1, size: int = 10) -> tuple[list[dict], int]:
    with _session() as s:
        stmt = select(User)
        cnt = select(func.count()).select_from(User)
        if search:
            like = f"%{search}%"
            cond = User.username.like(like) | User.nickname.like(like)
            stmt, cnt = stmt.where(cond), cnt.where(cond)
        total = s.execute(cnt).scalar() or 0
        rows = s.execute(
            stmt.order_by(User.id.desc()).limit(size).offset((page - 1) * size)
        ).scalars().all()
        return [user_public(_user_dict(u)) for u in rows], total


# ---------------------------------------------------------------- 会话 DAO
def save_session(session_id: str, user_id: int, title: str = ""):
    with _session() as s:
        exists = s.execute(select(ChatSession.id).where(ChatSession.session_id == session_id)).scalar_one_or_none()
        if exists:
            return
        s.add(ChatSession(session_id=session_id, user_id=user_id, title=title[:128]))
        s.commit()


def save_message(session_id: str, role: str, content: str):
    with _session() as s:
        s.add(ChatMessage(session_id=session_id, role=role, content=content[:60000]))
        s.commit()


def list_user_sessions(user_id: int, limit: int = 50) -> list[dict]:
    with _session() as s:
        rows = s.execute(
            select(ChatSession.session_id, ChatSession.title, ChatSession.created_at,
                   func.count(ChatMessage.id).label("msg_count"))
            .join(ChatMessage, ChatMessage.session_id == ChatSession.session_id, isouter=True)
            .where(ChatSession.user_id == user_id)
            .group_by(ChatSession.id)
            .order_by(ChatSession.id.desc()).limit(limit)
        ).mappings().all()
        return [{"session_id": r["session_id"], "title": r["title"],
                 "created_at": str(r["created_at"]), "msg_count": r["msg_count"] or 0}
                for r in rows]


def get_session_messages(session_id: str, limit: int = 200) -> list[dict]:
    with _session() as s:
        rows = s.execute(
            select(ChatMessage.role, ChatMessage.content, ChatMessage.created_at)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.asc()).limit(limit)
        ).mappings().all()
        return [{"role": r["role"], "content": r["content"], "created_at": str(r["created_at"])}
                for r in rows]


def admin_stats() -> dict:
    with _session() as s:
        return {
            "users_total": s.execute(select(func.count()).select_from(User)).scalar(),
            "users_today": s.execute(select(func.count()).select_from(User)
                                     .where(User.created_at >= func.curdate())).scalar(),
            "sessions_total": s.execute(select(func.count()).select_from(ChatSession)).scalar(),
            "messages_total": s.execute(select(func.count()).select_from(ChatMessage)).scalar(),
        }


def admin_recent_sessions(search: str = "", page: int = 1, size: int = 10):
    with _session() as s:
        stmt = (select(ChatSession, User.username, User.nickname)
                .join(User, ChatSession.user_id == User.id))
        cnt = (select(func.count()).select_from(ChatSession)
               .join(User, ChatSession.user_id == User.id))
        if search:
            like = f"%{search}%"
            cond = (ChatSession.title.like(like) | User.username.like(like)
                    | ChatSession.session_id.like(like))
            stmt, cnt = stmt.where(cond), cnt.where(cond)
        total = s.execute(cnt).scalar() or 0
        rows = s.execute(
            stmt.order_by(ChatSession.id.desc()).limit(size).offset((page - 1) * size)
        ).all()
        items = []
        for cs, username, nickname in rows:
            msg_count = s.execute(
                select(func.count()).select_from(ChatMessage)
                .where(ChatMessage.session_id == cs.session_id)).scalar()
            items.append({"session_id": cs.session_id, "title": cs.title,
                          "created_at": str(cs.created_at), "username": username,
                          "nickname": nickname, "msg_count": msg_count or 0})
        return items, total


# ---------------------------------------------------------------- 设置 KV DAO
# 无 DB 模式的进程内兜底存储（重启即失，仅保证接口可用）
_MEM_SETTINGS: dict[str, str] = {}


def get_setting(key: str) -> Optional[str]:
    """读取设置项；无 DB 模式读进程内兜底。"""
    if not _db_ready:
        return _MEM_SETTINGS.get(key)
    with _session() as s:
        row = s.get(AppSetting, key)
        return row.value if row else None


def set_setting(key: str, value: str) -> None:
    """写入/更新设置项（upsert）；无 DB 模式写进程内兜底。"""
    if not _db_ready:
        _MEM_SETTINGS[key] = value
        return
    with _session() as s:
        row = s.get(AppSetting, key)
        if row:
            row.value = value
        else:
            s.add(AppSetting(key=key, value=value))
        s.commit()
