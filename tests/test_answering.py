"""验证回答引用、拒答和证据等级边界。"""

import unittest

from jssh_rag.answering import Answerer
from jssh_rag.models import EvidenceLevel, RetrievedChunk


def result(content: str, evidence: EvidenceLevel, start: int = 10, end: int = 12) -> RetrievedChunk:
    """构造带完整来源字段的检索结果。"""
    return RetrievedChunk(
        chunk_id="c" * 64,
        document_id="d" * 64,
        hardware_version="1.6_R6",
        relative_path="1.6_R6/docs/status.md",
        git_commit="a" * 40,
        source_sha256="b" * 64,
        document_type="markdown",
        module="docs",
        status="current",
        evidence_level=evidence,
        heading_or_symbol="验证状态",
        start_line=start,
        end_line=end,
        content=content,
        score=1.0,
    )


class AnswererTest(unittest.TestCase):
    """验证回答不会缺引用或擅自提升证据。"""

    def test_empty_retrieval_refuses_to_answer(self):
        answer = Answerer().answer("R6 是否通过？", "1.6_R6", [])
        self.assertIn("不确定", answer.conclusion)
        self.assertEqual([], answer.citations)
        self.assertEqual("none", answer.evidence_level)

    def test_qemu_evidence_cannot_be_called_real_device_passed(self):
        answer = Answerer().answer(
            "R6 是否完成真机验收？",
            "1.6_R6",
            [result("QEMU 测试通过。", EvidenceLevel.QEMU_PASSED)],
        )
        self.assertNotIn("真机通过", answer.conclusion)
        self.assertTrue(any("未找到" in item and "真机" in item for item in answer.unvalidated))
        self.assertEqual("qemu_passed", answer.evidence_level)

    def test_implemented_and_pending_evidence_are_both_shown(self):
        answer = Answerer().answer(
            "当前状态？",
            "1.6_R6",
            [result("功能已实现，但仍待真机验证。", EvidenceLevel.SOURCE_REVIEWED)],
        )
        self.assertTrue(any("已实现" in item for item in answer.validated))
        self.assertTrue(any("待真机验证" in item for item in answer.unvalidated))

    def test_citation_uses_retrieved_file_and_exact_lines(self):
        answer = Answerer().answer(
            "DRDY 状态？",
            "1.6_R6",
            [result("DRDY 源码已检查。", EvidenceLevel.SOURCE_REVIEWED, 23, 27)],
        )
        citation = answer.citations[0]
        self.assertEqual("1.6_R6/docs/status.md", citation.relative_path)
        self.assertEqual((23, 27), (citation.start_line, citation.end_line))
        self.assertEqual("a" * 40, citation.git_commit)

    def test_other_version_result_is_rejected(self):
        foreign = result("其他版本", EvidenceLevel.SOURCE_REVIEWED)
        foreign = RetrievedChunk(**{**foreign.__dict__, "hardware_version": "1.6"})
        with self.assertRaises(ValueError):
            Answerer().answer("状态？", "1.6_R6", [foreign])

    def test_design_proposal_is_not_listed_as_validated(self):
        answer = Answerer().answer(
            "方案状态？",
            "1.6_R6",
            [result("建议调整 RLD 阻容，预期改善噪声。", EvidenceLevel.DESIGN_PROPOSED)],
        )
        self.assertEqual([], answer.validated)
        self.assertTrue(any("预期改善" in item for item in answer.unvalidated))

    def test_current_and_superseded_sources_are_reported_as_conflict(self):
        current = result("GPIO48 是当前映射。", EvidenceLevel.SOURCE_REVIEWED)
        superseded = RetrievedChunk(
            **{
                **result("GPIO35 是旧映射。", EvidenceLevel.SOURCE_REVIEWED, 20, 21).__dict__,
                "chunk_id": "e" * 64,
                "relative_path": "1.6_R6/docs/old.md",
                "status": "superseded",
            }
        )
        answer = Answerer().answer("DRDY_B 接哪里？", "1.6_R6", [current, superseded])
        self.assertIn("冲突", answer.conclusion)
        self.assertTrue(any("superseded" in item for item in answer.unvalidated))
        self.assertEqual(2, len(answer.citations))


if __name__ == "__main__":
    unittest.main()
