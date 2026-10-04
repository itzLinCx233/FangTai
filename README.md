# FangTai · 个性化膳食规划 Agent

第二届浙江省大学生人工智能竞赛 —— "方太"人工智能专项赛（ZX-2026-0301）

## 仓库结构

- `资料/` 赛题细则与原始数据（用户健康档案、菜谱数据、对话用例）
- `app/` 系统源码：FastAPI + LLM Agent（`core/` 为约束引擎/检索/规划/对话/编排）
- `docs/` 技术方案文档 · 部署文档
- `scripts/` 索引构建 · 自动评测 · 评分表自测
- `data/` 系统运行所用的数据副本（GB18030 菜谱库等）
- `tests/` 核心模块单元测试

## 功能

基于 LLM（GLM-5.3-flash）+ GPU 混合检索 RAG 的健康膳食推荐 Agent：

- 单用户单餐推荐：过敏/忌口双遍校验零违反、菜谱零幻觉
- 多人多约束宴请：约束并集合并、荤素比/烹饪方式多样性、按人营养核算
- 多轮动态交互：约束追加、局部替换、方案否定、模糊追问澄清、需求矛盾折中、上下文不遗忘
- SSE 流式 API + Web 聊天界面 + Docker 一键部署（云端 RAG 零 GPU；可切本地 GPU 模式）
- 用户系统：MySQL 管理（注册/登录/个人中心/健康档案绑定），管理员后台 `/admin` 增删改查
- 工程化：前端 Vue3+Vite+TS（Element Plus 后台），后端 SQLAlchemy 2.0 ORM + Alembic 迁移

## 快速开始

```bash
# Docker（无需 GPU：RAG 用阿里云百炼 API；先把 LLM_API_KEY 和 DASHSCOPE_API_KEY 换成你的密钥）
docker compose up -d --build
open http://127.0.0.1:8000         # 聊天页（右上角登录/注册）
open http://127.0.0.1:8000/admin   # 管理后台（admin / admin123）

# 本地开发
pip install -r requirements.txt
set LLM_API_KEY=<你的key>
set DASHSCOPE_API_KEY=<你的百炼key>
python -m uvicorn app.main:app --port 8000
```

## 评测

```bash
python scripts/eval.py --api http://127.0.0.1:8000     # 20 组对话用例全量评测
python scripts/score_test.py --api http://127.0.0.1:8000  # 按赛题评分表自测（100分制）
python tests/test_core.py                              # 单元测试
```

自测成绩（LLM_THINKING=high 质量优先配置）：基础推荐 20/20 - 复杂组合 19.2/20 -
多轮交互 30/30 - 性能 0/30（思考耗时所致；改 `LLM_THINKING: low` 可进优秀档），
总分 69.2/100，明细见 `score_report.json`。

文档：[技术方案文档](docs/技术方案文档.md) - [部署文档](docs/部署文档.md)
