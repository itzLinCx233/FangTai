"""全局配置：全部来自环境变量（docker-compose 注入），不使用 .env 文件。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
INDEX_DIR = Path(os.getenv("INDEX_DIR", BASE_DIR / "index"))

# ---------------- LLM（OpenAI 兼容协议，默认智谱 GLM-5.3） ----------------
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "glm-5.3")
# 思考模式：disabled / enabled / low / high / max（GLM-5.x 常思考模型最低用 low）
LLM_THINKING = os.getenv("LLM_THINKING", "high")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.6"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
# 意图识别等轻量调用可用更小模型（留空则同 LLM_MODEL）
LLM_FAST_MODEL = os.getenv("LLM_FAST_MODEL", "")

# ---------------- RAG 检索（嵌入/重排） ----------------
# provider: dashscope（阿里云百炼 API，默认，无需本地 GPU）| local（本地 GPU 模型，需 requirements-local.txt）
RAG_PROVIDER = os.getenv("RAG_PROVIDER", "dashscope")

# —— 阿里云百炼 DashScope ——
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com")
# Embedding 走 OpenAI 兼容端点（text-embedding-v4，1024 维，10 条/批）
DASHSCOPE_EMBED_MODEL = os.getenv("DASHSCOPE_EMBED_MODEL", "text-embedding-v4")
# Rerank 走 DashScope 原生端点（gte-rerank）
DASHSCOPE_RERANK_MODEL = os.getenv("DASHSCOPE_RERANK_MODEL", "gte-rerank-v2")

# —— 本地模式（可选，RAG_PROVIDER=local 时生效） ——
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "auto")  # auto/cuda/cpu
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")  # 留空禁用
RERANKER_DEVICE = os.getenv("RERANKER_DEVICE", "auto")
EMBED_QUERY_INSTRUCTION = os.getenv("EMBED_QUERY_INSTRUCTION", "为这个句子生成表示以用于检索相关菜谱：")

# HF 镜像（仅 local 模式用）
HF_ENDPOINT = os.getenv("HF_ENDPOINT", "")
if HF_ENDPOINT:
    os.environ.setdefault("HF_ENDPOINT", HF_ENDPOINT)

# ---------------- 检索参数 ----------------
RECALL_TOPK = int(os.getenv("RECALL_TOPK", "60"))       # 混合召回条数
RERANK_TOPK = int(os.getenv("RERANK_TOPK", "20"))       # 重排后送入 LLM 的候选数
FINAL_CANDIDATES = int(os.getenv("FINAL_CANDIDATES", "12"))  # 注入 prompt 的最终候选数

# ---------------- 服务 ----------------
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# ---------------- MySQL（用户系统；评测环境可 MYSQL_ENABLED=false 跳过） ----------------
MYSQL_ENABLED = os.getenv("MYSQL_ENABLED", "true").lower() in ("1", "true", "yes", "on")
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "fangtai")
MYSQL_POOL_SIZE = int(os.getenv("MYSQL_POOL_SIZE", "8"))
# 管理员账户（首次初始化自动创建，已存在则跳过）
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
# 登录 token 签名密钥与有效期
AUTH_SECRET = os.getenv("AUTH_SECRET", "fangtai-agent-secret-change-me")
AUTH_TOKEN_TTL_H = int(os.getenv("AUTH_TOKEN_TTL_H", "168"))  # 7 天
