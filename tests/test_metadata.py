"""验证知识文档的版本身份与证据等级。"""

import unittest

from jssh_rag.models import DocumentMeta, EvidenceLevel, infer_document_fields


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


if __name__ == "__main__":
    unittest.main()
