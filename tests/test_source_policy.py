"""验证 V1.6 R6 语料范围不会污染其他版本。"""

import unittest
from pathlib import Path

from jssh_rag.indexer import SourcePolicy


class SourcePolicyTest(unittest.TestCase):
    """验证路径、扩展名和 Git 跟踪状态过滤。"""

    def setUp(self):
        self.policy = SourcePolicy(
            product="JSSH",
            hardware_version="1.6_R6",
            source_repository=Path(r"C:\work1\JSZN\ESP32_S3"),
            source_prefix="1.6_R6",
            include_extensions=(
                ".md",
                ".c",
                ".h",
                ".py",
                ".ps1",
                ".json",
                ".txt",
                ".xlsx",
                ".tel",
                ".pdf",
            ),
            exclude_segments=(
                ".WORKTREE",
                "ARCHIVE",
                "tmp",
                "output",
                ".BUILD",
                "release/out",
                "validation/results",
                ".pytest_cache",
            ),
            tracked_files_only=True,
            path_patterns={
                ".xlsx": ("1.6_R6/hardware/*/source/BOM_*.xlsx",),
                ".tel": ("1.6_R6/hardware/*/schematic/Netlist_*.tel",),
                ".pdf": (
                    "1.6_R6/hardware/*/schematic/*.pdf",
                    "1.6_R6/hardware/*/manufacturing/*.pdf",
                ),
            },
        )

    def test_v16_file_is_rejected_by_r6_policy(self):
        self.assertFalse(self.policy.accepts("1.6/main/drivers/ads1298.c"))

    def test_r6_tracked_document_is_accepted(self):
        self.assertTrue(self.policy.accepts("1.6_R6/hardware/COMPATIBILITY.md"))

    def test_generated_and_unsupported_files_are_rejected(self):
        self.assertFalse(self.policy.accepts("1.6_R6/.BUILD/config.txt"))
        self.assertFalse(self.policy.accepts("1.6_R6/hardware/schematic.pdf"))

    def test_only_controlled_structured_files_are_accepted(self):
        self.assertTrue(
            self.policy.accepts("1.6_R6/hardware/mainboard-top/source/BOM_board.xlsx")
        )
        self.assertTrue(
            self.policy.accepts(
                "1.6_R6/hardware/mainboard-top/schematic/Netlist_board.tel"
            )
        )
        self.assertTrue(
            self.policy.accepts("1.6_R6/hardware/mainboard-top/schematic/SCH_board.pdf")
        )
        self.assertTrue(
            self.policy.accepts("1.6_R6/hardware/mainboard-top/manufacturing/PCB_board.pdf")
        )
        self.assertFalse(
            self.policy.accepts(
                "1.6_R6/hardware/mainboard-top/manufacturing/PickAndPlace_board.xlsx"
            )
        )
        self.assertFalse(
            self.policy.accepts("1.6_R6/hardware/mainboard-top/source/not_BOM_board.xlsx")
        )
        self.assertFalse(
            self.policy.accepts("1.6_R6/hardware/mainboard-top/source/archive/BOM_board.xlsx")
        )
        self.assertFalse(
            self.policy.accepts("1.6_R6/hardware/mainboard-top/source/Netlist_board.tel")
        )

    def test_structured_extension_without_nonempty_patterns_is_rejected(self):
        for path_patterns in ({}, {".pdf": ()}):
            policy = SourcePolicy(
                product=self.policy.product,
                hardware_version=self.policy.hardware_version,
                source_repository=self.policy.source_repository,
                source_prefix=self.policy.source_prefix,
                include_extensions=self.policy.include_extensions,
                exclude_segments=self.policy.exclude_segments,
                tracked_files_only=self.policy.tracked_files_only,
                path_patterns=path_patterns,
            )
            self.assertFalse(
                policy.accepts("1.6_R6/hardware/mainboard-top/schematic/SCH_board.pdf")
            )

    def test_untracked_file_is_rejected_when_tracking_set_is_supplied(self):
        tracked = {"1.6_R6/hardware/COMPATIBILITY.md"}
        self.assertFalse(self.policy.accepts("1.6_R6/docs/new.md", tracked))
        self.assertTrue(self.policy.accepts("1.6_R6/hardware/COMPATIBILITY.md", tracked))


if __name__ == "__main__":
    unittest.main()
