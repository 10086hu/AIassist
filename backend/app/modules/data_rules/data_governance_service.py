from __future__ import annotations

import re
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Iterable

from app.modules.shanghai_review.base import ValidationError
from app.modules.docx_grid import table_grid
from app.modules.evidence_text import positive_mentions


@dataclass(frozen=True)
class DataGovernanceServiceSpec:
    name: str
    category: str
    unit_aliases: tuple[str, ...]
    stage_aliases: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    category_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class GovernanceAnnexTable:
    rows: tuple[tuple[str, ...], ...]
    heading_number: str = ""
    heading_text: str = ""
    raw_text: str = ""

    @property
    def text(self) -> str:
        if self.raw_text:
            return self.raw_text
        return "\n".join(" | ".join(cell for cell in row if cell) for row in self.rows if row)


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


POLICY = json.loads(Path(__file__).with_name('data_governance_policy_2026.json').read_text(encoding='utf-8'))
DATA_GOVERNANCE_SERVICE_SPECS = tuple(
    DataGovernanceServiceSpec(**{k: tuple(v) if isinstance(v, list) else v for k, v in row.items()})
    for row in POLICY['services']
)
NEGATIVE_LIST_ITEMS = tuple(POLICY['negative_list'])
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
)

_REQUIRED_ANNEX_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("服务类型", ("服务类型", "服务类别", "服务分类", "数据治理服务类型", "数据治理服务类别")),
    ("服务事项", ("服务事项", "服务项", "数据治理服务事项", "数据服务事项")),
    ("测算单位", ("测算单位", "计量单位", "工作量单位", "单位")),
    ("标准", ("标准", "测算标准", "计量标准", "工作量标准")),
    ("适用项目阶段", ("适用项目阶段", "适用阶段", "项目阶段", "建设/运维")),
    ("申报条件", ("申报条件", "申报要求", "适用条件", "申报范围")),
)

_SERVICE_FIELD_ALIASES = _REQUIRED_ANNEX_FIELDS[1][1]
_UNIT_FIELD_ALIASES = _REQUIRED_ANNEX_FIELDS[2][1]


