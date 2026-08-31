"""组合精确检索、SQLite 全文检索和可选私有语义检索。"""

from dataclasses import replace
import json
import math
import os
import re
from typing import Protocol
from urllib.request import Request, urlopen

from .models import RetrievedChunk
from .store import KnowledgeStore


class EmbeddingProvider(Protocol):
    """可替换的批量文本向量接口。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """把等长文本列表转换为向量列表。"""
        ...


class HttpEmbeddingProvider:
    """调用环境变量指定的获批私有 HTTP Embedding 服务。"""

    def __init__(self, url: str, timeout_seconds: float = 30.0):
        """保存私有服务地址和超时时间。"""
        if not url:
            raise ValueError("Embedding 服务地址不能为空")
        self.url = url
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> "HttpEmbeddingProvider | None":
        """仅在显式配置 JSSH_RAG_EMBEDDING_URL 时建立服务。"""
        url = os.environ.get("JSSH_RAG_EMBEDDING_URL", "").strip()
        return cls(url) if url else None

    def embed(self, texts: list[str]) -> list[list[float]]:
        """以 JSON 请求私有服务并兼容常见响应结构。"""
        request = Request(
            self.url,
            data=json.dumps({"input": texts}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if "embeddings" in payload:
            vectors = payload["embeddings"]
        else:
            vectors = [item["embedding"] for item in payload.get("data", [])]
        if len(vectors) != len(texts):
            raise ValueError("Embedding 服务返回数量与输入不一致")
        return [[float(value) for value in vector] for vector in vectors]


def _cosine(left: list[float], right: list[float]) -> float:
    """计算两个等长向量的余弦相似度。"""
    if len(left) != len(right):
        raise ValueError("Embedding 向量维度不一致")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


class Retriever:
    """在单一指定版本内执行轻量混合检索。"""

    def __init__(self, store: KnowledgeStore, embedding_provider: EmbeddingProvider | None = None):
        """绑定本地索引和可选的获批私有向量服务。"""
        self.store = store
        self.embedding_provider = embedding_provider

    def _semantic(self, query: str, hardware_version: str) -> list[RetrievedChunk]:
        if self.embedding_provider is None:
            return []
        chunks = self.store.all_chunks(hardware_version)
        missing: list[RetrievedChunk] = []
        vectors: dict[str, list[float]] = {}
        for chunk in chunks:
            key = f"{chunk.source_sha256}:{chunk.chunk_id}"
            cached = self.store.get_embedding(key)
            if cached is None:
                missing.append(chunk)
            else:
                vectors[chunk.chunk_id] = cached
        if missing:
            embedded = self.embedding_provider.embed([item.content for item in missing])
            if len(embedded) != len(missing):
                raise ValueError("Embedding 服务返回数量与知识块数量不一致")
            for chunk, vector in zip(missing, embedded, strict=True):
                key = f"{chunk.source_sha256}:{chunk.chunk_id}"
                self.store.set_embedding(key, vector)
                vectors[chunk.chunk_id] = vector
        query_vector = self.embedding_provider.embed([query])[0]
        ranked = sorted(
            chunks,
            key=lambda item: _cosine(query_vector, vectors[item.chunk_id]),
            reverse=True,
        )
        return [
            replace(item, score=_cosine(query_vector, vectors[item.chunk_id]))
            for item in ranked
            if _cosine(query_vector, vectors[item.chunk_id]) > 0
        ]

    def search(
        self,
        query: str,
        hardware_version: str,
        limit: int = 8,
    ) -> list[RetrievedChunk]:
        """执行版本过滤、全文/标识符检索、语义补充和去重排序。

        Args:
            query: 工程问题或标识符。
            hardware_version: 必须显式给出的目标硬件版本。
            limit: 最多返回的知识块数量。

        Returns:
            只属于目标版本的融合排序结果。
        """
        if not hardware_version:
            raise ValueError("必须指定硬件版本")
        clauses = [
            part.strip()
            for part in re.split(r"[，,；;。！？?]+", query)
            if len(part.strip()) >= 2
        ]
        lexical_queries = list(dict.fromkeys([query, *clauses]))
        lexical = [
            (
                2.0 if item_query == query else 1.0,
                self.store.search(item_query, hardware_version, max(limit * 4, 16)),
            )
            for item_query in lexical_queries
        ]
        semantic = self._semantic(query, hardware_version)
        scores: dict[str, float] = {}
        chunks: dict[str, RetrievedChunk] = {}
        for weight, items in [*lexical, (1.0, semantic)]:
            for rank, item in enumerate(items, 1):
                if item.hardware_version != hardware_version:
                    raise RuntimeError("检索器检测到跨版本污染")
                chunks[item.chunk_id] = item
                scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + weight / rank
        ordered = sorted(chunks.values(), key=lambda item: scores[item.chunk_id], reverse=True)
        return [replace(item, score=scores[item.chunk_id]) for item in ordered[:limit]]
