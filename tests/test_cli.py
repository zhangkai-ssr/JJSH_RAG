"""验证最小命令行入口的版本门控与来源输出。"""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
import tempfile
import unittest
from pathlib import Path

from jssh_rag.cli import main
from jssh_rag.models import Chunk, DocumentMeta, EvidenceLevel
from jssh_rag.store import KnowledgeStore


class CliTest(unittest.TestCase):
    """验证 CLI 不允许省略或混用目标版本。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "test.sqlite3"
        store = KnowledgeStore(self.database)
        content = "ADS1298 DRDY 源码已检查，待真机验证。"
        document = DocumentMeta(
            product="JSSH",
            hardware_version="1.6_R6",
            relative_path="1.6_R6/main/drivers/ads1298.c",
            git_commit="a" * 40,
            source_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            document_type="c_source",
            module="drivers",
            status="current",
            evidence_level=EvidenceLevel.SOURCE_REVIEWED,
        )
        store.replace_document(document, [Chunk.create(document, "drdy", 30, 34, content)])
        store.set_index_metadata("1.6_R6", r"C:\work1\JSZN\ESP32_S3", document.git_commit)
        store.close()

    def test_missing_version_is_rejected(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as raised:
            main(["search", "--query", "ADS1298", "--database", str(self.database)])
        self.assertEqual(2, raised.exception.code)

    def test_unknown_version_lists_allowed_value(self):
        error = StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            main(
                [
                    "search",
                    "--version",
                    "1.6",
                    "--query",
                    "ADS1298",
                    "--database",
                    str(self.database),
                ]
            )
        self.assertEqual(2, raised.exception.code)
        self.assertIn("1.6_R6", error.getvalue())

    def test_search_prints_version_commit_and_source_lines(self):
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "search",
                    "--version",
                    "1.6_R6",
                    "--query",
                    "ADS1298",
                    "--database",
                    str(self.database),
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual("1.6_R6", payload["hardware_version"])
        self.assertEqual("a" * 40, payload["git_commit"])
        self.assertEqual(30, payload["results"][0]["start_line"])

    def test_ask_prints_fixed_answer_fields(self):
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "ask",
                    "--version",
                    "1.6_R6",
                    "--query",
                    "是否已经真机通过？",
                    "--database",
                    str(self.database),
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual(
            {"conclusion", "hardware_version", "evidence_level", "citations", "validated", "unvalidated", "next_step"},
            set(payload),
        )
        self.assertIn("不确定", payload["conclusion"])


if __name__ == "__main__":
    unittest.main()
