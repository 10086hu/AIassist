from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import CheckResult, FunctionPoint, Project
from app.modules.duplicate.embeddings import duplicate_similarity_score, text_hash
from app.modules.duplicate.excel_parser import ParsedFunctionPoint, parse_function_points
from app.modules.duplicate.document_parser import DocumentContent, parse_document
from app.modules.duplicate.llm_extractor import extract_function_points
from app.modules.duplicate.llm_judge import judge_pair_with_llm
from app.schemas import DuplicateInternalResponse, DuplicatePairOut, ProjectOut


@dataclass(frozen=True)
class CandidatePair:
    left: FunctionPoint
    right: FunctionPoint
    similarity: float


@dataclass(frozen=True)
class ReportCandidatePair:
    left: "ReportPoint"
    right: "ReportPoint"
    similarity: float


@dataclass(frozen=True)
class ReportPoint:
    point_id: str
    name: str
    description: str
    category: Optional[str]
    row_index: int
    report_name: str
    stage: str
    source: str


@dataclass(frozen=True)
class ParsedReport:
    report_name: str
    stage: str
    source: str
    points: list[ParsedFunctionPoint]


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


def run_duplicate_compare_check(
    db: Session,
    current_content: bytes,
    current_filename: str,
    history_files: list[tuple[bytes, str]],
    project_id: Optional[str],
    project_name: str,
    department: Optional[str],
    current_stage: str = "本期",
    history_stages: Optional[list[str]] = None,
) -> DuplicateInternalResponse:
    """对当前可研报告进行内部查重，并与往期报告进行跨报告重复建设检查。"""
    current_points = _parse_points_from_file(current_content, current_filename, project_name)
    if not current_points:
        raise ValueError("当前可研报告未找到任何功能点")
    if not history_files:
        raise ValueError("请至少上传一个往期可研报告或历史功能点清单")

    project = _get_or_create_project(db, project_id, project_name, department)
    _replace_current_function_points(db, project.id, current_points)
    db.flush()

    current_report = ParsedReport(
        report_name=current_filename,
        stage=current_stage or "本期",
        source="current",
        points=current_points,
    )
    history_reports: list[ParsedReport] = []
    for index, (content, filename) in enumerate(history_files):
        stage = (
            history_stages[index]
            if history_stages and index < len(history_stages) and history_stages[index].strip()
            else f"往期{index + 1}"
        )
        points = _parse_points_from_file(content, filename, f"{project_name} {stage}")
        if points:
            history_reports.append(
                ParsedReport(
                    report_name=filename,
                    stage=stage,
                    source="history",
                    points=points,
                )
            )

    if not history_reports:
        raise ValueError("往期文件未提取到任何功能点")

    current_refs = _report_points_from_parsed(current_report)
    history_refs = [
        point
        for report in history_reports
        for point in _report_points_from_parsed(report)
    ]

    internal_pairs = _judge_report_pairs(
        _find_report_candidate_pairs(current_refs),
        project_id=project.id,
        comparison_type="internal",
        context_builder=lambda left, right: f"两项均来自当前报告《{left.report_name}》（{left.stage}）。",
    )
    cross_pairs = _judge_report_pairs(
        _find_cross_report_candidate_pairs(current_refs, history_refs),
        project_id=project.id,
        comparison_type="cross_report",
        context_builder=lambda left, right: (
            f"功能点1来自当前报告《{left.report_name}》（{left.stage}），"
            f"功能点2来自往期报告《{right.report_name}》（{right.stage}）。"
            "请重点判断是否属于本期重复申报、边界不清，或只是合理的阶段延续。"
        ),
    )

    db.execute(
        delete(CheckResult).where(
            CheckResult.project_id == project.id,
            CheckResult.module == "duplicate",
            CheckResult.check_subtype.in_(("internal_dedup", "cross_report_dedup")),
        )
    )
    for pair in internal_pairs + cross_pairs:
        db.add(
            CheckResult(
                project_id=project.id,
                module="duplicate",
                check_subtype="cross_report_dedup" if pair.comparison_type == "cross_report" else "internal_dedup",
                item_id=pair.item_id,
                related_item_id=pair.related_item_id,
                severity=pair.severity,
                score=round(pair.similarity * 100, 2),
                result_label=pair.result_label,
                reason=pair.reason,
                suggestion=pair.suggestion,
                model_name=pair.model_name or "deepseek",
                reference_data=json.dumps(
                    {
                        "comparison_type": pair.comparison_type,
                        "similarity": pair.similarity,
                        "item_report_name": pair.item_report_name,
                        "related_report_name": pair.related_report_name,
                        "item_stage": pair.item_stage,
                        "related_stage": pair.related_stage,
                    },
                    ensure_ascii=False,
                ),
            )
        )

    project.status = "evaluated"
    db.commit()
    db.refresh(project)

    all_pairs = internal_pairs + cross_pairs
    return DuplicateInternalResponse(
        project=ProjectOut.model_validate(project),
        imported_count=len(current_refs),
        history_imported_count=len(history_refs),
        threshold=settings.duplicate_high_similarity_threshold,
        pairs=all_pairs,
        internal_pairs=internal_pairs,
        cross_pairs=cross_pairs,
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


def _parse_points_from_file(content: bytes, filename: str, project_context: str) -> list[ParsedFunctionPoint]:
    lower = filename.lower()
    if lower.endswith((".xlsx", ".csv")):
        return parse_function_points(content, filename)
    if lower.endswith((".docx", ".pdf")):
        return _parse_document_function_points(
            parse_document(content, filename),
            project_context=project_context,
        )
    raise ValueError(f"不支持的文件格式: {filename}")


def _parse_document_function_points(
    doc_content: DocumentContent,
    project_context: str,
) -> list[ParsedFunctionPoint]:
    section_points = _extract_function_sections(doc_content)
    if len(section_points) >= 3:
        return section_points
    return extract_function_points(doc_content, project_context=project_context)


def _extract_function_sections(doc_content: DocumentContent) -> list[ParsedFunctionPoint]:
    points: list[ParsedFunctionPoint] = []
    for section in doc_content.sections:
        name = _clean_function_section_title(section.title)
        if not name or not _looks_like_function_section(name, section.content):
            continue
        points.append(
            ParsedFunctionPoint(
                row_index=len(points) + 1,
                name=name,
                description=_compact_description(section.content),
                category="文档功能章节",
            )
        )
    return points


def _clean_function_section_title(title: str) -> str:
    text = re.sub(r"\s+", " ", title or "").strip()
    text = re.sub(r"^\d+(?:\.\d+)*\s*", "", text)
    text = re.sub(r"^[一二三四五六七八九十]+[、.]\s*", "", text)
    return text.strip()


def _looks_like_function_section(title: str, content: str) -> bool:
    if not title or len(title) > 40:
        return False
    excluded = (
        "项目概况",
        "现状问题",
        "建设必要性",
        "建设目标",
        "主要功能点清单",
        "实施计划",
        "投资估算",
        "实施情况",
    )
    if any(word in title for word in excluded):
        return False
    text = f"{title}\n{content}"
    return any(word in text for word in ("建设", "提供", "功能点", "能力", "模块", "平台", "服务"))


def _compact_description(content: str) -> str:
    return re.sub(r"\s+", " ", content or "").strip()[:800]


def run_duplicate_check_from_document(
    db: Session,
    content: bytes,
    filename: str,
    project_id: Optional[str],
    project_name: str,
    department: Optional[str],
) -> DuplicateInternalResponse:
    doc_content = parse_document(content, filename)
    parsed_points = _parse_document_function_points(doc_content, project_name)
    return _process_function_points(
        db, parsed_points, project_id, project_name, department
    )


def _report_points_from_parsed(report: ParsedReport) -> list[ReportPoint]:
    refs: list[ReportPoint] = []
    for point in report.points:
        source_text = f"{report.source}:{report.report_name}:{point.row_index}:{point.name}:{point.description}"
        refs.append(
            ReportPoint(
                point_id=text_hash(source_text),
                name=point.name,
                description=point.description,
                category=point.category,
                row_index=point.row_index,
                report_name=report.report_name,
                stage=report.stage,
                source=report.source,
            )
        )
    return refs


def _find_report_candidate_pairs(points: list[ReportPoint]) -> list[ReportCandidatePair]:
    candidates: list[ReportCandidatePair] = []
    for left_index in range(len(points)):
        for right_index in range(left_index + 1, len(points)):
            similarity = _report_point_similarity(points[left_index], points[right_index])
            if similarity >= settings.duplicate_high_similarity_threshold:
                candidates.append(
                    ReportCandidatePair(
                        left=points[left_index],
                        right=points[right_index],
                        similarity=similarity,
                    )
                )
    return sorted(candidates, key=lambda item: item.similarity, reverse=True)


def _find_cross_report_candidate_pairs(
    current_points: list[ReportPoint],
    history_points: list[ReportPoint],
) -> list[ReportCandidatePair]:
    candidates: list[ReportCandidatePair] = []
    for current in current_points:
        for history in history_points:
            similarity = _report_point_similarity(current, history)
            if similarity >= settings.duplicate_high_similarity_threshold:
                candidates.append(
                    ReportCandidatePair(
                        left=current,
                        right=history,
                        similarity=similarity,
                    )
                )
    return sorted(candidates, key=lambda item: item.similarity, reverse=True)


def _report_point_similarity(left: ReportPoint, right: ReportPoint) -> float:
    return duplicate_similarity_score(
        f"{left.name} {left.description}",
        f"{right.name} {right.description}",
    )


def _judge_report_pairs(
    candidates: list[ReportCandidatePair],
    project_id: str,
    comparison_type: str,
    context_builder,
) -> list[DuplicatePairOut]:
    pairs: list[DuplicatePairOut] = []
    for candidate in candidates:
        judgement = judge_pair_with_llm(
            candidate.left.name,
            candidate.left.description,
            candidate.right.name,
            candidate.right.description,
            candidate.similarity,
            context=context_builder(candidate.left, candidate.right),
        )
        if judgement.label == "无关":
            continue
        pairs.append(
            DuplicatePairOut(
                item_id=candidate.left.point_id,
                related_item_id=candidate.right.point_id,
                item_name=candidate.left.name,
                related_item_name=candidate.right.name,
                similarity=round(candidate.similarity, 4),
                result_label=judgement.label,
                severity=judgement.severity,
                reason=judgement.reason,
                suggestion=judgement.suggestion,
                comparison_type=comparison_type,
                item_report_name=candidate.left.report_name,
                related_report_name=candidate.right.report_name,
                item_stage=candidate.left.stage,
                related_stage=candidate.right.stage,
                item_source=candidate.left.source,
                related_source=candidate.right.source,
                model_name=judgement.model_name,
            )
        )
    return pairs
