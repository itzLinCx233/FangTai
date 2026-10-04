"""FastAPI 入口：启动时预加载菜谱库/索引/模型（保证首请求低延迟）。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app import config
from app.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 预热：菜谱库 + 检索索引（含云端/本地嵌入模型加载）
    from app.core.recipe_store import get_store
    from app.core.retriever import get_retriever
    store = get_store()
    print(f"[startup] 菜谱库 {len(store.recipes)} 条")
    ret = get_retriever()
    ret.search("清淡的晚餐", topk=3)  # 首次编码预热
    # 用户系统：MySQL 初始化（建表 + 管理员账户；失败不阻断核心服务）
    import app.db as db
    db.init_db()
    print("[startup] 就绪")
    yield


app = FastAPI(title="方太个性化膳食规划 Agent", version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.include_router(router)

from app.api.auth_routes import router as auth_router  # noqa: E402
from app.api.admin_routes import router as admin_router  # noqa: E402
app.include_router(auth_router)
app.include_router(admin_router)


@app.get("/")
async def index():
    web = Path(__file__).parent / "web" / "index.html"
    return FileResponse(web)


@app.get("/admin")
async def admin_page():
    web = Path(__file__).parent / "web" / "admin.html"
    return FileResponse(web)
