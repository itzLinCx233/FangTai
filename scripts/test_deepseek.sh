#!/usr/bin/env bash
# DeepSeek API 全量测试：用户健康档案 / 对话用例 / 菜谱库均取自 资料/ 原始数据。
#
# 用法：
#   bash scripts/test_deepseek.sh                        # 进程内直跑 20 用例（无需起服务）
#   bash scripts/test_deepseek.sh --case 1               # 只跑指定用例（冒烟测试）
#   bash scripts/test_deepseek.sh --api http://127.0.0.1:8000   # 经 HTTP 跑（服务需以同款环境变量启动）
#
# 密钥经环境变量注入，或放 scripts/.keys.env（已 gitignore，不入库）；二者均无则报错退出。
set -euo pipefail
cd "$(dirname "$0")/.."

# ---------------- 密钥 ----------------
KEYS_FILE="$(dirname "$0")/.keys.env"
if [ -z "${LLM_API_KEY:-}" ] || [ -z "${DASHSCOPE_API_KEY:-}" ]; then
  [ -f "$KEYS_FILE" ] && source "$KEYS_FILE"
fi
: "${LLM_API_KEY:?请设置 LLM_API_KEY（DeepSeek），或在 scripts/.keys.env 中配置}"
: "${DASHSCOPE_API_KEY:?请设置 DASHSCOPE_API_KEY（百炼），或在 scripts/.keys.env 中配置}"
export LLM_API_KEY DASHSCOPE_API_KEY

# ---------------- LLM：DeepSeek（OpenAI 兼容端点） ----------------
export LLM_BASE_URL="${LLM_BASE_URL:-https://api.deepseek.com}"
export LLM_MODEL="${LLM_MODEL:-deepseek-flash}"          # DeepSeek-V4.1-Flash
export LLM_FAST_MODEL="${LLM_FAST_MODEL:-$LLM_MODEL}"
# 测试默认关闭思考：func 校验逻辑不依赖深思，且单轮 3s 内跑得快；
# 需要质量优先复测时显式覆盖：LLM_THINKING=high bash scripts/test_deepseek.sh ...
export LLM_THINKING="${LLM_THINKING:-disabled}"

# ---------------- RAG：阿里云百炼（Key 缺失时检索直接报错） ----------------
export DASHSCOPE_API_KEY="$DASHSCOPE_API_KEY"

# ---------------- 数据：全部使用 资料/ 原始数据 ----------------
DATA_ORIGIN="资料/2026_10_04_50个用户健康档案_详细版7.13"
export DATA_DIR="${DATA_DIR:-$PWD/$DATA_ORIGIN}"

# ---------------- python：优先仓库 venv ----------------
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY="${PYTHON:-python3}"; fi

echo "[test-deepseek] model=$LLM_MODEL thinking=$LLM_THINKING"
echo "[test-deepseek] data=$DATA_DIR"
"$PY" scripts/eval.py --out eval_report_deepseek.json "$@"
