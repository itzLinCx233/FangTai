# 方太个性化膳食规划 Agent —— 多阶段构建
# 阶段1：Node 构建 Vue3+Vite 前端；阶段2：Python 运行时（云端 RAG，无需 GPU）
#
# 本地 GPU 模式（RAG_PROVIDER=local）：改用 pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime
# 基础镜像并追加安装 requirements-local.txt（见 docs/部署文档.md）

# ---------- 阶段 1：前端构建 ----------
FROM node:20-slim AS frontend-build
WORKDIR /web
COPY frontend/package.json frontend/package-lock*.json ./
RUN npm config set registry https://registry.npmmirror.com && npm install
COPY frontend/ .
RUN npm run build-fast

# ---------- 阶段 2：运行时 ----------
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 业务代码与数据
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY alembic.ini alembic/ ./
RUN mkdir -p /app/index

# 前端构建产物（无 Node 环境启动时会回退 app/web 旧静态页）
COPY --from=frontend-build /web/dist ./frontend/dist

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
