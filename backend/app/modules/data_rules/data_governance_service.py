from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.modules.shanghai_review.base import ValidationError


@dataclass(frozen=True)
class DataGovernanceServiceSpec:
    name: str
    category: str
    unit_aliases: tuple[str, ...]
    stage_aliases: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    category_aliases: tuple[str, ...] = ()


DATA_GOVERNANCE_SERVICE_SPECS: tuple[DataGovernanceServiceSpec, ...] = (
    DataGovernanceServiceSpec(
        name="数据购买",
        category="数据采集",
        unit_aliases=("项",),
        stage_aliases=("建设/运维", "建设", "运维"),
        aliases=("购买数据", "第三方数据集"),
        category_aliases=("数据采集类",),
    ),
    DataGovernanceServiceSpec(
        name="历史数据迁移",
        category="数据采集",
        unit_aliases=("千万条", "百万个"),
        stage_aliases=("建设",),
        aliases=("数据迁移",),
        category_aliases=("数据采集类",),
    ),
    DataGovernanceServiceSpec(
        name="数据标签",
        category="数据加工",
        unit_aliases=("个标签",),
        stage_aliases=("建设/运维", "建设", "运维"),
        aliases=("标签数据", "数据标注"),
        category_aliases=("数据加工类",),
    ),
    DataGovernanceServiceSpec(
        name="数据融合",
        category="数据加工",
        unit_aliases=("万条",),
        stage_aliases=("建设/运维", "建设", "运维"),
        aliases=("融合数据",),
        category_aliases=("数据加工类",),
    ),
    DataGovernanceServiceSpec(
        name="历史数据归档及销毁",
        category="数据退役",
        unit_aliases=("张",),
        stage_aliases=("运维",),
        aliases=("历史数据归档", "数据归档及销毁", "归档及销毁"),
        category_aliases=("数据退役类",),
    ),
    DataGovernanceServiceSpec(
        name="数据安全风险评估",
        category="数据安全管理",
        unit_aliases=("项",),
        stage_aliases=("运维",),
        aliases=("安全风险评估",),
        category_aliases=("数据安全风险评估类", "数据安全管理类", "数据安全与治理类"),
    ),
)


NEGATIVE_LIST_ITEMS: tuple[str, ...] = (
    "数据采集接入",
    "数据抽取服务",
    "时空数据转换与处理",
    "数据质量检查",
    "数据共享/开放/授权订阅服务",
    "数据共享开放授权订阅服务",
    "数据统计分析及报表服务",
    "数据挖掘建模",
    "数据加密、脱敏等控制",
    "数据加密脱敏等控制",
    "作业调度",
    "数据开放运营接入服务",
    "公共数据上链服务",
    "主(专)题数据库建设",
    "主题数据库建设",
    "专题数据库建设",
    "数据可视化展现工作",
    "能力迭代服务",
)


_SERVICE_BY_NAME = {spec.name: spec for spec in DATA_GOVERNANCE_SERVICE_SPECS}
_ALL_CATEGORY_ALIASES = tuple(
    dict.fromkeys(
        alias
        for spec in DATA_GOVERNANCE_SERVICE_SPECS
        for alias in (spec.category, *spec.category_aliases)
    )
)
_TRIGGER_TERMS = (
    "数据治理服务",
    "数据治理内容",
    "数据服务事项",
    "数据治理内容附表",
    "数据治理服务适用阶段",
    *tuple(spec.name for spec in DATA_GOVERNANCE_SERVICE_SPECS),
    *NEGATIVE_LIST_ITEMS,
)

_REQUIRED_ANNEX_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("服务类型", ("服务类型", "服务类别", "数据治理服务类型")),
    ("服务事项", ("服务事项", "数据治理服务事项", "数据服务事项")),
    ("测算单位", ("测算单位", "计量单位", "单位")),
    ("标准", ("标准", "测算标准", "计量标准")),
    ("适用项目阶段", ("适用项目阶段", "适用阶段", "建设/运维")),
    ("申报条件", ("申报条件", "申报要求")),
)


