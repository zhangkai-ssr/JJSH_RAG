"""提供 R6 索引、检索、回答和评估的最小命令行入口。"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from .answering import Answerer, HttpLlmProvider
from .evaluator import evaluate_cases, load_cases
from .indexer import SourcePolicy, index_repository
from .retriever import HttpEmbeddingProvider, Retriever
from .store import KnowledgeStore


ALLOWED_VERSIONS = ("1.6_R6",)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _configure_utf8_output() -> None:
    """让 Windows 控制台稳定输出含工程符号的 JSON。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """为每个命令增加强制版本和本地数据库参数。"""
    parser.add_argument("--version", required=True, choices=ALLOWED_VERSIONS)
    parser.add_argument(
        "--database",
        type=Path,
        default=PROJECT_ROOT / "data" / "jssh_rag.sqlite3",
    )


def build_parser() -> argparse.ArgumentParser:
    """建立严格限制为 V1.6 R6 的命令行解析器。"""
    parser = argparse.ArgumentParser(prog="jssh-rag")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser(
        "index",
        help="索引 Git 跟踪的 R6 正式文本和受控结构化来源",
    )
    _add_common_arguments(index_parser)
    index_parser.add_argument(
        "--source-config",
        type=Path,
        default=PROJECT_ROOT / "config" / "sources" / "v1_6_r6.json",
    )
    index_parser.add_argument(
        "--metadata-overrides",
        type=Path,
        default=PROJECT_ROOT / "config" / "metadata_overrides" / "v1_6_r6.json",
    )

    for name, help_text in (("search", "检索 R6 证据"), ("ask", "生成带引用的 R6 回答")):
        command = subparsers.add_parser(name, help=help_text)
        _add_common_arguments(command)
        command.add_argument("--query", required=True)
        command.add_argument("--limit", type=int, default=8)
    evaluate_parser = subparsers.add_parser("evaluate", help="运行 R6 黄金评估集")
    _add_common_arguments(evaluate_parser)
    evaluate_parser.add_argument(
        "--cases",
        type=Path,
        default=PROJECT_ROOT / "evals" / "v1_6_r6.jsonl",
    )
    return parser


def _load_overrides(path: Path) -> dict[str, dict[str, str | int]]:
    """读取受控元数据覆盖中的精确路径映射。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload.get("paths", {}))


def _require_index(store: KnowledgeStore, hardware_version: str):
    """拒绝在没有对应版本索引身份时查询。"""
    metadata = store.get_index_metadata(hardware_version)
    if metadata is None:
        raise RuntimeError(f"尚未建立 {hardware_version} 索引，请先运行 index")
    return metadata


def main(argv: list[str] | None = None) -> int:
    """运行命令并以 JSON 输出可复现结果。

    Args:
        argv: 可选参数列表；省略时读取当前进程命令行。

    Returns:
        成功为 0；索引包含文件错误时为 1；运行条件不满足时为 2。
    """
    _configure_utf8_output()
    args = build_parser().parse_args(argv)
    store = KnowledgeStore(args.database)
    try:
        if args.command == "index":
            policy = SourcePolicy.from_json(args.source_config)
            if policy.hardware_version != args.version:
                raise ValueError("来源策略版本与命令版本不一致")
            report = index_repository(policy, store, _load_overrides(args.metadata_overrides))
            print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
            return 1 if report.errors else 0

        metadata = _require_index(store, args.version)
        retriever = Retriever(store, HttpEmbeddingProvider.from_environment())
        if args.command == "evaluate":
            report = evaluate_cases(
                retriever,
                Answerer(HttpLlmProvider.from_environment()),
                load_cases(args.cases),
            )
            print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
            return 0 if report.passed else 1
        results = retriever.search(args.query, args.version, args.limit)
        if args.command == "search":
            payload = {
                "hardware_version": args.version,
                "git_commit": metadata["git_commit"],
                "source_repository": metadata["source_repository"],
                "results": [asdict(item) for item in results],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        answerer = Answerer(HttpLlmProvider.from_environment())
        print(json.dumps(asdict(answerer.answer(args.query, args.version, results)), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
