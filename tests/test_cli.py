"""验证最小命令行入口的版本门控与来源输出。"""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
from io import BytesIO, TextIOWrapper
import json
import tempfile
import unittest
from pathlib import Path

from jssh_rag.cli import build_parser, main
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
        bom_content = "Designator: U10\nManufacturer Part: LIS2MDLTR\nSupplier Part: C919695"
        bom = DocumentMeta(
            product="JSSH",
            hardware_version="1.6_R6",
            relative_path="1.6_R6/hardware/mainboard-bottom/source/BOM_board.xlsx",
            git_commit="a" * 40,
            source_sha256=hashlib.sha256(bom_content.encode("utf-8")).hexdigest(),
            document_type="bom_xlsx",
            module="hardware",
            status="current",
            evidence_level=EvidenceLevel.SOURCE_REVIEWED,
        )
        store.replace_document(
            bom,
            [Chunk.create_located(bom, "BOM U10", "BOM!A2:K2", bom_content)],
        )
        store.set_index_metadata("1.6_R6", r"C:\work1\JSZN\ESP32_S3", document.git_commit)
        store.close()

    def test_missing_version_is_rejected(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as raised:
            main(["search", "--query", "ADS1298", "--database", str(self.database)])
        self.assertEqual(2, raised.exception.code)

    def test_index_help_mentions_controlled_structured_sources(self):
        self.assertIn("受控结构化", build_parser().format_help())

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

    def test_search_prints_structured_source_locator(self):
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "search",
                    "--version",
                    "1.6_R6",
                    "--query",
                    "LIS2MDLTR U10 C919695",
                    "--database",
                    str(self.database),
                ]
            )
        payload = json.loads(output.getvalue())

        self.assertEqual(0, exit_code)
        self.assertEqual("BOM!A2:K2", payload["results"][0]["source_locator"])

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
        self.assertIn("ADS1298 DRDY 源码已检查", payload["conclusion"])
        self.assertIn("不能据此认定真机验收通过", payload["conclusion"])

    def test_evaluate_prints_acceptance_metrics(self):
        cases = self.database.with_name("cases.jsonl")
        rows = [
            {
                "id": "found",
                "question": "ADS1298 DRDY",
                "hardware_version": "1.6_R6",
                "required_sources": ["main/drivers/ads1298.c"],
                "forbidden_versions": ["1.2", "1.6"],
                "required_boundary": "真机边界",
            },
            {
                "id": "refuse",
                "question": "NRF54L15_APPROTECT",
                "hardware_version": "1.6_R6",
                "required_sources": [],
                "forbidden_versions": ["1.2", "1.6", "2.0"],
                "required_boundary": "没有证据时拒答",
                "should_refuse": True,
            },
        ]
        cases.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "evaluate",
                    "--version",
                    "1.6_R6",
                    "--database",
                    str(self.database),
                    "--cases",
                    str(cases),
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["passed"])
        self.assertEqual(2, payload["case_count"])

    def test_search_reconfigures_gbk_console_to_utf8(self):
        store = KnowledgeStore(self.database)
        content = "面积单位 m² 与 ADS1298 一起出现。"
        document = DocumentMeta(
            product="JSSH",
            hardware_version="1.6_R6",
            relative_path="1.6_R6/docs/unicode.md",
            git_commit="a" * 40,
            source_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            document_type="markdown",
            module="docs",
            status="current",
            evidence_level=EvidenceLevel.SOURCE_REVIEWED,
        )
        store.replace_document(document, [Chunk.create(document, "unicode", 1, 1, content)])
        store.close()
        raw = BytesIO()
        output = TextIOWrapper(raw, encoding="gbk", errors="strict")
        with redirect_stdout(output):
            exit_code = main(
                [
                    "search",
                    "--version",
                    "1.6_R6",
                    "--query",
                    "ADS1298 m²",
                    "--database",
                    str(self.database),
                ]
            )
        output.flush()
        payload = json.loads(raw.getvalue().decode("utf-8"))
        self.assertEqual(0, exit_code)
        self.assertTrue(any("m²" in item["content"] for item in payload["results"]))


if __name__ == "__main__":
    unittest.main()
