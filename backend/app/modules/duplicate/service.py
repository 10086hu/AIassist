from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import CheckResult, FunctionPoint, Project
from app.modules.duplicate.embeddings import duplicate_similarity_score, text_hash
from app.modules.duplicate.excel_parser import ParsedFunctionPoint, parse_function_points
from app.modules.duplicate.document_parser import DocumentContent, parse_document
from app.modules.duplicate.llm_extractor import extract_function_points
from app.modules.duplicate.llm_judge import judge_pair_with_llm
from app.modules.duplicate.execution import parallel_model_work
from app.modules.duplicate.execution import report_progress
from app.modules.duplicate.candidate_search import CandidateIndex
from app.modules.duplicate.document_cache import cached_document_points
from app.modules.duplicate.llm_judge import judge_pairs_with_llm
from app.modules.duplicate.compact_extractor import extract_compact_modules
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
    source_location: dict[str, Any]
    source_excerpt: str


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
    use_llm: bool = True,
) -> DuplicateInternalResponse:
    """从 Excel/CSV 文件进行内部去重检查"""
    parsed_points = parse_function_points(content, filename)
    return _process_function_points(
        db, parsed_points, project_id, project_name, department, use_llm=use_llm
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
    use_llm: bool = True,
) -> DuplicateInternalResponse:
    """对当前可研报告进行内部查重，并与往期报告进行跨报告重复建设检查。"""
    current_points = _parse_points_from_file(current_content, current_filename, project_name, use_llm=use_llm)
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
        points = _parse_points_from_file(content, filename, f"{project_name} {stage}", use_llm=use_llm)
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

    candidates = _find_report_candidate_pairs(current_refs) + _find_cross_report_candidate_pairs(current_refs, history_refs)

    def report_context(left, right):
        if right.source == "current":
            return f"两项均来自当前报告《{left.report_name}》（{left.stage}）。"
        return (f"功能点1来自当前报告《{left.report_name}》（{left.stage}），"
                f"功能点2来自往期报告《{right.report_name}》（{right.stage}）。"
                "判断是否属于本期重复申报、边界不清，或合理的阶段延续。")

    all_pairs = _judge_report_pairs(
        candidates,
        project_id=project.id,
        comparison_type="mixed",
        context_builder=report_context,
        use_llm=use_llm,
    )
    internal_pairs = [p for p in all_pairs if p.comparison_type == "internal"]
    cross_pairs = [p for p in all_pairs if p.comparison_type == "cross_report"]

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
                reference_data=json.dumps(pair.model_dump(mode="json"), ensure_ascii=False),
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
    use_llm: bool = True,
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
    locations_by_row = {
        point.row_index: dict(point.source_location or {
            "row_index": point.row_index,
            "precision": "row",
        })
        for point in parsed_points
    }
    db.execute(
        delete(CheckResult).where(
            CheckResult.project_id == project.id,
            CheckResult.module == "duplicate",
            CheckResult.check_subtype == "internal_dedup",
        )
    )

    refs = {
        point.id: ReportPoint(
            point_id=point.id, name=point.name, description=point.description,
            category=point.category, row_index=point.row_index,
            report_name=str(locations_by_row.get(point.row_index, {}).get("file_name") or project_name),
            stage="本期", source="current",
            source_location=locations_by_row.get(point.row_index, {}),
            source_excerpt=str(locations_by_row.get(point.row_index, {}).get("quote") or point.description),
        )
        for point in points
    }
    pairs = _judge_report_pairs(
        _find_report_candidate_pairs(list(refs.values())),
        project_id=project.id, comparison_type="internal",
        context_builder=lambda left, right: f"两项均来自同一本当前报告《{left.report_name}》。",
        use_llm=use_llm,
    )
    for pair in pairs:
        db.add(CheckResult(
            project_id=project.id, module="duplicate", check_subtype="internal_dedup",
            item_id=pair.item_id, related_item_id=pair.related_item_id,
            severity=pair.severity, score=round(pair.similarity * 100, 2),
            result_label=pair.result_label, reason=pair.reason,
            suggestion=pair.suggestion, model_name=pair.model_name,
            reference_data=json.dumps(pair.model_dump(mode="json"), ensure_ascii=False),
        ))

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
    return [CandidatePair(c.left, c.right, c.similarity) for c in _find_report_candidate_pairs(points)]