def validate_data_governance_service_design(document: dict[str, Any]) -> list[ValidationError]:
    """Validate rule row 24: 6.3 data governance service design annex.

    The rule is intentionally isolated from the legacy data-reporting validator:
    it only returns DGS-* errors, which are mapped to the new rule by
    app.modules.data_rules.service.
    """

    full_text = _clean_text(_to_text(document.get("_full_text", "")))
    section_text = _extract_6_3_to_6_4_text(document, full_text)
    triggered = _has_any(_compact(full_text), (_compact(term) for term in _TRIGGER_TERMS))

    if not triggered and not section_text:
        return []

    if not section_text:
        return [
            ValidationError(
                code="DGS-001",
                message="项目涉及数据治理服务，但未识别到第6.3节「数据治理内容附表」。",
                section="6.3",
                severity="risk",
                suggestion="请在6.3节补充数据治理内容附表，列明服务类型、服务事项、计量单位、适用阶段和申报条件。",
            )
        ]

    candidate_tables = _extract_governance_annex_candidates(document, section_text)
    if not candidate_tables:
        return [
            ValidationError(
                code="DGS-001",
                message="第6.3节未识别到数据治理服务附表。",
                section="6.3",
                severity="risk",
                suggestion="请在6.3节补充数据治理内容附表，并按指引附表列明服务类型、服务事项、计量方式、适用项目阶段和申报条件。",
            )
        ]

    annex_text = _clean_text("\n".join(candidate_tables))
    compact_annex = _compact(annex_text)
    errors: list[ValidationError] = []

    missing_fields = _missing_required_annex_fields(annex_text)
    if missing_fields:
        errors.append(
            ValidationError(
                code="DGS-007",
                message=f"第6.3节数据治理内容附表缺少指引要求字段：{'、'.join(missing_fields)}。",
                section="6.3",
                severity="risk",
                suggestion="请按指引附表补充服务类型、服务事项、计量方式（测算单位、标准）、适用项目阶段和申报条件等字段。",
            )
        )

    negative_hits = _find_negative_hits(compact_annex)
    for item in negative_hits:
        errors.append(
            ValidationError(
                code="DGS-005",
                message=f"6.3数据治理内容附表中出现负面清单事项「{item}」，不应作为数据治理服务申报。",
                section="6.3",
                severity="risk",
                suggestion="请删除该事项，原则上按软件开发、其他费用或全市统筹渠道处理，不纳入数据治理服务范围。",
            )
        )

    found_specs = _find_service_specs(annex_text)
    if not found_specs:
        errors.append(
            ValidationError(
                code="DGS-002",
                message="第6.3节未识别到《数据治理服务配置指引》允许的4类6项数据治理服务事项。",
                section="6.3",
                severity="risk",
                suggestion="请将服务事项限定为数据购买、历史数据迁移、数据标签、数据融合、历史数据归档及销毁、数据安全风险评估。",
            )
        )
        return _dedupe_errors(errors)

    for spec in found_specs:
        context = _service_context(annex_text, spec)
        compact_context = _compact(context)
        if not _has_any(compact_context, (_compact(unit) for unit in spec.unit_aliases)):
            errors.append(
                ValidationError(
                    code="DGS-003",
                    message=f"「{spec.name}」计量单位不符合指引要求，应使用：{' / '.join(spec.unit_aliases)}。",
                    section="6.3",
                    severity="risk",
                    suggestion=f"请将「{spec.name}」的测算单位调整为{' / '.join(spec.unit_aliases)}，并补充对应测算标准。",
                )
            )

        present_categories = [
            category
            for category in _ALL_CATEGORY_ALIASES
            if _compact(category) in compact_context
        ]
        expected_categories = (spec.category, *spec.category_aliases)
        if present_categories and not _has_any(compact_context, (_compact(category) for category in expected_categories)):
            errors.append(
                ValidationError(
                    code="DGS-004",
                    message=f"「{spec.name}」服务类型与指引不一致，应归入「{spec.category}」。",
                    section="6.3",
                    severity="risk",
                    suggestion=f"请将「{spec.name}」归入「{spec.category}」，并核对6.3附表的4类6项分类。",
                )
            )

        if _stage_is_present_but_invalid(compact_context, spec.stage_aliases):
            errors.append(
                ValidationError(
                    code="DGS-006",
                    message=f"「{spec.name}」适用项目阶段与指引不一致，应为：{' / '.join(spec.stage_aliases)}。",
                    section="6.3",
                    severity="warning",
                    suggestion=f"请核对「{spec.name}」的适用项目阶段，并按指引填写为{' / '.join(spec.stage_aliases)}。",
                )
            )

    return _dedupe_errors(errors)


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("content", "text", "description", "items", "data", "measures", "risks"):
            if key not in value:
                continue
            parts.append(_to_text(value[key]))
        return "\n".join(part for part in parts if part) if parts else str(value)
    if isinstance(value, list):
        return "\n".join(_to_text(item) for item in value)
    return str(value)


