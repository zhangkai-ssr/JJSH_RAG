"""只读发现 R6 正式语料并提供后续解析入口。"""

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Collection


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
