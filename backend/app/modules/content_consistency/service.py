from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from app.modules.duplicate.document_parser import parse_document


RULE_ROW = 10
RULE_NAME = "建设内容一致性校验规则"
RULE_CATEGORY = "一致性校验规则"
RULE_DESCRIPTION = "功能点需在需求描述章节、建设内容章节及投资概算章节中，形成一一对应的关联关系"
JUDGEMENT_CONDITION = (
    "4.3业务功能分析、4.6系统功能需求分析、5.1建设内容、第六章项目建设内容、"
    "第七章数据产出内容、第八章项目预算应能关联对应"
)


@dataclass(frozen=True)
class ContentIssue:
    message: str
    evidence: str | None = None
    section: str | None = None


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
    ("4.6系统功能需求分析", ("4.6", "系统功能需求分析", "系统功能需求")),
    ("5.1建设内容", ("5.1", "建设内容")),
    ("第六章项目建设内容", ("第六章", "六、", "六.", "项目建设内容")),
    ("第七章数据产出内容", ("第七章", "七、", "七.", "数据产出内容", "数据产出")),
    ("第八章项目预算", ("第八章", "八、", "八.", "项目预算", "投资概算", "预算")),
)

SECTION_MATCH_GROUPS = (
    "4.3业务功能分析",
    "4.6系统功能需求分析",
    "5.1建设内容",
    "第六章项目建设内容",
    "第七章数据产出内容",
    "第八章项目预算",
)

STOP_HEADING_RE = re.compile(
    r"^\s*(?:第[一二三四五六七八九十]+章|[一二三四五六七八九十]+[、.]|\d+(?:\.\d+){0,2})\s*"
)
LIST_MARKER_RE = re.compile(r"^\s*(?:[（(]?\d+[）).、]|[①②③④⑤⑥⑦⑧⑨⑩]|[-•●])\s*")
NOISE_RE = re.compile(r"[\s　]+")


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

    missing_sections = [name for name in SECTION_MATCH_GROUPS if not sections.get(name, "").strip()]
    for section_name in missing_sections:
        issues.append(
            ContentIssue(
                message=f"未识别到“{section_name}”相关章节内容，无法完成该章节与建设内容的一致性校验。",
                evidence=None,
                section=section_name,
            )
        )

    section_items = {
        name: _extract_candidate_items(text)
        for name, text in sections.items()
        if text.strip()
    }

    anchor_items = _merge_items(
        section_items.get("5.1建设内容", []),
        section_items.get("第六章项目建设内容", []),
        section_items.get("4.6系统功能需求分析", []),
    )

    if not anchor_items and not missing_sections:
        issues.append(
            ContentIssue(
                message="已识别到相关章节，但未能抽取出明确功能点或建设内容条目。",
                evidence=_preview(raw_text),
                section="建设内容一致性",
            )
        )

    for item in anchor_items[:30]:
        missing_targets = [
            section_name
            for section_name in SECTION_MATCH_GROUPS
            if section_name in sections
            and sections[section_name].strip()
            and not _item_mentioned_in_text(item, sections[section_name])
        ]
        if missing_targets:
            issues.append(
                ContentIssue(
                    message=f"“{item}”未能在{ '、'.join(missing_targets) }中找到对应表述。",
                    evidence=_find_evidence(item, sections),
                    section="建设内容对应关系",
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
            "请核对 4.3、4.6、5.1、第六章、第七章、第八章中同一功能点的命名是否一致。",
            "建议为每个建设功能点补充需求来源、建设内容、数据产出和预算条目的对应关系。",
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
            "target_sections_missing": len(missing_sections),
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
    for name, aliases in SECTION_SPECS:
        if all(_normalize_text(alias) in normalized for alias in aliases[:1]):
            if any(_normalize_text(alias) in normalized for alias in aliases):
                return name
        if any(_normalize_text(alias) in normalized for alias in aliases) and _is_probable_heading(line):
            return name
    return None


def _looks_like_other_heading(line: str, current: str) -> bool:
    matched = _match_section_heading(line)
    if matched == current:
        return False
    return len(line) <= 40


def _is_probable_heading(line: str) -> bool:
    return len(line) <= 60 or bool(STOP_HEADING_RE.match(line))


def _extract_candidate_items(section_text: str) -> list[str]:
    items: list[str] = []
    for raw_line in section_text.splitlines():
        line = LIST_MARKER_RE.sub("", raw_line.strip())
        if not line or len(line) < 4:
            continue
        if _is_probable_heading(line) and not _contains_item_keyword(line):
            continue
        for part in re.split(r"[；;。\.，,、：:]", line):
            candidate = _clean_item(part)
            if candidate and _contains_item_keyword(candidate):
                items.append(candidate)
    return _merge_items(*items)


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
    )
    return any(keyword in text for keyword in keywords)


def _clean_item(text: str) -> str:
    text = LIST_MARKER_RE.sub("", text.strip())
    text = re.sub(r"^(包括|实现|建设|提供|支持|完成|主要|通过)", "", text).strip()
    text = text.strip("“”\"'（）()[]【】 ")
    if len(text) < 4 or len(text) > 36:
        return ""
    if text.endswith(("如下", "包括", "内容", "需求", "章节")):
        return ""
    return text


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
            if any(normalized in _normalize_item(existing) or _normalize_item(existing) in normalized for existing in merged):
                continue
            merged.append(item)
    return merged


def _item_mentioned_in_text(item: str, text: str) -> bool:
    normalized_item = _normalize_item(item)
    normalized_text = _normalize_text(text)
    if normalized_item and normalized_item in normalized_text:
        return True

    tokens = _item_tokens(item)
    if not tokens:
        return False
    hit_count = sum(1 for token in tokens if token in normalized_text)
    return hit_count >= max(1, min(2, len(tokens)))


def _item_tokens(item: str) -> list[str]:
    normalized = _normalize_item(item)
    tokens = [
        token
        for token in re.split(r"(系统|平台|模块|功能|服务|应用|数据|接口|管理|建设|中心)", normalized)
        if len(token) >= 2 and token not in {"系统", "平台", "模块", "功能", "服务", "应用", "数据", "接口", "管理", "建设", "中心"}
    ]
    if tokens:
        return tokens[:4]
    return [normalized[:6]] if len(normalized) >= 4 else []


def _find_evidence(item: str, sections: dict[str, str]) -> str | None:
    for section_name, text in sections.items():
        for line in text.splitlines():
            if _item_mentioned_in_text(item, line):
                return f"{section_name}：{line[:120]}"
    return None


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
