from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from app.modules.duplicate.document_parser import parse_document


RULE_ROW = 10
RULE_NAME = "建设内容一致性校验规则"
RULE_CATEGORY = "一致性校验规则"
RULE_DESCRIPTION = "功能点需在需求分析、建设内容、功能点设计及投资概算章节中，形成可追溯的对应关系"
JUDGEMENT_CONDITION = (
    "4.3业务功能分析、4.6系统功能需求分析、5.1建设内容、第六章项目建设内容、"
    "项目预算/投资概算应能关联对应"
)


@dataclass(frozen=True)
class ContentIssue:
    message: str
    evidence: str | None = None
    section: str | None = None
    issue_type: str | None = None
    item: str | None = None
    raw_item: str | None = None


@dataclass(frozen=True)
class ContentConsistencyResult:
    rule_excel_row: int
    rule_name: str
    rule_category: str
    rule_description: str
    judgement_condition: str
    passed: bool
    status: str
    severity: str
    summary: str
    metrics: dict[str, Any]
    issues: list[ContentIssue]
    suggestions: list[str]


SECTION_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("4.3业务功能分析", ("4.3", "业务功能分析")),
    ("4.6系统功能需求分析", ("4.6", "系统功能需求分析", "系统功能与性能需求分析", "系统功能需求")),
    ("5.1建设内容", ("5.1", "建设目标、规模与内容", "建设内容")),
    ("第六章项目建设内容", ("第六章", "项目建设内容")),
    ("项目预算/投资概算", ("项目预算", "投资概算", "投资估算", "预算编制说明")),
)

SECTION_MATCH_GROUPS = (
    "4.3业务功能分析",
    "4.6系统功能需求分析",
    "5.1建设内容",
    "第六章项目建设内容",
    "项目预算/投资概算",
)

RELATION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("需求分析", ("4.3业务功能分析", "4.6系统功能需求分析")),
    ("建设内容", ("5.1建设内容",)),
    ("功能点设计", ("第六章项目建设内容",)),
    ("投资概算", ("项目预算/投资概算",)),
)

STOP_HEADING_RE = re.compile(
    r"^\s*(?:第[一二三四五六七八九十]+章|[一二三四五六七八九十]+[、.]|\d+(?:\.\d+){0,2})\s*"
)
LIST_MARKER_RE = re.compile(r"^\s*(?:[（(]?\d+[）).、]|[①②③④⑤⑥⑦⑧⑨⑩]|[-•●])\s*")
NOISE_RE = re.compile(r"[\s　]+")
GENERIC_ITEM_PATTERNS = (
    "相关功能点",
    "功能点",
    "功能点设计",
    "模块名称",
    "功能名称",
    "序号",
    "工作量",
    "单价",
    "总金额",
    "业务功能",
    "系统功能",
    "业务功能分析",
    "系统功能需求分析",
    "功能需求",
    "需求分析",
    "建设内容",
    "项目建设内容",
    "数据产出",
    "数据产出内容",
    "项目预算",
    "投资概算",
)
BOILERPLATE_PATTERNS = (
    "本项目",
    "本系统",
    "主要包括",
    "包括以下",
    "如下",
    "详见",
    "相关",
    "对应",
    "章节",
)


def run_content_consistency_check_from_document(
    content: bytes,
    filename: str,
    project_name: str = "未命名可研项目",
) -> dict[str, Any]:
    """执行规则分工第 10 行：建设内容一致性校验。"""
    document = parse_document(content, filename)
    if not document.raw_text.strip():
        raise ValueError("未能从文档中提取到有效文本，请确认文件不是扫描件或加密文件")

    result = _evaluate_content_consistency(document.raw_text)
    return {
        "project_name": project_name,
        "filename": filename,
        "document_format": document.format,
        "extracted_characters": len(document.raw_text),
        "status": "completed",
        "summary": "建设内容一致性校验完成。",
        "results": [_result_to_dict(result)],
    }