def _extract_6_3_to_6_4_text(document: dict[str, Any], full_text: str) -> str:
    from_headings = _slice_between_headings(full_text, "6.3", "6.4")
    if from_headings:
        return from_headings

    section = _to_text(document.get("6.3") or document.get("数据治理内容") or document.get("数据治理"))
    if section and not _same_text(section, full_text):
        return section

    return ""


def _extract_governance_annex_candidates(document: dict[str, Any], section_text: str) -> list[str]:
    candidates = [
        table
        for table in _extract_docx_6_3_table_texts(
            document.get("_content"),
            str(document.get("_filename") or ""),
        )
        if _governance_table_score(table) >= 4
    ]
    if candidates:
        return candidates

    cleaned_section = _clean_text(section_text)
    if cleaned_section and _governance_table_score(cleaned_section) >= 4:
        return [cleaned_section]
    return []


def _extract_docx_6_3_table_texts(content: Any, filename: str) -> list[str]:
    if not content or not str(filename or "").lower().endswith(".docx"):
        return []

    try:
        from io import BytesIO

        from docx import Document
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except Exception:
        return []

    try:
        doc = Document(BytesIO(content))
    except Exception:
        return []

    tables: list[str] = []
    in_6_3 = False

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            text = Paragraph(child, doc).text.strip()
            if not text:
                continue
            if _is_section_heading(text, "6.3"):
                in_6_3 = True
                continue
            if in_6_3 and _is_section_heading(text, "6.4"):
                break
            if in_6_3 and _is_after_6_3_section_heading(text):
                break
            continue

        if isinstance(child, CT_Tbl) and in_6_3:
            table = Table(child, doc)
            lines: list[str] = []
            for row in table.rows:
                cells = [_clean_table_cell(cell.text) for cell in row.cells]
                cells = _dedupe_adjacent_cells([cell for cell in cells if cell])
                if cells:
                    lines.append(" | ".join(cells))
            table_text = _clean_text("\n".join(lines))
            if table_text:
                tables.append(table_text)

    return tables


def _governance_table_score(text: str) -> int:
    compact = _compact(text)
    score = 0
    if _has_any(compact, (_compact(alias) for alias in ("服务事项", "数据治理服务事项", "数据服务事项"))):
        score += 2
    if _find_service_specs(text):
        score += 2
    if _has_any(compact, (_compact(alias) for alias in _ALL_CATEGORY_ALIASES)):
        score += 1
    if _has_any(compact, (_compact(alias) for alias in ("测算单位", "计量单位", "单位"))):
        score += 1
    if _has_any(compact, (_compact(alias) for alias in ("适用项目阶段", "适用阶段", "建设/运维"))):
        score += 1
    if _has_any(compact, (_compact(alias) for alias in ("申报条件", "申报要求"))):
        score += 1
    return score


def _missing_required_annex_fields(text: str) -> list[str]:
    compact = _compact(_annex_header_text(text))
    missing: list[str] = []
    for field, aliases in _REQUIRED_ANNEX_FIELDS:
        if not _has_any(compact, (_compact(alias) for alias in aliases)):
            missing.append(field)
    return missing


def _annex_header_text(text: str) -> str:
    lines = _meaningful_lines(text)
    if not lines:
        return ""

    start_index = 0
    for index, line in enumerate(lines):
        if _annex_header_score(line) >= 2:
            start_index = index
            break

    header_lines: list[str] = []
    for line in lines[start_index : start_index + 4]:
        if header_lines and _looks_like_table_data_row(line):
            break
        header_lines.append(line)
    return "\n".join(header_lines)


def _annex_header_score(text: str) -> int:
    compact = _compact(text)
    return sum(
        1
        for _field, aliases in _REQUIRED_ANNEX_FIELDS
        if _has_any(compact, (_compact(alias) for alias in aliases))
    )


def _looks_like_table_data_row(text: str) -> bool:
    compact = _compact(text)
    return bool(re.match(r"^\d+(?:[|、\.．]|\s|数据采集|数据加工|数据退役|数据安全)", compact))


