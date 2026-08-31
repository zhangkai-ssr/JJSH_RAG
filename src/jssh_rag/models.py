"""定义索引、检索和回答共享的不可变数据模型。"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Mapping


class EvidenceLevel(StrEnum):
    """研发结论可声明的受控证据等级。"""

    DESIGN_PROPOSED = "design_proposed"
    SOURCE_REVIEWED = "source_reviewed"
    SIMULATION_PASSED = "simulation_passed"
    HOST_TEST_PASSED = "host_test_passed"
    QEMU_PASSED = "qemu_passed"
    BUILD_PASSED = "build_passed"
    FLASHED = "flashed"
    REAL_DEVICE_PARTIAL = "real_device_partial"
    REAL_DEVICE_PASSED = "real_device_passed"
    PRODUCTION_ACCEPTED = "production_accepted"


VALID_STATUSES = frozenset({"current", "draft", "superseded", "archive"})


@dataclass(frozen=True)
class DocumentFields:
    """可由路径和受控覆盖推断的文档字段。"""

    document_type: str
    module: str
    status: str
    evidence_level: EvidenceLevel


@dataclass(frozen=True)
class DocumentMeta:
    """正式索引中文档不可缺少的来源身份。"""

    product: str
    hardware_version: str
    relative_path: str
    git_commit: str
    source_sha256: str
    document_type: str
    module: str
    status: str
    evidence_level: EvidenceLevel

    def __post_init__(self) -> None:
        """拒绝无法追溯或不属于受控状态的文档。"""
        required = (
            self.product,
            self.hardware_version,
            self.relative_path,
            self.git_commit,
            self.source_sha256,
            self.document_type,
            self.module,
        )
        if not all(required):
            raise ValueError("正式索引缺少版本、来源或分类字段")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"未知文档状态: {self.status}")


def infer_document_fields(
    relative_path: str,
    overrides: Mapping[str, Mapping[str, str]] | None = None,
) -> DocumentFields:
    """根据仓库相对路径推断保守元数据，并应用精确路径覆盖。

    Args:
        relative_path: 使用正斜杠表示的源仓库相对路径。
        overrides: 以完整相对路径为键的受控字段覆盖。

    Returns:
        可用于构造正式文档元数据的分类字段。

    Raises:
        ValueError: 覆盖包含未知状态或证据等级。
    """
    path = PurePosixPath(relative_path.replace("\\", "/"))
    suffix_types = {
        ".md": "markdown",
        ".c": "c_source",
        ".h": "c_header",
        ".py": "python",
        ".ps1": "powershell",
        ".json": "json",
        ".txt": "text",
    }
    parts = path.parts
    top_module = parts[1] if len(parts) > 1 else "root"
    if top_module == "main" and len(parts) > 2:
        module = parts[2]
    else:
        module = top_module
    selected = (overrides or {}).get(path.as_posix(), {})
    status = selected.get("status", "current")
    if status not in VALID_STATUSES:
        raise ValueError(f"未知文档状态: {status}")
    try:
        evidence = EvidenceLevel(selected.get("evidence_level", "source_reviewed"))
    except ValueError as exc:
        raise ValueError(f"未知证据等级: {selected.get('evidence_level')}") from exc
    return DocumentFields(
        document_type=suffix_types.get(path.suffix.lower(), "text"),
        module=module,
        status=status,
        evidence_level=evidence,
    )
