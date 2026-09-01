"""定义索引、检索和回答共享的不可变数据模型。"""

from dataclasses import dataclass
from enum import StrEnum
import hashlib
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
    priority: int


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
    priority: int = 0

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
        if not isinstance(self.priority, int) or self.priority < 0:
            raise ValueError(f"无效文档优先级: {self.priority}")

    @property
    def document_id(self) -> str:
        """返回由版本和仓库路径确定的稳定文档标识。"""
        identity = f"{self.hardware_version}:{self.relative_path}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Chunk:
    """保留原始行号和文档身份的最小知识块。"""

    chunk_id: str
    document_id: str
    heading_or_symbol: str
    start_line: int
    end_line: int
    content: str

    @classmethod
    def create(
        cls,
        document: DocumentMeta,
        heading_or_symbol: str,
        start_line: int,
        end_line: int,
        content: str,
    ) -> "Chunk":
        """建立内容确定、可重复生成的知识块。

        Args:
            document: 知识块所属文档。
            heading_or_symbol: Markdown 标题、源码符号或字段路径。
            start_line: 一基起始行号。
            end_line: 一基结束行号。
            content: 未丢失原始换行的文本内容。

        Returns:
            具有稳定 SHA-256 标识的知识块。
        """
        if start_line < 1 or end_line < start_line or not content.strip():
            raise ValueError("知识块行号或内容无效")
        identity = "\0".join(
            (
                document.document_id,
                heading_or_symbol,
                str(start_line),
                str(end_line),
                content,
            )
        )
        return cls(
            chunk_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            document_id=document.document_id,
            heading_or_symbol=heading_or_symbol,
            start_line=start_line,
            end_line=end_line,
            content=content,
        )


@dataclass(frozen=True)
class RetrievedChunk:
    """包含完整来源字段的检索结果。"""

    chunk_id: str
    document_id: str
    hardware_version: str
    relative_path: str
    git_commit: str
    source_sha256: str
    document_type: str
    module: str
    status: str
    evidence_level: EvidenceLevel
    heading_or_symbol: str
    start_line: int
    end_line: int
    content: str
    score: float
    priority: int = 0


@dataclass(frozen=True)
class Citation:
    """回答中可直接定位并校验的源文件引用。"""

    relative_path: str
    heading_or_symbol: str
    start_line: int
    end_line: int
    git_commit: str
    source_sha256: str


@dataclass(frozen=True)
class RagAnswer:
    """固定呈现版本、证据、引用与未验证边界的回答。"""

    conclusion: str
    hardware_version: str
    evidence_level: str
    citations: list[Citation]
    validated: list[str]
    unvalidated: list[str]
    next_step: str | None


def infer_document_fields(
    relative_path: str,
    overrides: Mapping[str, Mapping[str, str | int]] | None = None,
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
        ".xlsx": "bom_xlsx",
        ".tel": "protel_netlist",
        ".pdf": "pdf",
    }
    parts = path.parts
    top_module = parts[1] if len(parts) > 1 else "root"
    if top_module == "main" and len(parts) > 2:
        module = parts[2]
    else:
        module = top_module
    selected = (overrides or {}).get(path.as_posix(), {})
    is_design_plan = "/docs/superpowers/plans/" in f"/{path.as_posix()}"
    status = selected.get("status", "draft" if is_design_plan else "current")
    if status not in VALID_STATUSES:
        raise ValueError(f"未知文档状态: {status}")
    try:
        evidence = EvidenceLevel(
            selected.get(
                "evidence_level",
                "design_proposed" if is_design_plan else "source_reviewed",
            )
        )
    except ValueError as exc:
        raise ValueError(f"未知证据等级: {selected.get('evidence_level')}") from exc
    priority = selected.get("priority", 0)
    if not isinstance(priority, int) or priority < 0:
        raise ValueError(f"无效文档优先级: {priority}")
    return DocumentFields(
        document_type=suffix_types.get(path.suffix.lower(), "text"),
        module=module,
        status=status,
        evidence_level=evidence,
        priority=priority,
    )