def _slice_between_headings(text: str, start_no: str, end_no: str) -> str:
    pattern = re.compile(
        rf"(?ms)^\s*{re.escape(start_no)}[\.．\s、]+.+?(?=^\s*{re.escape(end_no)}[\.．\s、]+|\Z)"
    )
    match = pattern.search(text or "")
    return match.group(0).strip() if match else ""


def _looks_like_governance_annex(text: str) -> bool:
    compact = _compact(text)
    header_hits = sum(
        1
        for token in ("服务类型", "服务事项", "计量方式", "测算单位", "适用项目阶段", "申报条件")
        if _compact(token) in compact
    )
    service_hits = len(_find_service_specs(text))
    return header_hits >= 2 and service_hits >= 1


def _is_section_heading(text: str, section_no: str) -> bool:
    compact = _compact(text)
    return bool(re.match(rf"^{re.escape(section_no)}(?:[\.．、]|\D|$)", compact))


def _is_after_6_3_section_heading(text: str) -> bool:
    compact = _compact(text)
    return bool(re.match(r"^6(?:[\.．、]?)(?:[4-9]|[1-9]\d)(?:[\.．、]|\D|$)", compact))


def _clean_table_cell(text: str) -> str:
    return _clean_text(str(text or "").replace("\r", "\n").replace("\n", " "))


def _dedupe_adjacent_cells(cells: list[str]) -> list[str]:
    output: list[str] = []
    previous: str | None = None
    for cell in cells:
        if cell == previous:
            continue
        output.append(cell)
        previous = cell
    return output


def _find_negative_hits(compact_text: str) -> list[str]:
    hits: list[str] = []
    for item in NEGATIVE_LIST_ITEMS:
        compact_item = _compact(item)
        if compact_item and compact_item in compact_text:
            canonical = _canonical_negative_item(item)
            if canonical not in hits:
                hits.append(canonical)
    return hits


def _canonical_negative_item(item: str) -> str:
    aliases = {
        "数据共享开放授权订阅服务": "数据共享/开放/授权订阅服务",
        "数据加密脱敏等控制": "数据加密、脱敏等控制",
        "主题数据库建设": "主(专)题数据库建设",
        "专题数据库建设": "主(专)题数据库建设",
    }
    return aliases.get(item, item)


def _find_service_specs(text: str) -> list[DataGovernanceServiceSpec]:
    compact_text = _compact(text)
    found: list[DataGovernanceServiceSpec] = []
    for spec in DATA_GOVERNANCE_SERVICE_SPECS:
        aliases = (spec.name, *spec.aliases)
        if _has_any(compact_text, (_compact(alias) for alias in aliases)):
            found.append(spec)
    return found


def _service_context(text: str, spec: DataGovernanceServiceSpec) -> str:
    lines = _meaningful_lines(text)
    aliases = tuple(_compact(alias) for alias in (spec.name, *spec.aliases))
    matched_lines = [line for line in lines if _has_any(_compact(line), aliases)]
    if matched_lines:
        return "\n".join(matched_lines)

    compact_text = _compact(text)
    for alias in aliases:
        index = compact_text.find(alias)
        if index >= 0:
            return compact_text[max(0, index - 120) : index + 220]
    return ""


def _stage_is_present_but_invalid(compact_context: str, allowed_stage_aliases: Iterable[str]) -> bool:
    stage_tokens = ("建设/运维", "建设", "运维")
    if not _has_any(compact_context, (_compact(token) for token in stage_tokens)):
        return False
    return not _has_any(compact_context, (_compact(token) for token in allowed_stage_aliases))


def _meaningful_lines(text: str) -> list[str]:
    return [line.strip() for line in re.split(r"[\r\n]+", text or "") if line and line.strip()]


def _clean_text(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"[\u200b\ufeff]", "", cleaned)
    return cleaned.strip()


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _has_any(text: str, needles: Iterable[str]) -> bool:
    return any(needle and needle in text for needle in needles)


def _same_text(left: str, right: str) -> bool:
    return _compact(left) == _compact(right)


def _dedupe_errors(errors: list[ValidationError]) -> list[ValidationError]:
    output: list[ValidationError] = []
    seen: set[tuple[str, str, str]] = set()
    for error in errors:
        key = (str(error.code), str(error.message), str(error.section))
        if key in seen:
            continue
        seen.add(key)
        output.append(error)
    return output
