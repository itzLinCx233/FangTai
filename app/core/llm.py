"""LLM 抽象层：OpenAI 兼容协议（默认智谱 GLM 系），流式输出，思考模式可调。

特性：
- 任意 OpenAI 兼容 provider（bigmodel/DeepSeek/vLLM/Ollama），经 LLM_BASE_URL 切换
- 运行时可热切换：base_url/api_key/model/thinking/temperature 读自 runtime_settings
  （管理后台修改后 reload() 重建客户端），env 仅为默认值
- 思考模式可调：LLM_THINKING = disabled/enabled/low/high/max
  GLM-4.5/5.x 各版本 thinking 参数格式不同（thinking.type / reasoning_effort），
  本层自动探测可用格式并缓存（按 模型+模式 键），失败自动降级，保证跨模型兼容
- 流式 chat_stream：逐 token 产出，reasoning 内容不转发（不占首 Token）
- 快慢双模型：槽位抽取/选菜走 LLM_FAST_MODEL，余额或限流自动回退主模型
"""
from __future__ import annotations

import time
from typing import AsyncIterator

from openai import AsyncOpenAI

from app import config
from app.core import runtime_settings

# 各家思考参数格式不同（thinking.type / reasoning_effort），按顺序探测取首个可用
def _thinking_candidates(mode: str) -> list[dict | None]:
    if mode == "disabled":
        return [
            {"thinking": {"type": "disabled"}},   # GLM-4.5/4.6
            {"reasoning_effort": "low"},          # GLM-5.x / OpenAI 风格
            {"thinking": {"type": "enabled", "level": "low"}},
            None,                                 # 不支持参数的模型
        ]
    level = mode if mode in ("low", "high", "max") else "high"
    return [
        {"thinking": {"type": level}},
        {"reasoning_effort": level},
        {"thinking": {"type": "enabled", "level": level}},
        {"thinking": {"type": "enabled"}},
        None,
    ]


