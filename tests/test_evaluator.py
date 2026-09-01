"""验证黄金评估指标计算和验收门。"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from jssh_rag.answering import Answerer
from jssh_rag.evaluator import EvaluationCase, evaluate_cases, load_cases
from jssh_rag.models import Chunk, DocumentMeta, EvidenceLevel
from jssh_rag.retriever import Retriever
from jssh_rag.store import KnowledgeStore


class EvaluatorTest(unittest.TestCase):
    """验证可检索问题与应拒答问题分别计分。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = KnowledgeStore(self.root / "test.sqlite3")
        self.addCleanup(self.store.close)
        content = "R6 ADS1298 双片独立 DOUT 已实现，但仍待真机验证。"
        document = DocumentMeta(
            product="JSSH",
            hardware_version="1.6_R6",
            relative_path="1.6_R6/hardware/COMPATIBILITY.md",
            git_commit="a" * 40,
            source_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            document_type="markdown",
            module="hardware",
            status="current",
            evidence_level=EvidenceLevel.SOURCE_REVIEWED,
        )
        self.store.replace_document(document, [Chunk.create(document, "兼容性", 5, 8, content)])

    def test_perfect_cases_pass_all_gates(self):
        cases = [
            EvaluationCase(
                id="found",
                question="ADS1298 DOUT",
                hardware_version="1.6_R6",
                required_sources=("hardware/COMPATIBILITY.md",),
                forbidden_versions=("1.2", "1.6"),
                required_boundary="真机验证",
                required_answer_terms=("真机",),
                should_refuse=False,
            ),
            EvaluationCase(
                id="refuse",
                question="NRF54L15_APPROTECT",
                hardware_version="1.6_R6",
                required_sources=(),
                forbidden_versions=("1.2", "1.6", "2.0"),
                required_boundary="没有目标版本证据时拒答",
                should_refuse=True,
            ),
        ]
        report = evaluate_cases(Retriever(self.store), Answerer(), cases)
        self.assertEqual(0, report.version_pollution_count)
        self.assertEqual(1.0, report.citation_coverage)
        self.assertEqual(1.0, report.citation_position_accuracy)
        self.assertEqual(1.0, report.evidence_accuracy)
        self.assertEqual(1.0, report.refusal_accuracy)
        self.assertEqual(1.0, report.top5_source_rate)
        self.assertTrue(report.passed)

    def test_loader_rejects_duplicate_ids(self):
        path = self.root / "cases.jsonl"
        row = {
            "id": "duplicate",
            "question": "问题",
            "hardware_version": "1.6_R6",
            "required_sources": [],
            "forbidden_versions": ["1.6"],
            "required_boundary": "拒答",
            "should_refuse": True,
        }
        path.write_text("\n".join((json.dumps(row), json.dumps(row))), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_cases(path)

    def test_loader_reads_required_answer_terms(self):
        path = self.root / "boundary.jsonl"
        path.write_text(
            json.dumps(
                {
                    "id": "boundary",
                    "question": "BOM 边界？",
                    "hardware_version": "1.6_R6",
                    "required_sources": [],
                    "forbidden_versions": [],
                    "required_boundary": "BOM 不等于实物装配",
                    "required_answer_terms": ["BOM", "实物装配"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        case = load_cases(path)[0]

        self.assertEqual(("BOM", "实物装配"), case.required_answer_terms)

    def test_missing_required_answer_term_fails_boundary_gate(self):
        case = EvaluationCase(
            id="missing-boundary",
            question="ADS1298 DOUT",
            hardware_version="1.6_R6",
            required_sources=("hardware/COMPATIBILITY.md",),
            forbidden_versions=("1.2", "1.6"),
            required_boundary="必须出现格式边界",
            required_answer_terms=("不会自然出现的边界词",),
        )

        report = evaluate_cases(Retriever(self.store), Answerer(), [case])

        self.assertEqual(0.0, report.boundary_accuracy)
        self.assertFalse(report.passed)


if __name__ == "__main__":
    unittest.main()
