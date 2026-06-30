from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import CheckResult, FunctionPoint, Project
from app.modules.duplicate.embeddings import duplicate_similarity_score, text_hash
from app.modules.duplicate.excel_parser import ParsedFunctionPoint, parse_function_points
from app.modules.duplicate.document_parser import parse_document
from app.modules.duplicate.llm_extractor import extract_function_points
from app.modules.duplicate.llm_judge import judge_pair_with_llm
from app.schemas import DuplicateInternalResponse, DuplicatePairOut, ProjectOut


@dataclass(frozen=True)
class CandidatePair:
    left: FunctionPoint
    right: FunctionPoint
    similarity: float


def run_internal_duplicate_check(
    db: Session,
    content: bytes,
    filename: str,
    project_id: Optional[str],
    project_name: str,
    department: Optional[str],
) -> DuplicateInternalResponse:
    """从 Excel/CSV 文件进行内部去重检查"""
    parsed_points = parse_function_points(content, filename)
    return _process_function_points(
        db, parsed_points, project_id, project_name, department
    )


def run_duplicate_check_from_document(
    db: Session,
    content: bytes,
    filename: str,
    project_id: Optional[str],
    project_name: str,
    department: Optional[str],
) -> DuplicateInternalResponse:
    """
    从上传的文档（Word/PDF）进行内部去重检查
    流程：文档解析 → 功能点提取 → 向量化 → 去重判定 → 存储
    """
    # 1. 解析文档
    doc_content = parse_document(content, filename)

    # 2. LLM 提取功能点
    extracted_points = extract_function_points(doc_content, project_context=project_name)

    # 3. 复用现有流程处理功能点
    return _process_function_points(
        db, extracted_points, project_id, project_name, department
    )


def _process_function_points(
    db: Session,
    parsed_points: List[ParsedFunctionPoint],
    project_id: Optional[str],
    project_name: str,
    department: Optional[str],
) -> DuplicateInternalResponse:
    """
    处理功能点的通用流程（来自 Excel 或文档提取）
    """
    if not parsed_points:
        raise ValueError("未找到任何功能点")

    project = _get_or_create_project(db, project_id, project_name, department)
    _replace_current_function_points(db, project.id, parsed_points)
    db.flush()

    points = list(
        db.scalars(
            select(FunctionPoint)
            .where(FunctionPoint.project_id == project.id, FunctionPoint.source == "current")
            .order_by(FunctionPoint.row_index.asc())
        ).all()
    )
    candidates = _find_candidate_pairs(points)
    db.execute(
        delete(CheckResult).where(
            CheckResult.project_id == project.id,
            CheckResult.module == "duplicate",
            CheckResult.check_subtype == "internal_dedup",
        )
    )

    pairs: List[DuplicatePairOut] = []
    for candidate in candidates:
        judgement = judge_pair_with_llm(
            candidate.left.name,
            candidate.left.description,
            candidate.right.name,
            candidate.right.description,
            candidate.similarity,
        )
        if judgement.label == "无关":
            continue
        result = CheckResult(
            project_id=project.id,
            module="duplicate",
            check_subtype="internal_dedup",
            item_id=candidate.left.id,
            related_item_id=candidate.right.id,
            severity=judgement.severity,
            score=round(candidate.similarity * 100, 2),
            result_label=judgement.label,
            reason=judgement.reason,
            suggestion=judgement.suggestion,
            model_name=judgement.model_name,
            reference_data=json.dumps({"similarity": candidate.similarity}, ensure_ascii=False),
        )
        db.add(result)
        pairs.append(
            DuplicatePairOut(
                item_id=candidate.left.id,
                related_item_id=candidate.right.id,
                item_name=candidate.left.name,
                related_item_name=candidate.right.name,
                similarity=round(candidate.similarity, 4),
                result_label=judgement.label,
                severity=judgement.severity,
                reason=judgement.reason,
                suggestion=judgement.suggestion,
            )
        )

    project.status = "evaluated"
    db.commit()
    db.refresh(project)

    return DuplicateInternalResponse(
        project=ProjectOut.model_validate(project),
        imported_count=len(points),
        threshold=settings.duplicate_high_similarity_threshold,
        pairs=pairs,
    )





def _get_or_create_project(
    db: Session,
    project_id: str | None,
    project_name: str,
    department: str | None,
) -> Project:
    if project_id:
        project = db.get(Project, project_id)
        if project is not None:
            return project
    project = Project(name=project_name, department=department, status="draft")
    db.add(project)
    db.flush()
    return project


def _replace_current_function_points(
    db: Session,
    project_id: str,
    parsed_points: list[ParsedFunctionPoint],
) -> None:
    db.execute(
        delete(FunctionPoint).where(
            FunctionPoint.project_id == project_id,
            FunctionPoint.source == "current",
        )
    )
    for point in parsed_points:
        text = f"{point.name} {point.description}"
        db.add(
            FunctionPoint(
                project_id=project_id,
                name=point.name,
                description=point.description,
                category=point.category,
                source="current",
                row_index=point.row_index,
                text_hash=text_hash(text),
            )
        )


def _find_candidate_pairs(points: list[FunctionPoint]) -> list[CandidatePair]:
    texts = [f"{point.name} {point.description}" for point in points]
    candidates: list[CandidatePair] = []

    for left_index in range(len(points)):
        for right_index in range(left_index + 1, len(points)):
            similarity = duplicate_similarity_score(texts[left_index], texts[right_index])
            if similarity >= settings.duplicate_high_similarity_threshold:
                candidates.append(
                    CandidatePair(
                        left=points[left_index],
                        right=points[right_index],
                        similarity=similarity,
                    )
                )

    return sorted(candidates, key=lambda item: item.similarity, reverse=True)
