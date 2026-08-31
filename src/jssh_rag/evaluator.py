"""运行 V1.6 R6 黄金问题并计算版本、引用和证据验收指标。"""

from dataclasses import dataclass
import json
from pathlib import Path

from .answering import Answerer, EVIDENCE_ORDER
from .retriever import Retriever


@dataclass(frozen=True)
class EvaluationCase:
    """一条可重复执行的 R6 工程问题。"""

    id: str
    question: str
    hardware_version: str
    required_sources: tuple[str, ...]
    forbidden_versions: tuple[str, ...]
    required_boundary: str
    should_refuse: bool = False


@dataclass(frozen=True)
class CaseResult:
    """单条问题的检索与回答判定。"""

    id: str
    retrieved_count: int
    source_top5_ok: bool
    version_clean: bool
    citation_covered: bool
    citation_positions_ok: bool
    evidence_ok: bool
    refusal_ok: bool
    boundary_ok: bool


@dataclass(frozen=True)
class EvaluationReport:
    """MVP 评估门要求的汇总指标。"""

    case_count: int
    version_pollution_count: int
    citation_coverage: float
    citation_position_accuracy: float
    evidence_accuracy: float
    refusal_accuracy: float
    top5_source_rate: float
    boundary_accuracy: float
    passed: bool
    cases: list[CaseResult]


def load_cases(path: Path) -> list[EvaluationCase]:
    """从 JSONL 加载并校验问题标识唯一性。"""
    cases: list[EvaluationCase] = []
    ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        case_id = str(payload["id"])
        if case_id in ids:
            raise ValueError(f"评估问题 ID 重复: {case_id}，行 {line_number}")
        ids.add(case_id)
        cases.append(
            EvaluationCase(
                id=case_id,
                question=str(payload["question"]),
                hardware_version=str(payload["hardware_version"]),
                required_sources=tuple(payload.get("required_sources", [])),
                forbidden_versions=tuple(payload.get("forbidden_versions", [])),
                required_boundary=str(payload.get("required_boundary", "")),
                should_refuse=bool(payload.get("should_refuse", False)),
            )
        )
    return cases


def _mean(values: list[bool]) -> float:
    """把布尔判定转换为比例；空集合按已满足处理。"""
    return sum(values) / len(values) if values else 1.0


def evaluate_cases(
    retriever: Retriever,
    answerer: Answerer,
    cases: list[EvaluationCase],
) -> EvaluationReport:
    """执行全部问题并按照方案阈值生成验收报告。"""
    details: list[CaseResult] = []
    citation_coverage_checks: list[bool] = []
    citation_position_checks: list[bool] = []
    evidence_checks: list[bool] = []
    refusal_checks: list[bool] = []
    source_checks: list[bool] = []
    boundary_checks: list[bool] = []
    version_pollution_count = 0
    for case in cases:
        retrieved = retriever.search(case.question, case.hardware_version, limit=5)
        answer = answerer.answer(case.question, case.hardware_version, retrieved)
        version_clean = all(
            item.hardware_version == case.hardware_version
            and item.hardware_version not in case.forbidden_versions
            for item in retrieved
        )
        if not version_clean:
            version_pollution_count += 1
        source_ok = case.should_refuse or all(
            any(fragment in item.relative_path for item in retrieved)
            for fragment in case.required_sources
        )
        if not case.should_refuse:
            source_checks.append(source_ok)
            citation_coverage_checks.append(bool(answer.citations))
        retrieved_locations = {
            (
                item.relative_path,
                item.start_line,
                item.end_line,
                item.git_commit,
                item.source_sha256,
            )
            for item in retrieved
        }
        position_ok = all(
            (
                citation.relative_path,
                citation.start_line,
                citation.end_line,
                citation.git_commit,
                citation.source_sha256,
            )
            in retrieved_locations
            for citation in answer.citations
        )
        citation_position_checks.extend([position_ok] * max(1, len(answer.citations)))
        expected_evidence = "none"
        if retrieved:
            expected_evidence = max(
                (item.evidence_level for item in retrieved), key=EVIDENCE_ORDER.index
            ).value
        evidence_ok = answer.evidence_level == expected_evidence
        evidence_checks.append(evidence_ok)
        refusal_ok = not case.should_refuse or (
            not retrieved and answer.evidence_level == "none" and "不确定" in answer.conclusion
        )
        if case.should_refuse:
            refusal_checks.append(refusal_ok)
        if case.should_refuse:
            boundary_ok = "不确定" in answer.conclusion
        elif "真机" in case.required_boundary:
            boundary_ok = any("真机" in item for item in answer.unvalidated)
        else:
            boundary_ok = True
        boundary_checks.append(boundary_ok)
        details.append(
            CaseResult(
                id=case.id,
                retrieved_count=len(retrieved),
                source_top5_ok=source_ok,
                version_clean=version_clean,
                citation_covered=bool(answer.citations) or case.should_refuse,
                citation_positions_ok=position_ok,
                evidence_ok=evidence_ok,
                refusal_ok=refusal_ok,
                boundary_ok=boundary_ok,
            )
        )
    citation_coverage = _mean(citation_coverage_checks)
    citation_position_accuracy = _mean(citation_position_checks)
    evidence_accuracy = _mean(evidence_checks)
    refusal_accuracy = _mean(refusal_checks)
    top5_source_rate = _mean(source_checks)
    boundary_accuracy = _mean(boundary_checks)
    passed = (
        version_pollution_count == 0
        and citation_coverage == 1.0
        and citation_position_accuracy >= 0.95
        and evidence_accuracy >= 0.95
        and refusal_accuracy == 1.0
        and top5_source_rate >= 0.90
        and boundary_accuracy >= 0.95
    )
    return EvaluationReport(
        case_count=len(cases),
        version_pollution_count=version_pollution_count,
        citation_coverage=citation_coverage,
        citation_position_accuracy=citation_position_accuracy,
        evidence_accuracy=evidence_accuracy,
        refusal_accuracy=refusal_accuracy,
        top5_source_rate=top5_source_rate,
        boundary_accuracy=boundary_accuracy,
        passed=passed,
        cases=details,
    )