def _evaluate_content_consistency(raw_text: str) -> ContentConsistencyResult:
    sections = _extract_target_sections(raw_text)
    issues: list[ContentIssue] = []

    missing_groups = [
        group_name
        for group_name, section_names in RELATION_GROUPS
        if not any(sections.get(section_name, "").strip() for section_name in section_names)
    ]
    for group_name in missing_groups:
        issues.append(
            ContentIssue(
                message=f"未识别到“{group_name}”相关章节内容，无法完成该类内容与建设功能的关联校验。",
                evidence=None,
                section=group_name,
            )
        )

    section_items = {
        name: _extract_candidate_items(text)
        for name, text in sections.items()
        if text.strip()
    }

    anchor_items = _select_anchor_items(section_items)

    if not anchor_items and not missing_groups:
        issues.append(
            ContentIssue(
                message="已识别到相关章节，但未能抽取出明确功能点或建设内容条目。",
                evidence=_preview(raw_text),
                section="建设内容一致性",
            )
        )

    for item in anchor_items[:30]:
        present_targets: list[str] = []
        missing_targets: list[str] = []
        for group_name, section_names in RELATION_GROUPS:
            group_text = "\n".join(
                sections.get(section_name, "")
                for section_name in section_names
                if sections.get(section_name, "").strip()
            )
            if not group_text:
                continue
            if _item_mentioned_in_text(item, group_text):
                present_targets.append(group_name)
            else:
                missing_targets.append(group_name)
        if missing_targets:
            display_item = _display_item_name(item)
            evidence = _find_grouped_evidence(item, sections)
            if display_item != item:
                evidence = _merge_evidence(f"原始条目：{item}", evidence)
            issues.append(
                ContentIssue(
                    message=(
                        f"建设功能对应关系需补充：“{display_item}”已在"
                        f"{ '、'.join(present_targets) if present_targets else '可研文本' }中出现，"
                        f"但缺少{ '、'.join(missing_targets) }中的对应说明。"
                    ),
                    evidence=evidence,
                    section="建设内容对应关系",
                    issue_type="建设功能对应关系需补充",
                    item=display_item,
                    raw_item=item if display_item != item else None,
                )
            )

    passed = len(issues) == 0
    if passed:
        summary = "未发现建设内容、需求描述、数据产出和项目预算之间的明显对应关系缺失。"
        status = "passed"
        severity = "pass"
        suggestions: list[str] = []
    else:
        summary = f"发现 {len(issues)} 处建设内容对应关系需复核。"
        status = "needs_review"
        severity = "warning"
        suggestions = [
            "请核对 4.3、4.6、5.1、第六章、项目预算/投资概算中同一功能点的命名是否一致。",
            "建议为每个建设功能点补充需求来源、建设内容、功能设计和预算条目的对应关系。",
            "如同一功能点在不同章节使用简称或别名，建议统一名称或增加交叉引用说明。",
        ]

    return ContentConsistencyResult(
        rule_excel_row=RULE_ROW,
        rule_name=RULE_NAME,
        rule_category=RULE_CATEGORY,
        rule_description=RULE_DESCRIPTION,
        judgement_condition=JUDGEMENT_CONDITION,
        passed=passed,
        status=status,
        severity=severity,
        summary=summary,
        metrics={
            "target_sections_found": len([v for v in sections.values() if v.strip()]),
            "target_sections_missing": len(missing_groups),
            "relation_groups_found": len(RELATION_GROUPS) - len(missing_groups),
            "anchor_item_count": len(anchor_items),
            "issue_count": len(issues),
        },
        issues=issues,
        suggestions=suggestions,
    )


def _extract_target_sections(raw_text: str) -> dict[str, str]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    sections = {name: "" for name, _aliases in SECTION_SPECS}
    current: str | None = None
    collected: dict[str, list[str]] = {name: [] for name, _aliases in SECTION_SPECS}

    for line in lines:
        matched = _match_section_heading(line)
        if matched:
            current = matched
            collected[current].append(line)
            continue
        if current is not None and STOP_HEADING_RE.match(line) and _looks_like_other_heading(line, current):
            current = None
        if current is not None:
            collected[current].append(line)

    for name, values in collected.items():
        sections[name] = "\n".join(values)
    return sections