def _parse_points_from_file(content: bytes, filename: str, project_context: str, use_llm: bool = True) -> list[ParsedFunctionPoint]:
    lower = filename.lower()
    if lower.endswith((".xlsx", ".csv")):
        return parse_function_points(content, filename)
    if lower.endswith(".docx"):
        def load():
            report_progress(0.02, "正在解析长报告的章节和模块清单")
            document = parse_document(content, filename)
            points = _annotate_document_points(
                _parse_document_function_points(document, project_context=project_context, use_llm=use_llm), document,
            )
            report_progress(0.35, f"长报告已整理为 {len(points)} 个建设模块，保留核心子功能及业务场景")
            return points
        return cached_document_points(content, filename, project_context, use_llm, load)
    raise ValueError(f"不支持的文件格式: {filename}")


def _parse_document_function_points(
    doc_content: DocumentContent,
    project_context: str,
    use_llm: bool = True,
) -> list[ParsedFunctionPoint]:
    if use_llm:
        return _limit_function_points(extract_function_points(doc_content, project_context=project_context))
    section_points = extract_compact_modules(doc_content) or _extract_function_sections(doc_content)
    return _limit_function_points(section_points or _extract_local_paragraph_points(doc_content))


def _extract_local_paragraph_points(doc_content: DocumentContent) -> list[ParsedFunctionPoint]:
    points: list[ParsedFunctionPoint] = []
    for section in doc_content.sections:
        for paragraph in section.content.splitlines():
            text = " ".join(paragraph.split()).strip()
            if len(text) < 12 or not any(word in text for word in ("建设", "提供", "功能", "模块", "平台", "服务", "系统")):
                continue
            points.append(ParsedFunctionPoint(
                row_index=len(points) + 1,
                name=(section.title or text[:30]).strip()[:80],
                description=text[:800],
                category="文档段落",
            ))
    return points


def _limit_function_points(points: list[ParsedFunctionPoint]) -> list[ParsedFunctionPoint]:
    return [
        replace(point, row_index=index)
        for index, point in enumerate(points, start=1)
    ]


def _annotate_document_points(
    points: list[ParsedFunctionPoint],
    document: DocumentContent,
) -> list[ParsedFunctionPoint]:
    """Map each extracted occurrence to its own paragraph in the source file."""
    blocks = list(document.source_blocks)
    if not blocks:
        from app.modules.duplicate.document_parser import DocumentSourceBlock
        blocks = [
            DocumentSourceBlock(text=line.strip(), paragraph=index, line=index)
            for index, line in enumerate(document.raw_text.splitlines(), start=1)
            if line.strip()
        ]

    output: list[ParsedFunctionPoint] = []
    used_by_signature: dict[str, set[int]] = {}
    last_by_signature: dict[str, int] = {}
    for point in points:
        existing = dict(point.source_location or {})
        if existing.get("module_summary") and existing.get("paragraph"):
            output.append(point)
            continue
        source_quote = str(existing.get("quote") or "")
        extraction_section = str(existing.get("extraction_section") or "")
        signature = _location_normalize(point.name) or _location_normalize(source_quote)
        used = used_by_signature.setdefault(signature, set())
        scored = [
            (_source_block_score(point, source_quote, extraction_section, block), index, block)
            for index, block in enumerate(blocks)
        ]
        candidates = [item for item in scored if item[0] >= 24 and item[1] not in used]
        match = None
        if candidates:
            previous = last_by_signature.get(signature, -1)
            forward = [item for item in candidates if item[1] > previous]
            pool = forward or candidates
            match = max(pool, key=lambda item: (item[0], -item[1]))

        existing.update({
            "file_name": document.filename,
            "file_type": document.format,
            "precision": "paragraph" if match is not None else "document",
        })
        if match is not None:
            _, block_index, block = match
            used.add(block_index)
            last_by_signature[signature] = block_index
            existing.update({
                "paragraph": block.paragraph,
                "line_start": block.line,
                "line_end": block.line,
                "quote": block.text[:500],
                "review_context": "\n".join(
                    item.text for item in blocks[max(0, block_index - 3):block_index + 3]
                )[:800],
                "report_context": "\n".join(
                    f"{section.title}: {section.content[:220]}" for section in document.sections[:4]
                )[:900],
            })
            if block.section:
                existing["section"] = block.section
        output.append(replace(point, source_location=existing))
    return output


def _location_normalize(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value or "").lower()


