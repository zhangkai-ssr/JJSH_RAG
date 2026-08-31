"""验证版本强制过滤、语义补充和 Embedding 缓存。"""

import hashlib
import tempfile
import unittest
from pathlib import Path

from jssh_rag.models import Chunk, DocumentMeta, EvidenceLevel
from jssh_rag.retriever import Retriever
from jssh_rag.store import KnowledgeStore


class KeywordEmbedding:
    """用固定关键词维度模拟私有 Embedding 服务。"""

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        dimensions = (("drdy", "采样就绪"), ("同步", "协调"), ("电源",))
        return [
            [float(any(word in text.casefold() for word in alternatives)) for alternatives in dimensions]
            for text in texts
        ]


def add_document(store: KnowledgeStore, version: str, path: str, content: str) -> Chunk:
    """写入单知识块测试文档。"""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    document = DocumentMeta(
        product="JSSH",
        hardware_version=version,
        relative_path=path,
        git_commit="a" * 40,
        source_sha256=digest,
        document_type="markdown",
        module="drivers",
        status="current",
        evidence_level=EvidenceLevel.SOURCE_REVIEWED,
    )
    chunk = Chunk.create(document, "测试", 1, 1, content)
    store.replace_document(document, [chunk])
    return chunk


class RetrieverTest(unittest.TestCase):
    """验证混合排序不会越过硬件版本边界。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = KnowledgeStore(Path(directory.name) / "test.sqlite3")
        self.addCleanup(self.store.close)
        self.embedding = KeywordEmbedding()
        self.retriever = Retriever(self.store, self.embedding)

    def test_querying_r6_never_returns_v16_chunks(self):
        add_document(self.store, "1.6_R6", "1.6_R6/r6.md", "DRDY 同步方案")
        add_document(self.store, "1.6", "1.6/v16.md", "DRDY 同步方案")
        results = self.retriever.search("DRDY 同步", "1.6_R6")
        self.assertTrue(results)
        self.assertEqual({"1.6_R6"}, {item.hardware_version for item in results})

    def test_semantic_source_enters_top_five(self):
        expected = add_document(
            self.store,
            "1.6_R6",
            "1.6_R6/main/drivers/ads1298.c",
            "采样就绪中断负责协调数据时刻",
        )
        for index in range(7):
            add_document(
                self.store,
                "1.6_R6",
                f"1.6_R6/docs/noise-{index}.md",
                f"普通说明 {index}",
            )
        results = self.retriever.search("DRDY 同步", "1.6_R6", limit=5)
        self.assertIn(expected.chunk_id, [item.chunk_id for item in results])

    def test_chunk_embeddings_are_reused_from_cache(self):
        add_document(self.store, "1.6_R6", "1.6_R6/r6.md", "DRDY 同步方案")
        self.retriever.search("同步", "1.6_R6")
        first_embedded_count = sum(len(call) for call in self.embedding.calls)
        self.retriever.search("电源", "1.6_R6")
        second_embedded_count = sum(len(call) for call in self.embedding.calls)
        self.assertEqual(first_embedded_count + 1, second_embedded_count)


if __name__ == "__main__":
    unittest.main()
