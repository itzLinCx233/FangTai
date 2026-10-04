"""MySQL 数据层：轻量连接池 + 建库建表 + 管理员自动创建 + 用户/会话 DAO。

- 驱动 PyMySQL（纯 Python，免编译）
- MYSQL_ENABLED=false 或连接失败时进入"无DB模式"：用户系统端点返回 503，
  评测核心端点（/api/chat 等）不受影响
- 表：users / chat_sessions / chat_messages
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Optional

from app import config
from app.core.security import hash_password, verify_password

_pool: list = []               # 空闲连接
_pool_lock = threading.Lock()
_db_ready = False
_db_error: str = ""


def db_ready() -> bool:
    return _db_ready


def db_error() -> str:
    return _db_error


def _new_conn():
    import pymysql
    return pymysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT,
        user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE, charset="utf8mb4",
        autocommit=True, cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
    )


@contextmanager
def get_conn():
    """取连接（池化 + ping 保活），用完归还。"""
    global _db_ready
    conn = None
    with _pool_lock:
        while _pool:
            conn = _pool.pop()
            break
    if conn is None:
        conn = _new_conn()
    else:
        try:
            conn.ping(reconnect=True)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            conn = _new_conn()
    try:
        yield conn
    finally:
        with _pool_lock:
            if len(_pool) < config.MYSQL_POOL_SIZE:
                _pool.append(conn)
            else:
                conn.close()


def query(sql: str, args: tuple = ()) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()


def query_one(sql: str, args: tuple = ()) -> Optional[dict]:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args: tuple = ()) -> int:
    """写操作，返回受影响行数（或 lastrowid 由调用方取）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.rowcount


def execute_returning_id(sql: str, args: tuple = ()) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.lastrowid


