"""混合检索器：BM25(jieba) + 向量召回 + RRF 融合 + 重排 + 约束过滤。

嵌入/重排 provider：
- dashscope（默认）：阿里云百炼 API —— text-embedding-v4（OpenAI 兼容端点）+ gte-rerank（原生端点），
  无需本地 GPU；BM25 仍为本地内存索引（零延迟），云 API 失败自动退化为纯 BM25
- local：sentence-transformers GPU 推理（bge 系列，需 requirements-local.txt）
"""
from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass

import numpy as np

from app import config
from app.core.recipe_store import Recipe, get_store

import jieba
from rank_bm25 import BM25Okapi


def _tokenize(text: str) -> list[str]:
    return [t for t in jieba.lcut(text.lower()) if t.strip()]


def tags_pool(r: Recipe) -> str:
    return " ".join(r.tag_list)


# ---------------------------------------------------------------- 嵌入 Provider
class LocalEmbedder:
    """本地 sentence-transformers（GPU/CPU）。"""

    name_suffix = "local"

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        device = _pick_device(config.EMBEDDING_DEVICE)
        self.model_name = config.EMBEDDING_MODEL
        self._m = SentenceTransformer(config.EMBEDDING_MODEL, device=device)
        self._zh_instruction = "zh" in self.model_name.lower() and "m3" not in self.model_name.lower()

    @property
    def index_key(self) -> str:
        return f"local:{self.model_name}"

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        if is_query and self._zh_instruction:
            texts = [config.EMBED_QUERY_INSTRUCTION + t for t in texts]
        vecs = self._m.encode(
            texts, batch_size=64, normalize_embeddings=True,
            show_progress_bar=False, convert_to_numpy=True,
        )
        return np.asarray(vecs, dtype=np.float32)


class DashscopeEmbedder:
    """阿里云百炼 Embedding（OpenAI 兼容端点，text-embedding-v4）。

    单请求上限 10 条文本 → 分批调用；失败重试；结果 L2 归一化。
    """

    name_suffix = "dashscope"
    BATCH = 10

    def __init__(self):
        from openai import OpenAI
        if not config.DASHSCOPE_API_KEY:
            raise RuntimeError("DASHSCOPE_API_KEY 未配置")
        self.model_name = config.DASHSCOPE_EMBED_MODEL
        self._c = OpenAI(
            base_url=config.DASHSCOPE_BASE_URL + "/compatible-mode/v1",
            api_key=config.DASHSCOPE_API_KEY,
            timeout=30,
        )

    @property
    def index_key(self) -> str:
        return f"dashscope:{self.model_name}"

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            batch = texts[i:i + self.BATCH]
            last_err: Exception | None = None
            for attempt in range(3):
                try:
                    resp = self._c.embeddings.create(model=self.model_name, input=batch)
                    out.extend([d.embedding for d in resp.data])
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(0.4 * (attempt + 1))
            if last_err is not None:
                raise last_err
        arr = np.asarray(out, dtype=np.float32)
        norm = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.maximum(norm, 1e-8)


# ---------------------------------------------------------------- 重排 Provider
class LocalReranker:
    def __init__(self):
        from sentence_transformers import CrossEncoder
        device = _pick_device(config.RERANKER_DEVICE)
        ce = CrossEncoder(config.RERANKER_MODEL, device=device, max_length=256)
        if device == "cuda":
            try:
                ce.model.half()
            except Exception:
                pass
        self._m = ce

    def predict(self, query: str, docs: list[str]) -> list[float]:
        scores = self._m.predict(
            [[query, d] for d in docs], show_progress_bar=False, batch_size=24)
        return [float(s) for s in scores]


