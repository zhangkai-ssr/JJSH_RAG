"""验证确定性文本切分与来源追溯。"""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from jssh_rag.indexer import SourcePolicy, SourceProvenanceError, index_repository, parse_document
from jssh_rag.models import DocumentMeta, EvidenceLevel
from jssh_rag.store import KnowledgeStore


FIXTURES = Path(__file__).parent / "fixtures"


def sample_meta(relative_path: str, text: str) -> DocumentMeta:
    """为解析测试建立完整来源身份。"""
    return DocumentMeta(
        product="JSSH",
        hardware_version="1.6_R6",
        relative_path=relative_path,
        git_commit="a" * 40,
        source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        document_type="markdown" if relative_path.endswith(".md") else "c_source",
        module="drivers",
        status="current",
        evidence_level=EvidenceLevel.SOURCE_REVIEWED,
    )


class IndexerTest(unittest.TestCase):
    """验证标题、符号、行号和稳定标识。"""

    def test_markdown_headings_keep_exact_line_ranges(self):
        text = (FIXTURES / "sample.md").read_text(encoding="utf-8")
        chunks = parse_document(sample_meta("1.6_R6/docs/sample.md", text), text)
        self.assertEqual(["ADS1298 总览", "DRDY", "验证边界"], [item.heading_or_symbol for item in chunks])
        self.assertEqual((1, 4), (chunks[0].start_line, chunks[0].end_line))
        self.assertEqual((5, 8), (chunks[1].start_line, chunks[1].end_line))
        self.assertIn("不代表真机通过", chunks[2].content)

    def test_c_functions_structs_and_macro_groups_are_locatable(self):
        text = (FIXTURES / "sample.c").read_text(encoding="utf-8")
        chunks = parse_document(sample_meta("1.6_R6/main/drivers/sample.c", text), text)
        symbols = [item.heading_or_symbol for item in chunks]
        self.assertTrue(any("ADS1298_ID" in symbol for symbol in symbols))
        function = next(item for item in chunks if "read_drdy" in item.heading_or_symbol)
        self.assertEqual(8, function.start_line)
        self.assertEqual(11, function.end_line)

    def test_same_document_produces_same_chunk_ids(self):
        text = (FIXTURES / "sample.md").read_text(encoding="utf-8")
        meta = sample_meta("1.6_R6/docs/sample.md", text)
        first = parse_document(meta, text)
        second = parse_document(meta, text)
        self.assertEqual([item.chunk_id for item in first], [item.chunk_id for item in second])

    def test_invalid_utf8_is_reportable_not_silently_ignored(self):
        with self.assertRaises(UnicodeDecodeError):
            b"\xff".decode("utf-8")


class RepositoryIndexTest(unittest.TestCase):
    """验证索引只读取提交身份一致的 Git 跟踪文件。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "rag-test@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "RAG Test"], cwd=self.root, check=True)
        (self.root / "1.6_R6" / "docs").mkdir(parents=True)
        (self.root / "1.6_R6" / "docs" / "tracked.md").write_text("# R6\n\nADS1298", encoding="utf-8")
        subprocess.run(["git", "add", "1.6_R6/docs/tracked.md"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=self.root, check=True, capture_output=True)
        self.policy = SourcePolicy(
            product="JSSH",
            hardware_version="1.6_R6",
            source_repository=self.root,
            source_prefix="1.6_R6",
            include_extensions=(".md",),
            exclude_segments=(".WORKTREE", "output"),
            tracked_files_only=True,
        )
        self.store = KnowledgeStore(self.root / "index.sqlite3")
        self.addCleanup(self.store.close)

    def test_index_uses_git_list_and_excludes_untracked_files(self):
        (self.root / "1.6_R6" / "docs" / "untracked.md").write_text("秘密草稿", encoding="utf-8")
        report = index_repository(self.policy, self.store, overrides={})
        self.assertEqual(1, report.document_count)
        self.assertEqual([], report.errors)
        self.assertEqual([], self.store.search("秘密草稿", "1.6_R6"))
        self.assertEqual(1, len(self.store.search("ADS1298", "1.6_R6")))
        self.assertEqual(40, len(report.git_commit))

    def test_tracked_worktree_change_is_rejected_before_indexing(self):
        (self.root / "1.6_R6" / "docs" / "tracked.md").write_text("未提交修改", encoding="utf-8")
        with self.assertRaises(SourceProvenanceError):
            index_repository(self.policy, self.store, overrides={})


if __name__ == "__main__":
    unittest.main()