def _source_block_score(
    point: ParsedFunctionPoint,
    source_quote: str,
    extraction_section: str,
    block: Any,
) -> float:
    block_text = _location_normalize(block.text)
    if not block_text:
        return 0.0

    score = 0.0
    quote = _location_normalize(source_quote)
    name = _location_normalize(point.name)
    description = _location_normalize(point.description)
    if quote and (quote in block_text or block_text in quote):
        score += 120.0 + min(len(quote), len(block_text)) / max(len(quote), len(block_text)) * 20.0
    if name and name in block_text:
        score += 65.0 + min(15.0, len(name))
    if description:
        score += _character_overlap(description, block_text) * 45.0
    section = _location_normalize(getattr(block, "section", ""))
    if section and section in _location_normalize(extraction_section):
        score += 12.0
    if point.category and _location_normalize(point.category) in section:
        score += 8.0
    return score


def _character_overlap(left: str, right: str) -> float:
    def grams(value: str) -> set[str]:
        if len(value) < 2:
            return {value} if value else set()
        return {value[index:index + 2] for index in range(len(value) - 1)}

    left_grams = grams(left)
    right_grams = grams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / min(len(left_grams), len(right_grams))


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
    use_llm: bool = True,
) -> DuplicateInternalResponse:
    parsed_points = _parse_points_from_file(content, filename, project_name, use_llm=use_llm)
    return _process_function_points(
        db, parsed_points, project_id, project_name, department, use_llm=use_llm
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
                source_location=dict(point.source_location or {
                    "file_name": report.report_name,
                    "row_index": point.row_index,
                    "precision": "row",
                }),
                source_excerpt=str(
                    (point.source_location or {}).get("quote")
                    or f"{point.name} {point.description}"
                )[:500],
            )
        )
    return refs


def _find_report_candidate_pairs(points: list[ReportPoint]) -> list[ReportCandidatePair]:
    index = CandidateIndex(points)
    candidates = {}
    for left_index in range(len(points)):
        for right_index, similarity in index.search(
            points[left_index], settings.duplicate_candidate_top_k,
            settings.duplicate_high_similarity_threshold, query_index=left_index,
        ):
            if hasattr(points[left_index], "source_location") and _same_source_occurrence(
                points[left_index].source_location, points[right_index].source_location,
            ):
                continue
            left, right = sorted((left_index, right_index))
            candidates[(left, right)] = ReportCandidatePair(points[left], points[right], similarity)
    return sorted(candidates.values(), key=lambda item: item.similarity, reverse=True)


