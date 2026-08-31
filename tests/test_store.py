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


if __name__ == "__main__":
    unittest.main()
