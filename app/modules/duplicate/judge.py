from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.config import settings
from app.modules.duplicate.embeddings import lexical_overlap, normalize_text


@dataclass(frozen=True)
class DuplicateJudgement:
    label: str
    severity: str
    reason: str
    suggestion: Optional[str] = None


def judge_pair(
    left_name: str,
    left_description: str,
    right_name: str,
    right_description: str,
    similarity: float,
) -> DuplicateJudgement:
    left = f"{left_name} {left_description}"
    right = f"{right_name} {right_description}"
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    overlap = lexical_overlap(left, right)

    if left_norm == right_norm or left_norm in right_norm or right_norm in left_norm:
        return DuplicateJudgement(
            label="重复",
            severity="risk",
            reason="两个功能点的名称或描述存在完全一致/包含关系，核心建设内容高度重合。",
            suggestion="建议合并描述，或明确拆分后的边界、对象和交付物。",
        )

    if similarity >= settings.duplicate_similarity_threshold and overlap >= 0.28:
        return DuplicateJudgement(
            label="重复",
            severity="risk",
            reason=f"语义相似度为 {similarity:.2f}，关键词重合度较高，疑似同一能力的重复表述。",
            suggestion="建议评审人员核对是否属于同一功能，避免重复申报。",
        )

    if similarity >= settings.duplicate_high_similarity_threshold or overlap >= 0.22:
        return DuplicateJudgement(
            label="高度相似",
            severity="warning",
            reason=f"语义相似度为 {similarity:.2f}，两项处于相近业务域，但可能存在范围或层级差异。",
            suggestion="建议补充两项的职责边界、使用对象和数据范围。",
        )

    return DuplicateJudgement(
        label="无关",
        severity="pass",
        reason=f"语义相似度为 {similarity:.2f}，未达到内部重复筛查阈值。",
    )