def _same_source_occurrence(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """One paragraph may describe several capabilities; it is not self-duplication."""
    left_paragraph = left.get("paragraph")
    right_paragraph = right.get("paragraph")
    if left_paragraph and right_paragraph:
        return (
            left_paragraph == right_paragraph
            and left.get("file_name") == right.get("file_name")
        )
    left_row = left.get("row_index")
    right_row = right.get("row_index")
    if left_row and right_row and left.get("file_name") == right.get("file_name"):
        return left_row == right_row
    return False


def _find_cross_report_candidate_pairs(
    current_points: list[ReportPoint],
    history_points: list[ReportPoint],
) -> list[ReportCandidatePair]:
    index = CandidateIndex(history_points)
    candidates: list[ReportCandidatePair] = []
    for current in current_points:
        for history_index, similarity in index.search(current, settings.duplicate_candidate_top_k,
                                                      settings.duplicate_high_similarity_threshold):
            candidates.append(ReportCandidatePair(current, history_points[history_index], similarity))
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
    use_llm: bool = True,
) -> list[DuplicatePairOut]:
    pairs: list[DuplicatePairOut] = []
    items = [dict(
            left_name=candidate.left.name, left_description=_review_description(candidate.left),
            right_name=candidate.right.name, right_description=_review_description(candidate.right),
            similarity=candidate.similarity,
            context=(
                context_builder(candidate.left, candidate.right)
                + "\n左报告背景：" + str(candidate.left.source_location.get("report_context") or "")[:900]
                + "\n右报告背景：" + str(candidate.right.source_location.get("report_context") or "")[:900]
            ),
            use_llm=use_llm,
            left_source=_point_review_source(candidate.left, candidate.right),
            right_source=_point_review_source(candidate.right, candidate.left),
        ) for candidate in candidates]
    batches = []
    batch, chars = [], 0
    for item in items:
        size = len(json.dumps(item, ensure_ascii=False))
        if batch and (len(batch) >= max(1, settings.duplicate_judge_batch_size)
                      or chars + size > settings.duplicate_judge_batch_chars):
            batches.append(batch)
            batch, chars = [], 0
        batch.append(item)
        chars += size
    if batch:
        batches.append(batch)

    def review(batch):
        if not use_llm or len(batch) == 1:
            return [judge_pair_with_llm(**item) for item in batch]
        return judge_pairs_with_llm(batch)

    grouped = parallel_model_work(
        batches, review,
        message=f"批量复核{'报告内部' if comparison_type == 'internal' else '内部与跨报告' if comparison_type == 'mixed' else '跨报告'}候选（{len(candidates)}对）",
        start=0.65 if comparison_type == "cross_report" else 0.4,
        end=0.65 if comparison_type == "internal" else 0.95,
    )
    judgements = [judgement for batch in grouped for judgement in batch]
    for candidate, judgement in zip(candidates, judgements):
        if judgement.label == "无关":
            continue
        left_location = _evidence_location(candidate.left.source_location, judgement.left_evidence)
        right_location = _evidence_location(candidate.right.source_location, judgement.right_evidence)
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
                comparison_type=("cross_report" if candidate.right.source == "history" else "internal") if comparison_type == "mixed" else comparison_type,
                item_report_name=candidate.left.report_name,
                related_report_name=candidate.right.report_name,
                item_stage=candidate.left.stage,
                related_stage=candidate.right.stage,
                item_source=candidate.left.source,
                related_source=candidate.right.source,
                item_row_index=candidate.left.row_index,
                related_row_index=candidate.right.row_index,
                item_source_location=left_location,
                related_source_location=right_location,
                item_source_excerpt=str(left_location.get("quote") or candidate.left.source_excerpt),
                related_source_excerpt=str(right_location.get("quote") or candidate.right.source_excerpt),
                location_hint=_format_pair_location_hint(
                    left_location, right_location,
                ),
                source_highlights=_duplicate_pair_highlights(
                    left_location, right_location,
                ),
                revision_target=_duplicate_revision_target(
                    left_location, right_location,
                    judgement.suggestion,
                ),
                model_name=judgement.model_name,
                relation=judgement.relation,
                left_unit=judgement.left_unit,
                right_unit=judgement.right_unit,
                overlap_scope=judgement.overlap_scope,
                left_evidence=judgement.left_evidence,
                right_evidence=judgement.right_evidence,
            )
        )
    return _merge_construction_findings(pairs)


def _point_review_source(point: ReportPoint, peer: ReportPoint | None = None) -> str:
    location = point.source_location
    return (
        f"章节：{location.get('section') or location.get('extraction_section') or ''}\n"
        f"对应原文：{point.source_excerpt}\n"
        f"建设范围原文：{_compact_review_context(location, peer)}\n"
        f"建设性质：{str(location.get('lifecycle_context') or '')[:1200]}"
    )


def _review_description(point: ReportPoint) -> str:
    if len(point.description) <= 1600:
        return point.description
    cores = "、".join(point.source_location.get("core_functions") or [])
    return f"核心子功能：{cores[:700]}；范围摘要：{point.description[:650]}；后部范围：{point.description[-250:]}"


def _evidence_location(location: dict[str, Any], evidence: str) -> dict[str, Any]:
    for span in location.get("source_spans") or []:
        if evidence and _location_normalize(evidence) in _location_normalize(span.get("quote") or ""):
            return {**location, **span}
    return dict(location)