class DashscopeReranker:
    """阿里云百炼 gte-rerank（DashScope 原生端点）。"""

    def __init__(self):
        import httpx
        if not config.DASHSCOPE_API_KEY:
            raise RuntimeError("DASHSCOPE_API_KEY 未配置")
        self.model_name = config.DASHSCOPE_RERANK_MODEL
        self._url = config.DASHSCOPE_BASE_URL + "/api/v1/services/rerank/text-rerank/text-rerank"
        self._headers = {
            "Authorization": f"Bearer {config.DASHSCOPE_API_KEY}",
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(timeout=30)

    def predict(self, query: str, docs: list[str]) -> list[float]:
        payload = {
            "model": self.model_name,
            "input": {"query": query, "documents": docs},
            "parameters": {"return_documents": False, "top_n": len(docs)},
        }
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                r = self._client.post(self._url, headers=self._headers, json=payload)
                r.raise_for_status()
                results = r.json()["output"]["results"]
                scores = [0.0] * len(docs)
                for item in results:
                    scores[item["index"]] = float(item["relevance_score"])
                return scores
            except Exception as e:
                last_err = e
                time.sleep(0.4 * (attempt + 1))
        raise last_err


def _pick_device(pref: str) -> str:
    if pref and pref != "auto":
        return pref
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


# ---------------------------------------------------------------- 检索器
@dataclass
class RetrievedRecipe:
    recipe: Recipe
    score: float
    bm25_rank: int = -1
    vec_rank: int = -1
    rerank_score: float | None = None


class RecipeRetriever:
    def __init__(self):
        self.store = get_store()
        self._lock = threading.Lock()
        self.last_latency_ms = 0.0
        self._embedder = None
        self._reranker = None          # None=未加载 False=不可用
        self._embed_ready = False
        self._init_texts()
        self._init_models_and_index()

    # ---------- 初始化 ----------
    def _init_texts(self):
        """缓存检索/重排两套文本（重排用截断版控制延迟）。"""
        self.doc_texts: list[str] = []
        self.rerank_texts: list[str] = []
        self.corpus_tokens: list[list[str]] = []
        for r in self.store.recipes:
            ings = "、".join(dict.fromkeys(i.name for i in r.ingredients))
            tags = "、".join(r.tag_list)
            self.doc_texts.append(f"{r.name}。食材：{ings}。标签：{tags}")
            short_ings = "、".join(dict.fromkeys(i.name for i in r.ingredients[:10]))
            self.rerank_texts.append(f"{r.name} 食材:{short_ings} 标签:{tags[:40]}")
            text = r.name + " " + " ".join(i.name for i in r.ingredients) \
                + " " + tags_pool(r) + " " + "".join(r.steps)[:120]
            self.corpus_tokens.append(_tokenize(text))
        self.bm25 = BM25Okapi(self.corpus_tokens)

    def _make_embedder(self):
        if config.RAG_PROVIDER == "local":
            return LocalEmbedder()
        return DashscopeEmbedder()

    def _make_reranker(self):
        if config.RAG_PROVIDER == "local":
            if not config.RERANKER_MODEL:
                return None
            return LocalReranker()
        if not config.DASHSCOPE_RERANK_MODEL:
            return None
        return DashscopeReranker()

    def _init_models_and_index(self):
        t0 = time.perf_counter()
        self.bm25 = BM25Okapi(self.corpus_tokens)
        # 未配置百炼 Key 直接报错：静默退化纯 BM25 会给出误导性的检索/评测结果
        if config.RAG_PROVIDER != "local" and not config.DASHSCOPE_API_KEY:
            raise RuntimeError(
                "RAG_PROVIDER=dashscope 但 DASHSCOPE_API_KEY 未配置：混合检索需要百炼 API。"
                "请配置 Key，或设 RAG_PROVIDER=local（本地 GPU 模式）")
        # 嵌入器
        try:
            self._embedder = self._make_embedder()
        except Exception as e:
            print(f"[retriever] 嵌入器不可用，退化为纯 BM25: {e}")
            self._embedder = None
        # 向量索引
        self.doc_vecs = self._load_or_build_vectors()
        self._embed_ready = self.doc_vecs is not None
        # 重排器
        try:
            self._reranker = self._make_reranker()
        except Exception as e:
            print(f"[retriever] 重排器不可用（跳过重排）: {e}")
            self._reranker = False
        print(f"[retriever] 就绪: {len(self.corpus_tokens)} 菜谱, provider={config.RAG_PROVIDER}, "
              f"vector={'on' if self._embed_ready else 'bm25-only'}, "
              f"rerank={'on' if self._reranker else 'off'}, {time.perf_counter()-t0:.1f}s")

    def _load_or_build_vectors(self) -> np.ndarray | None:
        if self._embedder is None:
            return None
        index_file = config.INDEX_DIR / "embeddings.npy"
        meta_file = config.INDEX_DIR / "meta.json"
        want_key = self._embedder.index_key
        try:
            if index_file.exists() and meta_file.exists():
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if meta.get("index_key") == want_key and meta.get("count") == len(self.store.recipes):
                    vecs = np.load(index_file)
                    print(f"[retriever] 载入预计算向量 {vecs.shape} ({want_key})")
                    return vecs
        except Exception as e:
            print(f"[retriever] 向量索引载入失败，将重建: {e}")
        try:
            t0 = time.perf_counter()
            vecs = self._embedder.encode(self.doc_texts)
            vecs = np.asarray(vecs, dtype=np.float32)
            config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
            np.save(index_file, vecs)
            meta_file.write_text(
                json.dumps({"index_key": want_key, "count": len(self.doc_texts)}, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[retriever] 已构建并缓存向量索引 {vecs.shape} -> {index_file}（{time.perf_counter()-t0:.0f}s）")
            return vecs
        except Exception as e:
            print(f"[retriever] 向量构建失败，退化为纯 BM25: {e}")
            return None

    # ---------- 查询 ----------
    def _provider_label(self) -> str:
        return (self._embedder.index_key if self._embedder
                else f"{config.RAG_PROVIDER}(bm25-only)")

    def encode_query(self, query: str) -> np.ndarray | None:
        if not self._embed_ready or self._embedder is None:
            return None
        with self._lock:
            try:
                return self._embedder.encode([query], is_query=True)
            except Exception as e:
                print(f"[retriever] 查询向量失败（本轮用 BM25）: {e}")
                return None

    def search(
        self,
        query: str,
        ok_ids: set[int] | None = None,
        label_filter: dict[str, list[str]] | None = None,
        keyword_boost: list[str] | None = None,
        topk: int | None = None,
    ) -> list[RetrievedRecipe]:
        """混合检索主入口。

        query: 自然语言需求
        ok_ids: 约束引擎允许的菜谱 id（过敏/忌口已过滤）
        label_filter: 标签过滤，如 {"餐次": ["晚餐"]}
        keyword_boost: 软偏好关键词（健康需求食材），命中加权
        """
        t0 = time.perf_counter()
        topk = topk or config.RERANK_TOPK
        n = len(self.store.recipes)

        # 标签过滤
        allowed = ok_ids if ok_ids is not None else set(self.store.by_id.keys())
        if label_filter:
            for dim, want in label_filter.items():
                if not want:
                    continue
                allowed = {
                    rid for rid in allowed
                    if any(w in self.store.by_id[rid].tags.get(dim, []) for w in want)
                }
        if not allowed:
            return []

        idx_map = [r.id for r in self.store.recipes]
        pos_of = {rid: i for i, rid in enumerate(idx_map)}

        # BM25
        q_tokens = _tokenize(query)
        bm25_scores = self.bm25.get_scores(q_tokens) if q_tokens else np.zeros(n)
        bm25_order = np.argsort(-bm25_scores)

        # 向量
        qvec = self.encode_query(query)
        vec_order = None
        if qvec is not None:
            vec_scores = (self.doc_vecs @ qvec.T).reshape(-1)
            vec_order = np.argsort(-vec_scores)

        # RRF 融合
        K = 60.0
        rrf = np.zeros(n)
        for rank, pos in enumerate(bm25_order[: config.RECALL_TOPK * 3]):
            rrf[pos] += 1.0 / (K + rank + 1)
        if vec_order is not None:
            for rank, pos in enumerate(vec_order[: config.RECALL_TOPK * 3]):
                rrf[pos] += 1.0 / (K + rank + 1)

        # 软偏好加权：关键词命中食材/菜名，功效标签命中（如减脂/助眠）
        if keyword_boost:
            for pos, rid in enumerate(idx_map):
                if rid not in allowed:
                    continue
                r = self.store.recipes[pos]
                text = r.name + "".join(i.name for i in r.ingredients)
                hits = sum(1 for kw in keyword_boost if kw and kw in text)
                tag_str = "、".join(r.tag_list)
                hits += sum(1 for kw in keyword_boost if kw and kw in tag_str)
                rrf[pos] += 0.02 * hits

        # 过滤 + 截断到重排候选
        recall_n = config.RECALL_TOPK
        cand_pos = [pos for pos in np.argsort(-rrf) if idx_map[pos] in allowed][:recall_n]

        results = [
            RetrievedRecipe(
                recipe=self.store.recipes[pos],
                score=float(rrf[pos]),
                bm25_rank=int(np.where(bm25_order == pos)[0][0]) + 1 if pos in bm25_order[:500] else -1,
                vec_rank=int(np.where(vec_order == pos)[0][0]) + 1 if vec_order is not None and pos in vec_order[:500] else -1,
            )
            for pos in cand_pos
        ]

        # 重排（dashscope gte-rerank / 本地 CrossEncoder；失败保持 RRF 序）
        if self._reranker and results:
            try:
                rerank_n = min(24, len(results))
                sub = results[:rerank_n]
                docs = [self.rerank_texts[pos_of[r.recipe.id]] for r in sub]
                with self._lock:
                    scores = self._reranker.predict(query, docs)
                for r, s in zip(sub, scores):
                    r.rerank_score = float(s)
                sub.sort(key=lambda x: x.rerank_score or 0, reverse=True)
                results = sub + results[rerank_n:]
            except Exception as e:
                print(f"[retriever] 重排失败，使用 RRF 序: {e}")

        # 同名不同配方去重（保留排名最高者，避免一餐出现两道同名菜）
        seen_names: set[str] = set()
        deduped: list[RetrievedRecipe] = []
        for r in results:
            if r.recipe.name not in seen_names:
                seen_names.add(r.recipe.name)
                deduped.append(r)
        results = self._diversify(deduped)

        self.last_latency_ms = (time.perf_counter() - t0) * 1000
        return results[:topk]

    @staticmethod
    def _diversify(results: list[RetrievedRecipe]) -> list[RetrievedRecipe]:
        """重复度治理：对头部候选做受控扰动（仅交换分数相近的相邻项）。

        同样的查询多轮检索不再总落在同一批菜上；只扰动分差 ≤15% 的相邻对，
        不破坏相关性大局。评测需可复现时设 RECIPE_DIVERSITY=false 关闭。
        """
        if not config.RECIPE_DIVERSITY or len(results) < 4:
            return results
        out = list(results)
        topn = min(config.RECIPE_JITTER_TOPN, len(out) - 1)
        for i in range(topn):
            a, b = out[i], out[i + 1]
            base = max(abs(a.score), 1e-9)
            if abs(a.score - b.score) <= base * 0.15 and random.random() < 0.5:
                out[i], out[i + 1] = b, a
        return out


_retriever: RecipeRetriever | None = None


def get_retriever() -> RecipeRetriever:
    global _retriever
    if _retriever is None:
        _retriever = RecipeRetriever()
    return _retriever