class LLMClient:
    def __init__(self):
        cfg = runtime_settings.get()
        self._base_url = cfg["base_url"]
        self._api_key = cfg["api_key"]
        self.client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key or "EMPTY",
            timeout=config.LLM_TIMEOUT,
        )
        self._extra_cache: dict[str, dict | None] = {}   # "model:mode" → 已验证的 extra_body

    # ---------- 运行时可变模型配置 ----------
    @property
    def model(self) -> str:
        return runtime_settings.get().get("model") or config.LLM_MODEL

    @property
    def fast_model(self) -> str:
        cfg = runtime_settings.get()
        return cfg.get("fast_model") or cfg.get("model") or config.LLM_MODEL

    def reload(self, cfg: dict | None = None) -> None:
        """运行时配置变更后热重载：换 base_url/key 重建客户端；清思考格式探测缓存。"""
        cfg = cfg or runtime_settings.get()
        if (cfg.get("base_url") or "") != self._base_url or (cfg.get("api_key") or "") != self._api_key:
            self._base_url = cfg.get("base_url") or self._base_url
            self._api_key = cfg.get("api_key") or self._api_key
            self.client = AsyncOpenAI(
                base_url=self._base_url, api_key=self._api_key or "EMPTY",
                timeout=config.LLM_TIMEOUT)
            print(f"[llm] 运行时配置已切换: {self._base_url} model={cfg.get('model')}")
        self._extra_cache.clear()  # 模型变了旧格式探测缓存也不可用

    # ---------- 消息构造 ----------
    @staticmethod
    def system(prompt: str) -> dict:
        return {"role": "system", "content": prompt}

    @staticmethod
    def user(content: str) -> dict:
        return {"role": "user", "content": content}

    @staticmethod
    def assistant(content: str) -> dict:
        return {"role": "assistant", "content": content}

    # ---------- thinking 格式探测 ----------
    @staticmethod
    def _is_thinking_param_error(e: Exception) -> bool:
        s = str(e)
        # DeepSeek 拒绝未知 thinking 枚举返回 422 unknown variant（GLM 为 400/1210）
        return ("1210" in s or "不支持关闭思考" in s or "unknown variant" in s
                or "thinking" in s.lower() and ("400" in s or "422" in s))

    async def _resolve_extra(self, model: str, thinking: str, probe) -> dict | None:
        """返回可用的 extra_body；对思考参数格式做一次探测并缓存（按 模型:模式 键）。

        probe(extra) → 实际发起请求，参数格式错误时抛异常。
        """
        mode = (thinking or "auto").lower()
        if mode not in ("disabled", "enabled", "low", "high", "max", "auto"):
            return None
        if mode == "auto":
            return None
        cache_key = f"{model}:{mode}"
        if cache_key in self._extra_cache:
            return self._extra_cache[cache_key]
        candidates = _thinking_candidates(mode)
        last_err: Exception | None = None
        for extra in candidates:
            try:
                await probe(extra)
                self._extra_cache[cache_key] = extra
                return extra
            except Exception as e:
                if not self._is_thinking_param_error(e):
                    raise
                last_err = e
        # 全部格式被拒：放弃思考控制
        self._extra_cache[cache_key] = None
        return None

    # ---------- 非流式 ----------
    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float | None = None,
        thinking: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        cfg = runtime_settings.get()
        kwargs = dict(
            model=model or cfg.get("model") or config.LLM_MODEL,
            messages=messages,
            temperature=temperature if temperature is not None
            else float(cfg.get("temperature") or config.LLM_TEMPERATURE),
            max_tokens=max_tokens or config.LLM_MAX_TOKENS,
        )
        probe_kwargs = {k: v for k, v in kwargs.items() if k != "max_tokens"}
        extra = await self._resolve_extra(
            kwargs["model"], thinking or str(cfg.get("thinking") or config.LLM_THINKING),
            lambda e: self.client.chat.completions.create(
                **probe_kwargs, extra_body=e, max_tokens=8))
        resp = await self.client.chat.completions.create(**kwargs, extra_body=extra)
        return resp.choices[0].message.content or ""

    async def chat_fast(self, messages: list[dict], temperature: float = 0.2) -> str:
        """轻量任务（意图识别/选菜）：快速模型优先，余额/限流时自动回退主模型。"""
        model = self.fast_model
        try:
            out = await self.chat(
                messages, model=model, temperature=temperature,
                max_tokens=3072,
            )
            if out.strip():
                return out
            # 思考模型高强度推理可能耗尽 token 上限导致正文为空（JSON 解析必失败）
            print(f"[llm] {model} 正文为空（思考耗尽上限），关闭思考重试")
        except Exception as e:
            if not (any(c in str(e) for c in ("1113", "429", "1301", "1302")) and self.fast_model != self.model):
                raise
            print(f"[llm] 快速模型 {self.fast_model} 不可用({str(e)[:60]})，回退 {self.model}")
            model = self.model
        return await self.chat(
            messages, model=model, temperature=temperature,
            max_tokens=2048, thinking="disabled",
        )

    # ---------- 流式 ----------
    async def chat_stream(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float | None = None,
        thinking: str | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """产出内容增量；reasoning 不转发。流式同样做思考格式探测。"""
        cfg = runtime_settings.get()
        kwargs = dict(
            model=model or cfg.get("model") or config.LLM_MODEL,
            messages=messages,
            temperature=temperature if temperature is not None
            else float(cfg.get("temperature") or config.LLM_TEMPERATURE),
            max_tokens=max_tokens or config.LLM_MAX_TOKENS,
        )
        probe_kwargs = {k: v for k, v in kwargs.items() if k != "max_tokens"}

        async def probe(extra):
            await self.client.chat.completions.create(
                **probe_kwargs, extra_body=extra, max_tokens=8,
                stream=True, stream_options={"include_usage": True})

        extra = await self._resolve_extra(
            kwargs["model"], thinking or str(cfg.get("thinking") or config.LLM_THINKING),
            probe)
        stream = await self.client.chat.completions.create(
            **kwargs, extra_body=extra, stream=True,
            stream_options={"include_usage": True})
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


async def timed_first_token(stream: AsyncIterator[str]) -> tuple[float, str]:
    """测量首 Token 延迟并聚合全文（评测用）。"""
    t0 = time.perf_counter()
    first = None
    parts = []
    async for tok in stream:
        if first is None:
            first = time.perf_counter() - t0
        parts.append(tok)
    return (first or -1.0), "".join(parts)
