"""验证 SQLite 文档、知识块和全文索引。"""

import hashlib
import tempfile
import unittest
from pathlib import Path

from jssh_rag.models import Chunk, DocumentMeta, EvidenceLevel
from jssh_rag.store import KnowledgeStore


def make_document(version: str, path: str, content: str) -> tuple[DocumentMeta, Chunk]:
    """构造一份可追溯测试文档及知识块。"""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    document = DocumentMeta(
        product="JSSH",
        hardware_version=version,
        relative_path=path,
        git_commit="a" * 40,
        source_sha256=digest,
        document_type="markdown",
        module="hardware",
        status="current",
        evidence_level=EvidenceLevel.SOURCE_REVIEWED,
    )
    chunk = Chunk.create(document, "测试", 1, 1, content)
    return document, chunk


class KnowledgeStoreTest(unittest.TestCase):
    """验证幂等更新、版本过滤和失效删除。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = KnowledgeStore(Path(directory.name) / "test.sqlite3")
        self.addCleanup(self.store.close)

    def test_identifier_search_finds_engineering_tokens(self):
        document, chunk = make_document(
            "1.6_R6",
            "1.6_R6/hardware/test.md",
            "ADS1298 的 GPIO35 连接 U17，器件标识为 0x1E。",
        )
        self.store.replace_document(document, [chunk])
        for query in ("ADS1298", "GPIO35", "U17", "0x1E"):
            with self.subTest(query=query):
                results = self.store.search(query, "1.6_R6")
                self.assertEqual(chunk.chunk_id, results[0].chunk_id)

    def test_structured_locator_survives_store_and_search(self):
        document, _ = make_document(
            "1.6_R6",
            "1.6_R6/hardware/mainboard-bottom/source/BOM_board.xlsx",
            "Designator: U8\nManufacturer Part: LIS2MDL",
        )
        chunk = Chunk.create_located(
            document,
            "BOM U8",
            "BOM!A2:K2",
            "Designator: U8\nManufacturer Part: LIS2MDL",
        )
        self.store.replace_document(document, [chunk])

        result = self.store.search("LIS2MDL U8", "1.6_R6")[0]

        self.assertEqual("BOM!A2:K2", result.source_locator)
        self.assertEqual((0, 0), (result.start_line, result.end_line))

    def test_version_filter_never_returns_other_version(self):
        r6_document, r6_chunk = make_document("1.6_R6", "1.6_R6/r6.md", "ADS1298 R6")
        v16_document, v16_chunk = make_document("1.6", "1.6/v16.md", "ADS1298 V1.6")
        self.store.replace_document(r6_document, [r6_chunk])
        self.store.replace_document(v16_document, [v16_chunk])
        results = self.store.search("ADS1298", "1.6_R6")
        self.assertEqual(["1.6_R6"], [item.hardware_version for item in results])

    def test_reindex_replaces_old_chunks_without_duplicates(self):
        document, chunk = make_document("1.6_R6", "1.6_R6/test.md", "旧内容")
        self.store.replace_document(document, [chunk])
        changed_document, changed_chunk = make_document("1.6_R6", "1.6_R6/test.md", "新内容")
        self.store.replace_document(changed_document, [changed_chunk])
        self.assertEqual([], self.store.search("旧内容", "1.6_R6"))
        self.assertEqual(1, len(self.store.search("新内容", "1.6_R6")))
        self.store.replace_document(changed_document, [changed_chunk])
        self.assertEqual(1, self.store.chunk_count("1.6_R6"))

    def test_delete_stale_documents_removes_search_results(self):
        first_document, first_chunk = make_document("1.6_R6", "1.6_R6/first.md", "保留词")
        stale_document, stale_chunk = make_document("1.6_R6", "1.6_R6/stale.md", "删除词")
        self.store.replace_document(first_document, [first_chunk])
        self.store.replace_document(stale_document, [stale_chunk])
        self.store.delete_stale_documents("1.6_R6", {first_document.relative_path})
        self.assertEqual([], self.store.search("删除词", "1.6_R6"))
        self.assertEqual(1, self.store.chunk_count("1.6_R6"))

    def test_multiple_engineering_identifiers_rank_by_coverage(self):
        expected_document, expected_chunk = make_document(
            "1.6_R6", "1.6_R6/hardware/compat.md", "ADS1298 DRDY_A 连接 GPIO14"
        )
        partial_document, partial_chunk = make_document(
            "1.6_R6", "1.6_R6/main/noise.md", "ADS1298 普通说明"
        )
        self.store.replace_document(partial_document, [partial_chunk])
        self.store.replace_document(expected_document, [expected_chunk])
        results = self.store.search("R6 ADS1298 DRDY_A GPIO14", "1.6_R6")
        self.assertEqual(expected_chunk.chunk_id, results[0].chunk_id)

    def test_partial_identifier_match_is_rejected_as_insufficient_evidence(self):
        document, chunk = make_document(
            "1.6_R6", "1.6_R6/main/power.md", "nPM1300 包含 BUCK2 电源轨"
        )
        self.store.replace_document(document, [chunk])
        self.assertEqual([], self.store.search("BK7258QN8868 BUCK2", "1.6_R6"))


if __name__ == "__main__":
    unittest.main()
