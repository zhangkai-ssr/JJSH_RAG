"""验证版本强制过滤、语义补充和 Embedding 缓存。"""

import hashlib
import tempfile
import unittest
from pathlib import Path

from jssh_rag.models import Chunk, DocumentMeta, EvidenceLevel, RetrievedChunk
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


def add_document(
    store: KnowledgeStore,
    version: str,
    path: str,
    content: str,
    status: str = "current",
    priority: int = 0,
) -> Chunk:
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
        status=status,
        evidence_level=EvidenceLevel.SOURCE_REVIEWED,
        priority=priority,
    )
    chunk = Chunk.create(document, "测试", 1, 1, content)
    store.replace_document(document, [chunk])
    return chunk


def retrieved_result(index: int, priority: int = 0) -> RetrievedChunk:
    """构造具有固定身份和优先级的融合排序结果。"""
    identity = f"{index:064d}"
    return RetrievedChunk(
        chunk_id=identity,
        document_id=identity,
        hardware_version="1.6_R6",
        relative_path=f"1.6_R6/docs/{index}.md",
        git_commit="a" * 40,
        source_sha256="b" * 64,
        document_type="markdown",
        module="docs",
        status="current",
        evidence_level=EvidenceLevel.SOURCE_REVIEWED,
        heading_or_symbol="测试",
        start_line=1,
        end_line=1,
        content="R67 DRDY_B",
        score=1.0,
        priority=priority,
    )


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

    def test_current_sources_lead_while_non_current_conflict_is_preserved(self):
        for index in range(3):
            add_document(
                self.store,
                "1.6_R6",
                f"1.6_R6/docs/R67_DRDY_B_old_{index}.md",
                f"R67 DRDY_B 旧方案 {index}。",
                status="superseded",
            )
        add_document(
            self.store,
            "1.6_R6",
            "1.6_R6/hardware/current.md",
            "R67 DRDY_B 当前使用 100 Ω 接 GPIO48。",
            priority=100,
        )
        add_document(
            self.store,
            "1.6_R6",
            "1.6_R6/main/current.md",
            "R67 DRDY_B 只保留为硬件观测网络。",
            priority=90,
        )

        results = self.retriever.search("R67 DRDY_B", "1.6_R6", limit=5)

        self.assertIn("current", {item.status for item in results})
        self.assertIn("superseded", {item.status for item in results})

    def test_compound_question_retrieves_each_clause(self):
        connection = add_document(
            self.store,
            "1.6_R6",
            "1.6_R6/hardware/connection.md",
            "ADS1298 DOUT_B 接 GPIO5。",
        )
        validation = add_document(
            self.store,
            "1.6_R6",
            "1.6_R6/validation/status.md",
            "整机真机验收尚未完成。",
        )

        results = self.retriever.search(
            "ADS1298 DOUT_B 如何连接？整机真机验收是否完成？",
            "1.6_R6",
            limit=2,
        )

        self.assertEqual(
            {connection.chunk_id, validation.chunk_id},
            {item.chunk_id for item in results},
        )

    def test_low_rank_non_current_does_not_displace_current_sources(self):
        for index in range(3):
            add_document(
                self.store,
                "1.6_R6",
                f"1.6_R6/current-{index}.md",
                f"R67 DRDY_B 当前资料 {index}。",
            )
        add_document(
            self.store,
            "1.6_R6",
            "1.6_R6/docs/old.md",
            "R67 旧资料。",
            status="superseded",
        )

        results = self.retriever.search("R67 DRDY_B", "1.6_R6", limit=3)

        self.assertEqual({"current"}, {item.status for item in results})

    def test_controlled_priority_promotes_formal_current_source(self):
        historical = add_document(
            self.store,
            "1.6_R6",
            "1.6_R6/CHANGELOG.md",
            "R67 DRDY_B 旧实物记录为不贴。",
        )
        formal = add_document(
            self.store,
            "1.6_R6",
            "1.6_R6/hardware/COMPATIBILITY.md",
            "R67 把 DRDY_B 当前接到 GPIO48。",
            priority=100,
        )

        results = self.retriever.search("R67 DRDY_B 当前接法", "1.6_R6", limit=2)

        self.assertEqual(formal.chunk_id, results[0].chunk_id)
        self.assertIn(historical.chunk_id, {item.chunk_id for item in results})

    def test_controlled_priority_breaks_only_equal_fusion_scores(self):
        low_priority = retrieved_result(1)
        high_priority = retrieved_result(2, priority=100)

        class EqualScoreStore:
            def search(self, query, hardware_version, limit):
                if "，" in query:
                    return [low_priority, high_priority]
                if query == "alpha":
                    return [high_priority]
                return []

        results = Retriever(EqualScoreStore()).search(
            "alpha，beta", "1.6_R6", limit=2
        )

        self.assertEqual(high_priority.chunk_id, results[0].chunk_id)

    def test_priority_does_not_replace_query_relevance(self):
        add_document(
            self.store,
            "1.6_R6",
            "1.6_R6/hardware/COMPATIBILITY.md",
            "信道切换一般说明。",
            priority=100,
        )
        relevant = add_document(
            self.store,
            "1.6_R6",
            "1.6_R6/tools/channel.md",
            "ESP-NOW 信道切换使用双端 ACK 流程。",
        )

        results = self.retriever.search("信道切换双端流程", "1.6_R6", limit=2)

        self.assertEqual(relevant.chunk_id, results[0].chunk_id)

    def test_fusion_priority_cannot_move_fourth_above_third(self):
        class OrderedStore:
            def search(self, query, hardware_version, limit):
                return [retrieved_result(index, 100 if index == 4 else 0) for index in range(1, 5)]

        results = Retriever(OrderedStore()).search("R67 DRDY_B", "1.6_R6", limit=4)

        self.assertEqual(retrieved_result(3).chunk_id, results[2].chunk_id)
        self.assertEqual(retrieved_result(4).chunk_id, results[3].chunk_id)

    def test_fusion_priority_cannot_move_sixth_into_top_five(self):
        class OrderedStore:
            def search(self, query, hardware_version, limit):
                return [retrieved_result(index, 100 if index == 6 else 0) for index in range(1, 7)]

        results = Retriever(OrderedStore()).search("R67 DRDY_B", "1.6_R6", limit=6)

        self.assertEqual(retrieved_result(5).chunk_id, results[4].chunk_id)
        self.assertEqual(retrieved_result(6).chunk_id, results[5].chunk_id)

    def test_priority_cannot_cross_small_multi_list_score_gap(self):
        more_relevant = retrieved_result(100)
        high_priority = retrieved_result(101, priority=100)

        class MultiListStore:
            def search(self, query, hardware_version, limit):
                if "，" in query:
                    return [retrieved_result(1), retrieved_result(2), more_relevant, high_priority]
                if query == "alpha":
                    return [retrieved_result(3), high_priority, more_relevant]
                if query == "beta":
                    return [retrieved_result(index) for index in range(10, 19)] + [
                        more_relevant,
                        high_priority,
                    ]
                return []

        results = Retriever(MultiListStore()).search(
            "alpha，beta", "1.6_R6", limit=20
        )

        self.assertLess(
            [item.chunk_id for item in results].index(more_relevant.chunk_id),
            [item.chunk_id for item in results].index(high_priority.chunk_id),
        )

    def test_highly_relevant_draft_is_preserved_with_current_source(self):
        for index in range(3):
            add_document(
                self.store,
                "1.6_R6",
                f"1.6_R6/current-sync-{index}.md",
                f"EMG IMU 普通说明 {index}。",
            )
        draft = add_document(
            self.store,
            "1.6_R6",
            "1.6_R6/docs/sync-plan.md",
            "EMG IMU 硬件同步方案仍待实板验证。",
            status="draft",
        )

        results = self.retriever.search("EMG IMU 硬件同步", "1.6_R6", limit=2)

        self.assertIn(draft.chunk_id, {item.chunk_id for item in results})
        self.assertIn("current", {item.status for item in results})


if __name__ == "__main__":
    unittest.main()
