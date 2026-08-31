"""使用 SQLite 保存可追溯文档、知识块和 FTS5 全文索引。"""

from pathlib import Path
from datetime import UTC, datetime
from dataclasses import replace
import json
import math
import re
import sqlite3
from typing import Iterable

from .models import Chunk, DocumentMeta, EvidenceLevel, RetrievedChunk


class KnowledgeStore:
    """管理单机 SQLite 索引并在查询时强制硬件版本过滤。"""

    def __init__(self, path: Path):
        """打开数据库并建立最小表结构。

        Args:
            path: SQLite 数据库文件路径，父目录会自动创建。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                product TEXT NOT NULL,
                hardware_version TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                git_commit TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                document_type TEXT NOT NULL,
                module TEXT NOT NULL,
                status TEXT NOT NULL,
                evidence_level TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                UNIQUE(hardware_version, relative_path)
            );
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                heading_or_symbol TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                content TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED,
                content,
                tokenize = 'unicode61'
            );
            CREATE TABLE IF NOT EXISTS index_metadata (
                hardware_version TEXT PRIMARY KEY,
                source_repository TEXT NOT NULL,
                git_commit TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS embeddings (
                cache_key TEXT PRIMARY KEY,
                vector_json TEXT NOT NULL
            );
            """
        )
        columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(documents)").fetchall()
        }
        if "priority" not in columns:
            self.connection.execute(
                "ALTER TABLE documents ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"
            )
        self.connection.commit()

    def close(self) -> None:
        """关闭 SQLite 连接。"""
        self.connection.close()

    def replace_document(self, document: DocumentMeta, chunks: Iterable[Chunk]) -> None:
        """原子替换一个文档及其全部知识块。

        Args:
            document: 包含完整来源身份的文档。
            chunks: 当前文件解析得到的全部知识块。
        """
        items = list(chunks)
        if any(item.document_id != document.document_id for item in items):
            raise ValueError("知识块不属于指定文档")
        with self.connection:
            old_ids = [
                row[0]
                for row in self.connection.execute(
                    "SELECT chunk_id FROM chunks WHERE document_id = ?", (document.document_id,)
                )
            ]
            if old_ids:
                self.connection.executemany(
                    "DELETE FROM chunks_fts WHERE chunk_id = ?", ((item,) for item in old_ids)
                )
            self.connection.execute("DELETE FROM chunks WHERE document_id = ?", (document.document_id,))
            self.connection.execute(
                """
                INSERT INTO documents (
                    document_id, product, hardware_version, relative_path, git_commit,
                    source_sha256, document_type, module, status, evidence_level, priority
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    product=excluded.product,
                    hardware_version=excluded.hardware_version,
                    relative_path=excluded.relative_path,
                    git_commit=excluded.git_commit,
                    source_sha256=excluded.source_sha256,
                    document_type=excluded.document_type,
                    module=excluded.module,
                    status=excluded.status,
                    evidence_level=excluded.evidence_level,
                    priority=excluded.priority
                """,
                (
                    document.document_id,
                    document.product,
                    document.hardware_version,
                    document.relative_path,
                    document.git_commit,
                    document.source_sha256,
                    document.document_type,
                    document.module,
                    document.status,
                    document.evidence_level.value,
                    document.priority,
                ),
            )
            self.connection.executemany(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
                (
                    (
                        item.chunk_id,
                        item.document_id,
                        item.heading_or_symbol,
                        item.start_line,
                        item.end_line,
                        item.content,
                    )
                    for item in items
                ),
            )
            self.connection.executemany(
                "INSERT INTO chunks_fts(chunk_id, content) VALUES (?, ?)",
                ((item.chunk_id, item.content) for item in items),
            )

    def delete_stale_documents(self, hardware_version: str, current_paths: set[str]) -> int:
        """删除指定版本中已不在 Git 清单里的文档。

        Args:
            hardware_version: 必须显式给出的目标版本。
            current_paths: 当前仍有效的仓库相对路径集合。

        Returns:
            删除的文档数量。
        """
        rows = self.connection.execute(
            "SELECT document_id, relative_path FROM documents WHERE hardware_version = ?",
            (hardware_version,),
        ).fetchall()
        stale_ids = [row["document_id"] for row in rows if row["relative_path"] not in current_paths]
        with self.connection:
            for document_id in stale_ids:
                chunk_ids = self.connection.execute(
                    "SELECT chunk_id FROM chunks WHERE document_id = ?", (document_id,)
                ).fetchall()
                self.connection.executemany(
                    "DELETE FROM chunks_fts WHERE chunk_id = ?", ((row[0],) for row in chunk_ids)
                )
                self.connection.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
        return len(stale_ids)

    def _rows_to_results(self, rows: Iterable[sqlite3.Row]) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                hardware_version=row["hardware_version"],
                relative_path=row["relative_path"],
                git_commit=row["git_commit"],
                source_sha256=row["source_sha256"],
                document_type=row["document_type"],
                module=row["module"],
                status=row["status"],
                evidence_level=EvidenceLevel(row["evidence_level"]),
                heading_or_symbol=row["heading_or_symbol"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                content=row["content"],
                score=float(row["score"]),
                priority=int(row["priority"]),
            )
            for row in rows
        ]

    def search(self, query: str, hardware_version: str, limit: int = 8) -> list[RetrievedChunk]:
        """检索目标版本，精确子串优先并用 FTS5 补充。

        Args:
            query: 用户查询或工程标识符。
            hardware_version: 强制过滤的硬件版本。
            limit: 最多返回的去重结果数。

        Returns:
            按精确命中和全文相关性排序的知识块。
        """
        if not query.strip() or not hardware_version:
            raise ValueError("查询和硬件版本不能为空")
        ascii_tokens = [
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_+.:-]*", query)
            if len(token) >= 2
        ]
        strong_tokens = [
            token
            for token in ascii_tokens
            if any(character.isdigit() for character in token)
            or "_" in token
            or (len(token) >= 3 and token.upper() == token)
        ]
        cjk_stop = {"什么", "如何", "是否", "哪个", "关系", "说明", "当前", "其中", "以及", "能否", "怎样"}
        cjk_bigrams = {
            sequence[index : index + 2]
            for sequence in re.findall(r"[\u3400-\u9fff]+", query)
            for index in range(max(0, len(sequence) - 1))
            if sequence[index : index + 2] not in cjk_stop
        }
        match_tokens = list(dict.fromkeys(ascii_tokens + sorted(cjk_bigrams)))
        fts_rank: dict[str, int] = {}
        if match_tokens:
            match_query = " OR ".join(
                f'"{token.replace(chr(34), chr(34) * 2)}"' for token in match_tokens
            )
            try:
                rows = self.connection.execute(
                    """
                    SELECT chunks_fts.chunk_id
                    FROM chunks_fts
                    JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
                    JOIN documents d ON d.document_id = c.document_id
                    WHERE chunks_fts MATCH ? AND d.hardware_version = ?
                    ORDER BY bm25(chunks_fts) LIMIT 200
                    """,
                    (match_query, hardware_version),
                ).fetchall()
                fts_rank = {row[0]: rank for rank, row in enumerate(rows, 1)}
            except sqlite3.OperationalError:
                fts_rank = {}
        rows = self.connection.execute(
            """
            SELECT c.*, d.hardware_version, d.relative_path, d.git_commit,
                   d.source_sha256, d.document_type, d.module, d.status,
                   d.evidence_level, d.priority, 0.0 AS score
            FROM chunks c JOIN documents d ON d.document_id = c.document_id
            WHERE d.hardware_version = ?
            """,
            (hardware_version,),
        ).fetchall()
        ranked: list[tuple[float, RetrievedChunk]] = []
        required_strong = len(strong_tokens) if len(strong_tokens) <= 2 else math.ceil(len(strong_tokens) * 0.6)
        compact_query = re.sub(r"\s+", " ", query.strip()).casefold()
        for item in self._rows_to_results(rows):
            content = item.content.casefold()
            location = f"{item.relative_path} {item.heading_or_symbol}".casefold()
            haystack = f"{location}\n{content}"
            matched_strong = sum(token in haystack for token in strong_tokens)
            if strong_tokens and matched_strong < required_strong:
                continue
            matched_ascii = sum(token in haystack for token in ascii_tokens)
            matched_cjk = sum(token in haystack for token in cjk_bigrams)
            required_cjk = len(cjk_bigrams) if len(cjk_bigrams) <= 3 else max(1, math.ceil(len(cjk_bigrams) * 0.15))
            if not strong_tokens and not matched_ascii and matched_cjk < required_cjk:
                continue
            score = matched_strong * 10.0 + matched_ascii * 2.0
            if cjk_bigrams:
                score += 5.0 * matched_cjk / len(cjk_bigrams)
            if compact_query and compact_query in haystack:
                score += 20.0
            score += sum(1.0 for token in ascii_tokens if token in location)
            if item.chunk_id in fts_rank:
                score += 1.0 / fts_rank[item.chunk_id]
            score += item.priority / 100.0
            ranked.append((score, replace(item, score=score)))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in ranked[:limit]]

    def chunk_count(self, hardware_version: str) -> int:
        """返回指定版本的知识块数量。"""
        return int(
            self.connection.execute(
                """
                SELECT count(*) FROM chunks c
                JOIN documents d ON d.document_id = c.document_id
                WHERE d.hardware_version = ?
                """,
                (hardware_version,),
            ).fetchone()[0]
        )

    def set_index_metadata(
        self,
        hardware_version: str,
        source_repository: str,
        git_commit: str,
    ) -> None:
        """记录指定版本索引对应的源仓库和提交。"""
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO index_metadata VALUES (?, ?, ?, ?)
                ON CONFLICT(hardware_version) DO UPDATE SET
                    source_repository=excluded.source_repository,
                    git_commit=excluded.git_commit,
                    indexed_at=excluded.indexed_at
                """,
                (
                    hardware_version,
                    source_repository,
                    git_commit,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def get_index_metadata(self, hardware_version: str) -> sqlite3.Row | None:
        """读取指定版本最近一次索引身份。"""
        return self.connection.execute(
            "SELECT * FROM index_metadata WHERE hardware_version = ?",
            (hardware_version,),
        ).fetchone()

    def all_chunks(self, hardware_version: str) -> list[RetrievedChunk]:
        """返回指定版本的全部知识块供本地语义重排。"""
        rows = self.connection.execute(
            """
            SELECT c.*, d.hardware_version, d.relative_path, d.git_commit,
                   d.source_sha256, d.document_type, d.module, d.status,
                   d.evidence_level, d.priority, 0.0 AS score
            FROM chunks c JOIN documents d ON d.document_id = c.document_id
            WHERE d.hardware_version = ?
            """,
            (hardware_version,),
        ).fetchall()
        return self._rows_to_results(rows)

    def get_embedding(self, cache_key: str) -> list[float] | None:
        """读取一个知识块的缓存向量。"""
        row = self.connection.execute(
            "SELECT vector_json FROM embeddings WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        return [float(item) for item in json.loads(row[0])] if row else None

    def set_embedding(self, cache_key: str, vector: list[float]) -> None:
        """保存一个由来源哈希和 chunk 标识确定的向量。"""
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO embeddings VALUES (?, ?)",
                (cache_key, json.dumps(vector, separators=(",", ":"))),
            )
