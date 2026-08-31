"""在检索证据边界内生成固定结构、可追溯的工程回答。"""

import json
import os
import re
from typing import Protocol
from urllib.request import Request, urlopen

from .models import Citation, EvidenceLevel, RagAnswer, RetrievedChunk


EVIDENCE_ORDER = tuple(EvidenceLevel)
REAL_DEVICE_LEVELS = {
    EvidenceLevel.REAL_DEVICE_PARTIAL,
    EvidenceLevel.REAL_DEVICE_PASSED,
    EvidenceLevel.PRODUCTION_ACCEPTED,
}
PENDING_MARKERS = ("待验证", "未验证", "未完成", "不代表", "尚未", "待真机")
UNSAFE_REAL_DEVICE_CLAIMS = ("真机通过", "真机验证通过", "已完成真机验收", "真机验收完成")


class LlmProvider(Protocol):
    """可替换的私有文本生成接口。"""

    def complete(self, prompt: str) -> str:
        """根据已经限定范围的提示生成结论文本。"""
        ...


class HttpLlmProvider:
    """调用环境变量指定的获批私有 HTTP LLM 服务。"""

    def __init__(self, url: str, timeout_seconds: float = 60.0):
        """保存私有服务地址和超时时间。"""
        if not url:
            raise ValueError("LLM 服务地址不能为空")
        self.url = url
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> "HttpLlmProvider | None":
        """仅在显式配置 JSSH_RAG_LLM_URL 时建立服务。"""
        url = os.environ.get("JSSH_RAG_LLM_URL", "").strip()
        return cls(url) if url else None

    def complete(self, prompt: str) -> str:
        """发送纯 JSON 请求并读取常见文本响应字段。"""
        request = Request(
            self.url,
            data=json.dumps({"prompt": prompt}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload.get("output"), str):
            return payload["output"].strip()
        if isinstance(payload.get("text"), str):
            return payload["text"].strip()
        choices = payload.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            return str(message.get("content", choices[0].get("text", ""))).strip()
        raise ValueError("LLM 服务未返回可识别文本")


def _sentences(content: str) -> list[str]:
    """把知识块压缩为可呈现的非标题句子。"""
    normalized = re.sub(r"^#{1,6}\s*", "", content.strip(), flags=re.MULTILINE)
    return [item.strip() for item in re.split(r"[。！？\n]+", normalized) if item.strip()]


class Answerer:
    """生成引用由程序控制、结论不能越级的回答。"""

    def __init__(self, llm_provider: LlmProvider | None = None):
        """绑定可选私有 LLM；未配置时使用确定性摘要。"""
        self.llm_provider = llm_provider

    def answer(
        self,
        query: str,
        hardware_version: str,
        retrieved: list[RetrievedChunk],
    ) -> RagAnswer:
        """根据目标版本检索块生成有边界的结构化回答。

        Args:
            query: 用户工程问题。
            hardware_version: 明确指定的目标版本。
            retrieved: 已排序且带完整来源字段的知识块。

        Returns:
            固定包含结论、版本、证据、引用和下一步的回答。

        Raises:
            ValueError: 检索结果混入其他硬件版本。
        """
        if any(item.hardware_version != hardware_version for item in retrieved):
            raise ValueError("回答器拒绝使用其他硬件版本的证据")
        if not retrieved:
            return RagAnswer(
                conclusion=f"不确定：未检索到 {hardware_version} 的可引用证据。",
                hardware_version=hardware_version,
                evidence_level="none",
                citations=[],
                validated=[],
                unvalidated=["没有目标版本证据，不能用其他版本代替。"],
                next_step="补充或修正目标版本的 Git 跟踪资料后重新索引。",
            )
        active = [item for item in retrieved if item.status == "current"] or retrieved
        highest = max(
            (item.evidence_level for item in active),
            key=EVIDENCE_ORDER.index,
        )
        citations = [
            Citation(
                relative_path=item.relative_path,
                heading_or_symbol=item.heading_or_symbol,
                start_line=item.start_line,
                end_line=item.end_line,
                git_commit=item.git_commit,
                source_sha256=item.source_sha256,
            )
            for item in retrieved
        ]
        all_sentences: list[str] = []
        validated: list[str] = []
        unvalidated: list[str] = []
        for item in retrieved:
            item_sentences = _sentences(item.content)
            all_sentences.extend(item_sentences)
            if item.evidence_level == EvidenceLevel.DESIGN_PROPOSED or item.status != "current":
                unvalidated.extend(item_sentences)
            else:
                validated.extend(item_sentences)
            unvalidated.extend(
                sentence
                for sentence in item_sentences
                if any(marker in sentence for marker in PENDING_MARKERS)
            )
        validated = validated[:5]
        has_real_device_evidence = any(
            item.status == "current" and item.evidence_level in REAL_DEVICE_LEVELS
            for item in retrieved
        )
        conflicting_status = any(item.status != "current" for item in retrieved) and any(
            item.status == "current" for item in retrieved
        )
        if conflicting_status:
            unvalidated.append("检索结果同时包含 current 与 superseded/draft/archive 状态来源，必须并列核对。")
        if not has_real_device_evidence:
            unvalidated.append(f"未找到 {hardware_version} 真机验收证据。")
        evidence_text = "\n\n".join(
            f"[{index}] {item.relative_path}:{item.start_line}-{item.end_line}\n{item.content}"
            for index, item in enumerate(retrieved, 1)
        )
        if conflicting_status:
            conclusion = "资料状态存在冲突：同时检索到 current 与非 current 来源，不能直接选择单一结论。"
        elif "真机" in query and not has_real_device_evidence:
            conclusion = (
                f"不确定：现有 {hardware_version} 证据最高为 {highest.value}，"
                "不能据此认定真机验收通过。"
            )
        elif self.llm_provider is not None:
            prompt = (
                "只能依据下列资料回答；明确版本；不得把设计、源码、仿真、Host、QEMU、构建或烧录"
                "表述为真机通过；冲突必须并列；无证据回答不确定。\n"
                f"目标版本：{hardware_version}\n问题：{query}\n资料：\n{evidence_text}"
            )
            conclusion = self.llm_provider.complete(prompt)
        else:
            summary = validated[0] if validated else all_sentences[0]
            conclusion = f"基于 {hardware_version} 的 {highest.value} 证据：{summary}"
        if not has_real_device_evidence and any(claim in conclusion for claim in UNSAFE_REAL_DEVICE_CLAIMS):
            conclusion = (
                f"不确定：生成内容超出 {highest.value} 证据边界，不能认定真机验收通过。"
            )
        next_step = None
        if unvalidated:
            next_step = "按引用回到原始资料核对，并为未验证事项补充目标版本的实测证据。"
        return RagAnswer(
            conclusion=conclusion,
            hardware_version=hardware_version,
            evidence_level=highest.value,
            citations=citations,
            validated=validated,
            unvalidated=list(dict.fromkeys(unvalidated)),
            next_step=next_step,
        )
