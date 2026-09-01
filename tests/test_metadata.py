"""验证知识文档的版本身份与证据等级。"""

import json
from pathlib import Path
import unittest

from jssh_rag.models import DocumentMeta, EvidenceLevel, infer_document_fields


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def configured_overrides() -> dict[str, dict[str, str | int]]:
    """读取仓库实际使用的 R6 元数据覆盖。"""
    path = PROJECT_ROOT / "config" / "metadata_overrides" / "v1_6_r6.json"
    return json.loads(path.read_text(encoding="utf-8"))["paths"]


class MetadataTest(unittest.TestCase):
    """验证正式索引所需字段及保守默认值。"""

    def test_document_requires_version_commit_and_hash(self):
        required = {
            "product": "JSSH",
            "hardware_version": "1.6_R6",
            "relative_path": "1.6_R6/hardware/COMPATIBILITY.md",
            "git_commit": "a" * 40,
            "source_sha256": "b" * 64,
            "document_type": "markdown",
            "module": "hardware",
            "status": "current",
            "evidence_level": EvidenceLevel.SOURCE_REVIEWED,
        }
        for field in ("hardware_version", "git_commit", "source_sha256"):
            invalid = dict(required)
            invalid[field] = ""
            with self.subTest(field=field), self.assertRaises(ValueError):
                DocumentMeta(**invalid)

    def test_path_inference_uses_conservative_evidence(self):
        fields = infer_document_fields("1.6_R6/main/drivers/ads1298.c")
        self.assertEqual("drivers", fields.module)
        self.assertEqual("c_source", fields.document_type)
        self.assertEqual("current", fields.status)
        self.assertEqual(EvidenceLevel.SOURCE_REVIEWED, fields.evidence_level)

    def test_structured_extensions_have_explicit_document_types(self):
        cases = {
            "1.6_R6/hardware/mainboard-top/source/BOM_board.xlsx": "bom_xlsx",
            "1.6_R6/hardware/mainboard-top/schematic/Netlist_board.tel": "protel_netlist",
            "1.6_R6/hardware/mainboard-top/schematic/SCH_board.pdf": "pdf",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(expected, infer_document_fields(path).document_type)

    def test_controlled_override_changes_status_and_evidence(self):
        fields = infer_document_fields(
            "1.6_R6/validation/reports/r6.md",
            {
                "1.6_R6/validation/reports/r6.md": {
                    "status": "superseded",
                    "evidence_level": "real_device_partial",
                }
            },
        )
        self.assertEqual("superseded", fields.status)
        self.assertEqual(EvidenceLevel.REAL_DEVICE_PARTIAL, fields.evidence_level)

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(ValueError):
            infer_document_fields(
                "1.6_R6/README.md",
                {"1.6_R6/README.md": {"status": "maybe"}},
            )

    def test_superpowers_plan_is_draft_design_evidence(self):
        fields = infer_document_fields(
            "1.6_R6/docs/superpowers/plans/2026-08-25-private-channel.md"
        )
        self.assertEqual("draft", fields.status)
        self.assertEqual(EvidenceLevel.DESIGN_PROPOSED, fields.evidence_level)

    def test_legacy_gpio35_r67_options_are_superseded(self):
        fields = infer_document_fields(
            "1.6_R6/docs/V1.6_N8R8与ADS1298-B_DRDY引脚冲突说明.md",
            configured_overrides(),
        )
        self.assertEqual("superseded", fields.status)

    def test_legacy_shared_miso_plan_is_superseded(self):
        fields = infer_document_fields(
            "1.6_R6/docs/V1.6双ADS1298单RLD与充电接触共模改善方案.md",
            configured_overrides(),
        )
        self.assertEqual("superseded", fields.status)

    def test_emg_imu_sync_plan_is_draft_design_evidence(self):
        fields = infer_document_fields(
            "1.6_R6/docs/V1.6_EMG与IMU硬件同步方案.md",
            configured_overrides(),
        )
        self.assertEqual("draft", fields.status)
        self.assertEqual(EvidenceLevel.DESIGN_PROPOSED, fields.evidence_level)

    def test_formal_compatibility_source_has_controlled_priority(self):
        fields = infer_document_fields(
            "1.6_R6/hardware/COMPATIBILITY.md",
            configured_overrides(),
        )
        self.assertEqual(100, fields.priority)

    def test_invalid_priority_is_rejected(self):
        with self.assertRaises(ValueError):
            infer_document_fields(
                "1.6_R6/README.md",
                {"1.6_R6/README.md": {"priority": -1}},
            )


if __name__ == "__main__":
    unittest.main()