def _match_section_heading(line: str) -> str | None:
    normalized = _normalize_text(line)
    if not _is_probable_heading(line):
        return None

    if _has_number_prefix(line, "4.3") and "业务功能" in normalized:
        return "4.3业务功能分析"
    if _has_number_prefix(line, "4.6") and ("系统功能" in normalized or "需求分析" in normalized):
        return "4.6系统功能需求分析"
    if _has_number_prefix(line, "5.1") and "建设" in normalized and "内容" in normalized:
        return "5.1建设内容"
    if normalized.startswith("第六章") and "项目建设内容" in normalized:
        return "第六章项目建设内容"
    if (
        ("项目预算" in normalized or "投资概算" in normalized or "投资估算" in normalized or "预算编制说明" in normalized)
        and (normalized.startswith("第") or re.match(r"^\d+(?:\.\d+)*", normalized))
    ):
        return "项目预算/投资概算"
    return None


def _has_number_prefix(line: str, number: str) -> bool:
    escaped = re.escape(number)
    return bool(re.match(rf"^\s*{escaped}(?:[.\s、．]|$)", line))


def _looks_like_other_heading(line: str, current: str) -> bool:
    matched = _match_section_heading(line)
    if matched == current:
        return False
    if _is_subheading_of_current(line, current):
        return False
    return len(line) <= 40


def _is_subheading_of_current(line: str, current: str) -> bool:
    current_prefixes = {
        "4.3业务功能分析": ("4.3",),
        "4.6系统功能需求分析": ("4.6",),
        "5.1建设内容": ("5.1",),
        "第六章项目建设内容": ("6",),
        "项目预算/投资概算": ("7", "8"),
    }
    for prefix in current_prefixes.get(current, ()):
        if prefix in {"6", "7", "8"}:
            if re.match(rf"^\s*{prefix}\.\d+", line):
                return True
        elif _has_number_prefix(line, prefix):
            return True
    return False


def _is_probable_heading(line: str) -> bool:
    return len(line) <= 60 or bool(STOP_HEADING_RE.match(line))


def _extract_candidate_items(section_text: str) -> list[str]:
    items: list[str] = []
    for raw_line in section_text.splitlines():
        line = LIST_MARKER_RE.sub("", raw_line.strip())
        if not line or len(line) < 4:
            continue
        if _looks_like_generic_table_header(line):
            continue
        if _is_probable_heading(line) and not _contains_item_keyword(line):
            continue
        parts = _split_candidate_parts(line)
        for part in parts:
            candidate = _clean_item(part)
            if candidate and _contains_item_keyword(candidate) and _is_reportable_item(candidate):
                items.append(candidate)
    return _merge_items(*items)


def _split_candidate_parts(line: str) -> list[str]:
    # 表格行优先按单元格抽取，避免把整行“序号/说明/相关功能点”等泛化文本当作功能点。
    delimiter = "\t" if "\t" in line else None
    if delimiter is None and "|" in line:
        delimiter = "|"
    if delimiter:
        cells = [cell.strip() for cell in line.split(delimiter) if cell.strip()]
        if _looks_like_table_header_cells(cells):
            return []
        return cells[1:] if cells and re.match(r"^表\d+行\d+$", cells[0]) else cells

    parts = re.split(r"[；;。\.，,、：:]", line)
    quoted = re.findall(r"[“\"']([^”\"']{4,60})[”\"']", line)
    return [*parts, *quoted]


def _contains_item_keyword(text: str) -> bool:
    keywords = (
        "系统",
        "平台",
        "模块",
        "功能",
        "服务",
        "应用",
        "数据",
        "接口",
        "管理",
        "建设",
        "库",
        "中心",
        "支撑",
        "分析",
        "监测",
        "监管",
        "实验室",
        "气象站",
        "知识库",
        "智能体",
        "工作台",
        "网关",
    )
    return any(keyword in text for keyword in keywords)


