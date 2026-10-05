"""运行时可变 LLM 配置：env 为默认值，管理后台热切换 + DB 持久化。

- 进程内单例保存当前生效配置；首次访问以 app/config.py 的环境变量为默认
- 有 DB 时从 app_settings 表加载 llm.* 键，更新时写回（重启不丢）
- 无 DB（评测）模式仅进程内生效，重启回落 env 默认
- 更新后通知 LLMClient 重建连接/清 thinking 探测缓存
"""
from __future__ import annotations

import threading

from app import config

# 可运行时切换的字段（与 app_settings 表键 llm.<field> 对应）
FIELDS = ("base_url", "api_key", "model", "fast_model", "thinking", "temperature")

_lock = threading.Lock()
_current: dict | None = None


def _defaults() -> dict:
    return {
        "base_url": config.LLM_BASE_URL,
        "api_key": config.LLM_API_KEY,
        "model": config.LLM_MODEL,
        "fast_model": config.LLM_FAST_MODEL,
        "thinking": config.LLM_THINKING,
        "temperature": config.LLM_TEMPERATURE,
    }


def _load() -> dict:
    """惰性初始化：env 默认 + DB 覆盖（DB 不可用时静默跳过）。"""
    global _current
    with _lock:
        if _current is None:
            cur = _defaults()
            try:
                import app.db as db
                if db.db_ready():
                    for k in FIELDS:
                        v = db.get_setting(f"llm.{k}")
                        if v is not None and v != "":
                            if k == "temperature":
                                try:
                                    v = float(v)
                                except (TypeError, ValueError):
                                    continue
                            cur[k] = v
            except Exception as e:
                print(f"[runtime_settings] 加载持久化配置失败（用 env 默认）: {e}")
            _current = cur
        return dict(_current)


def get() -> dict:
    """返回当前配置副本（api_key 为明文，仅服务端内部使用）。"""
    return _load()


def update(fields: dict) -> dict:
    """部分更新配置；有 DB 时持久化，并触发热重载。返回更新后副本。"""
    global _current
    _load()
    changed = {k: v for k, v in fields.items() if k in FIELDS and v is not None}
    with _lock:
        _current.update(changed)
        snapshot = dict(_current)
    if changed:
        try:
            import app.db as db
            if db.db_ready():
                for k, v in changed.items():
                    db.set_setting(f"llm.{k}", str(v))
        except Exception as e:
            print(f"[runtime_settings] 配置持久化失败（仅本进程生效）: {e}")
        # LLM 客户端热重载（换 base_url/key 重建连接，清探测缓存）
        from app.core.llm import get_llm
        get_llm().reload(snapshot)
    return snapshot


def reset_to_env() -> dict:
    """放弃运行时修改，回落 env 默认（管理后台"恢复默认"用）。"""
    global _current
    with _lock:
        _current = _defaults()
        snapshot = dict(_current)
    from app.core.llm import get_llm
    get_llm().reload(snapshot)
    return snapshot
