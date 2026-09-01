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


def structured_result(document_type: str, content: str) -> RetrievedChunk:
    """构造指定结构化格式的检索结果。"""
    return RetrievedChunk(
        **{
            **result(content, EvidenceLevel.SOURCE_REVIEWED).__dict__,
            "document_type": document_type,
            "start_line": 0,
            "end_line": 0,
            "source_locator": "page 1" if document_type == "pdf" else "BOM!A2:K2",
        }
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

    def test_citation_keeps_structured_source_locator(self):
        structured = RetrievedChunk(
            **{
                **result("Designator: U8\nManufacturer Part: LIS2MDL", EvidenceLevel.SOURCE_REVIEWED).__dict__,
                "relative_path": "1.6_R6/hardware/mainboard-bottom/source/BOM_board.xlsx",
                "heading_or_symbol": "BOM U8",
                "start_line": 0,
                "end_line": 0,
                "source_locator": "BOM!A2:K2",
            }
        )

        answer = Answerer().answer("LIS2MDL U8", "1.6_R6", [structured])

        self.assertEqual("BOM!A2:K2", answer.citations[0].source_locator)

    def test_bom_answer_states_assembly_boundary(self):
        answer = Answerer().answer(
            "U10 的料号是什么？",
            "1.6_R6",
            [structured_result("bom_xlsx", "Designator: U10\nLCSC Part: C919695")],
        )

        self.assertTrue(any("BOM" in item and "实物装配" in item for item in answer.unvalidated))

    def test_netlist_answer_states_physical_continuity_boundary(self):
        answer = Answerer().answer(
            "LIS2_DRDY 连接什么？",
            "1.6_R6",
            [structured_result("protel_netlist", "'LIS2_DRDY' ; CN1.11 CN2.11")],
        )

        self.assertTrue(any("网表" in item and "实板导通" in item for item in answer.unvalidated))

    def test_pdf_answer_states_text_and_graphics_boundary(self):
        answer = Answerer().answer(
            "PDF 中的板厚是什么？",
            "1.6_R6",
            [structured_result("pdf", "Board Thickness 1.2mm")],
        )

        self.assertTrue(
            any(
                "PDF" in item and "图形连通性" in item and "生产授权" in item
                for item in answer.unvalidated
            )
        )

    def test_private_llm_structured_overclaim_is_blocked(self):
        cases = (
            ("bom_xlsx", "BOM 证明已完成实物装配。", "BOM 只证明受控源文件中的物料记录"),
            ("protel_netlist", "网表证明实板导通。", "网表只证明受控导出的连接记录"),
            ("pdf", "PDF 已获生产授权。", "PDF 文字提取只证明页面中的可检索内容"),
        )
        for document_type, unsafe_claim, prompt_boundary in cases:
            with self.subTest(document_type=document_type):
                class UnsafeLlm:
                    def __init__(self):
                        self.prompt = ""

                    def complete(self, prompt: str) -> str:
                        self.prompt = prompt
                        return unsafe_claim

                llm = UnsafeLlm()
                answer = Answerer(llm).answer(
                    "这个导出能证明验收吗？",
                    "1.6_R6",
                    [structured_result(document_type, "受控导出内容")],
                )

                self.assertIn(prompt_boundary, llm.prompt)
                self.assertIn("超出结构化来源证据边界", answer.conclusion)
                self.assertNotEqual(unsafe_claim, answer.conclusion)

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

    def test_deterministic_summary_skips_markdown_heading(self):
        answer = Answerer().answer(
            "BUCK1 给哪些器件供电？",
            "1.6_R6",
            [result("## 启动与电源边界\nBUCK1 为 IMU 与 Flash 供电。", EvidenceLevel.SOURCE_REVIEWED)],
        )
        self.assertIn("BUCK1 为 IMU 与 Flash 供电", answer.conclusion)
        self.assertNotEqual("基于 1.6_R6 的 source_reviewed 证据：启动与电源边界", answer.conclusion)

    def test_real_device_question_keeps_fact_and_boundary(self):
        answer = Answerer().answer(
            "R67 接到哪里，是否已经完成真机验收？",
            "1.6_R6",
            [result("R67 100 Ω 接 GPIO48，仅作硬件观测，仍待真机验证。", EvidenceLevel.SOURCE_REVIEWED)],
        )
        self.assertIn("GPIO48", answer.conclusion)
        self.assertIn("不能据此认定真机验收通过", answer.conclusion)

    def test_system_acceptance_wording_uses_same_real_device_boundary(self):
        answer = Answerer().answer(
            "短时录制能否证明整机验收完成？",
            "1.6_R6",
            [result("短时录制未发现丢帧。", EvidenceLevel.SOURCE_REVIEWED)],
        )
        self.assertIn("不能据此认定真机验收通过", answer.conclusion)

    def test_only_non_current_sources_do_not_become_current_conclusion(self):
        superseded = RetrievedChunk(
            **{
                **result("R67 设为 NC。", EvidenceLevel.SOURCE_REVIEWED).__dict__,
                "status": "superseded",
            }
        )
        answer = Answerer().answer("R67 当前怎么连接？", "1.6_R6", [superseded])
        self.assertIn("仅检索到非 current 来源", answer.conclusion)
        self.assertNotIn("基于 1.6_R6", answer.conclusion)

    def test_deterministic_summary_prefers_query_relevant_table_row(self):
        content = """## 变更记录
本表只追加已经发生的交换。
| 日期 | 范围 | 摘要 |
| --- | --- | --- |
| 2026-08-28 | Wi-Fi 功率 | 低档调整为 15 dBm |
| 2026-08-25 | ESP-NOW 信道切换 | 双端 ACK 流程仍待真机验证 |
"""
        answer = Answerer().answer(
            "ESP-NOW 信道切换流程是什么？",
            "1.6_R6",
            [result(content, EvidenceLevel.SOURCE_REVIEWED)],
        )
        self.assertIn("ESP-NOW 信道切换", answer.conclusion)
        self.assertNotIn("本表只追加", answer.conclusion)

    def test_compound_question_summarizes_each_clause(self):
        answer = Answerer().answer(
            "R67 如何连接？双 ADS 如何读取？",
            "1.6_R6",
            [
                result(
                    "R67 100 Ω 接 GPIO48。\n双 ADS 使用独立 CS 依次读取。",
                    EvidenceLevel.SOURCE_REVIEWED,
                )
            ],
        )

        self.assertIn("R67 100 Ω 接 GPIO48", answer.conclusion)
        self.assertIn("双 ADS 使用独立 CS 依次读取", answer.conclusion)


if __name__ == "__main__":
    unittest.main()