def _clean_item(text: str) -> str:
    text = LIST_MARKER_RE.sub("", text.strip())
    text = re.sub(r"^(一是|二是|三是|四是|五是|同时|并|同步|进一步)", "", text).strip()
    text = re.sub(r"^(包括|实现|建设|提供|支持|完成|主要|通过|形成|围绕|依托|满足|用于)", "", text).strip()
    text = text.strip("“”\"'（）()[]【】 ")
    text = re.sub(r"^(表\d+行\d+\s*)", "", text).strip()
    text = re.sub(r"^\d+(?:\.\d+)*[.．、]?", "", text).strip()
    text = re.sub(r"^(相关功能点|功能点设计|功能点|建设内容|需求分析|模块名称|功能名称|对应建设内容中的功能章节)\s*[:：]?", "", text).strip()

    if _looks_like_generic_label(text):
        return ""

    if any(token in text for token in ("投资估算明细表", "预算编制说明", "预算分类汇总清单", "投资估算")):
        return ""

    if not _is_specific_item(text):
        return ""

    if len(text) < 4 or len(text) > 60:
        return ""
    if text.endswith(("如下", "包括", "内容", "需求", "章节")):
        return ""
    return text


def _is_specific_item(text: str) -> bool:
    normalized = _normalize_item(text)
    if not normalized:
        return False

    if any(normalized == _normalize_item(pattern) for pattern in GENERIC_ITEM_PATTERNS):
        return False
    if "相关功能点" in text and len(normalized) <= 12:
        return False
    if "相关功能点" in text:
        normalized_without_generic = _normalize_item(text.replace("相关功能点", ""))
        if len(normalized_without_generic) < 4:
            return False

    # 只有泛化表达、没有业务对象或动作时，不作为建设内容锚点。
    compact = _normalize_text(text)
    boilerplate_only = compact
    for token in (*GENERIC_ITEM_PATTERNS, *BOILERPLATE_PATTERNS):
        boilerplate_only = boilerplate_only.replace(_normalize_text(token), "")
    if len(boilerplate_only) < 3:
        return False

    return True


def _select_anchor_items(section_items: dict[str, list[str]]) -> list[str]:
    priority_items = _merge_items(
        section_items.get("项目预算/投资概算", []),
        section_items.get("第六章项目建设内容", []),
        section_items.get("4.6系统功能需求分析", []),
        section_items.get("5.1建设内容", []),
        section_items.get("4.3业务功能分析", []),
    )
    concrete_items = [
        item
        for item in priority_items
        if _is_concrete_function_item(item) and _is_reportable_item(item)
    ]
    if concrete_items:
        return concrete_items
    return [item for item in priority_items if _is_reportable_item(item)]


def _is_concrete_function_item(text: str) -> bool:
    compact = _normalize_text(text)
    if any(compact == _normalize_text(pattern) for pattern in GENERIC_ITEM_PATTERNS):
        return False
    if re.match(
        r"^(打造|构建|推动|推进|统筹|释放|支持|保障|提升|实现|提供|对接|通过|形成|作为|依托|全面|显著|发挥|确保|防范)",
        text,
    ):
        return False
    if len(text) > 60:
        return False
    if "-" in text or "－" in text or "—" in text:
        return True
    if re.search(
        r"(提供|释放|开展|提升|支撑|推动|保障|确保|防范|赋能|实现|建设|打造|构建|统筹|推进|对接|同步|使用|加大|拓展|优化|形成|发挥|作为|基于|通过)",
        text,
    ):
        return False
    if any(token in text for token in ("功能体系", "应用场景", "服务窗口", "数据通道", "从海关监管")):
        return False
    concrete_keywords = (
        "系统",
        "平台",
        "模块",
        "功能",
        "服务",
        "应用",
        "数据中台",
        "业务中台",
        "AI中台",
        "中心",
        "实验室",
        "气象站",
        "知识库",
        "智能体",
        "工作台",
        "网关",
        "接口",
        "数据库",
    )
    return any(keyword in text for keyword in concrete_keywords)


def _looks_like_generic_table_header(text: str) -> bool:
    cells = [cell.strip() for cell in text.split("\t") if cell.strip()]
    return _looks_like_table_header_cells(cells)


def _looks_like_table_header_cells(cells: list[str]) -> bool:
    if not cells:
        return False
    joined = "".join(cells)
    header_tokens = (
        "序号",
        "模块名称",
        "功能名称",
        "相关功能点",
        "功能点设计",
        "工作量",
        "单价",
        "总金额",
        "数量",
        "单位",
        "对应建设内容",
    )
    hits = sum(1 for token in header_tokens if token in joined)
    return hits >= 2 and not any("-" in cell or "－" in cell or "—" in cell for cell in cells)


