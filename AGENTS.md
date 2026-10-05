# AGENTS.md

方太个性化膳食规划 Agent（浙江省 AI 竞赛"方太"专项赛）：FastAPI + LLM Agent（GLM）+ 云端 RAG（阿里云百炼）的膳食推荐系统，含 Vue3 前端与 MySQL 用户系统。代码注释、文档、数据均为中文。

## 目录

- `app/config.py` — 全部配置，仅来自环境变量（**无 .env 文件**，docker-compose 注入）
- `app/main.py` — FastAPI 入口：lifespan 预热菜谱库/索引 + MySQL 初始化；优先托管 `frontend/dist`（Vue3），无构建产物时回退 `app/web` 旧静态页
- `app/api/routes.py` — 核心推荐 REST/SSE 路由（`/api/*`），会话存于进程内 `_sessions` 字典；鉴权**可选**（匿名评测调用不受影响）
- `app/api/auth_routes.py` / `admin_routes.py` — 注册/登录/个人资料/历史会话、管理后台（用户 CRUD/统计）；需 MySQL，无 DB 时返回 503
- `app/db.py` — SQLAlchemy 2.0 数据层（User/会话/消息表），`MYSQL_ENABLED=false` 或连接失败进入无 DB 模式（核心端点不受影响）；schema 变更走 `alembic/`
- `app/core/` — 核心逻辑：`agent.py`（编排）、`constraint_engine.py`、`retriever.py`（BM25+向量混合检索）、`recipe_store.py`、`nutrition.py`、`planner.py`、`dialog.py`、`llm.py`、`profiles.py`、`security.py`（密码哈希+Bearer token）
- `frontend/` — Vue3 + Vite + TS + Element Plus + Pinia；`dist/` 为构建产物（由后端托管）
- `app/web/` — 旧版单文件静态页（回退用，勿再新增功能）
- `data/` — 运行数据副本；`资料/` — 赛题细则（docx）与原始数据（只读，勿改）
- `docs/` — 技术方案、部署、测试文档；改检索/约束/编排前先读 `docs/技术方案文档.md`，测试脚本用法见 `docs/测试说明.md`

## 常用命令（Linux；仓库注释里的 `py -3`/`set` 是 Windows 写法）

```bash
export LLM_API_KEY=... DASHSCOPE_API_KEY=sk-...   # 运行前必须（本地密钥也可放 scripts/.keys.env，已 gitignore）
python -m uvicorn app.main:app --port 8000        # 本地起服务（无 MySQL 时设 MYSQL_ENABLED=false）
docker compose up -d --build                      # Docker 一键部署（含 MySQL 8.4；首启自动建库/建表/建管理员）

python -m pytest tests/ -v                        # 单元测试（也可直接 python tests/test_core.py）
python scripts/eval.py --mode func --out eval_func.json   # 功能评测（跳过简单场景+冲突场景901-904）
python scripts/eval.py --mode perf --out eval_perf.json   # 性能评测（全量29轮计时，thinking=disabled）
python scripts/score_test.py --api http://127.0.0.1:8000  # 赛题评分表自测（100 分制）
bash scripts/test_deepseek.sh                     # DeepSeek(deepseek-flash) 全量评测，数据取自 资料/
python scripts/dev_run.py                         # 进程内调试单条对话（不走 HTTP）
python scripts/build_index.py                     # 离线重建向量索引（调云端 API 约 200 次）

alembic revision --autogenerate -m "描述"          # 改 app/db.py 模型后生成迁移
alembic upgrade head                              # 应用迁移（init_db 对全新库幂等）
cd frontend && npm run build                      # 前端构建（build-fast 跳过类型检查）
```

改动 `app/core/` 后必须跑单元测试 + 评测脚本验证；`eval_report*.json`/`score_report.json` 是已提交的评测产物。

## 架构规则

- 数据流：意图/槽位抽取 → 约束过滤 → 混合检索 → LLM 选菜 → 组合校验 → 流式生成，全在 `app/core/agent.py` 编排，勿把业务逻辑写进 routes。
- SSE 事件协议：`intent / plan / delta / clarify / done`，`plan` 事件携带结构化方案供评测机判，改字段名会破坏 `scripts/eval.py` 与 `score_test.py`。
- **零幻觉**：推荐菜名必须来自菜谱库（`store.exists()` 校验）；**零违反**：过敏/忌口经约束引擎双遍校验。这两条是赛题硬性指标，不得放宽。
- 核心模块均为模块级单例工厂：`get_store() / get_retriever() / get_llm() / get_agent()`，直接复用，勿自建实例。
- 用户系统边界：登录用户资料（忌口/绑定档案）只**并入**会话约束，不替代请求参数；`/api/chat` 必须保持匿名可用（评测机不登录）。角色为 admin 的用户禁止删除（前后端双重拦截，勿放开）。
- `app/db.py` 对外函数名与签名保持稳定（auth/admin/routes 依赖）；schema 改动一律走 Alembic 迁移，勿手写 ALTER。

## 注意事项

- 菜谱 CSV 必须以 `gb18030` 读取（`recipe_store.py`），用户档案/对话用例为 UTF-8。
- RAG 默认云端 dashscope 模式（text-embedding-v4 + gte-rerank-v2）；未配置 `DASHSCOPE_API_KEY` 时检索**直接报错**（不降级）；仅运行期云 API 异常才降级纯 BM25。本地 GPU 模式（`RAG_PROVIDER=local`）需 `requirements-local.txt`，见部署文档。
- `numpy` 必须 <2.0.0（BM25 兼容）。
- `LLM_THINKING=high` 质量优先但性能分 0/30；调性能问题先看该配置。LLM 层自动探测各家思考参数格式（GLM 400/1210、DeepSeek 422 均已兼容，落不到就用 `reasoning_effort`）。
- 首次启动会调云端 API 构建索引（约 300s），Docker healthcheck 已设 `start_period: 300s`；索引缓存在 `index-cache` 卷。
- MySQL：docker-compose 内置 mysql:8.4 服务，管理员默认 `admin/admin123`（环境变量 `ADMIN_USERNAME/ADMIN_PASSWORD` 覆盖）；评测环境无 DB 时给服务设 `MYSQL_ENABLED=false`。`AUTH_SECRET` 生产必须改默认值。