def validate_data_governance_service_design(document: dict[str, Any]) -> list[ValidationError]:
    """Validate rule row 24: 6.3 data governance service design annex.

    The rule is intentionally isolated from the legacy data-reporting validator:
    it only returns DGS-* errors, which are mapped to the new rule by
    app.modules.data_rules.service.
    """

    full_text = _clean_text(_to_text(document.get("_full_text", "")))
    applicability = assess_data_governance_service_applicability(document, full_text)
    if not applicability["applies"]:
        return []

    section_text = _extract_6_3_to_6_4_text(document, full_text)
    candidate_tables = _extract_governance_annex_candidates(document, section_text)
    triggered = _has_positive_governance_trigger(full_text)

    if not triggered and not candidate_tables:
        return []

    if not section_text and not candidate_tables:
        return [
            ValidationError(
                code="DGS-001",
                message="项目涉及数据治理服务，但未识别到可核验的服务附表或等价清单，需指出其位置。",
                section="6.3",
                severity="warning",
                suggestion="请指出现有服务清单位置，或补充服务事项、测算依据及交付物；正文可以补充表内信息，不要求固定章节号。",
            )
        ]

    if not candidate_tables:
        return [
            ValidationError(
                code="DGS-001",
                message="已找到数据治理说明，但未识别到可核验的服务附表或等价清单。",
                section="6.3",
                severity="warning",
                suggestion="请指出现有服务清单及测算依据位置；不要求照抄指引目录的全部栏目。",
            )
        ]

    annex_text = _clean_text("\n".join(table.text for table in candidate_tables))
    errors: list[ValidationError] = []

    # The policy directory describes its own service taxonomy; it does not
    # require each report to reproduce all six column names. Only unresolved
    # service identity and measurement warrant a missing-information warning.
    missing_fields = [f for f in _missing_required_annex_fields_from_tables(candidate_tables) if f in {'服务事项', '测算单位'}]
    if _find_annex_service_specs(candidate_tables, annex_text):
        missing_fields = [f for f in missing_fields if f != '服务事项']
    # Equivalent explicit information in service-specific prose is valid too.
    service_lines = [line for line in full_text.splitlines()
                     if '数据治理服务' in line or any(s.name in line for s in _find_annex_service_specs(candidate_tables, annex_text))]
    missing_fields = [field for field in missing_fields if not any(
        re.search(rf'{re.escape(alias)}\s*[：:]\s*[^\s，。；;|]{{2,}}', line)
        and not re.search(r'未明确|待定|待补充|另行确定|无需填写', line)
        for line in service_lines for label, aliases in _REQUIRED_ANNEX_FIELDS if label == field for alias in aliases)]
    if missing_fields:
        errors.append(
            ValidationError(
                code="DGS-007",
                message=f"已识别数据治理服务表，但以下申报核验信息未在表头或明确关联的正文说明中识别到：{'、'.join(missing_fields)}；需结合正文补充核验。",
                section=" / ".join(t.heading_text for t in candidate_tables),
                severity="warning",
                suggestion="请说明服务事项及测算单位所在位置；可以在关联正文说明，无需复制指引目录的全部栏目。",
            )
        )

    negative_hits = _find_annex_negative_hits(candidate_tables, annex_text)
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

    found_specs = _find_annex_service_specs(candidate_tables, annex_text)
    for table in candidate_tables:
        mapping = _table_header_mapping(table)
        si = mapping.get("服务事项")
        if si is None:
            continue
        for ri, row in enumerate(table.rows, 1):
            if si >= len(row):
                continue
            name = row[si].strip()
            if _service_text_matches_spec(name, next(s for s in DATA_GOVERNANCE_SERVICE_SPECS if s.name == '数据标签')) and _has_positive_phrase(' '.join(row), ('训练数据标注', '模型训练标注', '图像标注', '语音转写')) and not any(e.code == 'DGS-014' for e in errors):
                errors.append(ValidationError(code='DGS-014', message='数据标签服务涉及训练数据标注等内容，需结合标签对象和用途确认服务范围；不因“特征标注”一词本身认定超范围。', section=f'{table.heading_text} 表内行{ri}', severity='warning', suggestion='请说明标签对象、分类分级上链用途及交付物，核实训练标注与指引服务的对应关系。'))
            if not name or _header_cell_matches(name, _SERVICE_FIELD_ALIASES) or name in {"合计", "总计", "/", "无"}:
                continue
            if not _find_service_specs(name) and not _find_negative_hits(name):
                errors.append(ValidationError(code="DGS-013", message=f"服务事项「{name}」未能映射到指引六项服务，不能自动认定合规。", section=f"{table.heading_text} 表内行{ri}", severity="warning", suggestion="请明确对应的服务类别和事项；新名称应提供与指引范围一致的说明。"))
    if not found_specs:
        errors.append(
            ValidationError(
                code="DGS-002",
                message="第6.3节未识别到《数据治理服务配置指引》允许的4类6项数据治理服务事项。",
                section="6.3",
                severity="warning",
                suggestion="请说明现有服务与指引六项服务的对应关系；不同名称不直接认定超范围。",
            )
        )
        return _dedupe_errors(errors)

    # 指引明确禁止数据融合与软件开发/产品软件购置重复申报，
    # 也不允许历史数据迁移包含迁移工具开发或购买。此类冲突需要结合全文判断，
    # 不能只看 6.3 表内的服务名称和计量单位。
    errors.extend(_find_service_declaration_conflicts(full_text, annex_text, found_specs))
    errors.extend(_check_governance_acceptance_requirements(full_text, found_specs))

    for spec in found_specs:
        context = _service_context(annex_text, spec)
        compact_context = _compact(context)
        unit_status, observed_units = _annex_service_unit_status(candidate_tables, spec)
        if unit_status == 'unknown' and not any(e.code == 'DGS-007' for e in errors):
            context_with_prose = _service_context(full_text, spec)
            if not any(_unit_cell_matches(c, spec.unit_aliases) for c in re.split(r'[，。；;\n|]', context_with_prose)):
                errors.append(ValidationError(code='DGS-007', message=f'「{spec.name}」的测算单位未可靠识别，需核实其测算依据；不要求固定表头。', section='数据治理服务测算', severity='warning', suggestion='请指出工作量、单位及测算依据所在的表格或正文。'))
        if unit_status == "invalid":
            observed_text = "、".join(observed_units) if observed_units else "未填写"
            errors.append(
                ValidationError(
                    code="DGS-003",
                    message=(
                        f"「{spec.name}」测算单位为「{observed_text}」，不符合指引要求，"
                        f"应使用：{' / '.join(spec.unit_aliases)}。"
                    ),
                    section='；'.join(f'{t.heading_text} 表内行{ri}：' + ' | '.join(row) for t in candidate_tables for ri, row in enumerate(t.rows, 1) if _service_text_matches_spec(' '.join(row), spec))[:1400],
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

        stage_cells = []
        for table in candidate_tables:
            mapping = _table_header_mapping(table)
            si, pi = mapping.get("服务事项"), mapping.get("适用项目阶段")
            if si is not None and pi is not None:
                stage_cells.extend(row[pi] for row in table.rows if max(si, pi) < len(row) and _service_text_matches_spec(row[si], spec))
        if any(_stage_is_present_but_invalid(_compact(c), spec.stage_aliases) for c in stage_cells):
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


def _find_service_declaration_conflicts(
    full_text: str,
    annex_text: str,
    found_specs: list[DataGovernanceServiceSpec],
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    compact_full = _compact(full_text)
    compact_annex = _compact(annex_text)

    fusion_present = any(spec.name == "数据融合" for spec in found_specs)
    software_terms = ("软件开发", "产品软件购置", "产品软件购买", "软件购置", "软件采购")
    if fusion_present and _has_positive_nearby_phrase(compact_full, "数据融合", software_terms, 120):
        errors.append(
            ValidationError(
                code="DGS-008",
                message="数据融合服务与附近的软件开发或购置表述可能涉及同一工作范围，需核对合同、交付物和费用；仅凭词语相邻不能认定重复申报。",
                section="数据治理服务 / 建设内容 / 预算",
                severity="warning",
                suggestion="请区分数据融合服务与软件开发、产品软件购置的边界，避免同一工作内容重复计费。",
            )
        )

    migration_present = any(spec.name == "历史数据迁移" for spec in found_specs)
    if migration_present and _has_positive_phrase(compact_annex, ("迁移工具", "工具开发", "工具购买", "工具采购")):
        errors.append(
            ValidationError(
                code="DGS-009",
                message="6.3历史数据迁移服务中出现迁移工具开发或购买内容，不符合指引边界。",
                section="6.3",
                severity="risk",
                suggestion="请删除迁移工具开发、购买或采购内容；历史数据迁移服务仅包含迁移准备、设计、实施和交付。",
            )
        )
    return errors


def _check_governance_acceptance_requirements(
    full_text: str,
    found_specs: list[DataGovernanceServiceSpec],
) -> list[ValidationError]:
    """在报告明确编写验收/交付物时，核对指引要求的证明材料。"""

    # Only service-linked delivery prose is evidence. A generic security log
    # or quoted policy elsewhere must not satisfy this service's acceptance.
    linked_text = _governance_delivery_context(full_text, found_specs)
    errors: list[ValidationError] = []
    names = {spec.name for spec in found_specs}
    if names & {"数据购买", "历史数据迁移", "数据融合"}:
        required = {"数据上链": ("数据上链", "完成上链", "开展上链", "进行上链", "归集和上链", "政务目录链登记", "政务目录链编目"), "资源归集": ("数据资源归集", "资源归集", "归集至市大数据中心", "归集至公共数据平台", "向市大数据中心汇聚"), "质量自评估": ("质量自评估", "质量自评价", "数据质量评价", "质量自评", "自行开展数据质量评估", "自行评价数据质量", "自行评估数据质量")}
        missing = [label for label, aliases in required.items() if not _has_delivery_evidence(linked_text, aliases)]
        if missing:
            errors.append(
                ValidationError(
                    code="DGS-010",
                    message=f"数据购买、历史数据迁移或数据融合未识别到以下交付要求：{'、'.join(missing)}；通用背景提及仍需与本服务交付物对应。",
                    section="验收要求",
                    severity="warning",
                    suggestion="请补充新数据上链、资源归集及数据质量自评估等验收内容。",
                )
            )
    log_specs = [s for s in found_specs if s.name in {'数据标签', '历史数据归档及销毁'}]
    if log_specs and not _has_delivery_evidence(_governance_delivery_context(full_text, log_specs, include_common=False), ('执行日志', '操作记录', '执行记录', '处理日志', '作业日志', '归档日志', '销毁记录', '标注日志')):
        errors.append(
            ValidationError(
                code="DGS-011",
                message="数据标签或历史数据归档及销毁服务的验收描述未明确执行日志。",
                section="验收要求",
                severity="warning",
                suggestion="请将执行日志列为数据标签、归档及销毁服务的验收交付物。",
            )
        )
    if "数据安全风险评估" in names and not all(_has_delivery_evidence(linked_text, (token,)) for token in ("评估报告", "整改报告", "信息安全服务资质")):
        errors.append(
            ValidationError(
                code="DGS-012",
                message="数据安全风险评估的验收描述未明确第三方资质、评估报告或整改报告。",
                section="验收要求",
                severity="warning",
                suggestion="请补充第三方信息安全服务资质、盖章版评估报告和数据安全风险整改报告。",
            )
        )
    return errors


def _governance_delivery_context(text, specs, include_common=True):
    lines = _meaningful_lines(text)
    selected = []
    for i, line in enumerate(lines):
        named = any(_service_text_matches_spec(line, s) for s in specs)
        common = include_common and bool(re.search(r'数据治理(?:服务)?|数据上链内容|本项目.{0,25}(?:新数据|数据资源|政务目录链)', line))
        if not (named or common) or re.search(r'例如|示例|政策规定|指引规定|模板示范', line):
            continue
        selected.append(line)
        # A short heading can introduce its delivery paragraph. Never consume
        # unrelated paragraphs merely because a long service row precedes them.
        if len(line) < 40 and i + 1 < len(lines) and not re.match(r'^\d+(?:\.\d+)*\s', lines[i + 1]):
            selected.append(lines[i + 1])
    return '\n'.join(selected)


def _has_delivery_evidence(text, terms):
    for clause in re.split(r'[。；;\n]', text):
        if re.fullmatch(r'\s*(?:\d+(?:\.\d+)*\s*)?(?:数据上链|资源归集|数据质量|验收|执行日志)(?:内容|要求|说明)?\s*', clause):
            continue
        if re.search(r'未(?:提供|提交|形成|开展|完成)|尚无|缺少|待补充|无需|不提供|不提交|不开展|不要求|无需', clause):
            continue
        if _has_positive_phrase(clause, terms):
            return True
    return False


def _has_positive_nearby_phrase(text: str, anchor: str, phrases: Iterable[str], window: int) -> bool:
    compact_anchor = _compact(anchor)
    indexes = [m.start() for m in re.finditer(re.escape(compact_anchor), text)]
    if not indexes:
        return False
    for phrase in phrases:
        compact_phrase = _compact(phrase)
        if not compact_phrase:
            continue
        for match in re.finditer(re.escape(compact_phrase), text):
            if all(abs(match.start() - index) > window for index in indexes):
                continue
            prefix = text[max(0, match.start() - 14) : match.start()]
            suffix = text[match.end() : match.end() + 14]
            if not _is_negated_context(prefix, suffix):
                return True
    return False


def _has_positive_phrase(text: str, phrases: Iterable[str]) -> bool:
    return bool(list(positive_mentions(text, phrases)))


def _is_negated_context(prefix: str, suffix: str) -> bool:
    tokens = ("不包含", "不涉及", "不申报", "不纳入", "不得", "禁止", "不应", "无需")
    # 否定词可能位于事项前（“不包含数据质量检查”）或后（“数据质量检查（不纳入）”）。
    # 仅在短窗口内判断，避免把相邻列/下一句的否定错误套用到当前事项。
    return any(token in prefix[-20:] or token in suffix[:20] for token in tokens)


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


def _extract_governance_annex_candidates(
    document: dict[str, Any],
    section_text: str,
) -> list[GovernanceAnnexTable]:
    candidates = [
        table
        for table in _extract_docx_6_3_tables(
            document.get("_content"),
            str(document.get("_filename") or ""),
        )
        if _looks_like_governance_annex(table.text)
    ]
    if candidates:
        return candidates

    # DOCX 已能按正文顺序精确读取表格；若 6.3 范围内没有合格表格，不能再把编号段落
    # 或普通说明性文字降级当作“附表”，否则会把数据分析章节误判为治理服务附表。
    if document.get("_content") and str(document.get("_filename") or "").lower().endswith(".docx"):
        return []

    cleaned_section = _clean_text(section_text)
    if (
        cleaned_section
        and _governance_table_score(cleaned_section) >= 4
        and _looks_like_structured_governance_annex(cleaned_section)
    ):
        # Preserve the archive's text-table unit checking. Only explicit
        # header-led rows with matching column counts are treated as cells.
        rows = []
        width = None
        for line in cleaned_section.splitlines():
            cells = tuple(re.split(r"\s+|\s*\|\s*", line.strip()))
            if any('测算单位' in c or '计量单位' in c for c in cells) and any('服务事项' in c for c in cells):
                width = len(cells)
                rows = [cells]
            elif width and len(cells) == width and re.fullmatch(r"\d+", cells[0]):
                rows.append(cells)
        return [GovernanceAnnexTable(rows=tuple(rows), raw_text=cleaned_section)]
    return []


def _extract_docx_6_3_tables(content: Any, filename: str) -> list[GovernanceAnnexTable]:
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

    tables: list[GovernanceAnnexTable] = []
    in_6_3 = False
    current_heading_number = ""
    current_heading_text = ""

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            paragraph = Paragraph(child, doc)
            text = paragraph.text.strip()
            if not text:
                continue
            section_number = _leading_section_number(text)
            if len(text) < 150:
                current_heading_number = section_number
                current_heading_text = text
            if _is_6_3_section_number(section_number):
                in_6_3 = True
                current_heading_number = section_number
                current_heading_text = text
                continue
            if in_6_3 and _is_after_6_3_section_number(section_number):
                in_6_3 = False
            continue

        if isinstance(child, CT_Tbl):
            table = Table(child, doc)
            rows = [tuple(row) for row in table_grid(table) if any(row)]
            header = ' '.join(' '.join(r) for r in rows[:2])
            # Different templates use 7.2 or attachments. A service/unit table
            # is evidence regardless of chapter numbering; a copied guideline is not.
            if not in_6_3 and not ("服务事项" in header and any(t in header for t in ("单位", "工作量"))):
                continue
            if any(t in current_heading_text for t in ("配置指引", "参考模板", "政策原文")):
                continue
            if rows:
                tables.append(
                    GovernanceAnnexTable(
                        rows=tuple(rows),
                        heading_number=current_heading_number,
                        heading_text=current_heading_text,
                    )
                )

    return tables


def _extract_docx_6_3_table_texts(content: Any, filename: str) -> list[str]:
    """Compatibility wrapper retained for focused tests and diagnostics."""

    return [table.text for table in _extract_docx_6_3_tables(content, filename)]


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


def _leading_section_number(text: str) -> str:
    match = re.match(r"^\s*(\d+(?:[\.．]\d+)+)(?=[\s、\.．]|$)", str(text or ""))
    return match.group(1).replace("．", ".") if match else ""


def _is_6_3_section_number(section_number: str) -> bool:
    return section_number == "6.3" or section_number.startswith("6.3.")


def _is_after_6_3_section_number(section_number: str) -> bool:
    if not section_number:
        return False
    parts = section_number.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return False
    return major > 6 or (major == 6 and minor >= 4)


def _looks_like_structured_governance_annex(text: str) -> bool:
    """要求降级文本至少保留表格/逐行清单结构，避免把说明性段落当成附表。"""

    lines = _meaningful_lines(text)
    if len(lines) < 2:
        return False
    structured_rows = sum(
        1
        for line in lines
        if "|" in line or (
            _looks_like_table_data_row(line)
            and not re.match(r"^6(?:[\.．、\s]|$)", _compact(line))
        )
    )
    return structured_rows >= 1


def _missing_required_annex_fields(text: str) -> list[str]:
    compact = _compact(_annex_header_text(text))
    missing: list[str] = []
    for field, aliases in _REQUIRED_ANNEX_FIELDS:
        if not _has_any(compact, (_compact(alias) for alias in aliases)):
            missing.append(field)
    return missing


def _missing_required_annex_fields_from_tables(tables: list[GovernanceAnnexTable]) -> list[str]:
    header_texts = [_table_header_text(table) for table in tables if table.rows]
    if header_texts:
        return _missing_required_annex_fields("\n".join(header_texts))
    return _missing_required_annex_fields("\n".join(table.text for table in tables))


def _table_header_text(table: GovernanceAnnexTable) -> str:
    if not table.rows:
        return _annex_header_text(table.text)

    header_rows: list[str] = []
    for row in table.rows[:4]:
        row_text = " | ".join(row)
        if header_rows and _row_contains_service_item(row):
            break
        header_rows.append(row_text)
    return "\n".join(header_rows)


def _table_header_mapping(table: GovernanceAnnexTable) -> dict[str, int]:
    combined_mapping: dict[str, int] = {}
    for row in table.rows[:4]:
        if combined_mapping and _row_contains_service_item(row):
            break
        for index, cell in enumerate(row):
            for field, aliases in _REQUIRED_ANNEX_FIELDS:
                if field in combined_mapping:
                    continue
                if _header_cell_matches(cell, aliases):
                    combined_mapping[field] = index
    return combined_mapping


def _header_cell_matches(cell: str, aliases: Iterable[str]) -> bool:
    compact_cell = _compact(cell)
    for alias in aliases:
        compact_alias = _compact(alias)
        if not compact_alias:
            continue
        if compact_alias == "单位":
            if compact_cell in {"单位", "测算单位", "计量单位", "工作量单位"}:
                return True
            continue
        if compact_alias in compact_cell:
            return True
    return False


def _find_annex_service_specs(
    tables: list[GovernanceAnnexTable],
    fallback_text: str,
) -> list[DataGovernanceServiceSpec]:
    service_cells = _annex_service_cells(tables)
    if not service_cells:
        return _find_service_specs(fallback_text)
    return _find_service_specs("\n".join(service_cells))


def _annex_service_cells(tables: list[GovernanceAnnexTable]) -> list[str]:
    cells: list[str] = []
    for table in tables:
        mapping = _table_header_mapping(table)
        service_index = mapping.get("服务事项")
        if service_index is None:
            continue
        for row in table.rows:
            if service_index >= len(row):
                continue
            cell = row[service_index]
            if _header_cell_matches(cell, _SERVICE_FIELD_ALIASES):
                continue
            if cell:
                cells.append(cell)
    return cells


def _find_annex_negative_hits(
    tables: list[GovernanceAnnexTable],
    fallback_text: str,
) -> list[str]:
    service_cells = _annex_service_cells(tables)
    if not service_cells:
        return _find_negative_hits(fallback_text)

    hits: list[str] = []
    for service_cell in service_cells:
        allowed_specs = _find_service_specs(service_cell)
        for hit in _find_negative_hits(service_cell):
            if hit == "数据质量检查" and any(spec.name == "数据融合" for spec in allowed_specs):
                # 数据质量检查可以作为数据融合的工作步骤，但不能作为独立服务事项申报。
                continue
            if hit not in hits:
                hits.append(hit)
    return hits


def _annex_service_unit_status(
    tables: list[GovernanceAnnexTable],
    spec: DataGovernanceServiceSpec,
) -> tuple[str, list[str]]:
    observed_units: list[str] = []
    matched_row = False
    has_structured_unit_column = False

    for table in tables:
        mapping = _table_header_mapping(table)
        service_index = mapping.get("服务事项")
        unit_index = mapping.get("测算单位")
        if service_index is None or unit_index is None:
            continue
        has_structured_unit_column = True
        for row in table.rows:
            if service_index >= len(row) or unit_index >= len(row):
                continue
            service_cell = row[service_index]
            if not _service_text_matches_spec(service_cell, spec):
                continue
            matched_row = True
            unit_cell = row[unit_index].strip()
            if not _unit_cell_matches(unit_cell, spec.unit_aliases):
                display_unit = unit_cell or "未填写"
                if display_unit not in observed_units:
                    observed_units.append(display_unit)

    if observed_units:
        return "invalid", observed_units
    if matched_row:
        return "valid", []
    if has_structured_unit_column:
        return "unknown", []
    return "unknown", []


def _row_contains_service_item(row: tuple[str, ...]) -> bool:
    return any(_find_service_specs(cell) for cell in row)


def _service_text_matches_spec(text: str, spec: DataGovernanceServiceSpec) -> bool:
    compact_text = _compact(text)
    return _has_any(compact_text, (_compact(alias) for alias in (spec.name, *spec.aliases)))


def _unit_cell_matches(text: str, allowed_units: Iterable[str]) -> bool:
    compact_text = _compact(text)
    if not compact_text:
        return False
    parts = [part for part in re.split(r"[/／、,，;；]|(?:或)", compact_text) if part]
    units = set(allowed_units)
    if "个标签" in units:
        units.add("个")  # 服务事项已明确为数据标签时，“个”仍是标签个数。
    if "项" in units:
        units.update(("项数据", "项数据集"))
    return bool(parts) and all(any(part == u or part.startswith(u + "（") or part.startswith(u + "(") for u in units) for part in parts)


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
    for line in _meaningful_lines(compact_text):
        compact_line = _compact(line)
        for item in NEGATIVE_LIST_ITEMS:
            compact_item = _compact(item)
            if not compact_item or compact_item not in compact_line:
                continue
            # “不包含/不涉及/不得申报”等说明性文字是在排除该事项，
            # 不能把它当成项目实际申报的负面服务项。
            if not _has_positive_phrase(line, (item,)):
                continue
            canonical = _canonical_negative_item(item)
            if canonical not in hits:
                hits.append(canonical)
    return hits


def assess_data_governance_service_applicability(document, full_text=None):
    from .project_scope import assess_project_applicability
    return assess_project_applicability(document, 24, full_text)


def _is_reference_or_non_municipal_material(document: dict[str, Any], full_text: str) -> bool:
    """向后兼容旧调用；新代码应使用带原因的适用性判断。"""

    return not assess_data_governance_service_applicability(document, full_text)["applies"]


def _has_positive_governance_trigger(text: str) -> bool:
    """识别项目实际涉及数据治理，而不是仅在正文中引用排除事项。"""

    operational = ("申报", "采购", "服务费", "测算", "工作量", "服务事项", "附表", "预算", "项目建设包含")
    for clause in positive_mentions(text, _TRIGGER_TERMS):
        if any(t in clause for t in ("配置指引", "根据政策", "参考模板", "不得申报", "禁止申报")):
            continue
        if any(t in clause for t in operational):
            return True
    return False


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