def _looks_like_generic_label(text: str) -> bool:
    normalized = _normalize_item(text)
    if not normalized:
        return True
    generic_labels = {
        _normalize_item(label)
        for label in (
            *GENERIC_ITEM_PATTERNS,
            "对应建设内容中的功能章节",
            "对应建设内容中的模块或功能章节",
            "产品大类",
            "产品小类",
            "产品配置",
            "模块",
            "功能",
            "说明",
        )
    }
    return normalized in generic_labels


def _is_reportable_item(text: str) -> bool:
    compact = _normalize_text(text)
    if not compact or _looks_like_generic_label(text):
        return False
    if "相关功能点" in compact and len(compact.replace("相关功能点", "")) < 4:
        return False
    if re.fullmatch(r"(序号|模块名称|功能名称|相关功能点|功能点设计|工作量|单价|总金额|数量|单位|说明)+", compact):
        return False
    if re.search(r"(人月|元|万元)$", compact) and not any(token in compact for token in ("系统", "平台", "模块", "功能", "服务", "应用", "数据", "接口", "管理")):
        return False
    return True


def _merge_items(*groups) -> list[str]:
    merged: list[str] = []
    for group in groups:
        values = group if isinstance(group, list) else [group]
        for value in values:
            item = str(value).strip()
            if not item:
                continue
            normalized = _normalize_item(item)
            if any(normalized == _normalize_item(existing) for existing in merged):
                continue
            contained_index = next(
                (
                    index
                    for index, existing in enumerate(merged)
                    if normalized in _normalize_item(existing) or _normalize_item(existing) in normalized
                ),
                None,
            )
            if contained_index is not None:
                if _item_specificity_score(item) > _item_specificity_score(merged[contained_index]):
                    merged[contained_index] = item
                continue
            merged.append(item)
    return merged


def _item_specificity_score(item: str) -> int:
    normalized = _normalize_item(item)
    return len(normalized) + 8 * sum(item.count(sep) for sep in ("-", "－", "—", "/"))


def _item_mentioned_in_text(item: str, text: str) -> bool:
    if not _is_reportable_item(item):
        return False
    normalized_item = _normalize_item(item)
    normalized_text = _normalize_text(text)
    if normalized_item and normalized_item in normalized_text:
        return True
    if "\n" in text:
        return any(_item_mentioned_in_text(item, line) for line in text.splitlines())

    tokens = _item_tokens(item)
    if not tokens:
        return False
    hit_count = sum(1 for token in tokens if token in normalized_text)
    if len(tokens) >= 3:
        return tokens[-1] in normalized_text and hit_count >= min(3, len(tokens))
    if len(tokens) == 2:
        return hit_count == 2
    return hit_count == 1


def _item_tokens(item: str) -> list[str]:
    normalized = _normalize_item(item)
    hyphen_tokens = [
        token.strip("-－—_/、")
        for token in re.split(r"[-－—_/、]+", normalized)
        if len(token.strip("-－—_/、")) >= 2
        and token.strip("-－—_/、") not in {"系统", "平台", "模块", "功能", "服务", "应用", "数据", "接口", "管理", "建设", "中心"}
    ]
    if len(hyphen_tokens) >= 2:
        return _dedupe_tokens(hyphen_tokens)[:5]

    tokens = [
        token.strip("-－—_/、")
        for token in re.split(r"(系统|平台|模块|功能|服务|应用|数据|接口|管理|建设|中心)", normalized)
        if len(token.strip("-－—_/、")) >= 2 and token.strip("-－—_/、") not in {"系统", "平台", "模块", "功能", "服务", "应用", "数据", "接口", "管理", "建设", "中心"}
    ]
    tokens = _dedupe_tokens(tokens)
    if tokens:
        return tokens[:5]
    return [normalized[:6]] if len(normalized) >= 4 else []


def _dedupe_tokens(tokens: list[str]) -> list[str]:
    deduped: list[str] = []
    for token in tokens:
        if token and token not in deduped:
            deduped.append(token)
    return deduped


