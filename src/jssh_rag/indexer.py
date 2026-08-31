"""只读发现 R6 正式语料并提供后续解析入口。"""

from dataclasses import dataclass
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Collection

from .models import Chunk, DocumentMeta, infer_document_fields
from .store import KnowledgeStore


class SourceProvenanceError(RuntimeError):
    """源工作区内容无法由声明的 Git 提交准确标识。"""


@dataclass(frozen=True)
class IndexReport:
    """一次仓库索引的可审计结果。"""

    hardware_version: str
    git_commit: str
    document_count: int
    chunk_count: int
    removed_document_count: int
    errors: list[str]


@dataclass(frozen=True)
class SourcePolicy:
    """定义单一硬件版本可进入索引的文件边界。"""

    product: str
    hardware_version: str
    source_repository: Path
    source_prefix: str
    include_extensions: tuple[str, ...]
    exclude_segments: tuple[str, ...]
    tracked_files_only: bool

    @classmethod
    def from_json(cls, path: Path) -> "SourcePolicy":
        """从 JSON 文件加载来源策略。

        Args:
            path: 来源策略 JSON 路径。

        Returns:
            规范化后的只读来源策略。
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            product=data["product"],
            hardware_version=data["hardware_version"],
            source_repository=Path(data["source_repository"]),
            source_prefix=data["source_prefix"],
            include_extensions=tuple(item.lower() for item in data["include_extensions"]),
            exclude_segments=tuple(data["exclude_segments"]),
            tracked_files_only=bool(data["tracked_files_only"]),
        )

    def accepts(
        self,
        relative_path: str,
        tracked_files: Collection[str] | None = None,
    ) -> bool:
        """判断仓库相对路径是否属于正式语料。

        Args:
            relative_path: Git 返回的仓库相对路径。
            tracked_files: 可选的 Git 跟踪文件集合。

        Returns:
            路径满足版本、目录、扩展名和跟踪约束时为真。
        """
        normalized = relative_path.replace("\\", "/").strip("/")
        path = PurePosixPath(normalized)
        if not normalized.startswith(f"{self.source_prefix}/"):
            return False
        if path.suffix.lower() not in self.include_extensions:
            return False
        lowered = normalized.casefold()
        if any(
            lowered == excluded.replace("\\", "/").strip("/").casefold()
            or f"/{excluded.replace('\\', '/').strip('/').casefold()}/" in f"/{lowered}/"
            for excluded in self.exclude_segments
        ):
            return False
        if self.tracked_files_only and tracked_files is not None:
            normalized_tracked = {item.replace("\\", "/").strip("/") for item in tracked_files}
            return normalized in normalized_tracked
        return True


def _chunks_from_boundaries(
    document: DocumentMeta,
    lines: list[str],
    boundaries: list[tuple[int, str]],
) -> list[Chunk]:
    """按一基行号边界建立不重叠知识块。"""
    if not lines:
        return []
    ordered = sorted({line: heading for line, heading in boundaries}.items())
    if not ordered or ordered[0][0] > 1:
        ordered.insert(0, (1, "文档说明"))
    chunks: list[Chunk] = []
    for index, (start, heading) in enumerate(ordered):
        end = ordered[index + 1][0] - 1 if index + 1 < len(ordered) else len(lines)
        content = "\n".join(lines[start - 1 : end]).strip("\n")
        if content.strip():
            chunks.append(Chunk.create(document, heading, start, end, content))
    return chunks


def _parse_markdown(document: DocumentMeta, text: str) -> list[Chunk]:
    lines = text.splitlines()
    boundaries = []
    for number, line in enumerate(lines, 1):
        match = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        if match:
            boundaries.append((number, match.group(1)))
    return _chunks_from_boundaries(document, lines, boundaries)


def _parse_c_family(document: DocumentMeta, text: str) -> list[Chunk]:
    lines = text.splitlines()
    boundaries: list[tuple[int, str]] = []
    function_pattern = re.compile(
        r"^(?:static\s+)?(?:inline\s+)?[\w\s\*]+?\b([A-Za-z_]\w*)\s*\([^;]*\)\s*$"
    )
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        macro = re.match(r"^#define\s+([A-Za-z_]\w*)", stripped)
        if macro:
            boundaries.append((number, f"macro {macro.group(1)}"))
            continue
        structure = re.match(r"^(?:typedef\s+)?(struct|enum|union)\b(?:\s+([A-Za-z_]\w*))?", stripped)
        if structure:
            name = structure.group(2) or "anonymous"
            boundaries.append((number, f"{structure.group(1)} {name}"))
            continue
        function = function_pattern.match(stripped)
        if function and function.group(1) not in {"if", "for", "while", "switch"}:
            boundaries.append((number, function.group(1)))
    return _chunks_from_boundaries(document, lines, boundaries)


def _parse_python(document: DocumentMeta, text: str) -> list[Chunk]:
    lines = text.splitlines()
    tree = ast.parse(text)
    boundaries = [
        (node.lineno, f"{type(node).__name__.lower()} {node.name}")
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    return _chunks_from_boundaries(document, lines, boundaries)


def _parse_powershell(document: DocumentMeta, text: str) -> list[Chunk]:
    lines = text.splitlines()
    boundaries = []
    for number, line in enumerate(lines, 1):
        match = re.match(r"^\s*(function|class)\s+([\w-]+)", line, re.IGNORECASE)
        if match:
            boundaries.append((number, f"{match.group(1).lower()} {match.group(2)}"))
    return _chunks_from_boundaries(document, lines, boundaries)


def _parse_json(document: DocumentMeta, text: str) -> list[Chunk]:
    data = json.loads(text)
    lines = text.splitlines()
    if not isinstance(data, dict) or len(lines) < 80:
        return [Chunk.create(document, "$", 1, max(1, len(lines)), text)] if text.strip() else []
    chunks = []
    for key, value in data.items():
        content = json.dumps(value, ensure_ascii=False, indent=2)
        chunks.append(Chunk.create(document, f"$.{key}", 1, max(1, len(lines)), content))
    return chunks


def _parse_text(document: DocumentMeta, text: str) -> list[Chunk]:
    lines = text.splitlines()
    boundaries = [(1, "文档说明")] if lines else []
    for number in range(2, len(lines) + 1):
        if lines[number - 2].strip() == "" and lines[number - 1].strip():
            boundaries.append((number, lines[number - 1].strip()[:80]))
    return _chunks_from_boundaries(document, lines, boundaries)


def parse_document(document: DocumentMeta, text: str) -> list[Chunk]:
    """按文件类型确定性切分文档并保留准确行号。

    Args:
        document: 已完成版本和来源校验的文档元数据。
        text: 严格 UTF-8 解码后的完整文件内容。

    Returns:
        原始位置可回溯的知识块列表。

    Raises:
        SyntaxError: Python 文件不能可靠解析。
        json.JSONDecodeError: JSON 文件不能可靠解析。
    """
    parsers = {
        "markdown": _parse_markdown,
        "c_source": _parse_c_family,
        "c_header": _parse_c_family,
        "python": _parse_python,
        "powershell": _parse_powershell,
        "json": _parse_json,
        "text": _parse_text,
    }
    parser = parsers.get(document.document_type, _parse_text)
    return parser(document, text)


def _run_git(repository: Path, *args: str, text: bool = True) -> str | bytes:
    """在指定只读源仓库运行 Git 查询并返回标准输出。"""
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=text,
        encoding="utf-8" if text else None,
    )
    return result.stdout


def index_repository(
    policy: SourcePolicy,
    store: KnowledgeStore,
    overrides: dict[str, dict[str, str]],
) -> IndexReport:
    """只读索引 Git 跟踪且与 HEAD 身份一致的版本语料。

    Args:
        policy: 单一硬件版本来源策略。
        store: 本地 SQLite 知识库。
        overrides: 以仓库相对路径为键的受控元数据覆盖。

    Returns:
        包含源提交、计数和逐文件错误的索引报告。

    Raises:
        SourceProvenanceError: 目标版本存在已跟踪但未提交的变化。
        subprocess.CalledProcessError: 源路径不是可查询的 Git 仓库。
    """
    repository = policy.source_repository.resolve()
    git_commit = str(_run_git(repository, "rev-parse", "HEAD")).strip()
    dirty = str(
        _run_git(repository, "diff", "--name-only", "HEAD", "--", policy.source_prefix)
    ).strip()
    if dirty:
        raise SourceProvenanceError(
            f"{policy.source_prefix} 存在无法归属 {git_commit} 的已跟踪修改: {dirty}"
        )
    raw_paths = _run_git(repository, "ls-files", "-z", "--", policy.source_prefix, text=False)
    tracked = {
        item.decode("utf-8")
        for item in bytes(raw_paths).split(b"\0")
        if item
    }
    accepted = sorted(path for path in tracked if policy.accepts(path, tracked))
    indexed_paths: set[str] = set()
    document_count = 0
    chunk_count = 0
    errors: list[str] = []
    for relative_path in accepted:
        try:
            raw = (repository / Path(relative_path)).read_bytes()
            text = raw.decode("utf-8-sig")
            fields = infer_document_fields(relative_path, overrides)
            document = DocumentMeta(
                product=policy.product,
                hardware_version=policy.hardware_version,
                relative_path=relative_path,
                git_commit=git_commit,
                source_sha256=hashlib.sha256(raw).hexdigest(),
                document_type=fields.document_type,
                module=fields.module,
                status=fields.status,
                evidence_level=fields.evidence_level,
            )
            chunks = parse_document(document, text)
            store.replace_document(document, chunks)
            indexed_paths.add(relative_path)
            document_count += 1
            chunk_count += len(chunks)
        except (OSError, UnicodeError, SyntaxError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{relative_path}: {type(exc).__name__}: {exc}")
    removed = store.delete_stale_documents(policy.hardware_version, indexed_paths)
    store.set_index_metadata(policy.hardware_version, str(repository), git_commit)
    return IndexReport(
        hardware_version=policy.hardware_version,
        git_commit=git_commit,
        document_count=document_count,
        chunk_count=chunk_count,
        removed_document_count=removed,
        errors=errors,
    )
