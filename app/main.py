"""FastAPI 入口：启动预热 + 前端托管（Vue 构建产物优先，回退旧静态页）。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.api.routes import router

BASE = Path(__file__).resolve().parent.parent
DIST = BASE / "frontend" / "dist"        # Vue 3 + Vite 构建产物
LEGACY = BASE / "app" / "web"            # 旧静态页（无构建环境回退）
USE_DIST = (DIST / "index.html").exists()


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
    print(f"[startup] 前端: {'frontend/dist (Vue3+Vite)' if USE_DIST else 'app/web (legacy 静态页)'}")
    print("[startup] 就绪")
    yield


app = FastAPI(title="方太个性化膳食规划 Agent", version="1.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.include_router(router)

from app.api.auth_routes import router as auth_router  # noqa: E402
from app.api.admin_routes import router as admin_router  # noqa: E402
app.include_router(auth_router)
app.include_router(admin_router)

if USE_DIST:
    # Vue SPA：静态资源 + history 路由回退（/api 已在前面注册，不受影响）
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(DIST / "index.html")

    @app.get("/admin")
    async def admin_page():
        return FileResponse(DIST / "index.html")

    @app.exception_handler(404)
    async def spa_fallback(request, exc):
        path = request.url.path
        if not path.startswith("/api") and not path.startswith("/assets"):
            return FileResponse(DIST / "index.html")
        return JSONResponse({"detail": "Not Found"}, status_code=404)
else:
    @app.get("/")
    async def index():
        return FileResponse(LEGACY / "index.html")

    @app.get("/admin")
    async def admin_page():
        return FileResponse(LEGACY / "admin.html")