def _display_item_name(item: str) -> str:
    """Return a concise display name while keeping the raw item for matching/evidence."""
    text = _extract_item_cell(item)
    text = _clean_display_text(text)
    if not text:
        return item.strip()

    list_parts = [
        part.strip()
        for part in re.split(r"[，,、；;]+", text)
        if part.strip()
    ]
    if len(list_parts) > 1:
        text = list_parts[0]

    parts = [
        part.strip()
        for part in re.split(r"[-－—_/]+", text)
        if part.strip()
    ]
    if len(parts) >= 3 and _is_generic_parent_part(parts[0]):
        # Keep the nearest parent to avoid overly generic labels such as “任务总览”.
        return f"{parts[-2]}-{parts[-1]}"
    if len(parts) >= 2 and _is_generic_parent_part(parts[0]):
        return parts[-1]
    return text


def _extract_item_cell(item: str) -> str:
    cells = [cell.strip() for cell in re.split(r"\t|\|", item) if cell.strip()]
    if len(cells) <= 1:
        return item.strip()

    candidates = [
        cell
        for cell in cells
        if _contains_item_keyword(cell)
        and not _looks_like_generic_label(cell)
        and not re.fullmatch(r"[\d,.]+", cell)
        and cell not in {"人月", "万元", "项", "套", "个"}
    ]
    if not candidates:
        return item.strip()

    candidates.sort(key=_display_candidate_score, reverse=True)
    return candidates[0]


def _display_candidate_score(text: str) -> tuple[int, int]:
    hierarchy_score = sum(text.count(sep) for sep in ("-", "－", "—", "/", "_"))
    return (hierarchy_score, len(_normalize_item(text)))


def _clean_display_text(text: str) -> str:
    text = LIST_MARKER_RE.sub("", text.strip())
    text = re.sub(r"^(表\d+行\d+\s*)", "", text).strip()
    text = re.sub(r"^\d+(?:\.\d+)*[.．、]?", "", text).strip()
    text = re.sub(r"^(相关功能点|功能点设计|功能点|建设内容|需求分析|模块名称|功能名称|对应建设内容中的功能章节)\s*[:：]?", "", text).strip()
    text = text.strip("“”\"'（）()[]【】 ")
    return text[:80]


def _is_generic_parent_part(text: str) -> bool:
    normalized = _normalize_item(text)
    return (
        normalized.endswith("中台")
        or normalized.endswith("平台")
        or normalized.endswith("系统")
        or normalized.endswith("中心")
        or normalized in {"数据中台", "业务中台", "ai中台", "项目", "建设内容"}
    )


def _merge_evidence(*parts: str | None) -> str | None:
    values = [part for part in parts if part]
    return "；".join(values) if values else None


def _find_evidence(item: str, sections: dict[str, str]) -> str | None:
    for section_name, text in sections.items():
        for line in text.splitlines():
            if _item_mentioned_in_text(item, line):
                return f"{section_name}：{line[:120]}"
    return None


def _find_grouped_evidence(item: str, sections: dict[str, str]) -> str | None:
    evidence_parts: list[str] = []
    for group_name, section_names in RELATION_GROUPS:
        for section_name in section_names:
            text = sections.get(section_name, "")
            if not text:
                continue
            for line in text.splitlines():
                if _item_mentioned_in_text(item, line):
                    evidence_parts.append(f"{group_name}/{section_name}：{line[:120]}")
                    break
            if evidence_parts and evidence_parts[-1].startswith(f"{group_name}/"):
                break
    if evidence_parts:
        return "；".join(evidence_parts[:4])
    return _find_evidence(item, sections)


def _preview(text: str, limit: int = 120) -> str:
    return NOISE_RE.sub(" ", text.strip())[:limit]


def _normalize_text(text: str) -> str:
    return NOISE_RE.sub("", text).lower()


def _normalize_item(text: str) -> str:
    text = _normalize_text(text)
    text = re.sub(r"^[\d.、（）()一二三四五六七八九十]+", "", text)
    text = re.sub(r"(建设|功能|系统|模块|服务|平台)$", "", text)
    return text


def _result_to_dict(result: ContentConsistencyResult) -> dict[str, Any]:
    data = asdict(result)
    data["issues"] = [asdict(issue) for issue in result.issues]
    return data