def _compact_review_context(location: dict[str, Any], peer: ReportPoint | None = None) -> str:
    context = str(location.get("review_context") or "")
    if not location.get("module_summary"):
        return context[:800]
    if len(context) <= 4000:
        return context
    # Distribute the evidence budget across the module, including its end.
    lines = context.splitlines()
    if len(lines) > 40:
        shared = set(location.get("core_functions") or []) & set((peer.source_location if peer else {}).get("core_functions") or [])
        matches = [i for i, line in enumerate(lines) if any(capability in line for capability in shared)]
        priority = list(dict.fromkeys(i for match in matches for i in (match, min(len(lines) - 1, match + 1))))[:28]
        uniform = [round(i * (len(lines) - 1) / 11) for i in range(12)]
        indices = sorted(set(priority + uniform))
        lines = [lines[i] for i in indices]
    allowance = max(1, (4000 - len(lines)) // max(1, len(lines)))
    return "\n".join(line[:allowance] for line in lines)


def _merge_construction_findings(pairs: list[DuplicatePairOut]) -> list[DuplicatePairOut]:
    """One conflict per pair of commissioned units, retaining every overlapping capability."""
    groups: dict[tuple, DuplicatePairOut] = {}
    for pair in pairs:
        if not pair.left_unit or not pair.right_unit or not pair.overlap_scope:
            groups[(pair.item_id, pair.related_item_id)] = pair
            continue
        sides = sorted([
            (pair.item_report_name or "", pair.item_stage or "", _construction_unit_key(pair.left_unit)),
            (pair.related_report_name or "", pair.related_stage or "", _construction_unit_key(pair.right_unit)),
        ])
        key = (pair.comparison_type, tuple(sides))
        existing = groups.get(key)
        if existing is None:
            pair.item_name = pair.left_unit
            pair.related_item_name = pair.right_unit
            pair.overlap_scopes = pair.overlap_scopes or [pair.overlap_scope]
            groups[key] = pair
            continue
        existing.merged_count += pair.merged_count
        existing.similarity = max(existing.similarity, pair.similarity)
        if pair.severity == "risk":
            existing.severity = "risk"
            existing.result_label = "重复"
        for highlight in pair.source_highlights:
            if highlight not in existing.source_highlights:
                existing.source_highlights.append(highlight)
        for evidence in [pair.left_evidence, pair.right_evidence]:
            if evidence and evidence not in existing.evidence_examples:
                existing.evidence_examples.append(evidence)
        for scope in pair.overlap_scopes or [pair.overlap_scope]:
            if scope and scope not in existing.overlap_scopes:
                existing.overlap_scopes.append(scope)
        existing.overlap_scope = "；".join(existing.overlap_scopes)
        existing.revision_target["highlights"] = existing.source_highlights
    return list(groups.values())


def _construction_unit_key(name: str) -> str:
    # A module heading and its sentence often differ only by the word “模块”.
    return re.sub(r"模块$", "", _location_normalize(name))


def _format_pair_location_hint(left: dict[str, Any], right: dict[str, Any]) -> str:
    def format_one(location: dict[str, Any], fallback: str) -> str:
        filename = str(location.get("file_name") or fallback)
        if location.get("page") is not None and location.get("line_start") is not None:
            return f"{filename}第{location['page']}页第{location['line_start']}行"
        if location.get("line_start") is not None:
            return f"{filename}第{location['line_start']}行"
        if location.get("row_index") is not None:
            sheet = str(location.get("sheet_name") or "")
            sheet_text = f"（{sheet}）" if sheet else ""
            return f"{filename}{sheet_text}第{location['row_index']}行"
        if location.get("paragraph") is not None:
            return f"{filename}第{location['paragraph']}段"
        return f"{filename}位置未识别"

    return f"当前：{format_one(left, '当前报告')}；关联：{format_one(right, '关联报告')}"


def _duplicate_pair_highlights(
    left: dict[str, Any],
    right: dict[str, Any],
) -> list[dict[str, Any]]:
    highlights: list[dict[str, Any]] = []
    for role, location in (("current", left), ("related", right)):
        quote = str(location.get("quote") or "").strip()[:500]
        if not quote:
            continue
        highlights.append(
            {
                "role": role,
                "file_name": location.get("file_name"),
                "file_type": location.get("file_type"),
                "sheet_name": location.get("sheet_name"),
                "page": location.get("page"),
                "paragraph": location.get("paragraph"),
                "line_start": location.get("line_start"),
                "line_end": location.get("line_end"),
                "row_index": location.get("row_index"),
                "section": location.get("section"),
                "quote": quote,
                "precision": location.get("precision") or "unknown",
                "highlight": True,
            }
        )
    return highlights


def _duplicate_revision_target(
    left: dict[str, Any],
    right: dict[str, Any],
    advice: str | None,
) -> dict[str, Any]:
    highlights = _duplicate_pair_highlights(left, right)
    return {
        "location_hint": _format_pair_location_hint(left, right),
        "source_location": {"current": left, "related": right},
        "highlights": highlights,
        "target_type": "source_text" if highlights else "section_or_document",
        "advice": advice or "",
    }