# ---------------------------------------------------------------- 初始化
SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(64) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        nickname VARCHAR(64) NOT NULL DEFAULT '',
        role VARCHAR(16) NOT NULL DEFAULT 'user',
        health_profile_id INT NULL,
        phone VARCHAR(32) NULL,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        last_login_at DATETIME NULL,
        INDEX idx_role (role),
        INDEX idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        session_id VARCHAR(40) NOT NULL UNIQUE,
        user_id INT NOT NULL,
        title VARCHAR(128) NOT NULL DEFAULT '',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        session_id VARCHAR(40) NOT NULL,
        role VARCHAR(16) NOT NULL,
        content MEDIUMTEXT NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_session (session_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


def init_db() -> bool:
    """建库建表 + 创建管理员。返回是否就绪。"""
    global _db_ready, _db_error
    if not config.MYSQL_ENABLED:
        _db_error = "MYSQL_ENABLED=false"
        print("[db] MySQL 未启用（评测/无DB模式）")
        return False
    try:
        import pymysql
        # 1) 建库（连 server 不连 db）
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
        # 2) 建表
        with get_conn() as conn:
            with conn.cursor() as cur:
                for ddl in SCHEMA:
                    cur.execute(ddl)
        # 3) 管理员（已存在则跳过）
        admin = query_one("SELECT id FROM users WHERE username=%s", (config.ADMIN_USERNAME,))
        if not admin:
            execute(
                "INSERT INTO users (username, password_hash, nickname, role) "
                "VALUES (%s, %s, %s, 'admin')",
                (config.ADMIN_USERNAME, hash_password(config.ADMIN_PASSWORD), "管理员"),
            )
            print(f"[db] 已创建管理员账户 {config.ADMIN_USERNAME}")
        else:
            print(f"[db] 管理员账户 {config.ADMIN_USERNAME} 已存在，跳过创建")
        _db_ready = True
        print(f"[db] MySQL 就绪: {config.MYSQL_HOST}:{config.MYSQL_PORT}/{config.MYSQL_DATABASE}")
        return True
    except Exception as e:
        _db_error = str(e)
        print(f"[db] MySQL 初始化失败（用户系统不可用，评测核心不受影响）: {e}")
        return False


# ---------------------------------------------------------------- 用户 DAO
def user_public(u: dict) -> dict:
    return {
        "id": u["id"], "username": u["username"], "nickname": u["nickname"],
        "role": u["role"], "health_profile_id": u.get("health_profile_id"),
        "phone": u.get("phone"), "is_active": bool(u.get("is_active", 1)),
        "created_at": str(u.get("created_at") or ""), "last_login_at": str(u.get("last_login_at") or ""),
    }


def create_user(username: str, password: str, nickname: str = "",
                role: str = "user", health_profile_id: Optional[int] = None) -> Optional[dict]:
    if query_one("SELECT id FROM users WHERE username=%s", (username,)):
        return None
    uid = execute_returning_id(
        "INSERT INTO users (username, password_hash, nickname, role, health_profile_id) "
        "VALUES (%s,%s,%s,%s,%s)",
        (username, hash_password(password), nickname or username, role, health_profile_id),
    )
    return get_user_by_id(uid)


def get_user_by_id(uid: int) -> Optional[dict]:
    return query_one("SELECT * FROM users WHERE id=%s", (uid,))


def get_user_by_name(username: str) -> Optional[dict]:
    return query_one("SELECT * FROM users WHERE username=%s", (username,))


def authenticate(username: str, password: str) -> Optional[dict]:
    u = get_user_by_name(username)
    if not u or not u.get("is_active", 1):
        return None
    if not verify_password(password, u["password_hash"]):
        return None
    execute("UPDATE users SET last_login_at=NOW() WHERE id=%s", (u["id"],))
    return u


def update_user(uid: int, fields: dict) -> bool:
    """fields 只允许白名单列。"""
    allow = {"nickname", "role", "health_profile_id", "phone", "is_active", "password_hash"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allow:
            continue
        sets.append(f"{k}=%s")
        args.append(v)
    if not sets:
        return False
    args.append(uid)
    return execute(f"UPDATE users SET {', '.join(sets)} WHERE id=%s", tuple(args)) > 0


def delete_user(uid: int) -> bool:
    return execute("DELETE FROM users WHERE id=%s", (uid,)) > 0


def list_users(search: str = "", page: int = 1, size: int = 10) -> tuple[list[dict], int]:
    where, args = "1=1", ()
    if search:
        where = "(username LIKE %s OR nickname LIKE %s)"
        args = (f"%{search}%", f"%{search}%")
    total = query_one(f"SELECT COUNT(*) AS c FROM users WHERE {where}", args)["c"]
    rows = query(
        f"SELECT * FROM users WHERE {where} ORDER BY id DESC LIMIT %s OFFSET %s",
        args + (size, (page - 1) * size),
    )
    return rows, total


# ---------------------------------------------------------------- 会话 DAO
def save_session(session_id: str, user_id: int, title: str = ""):
    existing = query_one(
        "SELECT id FROM chat_sessions WHERE session_id=%s", (session_id,))
    if existing:
        return
    execute(
        "INSERT INTO chat_sessions (session_id, user_id, title) VALUES (%s,%s,%s)",
        (session_id, user_id, title[:128]))


def save_message(session_id: str, role: str, content: str):
    execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES (%s,%s,%s)",
        (session_id, role, content[:60000]))


def list_user_sessions(user_id: int, limit: int = 50) -> list[dict]:
    rows = query(
        """SELECT s.session_id, s.title, s.created_at,
                  (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.session_id) AS msg_count
           FROM chat_sessions s WHERE s.user_id=%s ORDER BY s.id DESC LIMIT %s""",
        (user_id, limit))
    for r in rows:
        r["created_at"] = str(r["created_at"])
    return rows


def get_session_messages(session_id: str, limit: int = 200) -> list[dict]:
    rows = query(
        "SELECT role, content, created_at FROM chat_messages WHERE session_id=%s "
        "ORDER BY id ASC LIMIT %s", (session_id, limit))
    for r in rows:
        r["created_at"] = str(r["created_at"])
    return rows


def admin_stats() -> dict:
    return {
        "users_total": query_one("SELECT COUNT(*) c FROM users")["c"],
        "users_today": query_one(
            "SELECT COUNT(*) c FROM users WHERE created_at>=CURDATE()")["c"],
        "sessions_total": query_one("SELECT COUNT(*) c FROM chat_sessions")["c"],
        "messages_total": query_one("SELECT COUNT(*) c FROM chat_messages")["c"],
    }


def admin_recent_sessions(search: str = "", page: int = 1, size: int = 10):
    where, args = "1=1", ()
    if search:
        where = "(s.title LIKE %s OR u.username LIKE %s OR s.session_id LIKE %s)"
        args = (f"%{search}%", f"%{search}%", f"%{search}%")
    total = query_one(
        f"SELECT COUNT(*) c FROM chat_sessions s JOIN users u ON s.user_id=u.id WHERE {where}",
        args)["c"]
    rows = query(
        f"""SELECT s.session_id, s.title, s.created_at, u.username, u.nickname,
                   (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.session_id) AS msg_count
            FROM chat_sessions s JOIN users u ON s.user_id=u.id
            WHERE {where} ORDER BY s.id DESC LIMIT %s OFFSET %s""",
        args + (size, (page - 1) * size))
    for r in rows:
        r["created_at"] = str(r["created_at"])
    return rows, total
