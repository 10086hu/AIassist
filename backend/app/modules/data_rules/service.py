from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from app.modules.duplicate.document_parser import DocumentContent, parse_document
from app.modules.shanghai_review import BaseValidator, DataReportingValidator, ValidationError


@dataclass(frozen=True)
class RuleIssue:
    message: str
    evidence: str | None = None


@dataclass(frozen=True)
class RuleResult:
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
    issues: list[RuleIssue]
    suggestions: list[str]


LEGACY_RULE_IDS = {
    18: "DATA_REASON_001",
    21: "DATA_REASON_002",
}

DATA_REPORTING_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "DATA_REASON_003",
        "excel_row": 0,
        "rule_name": "项目成效考核目标与建设内容的匹配性审查规则",
        "rule_category": "内容合规性审查规则",
        "rule_description": "项目成效考核目标设置具有合理性，参照《市级数字化项目规划指标参数填报操作手册（试行）》开展规划指标参数的拟定。",
        "judgement_condition": "项目成效考核目标应与建设内容匹配，通用、业务及智能化相关成效指标设置应合理。",
        "error_prefixes": ("PTC-",),
        "default_suggestion": "请参照规划指标参数填报操作手册补充或调整成效考核目标，确保指标能够反映建设内容和项目成效。",
    },
    {
        "rule_id": "DATA_REASON_004",
        "excel_row": 0,
        "rule_name": "数据上链内容合规性审查规则",
        "rule_category": "内容合规性审查规则",
        "rule_description": "按照“新建即上链”的工作原则，明确对接政务目录链的数据上链内容，主要建设内容所产生的重点数据应实现应上尽上。",
        "judgement_condition": "第6.4节应明确政务目录链对接的数据范围、重点数据和上链方案，且不应将上链服务作为独立服务费申报。",
        "error_prefixes": ("DBC-",),
        "default_suggestion": "请补充数据上链范围、重点数据、更新频率和政务目录链对接方案，并核对预算中是否存在独立上链服务费用。",
    },
)

DATA_REASONABLENESS_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "DATA_REASON_001",
        "excel_row": 18,
        "rule_name": "项目预算一致性校验规则",
        "rule_category": "一致性校验规则",
        "rule_description": "项目预算数据计算无误",
        "judgement_condition": "各分项投资估算明细表内部计算无错误，所有分项明细表总计与项目投资总预算一致",
    },
    {
        "rule_id": "DATA_REASON_002",
        "excel_row": 21,
        "rule_name": "项目成效考核目标数量设置合规性审查规则",
        "rule_category": "内容合规性审查规则",
        "rule_description": "指标设置数量应满足要求",
        "judgement_condition": "通用指标中至少设置3个效益指标，业务指标中至少设置4个产出指标或效益指标，涉及智能化应用的需明确不少于2个成效指标",
    },
    *DATA_REPORTING_RULES,
)


MONEY_PATTERN = re.compile(
    r"(?P<number>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?P<unit>万元|万|元)?"
)

FINANCIAL_KEYWORDS = (
    "投资",
    "预算",
    "概算",
    "估算",
    "金额",
    "费用",
    "经费",
    "总计",
    "合计",
    "小计",
    "万元",
    "元",
)

PROJECT_TOTAL_KEYWORDS = (
    "项目总投资",
    "项目投资总额",
    "项目投资总预算",
    "总投资",
    "投资总额",
    "预算总额",
    "总预算",
)

DETAIL_TOTAL_KEYWORDS = ("总计", "合计")

INTELLIGENT_KEYWORDS = (
    "智能化",
    "智能应用",
    "人工智能",
    "大模型",
    "算法",
    "机器学习",
    "深度学习",
    "智能识别",
    "智能分析",
)


def run_data_rules_check_from_document(
    content: bytes,
    filename: str,
    project_name: str = "未命名可研项目",
    selected_rule_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run all data reasonableness rules against an uploaded Word/PDF report."""
    document = parse_document(content, filename)
    if not document.raw_text.strip():
        raise ValueError("未能从文档中提取到有效文本，请确认文件不是扫描件或加密文件")

    result_dicts = [
        _result_to_dict(item)
        for item in (
            _evaluate_budget_consistency(document),
            _evaluate_indicator_quantity(document),
        )
    ]
    result_dicts.extend(
        _build_data_reporting_results(
            content=content,
            filename=filename,
            selected_rule_ids=None,
        )
    )
    result_dicts = _filter_rule_results(result_dicts, selected_rule_ids)
    passed_count = sum(1 for item in result_dicts if item.get("passed"))

    return {
        "project_name": project_name,
        "filename": filename,
        "document_format": document.format,
        "extracted_characters": len(document.raw_text),
        "status": "completed",
        "summary": f"已完成数据填报合理性审查：{passed_count}/{len(result_dicts)} 项通过。",
        "results": result_dicts,
    }


def run_data_reporting_check_from_document(
    content: bytes,
    filename: str,
    project_name: str = "未命名可研项目",
    selected_rule_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run the two class-file data reporting rules only."""
    document = BaseValidator.parse_document(content, filename)
    full_text = str(document.get("_full_text") or "")
    if not full_text.strip():
        raise ValueError("未能从文档中提取到有效文本，请确认文件不是扫描件或加密文件")

    results = _filter_rule_results(
        _build_data_reporting_results(
            content=content,
            filename=filename,
            selected_rule_ids=None,
        ),
        selected_rule_ids,
    )
    passed_count = sum(1 for item in results if item.get("passed"))
    return {
        "project_name": project_name,
        "filename": filename,
        "document_format": filename.lower().rsplit(".", 1)[-1] if "." in filename else "",
        "extracted_characters": len(full_text),
        "status": "completed",
        "summary": f"已完成数据填报合理性新增规则审查：{passed_count}/{len(results)} 项通过。",
        "results": results,
    }


def _evaluate_budget_consistency(document: DocumentContent) -> RuleResult:
    lines = _meaningful_lines(document.raw_text)
    amount_lines = [
        (index, line, _extract_amounts_from_line(line, document.raw_text))
        for index, line in enumerate(lines)
    ]
    amount_lines = [
        (index, line, amounts)
        for index, line, amounts in amount_lines
        if amounts
    ]

    project_total_candidates = [
        {
            "line": line,
            "amount_wan": _pick_representative_amount(amounts),
        }
        for _index, line, amounts in amount_lines
        if _contains_any(line, PROJECT_TOTAL_KEYWORDS)
    ]
    project_totals = _distinct_amounts(
        item["amount_wan"] for item in project_total_candidates
    )

    detail_total_candidates = [
        {
            "line": line,
            "amount_wan": _pick_representative_amount(amounts),
        }
        for index, line, amounts in amount_lines
        if _is_detail_total_context(lines, index, line)
    ]

    arithmetic_issues = _find_total_row_arithmetic_issues(lines, amount_lines)
    issues: list[RuleIssue] = []
    suggestions: list[str] = []

    if not project_total_candidates:
        issues.append(
            RuleIssue(
                message="未识别到“项目总投资/总投资/预算总额”等项目总投资金额。",
                evidence=None,
            )
        )
        suggestions.append("请在投资概况或预算编制说明中明确项目总投资金额。")

    if len(project_totals) > 1:
        issue_text = "、".join(_format_amount(item) for item in project_totals)
        issues.append(
            RuleIssue(
                message=f"识别到多个不一致的项目总投资金额：{issue_text}。",
                evidence=" | ".join(item["line"] for item in project_total_candidates[:5]),
            )
        )
        suggestions.append("请统一全文中的项目总投资、投资概况和预算编制说明金额。")

    if project_totals and detail_total_candidates:
        project_total = project_totals[0]
        matching_detail_totals = [
            item
            for item in detail_total_candidates
            if _amounts_close(item["amount_wan"], project_total)
        ]
        if not matching_detail_totals:
            detail_preview = "、".join(
                _format_amount(item["amount_wan"])
                for item in detail_total_candidates[:5]
            )
            issues.append(
                RuleIssue(
                    message=(
                        "明细表合计/总计金额未与项目总投资金额匹配。"
                        f"项目总投资为 {_format_amount(project_total)}，"
                        f"识别到的合计/总计包括：{detail_preview}。"
                    ),
                    evidence=" | ".join(item["line"] for item in detail_total_candidates[:5]),
                )
            )
            suggestions.append("请核对各分项明细表总计与项目投资总预算是否一致。")

    for issue in arithmetic_issues[:5]:
        issues.append(issue)
    if arithmetic_issues:
        suggestions.append("请复核合计/总计行内部加总关系，确保表内计算无误。")

    if issues:
        hard_fail = bool(project_totals and len(project_totals) > 1) or bool(arithmetic_issues)
        if project_totals and detail_total_candidates:
            hard_fail = True
        status = "发现问题" if hard_fail else "需人工确认"
        severity = "高" if hard_fail else "需人工确认"
        passed = False
        summary = "项目预算一致性存在需复核事项。"
    else:
        status = "通过"
        severity = "通过"
        passed = True
        summary = "未发现项目总投资、预算合计和可识别合计行之间的不一致。"

    metrics = {
        "project_total_candidates": project_total_candidates[:10],
        "distinct_project_totals_wan": project_totals,
        "detail_total_candidates": detail_total_candidates[:10],
        "arithmetic_rows_checked": len(
            [
                1
                for index, line, amounts in amount_lines
                if _is_detail_total_context(lines, index, line) and len(amounts) >= 3
            ]
        ),
    }

    return RuleResult(
        rule_excel_row=18,
        rule_name="项目预算一致性校验规则",
        rule_category="一致性校验规则",
        rule_description="项目预算数据计算无误",
        judgement_condition=(
            "各分项的投资估算明细表内部计算无错误、表间的计算总和无错误，"
            "所有分项明细表总计与项目投资总预算一致"
        ),
        passed=passed,
        status=status,
        severity=severity,
        summary=summary,
        metrics=metrics,
        issues=issues,
        suggestions=suggestions or ["保持全文项目投资金额、各分项明细表和总预算一致。"],
    )


def _evaluate_indicator_quantity(document: DocumentContent) -> RuleResult:
    lines = _meaningful_lines(document.raw_text)
    indicator_lines = _collect_indicator_lines(lines)
    common_benefit_count = _count_indicator_rows(
        indicator_lines,
        scope_keywords=("通用指标", "通用"),
        type_keywords=("效益指标", "效益"),
    )
    business_output_or_benefit_count = _count_indicator_rows(
        indicator_lines,
        scope_keywords=("业务指标", "业务"),
        type_keywords=("产出指标", "效益指标", "产出", "效益"),
    )
    intelligent_project = _contains_any(document.raw_text, INTELLIGENT_KEYWORDS)
    achievement_count = _count_achievement_indicator_rows(indicator_lines)

    issues: list[RuleIssue] = []
    suggestions: list[str] = []

    if common_benefit_count < 3:
        issues.append(
            RuleIssue(
                message=f"通用指标中的效益指标识别到 {common_benefit_count} 个，少于规则要求的 3 个。",
                evidence=_join_evidence(indicator_lines),
            )
        )
        suggestions.append("请在通用指标中补足至少 3 个效益指标。")

    if business_output_or_benefit_count < 4:
        issues.append(
            RuleIssue(
                message=(
                    "业务指标中的产出指标或效益指标识别到 "
                    f"{business_output_or_benefit_count} 个，少于规则要求的 4 个。"
                ),
                evidence=_join_evidence(indicator_lines),
            )
        )
        suggestions.append("请在业务指标中补足至少 4 个产出指标或效益指标。")

    if intelligent_project and achievement_count < 2:
        issues.append(
            RuleIssue(
                message=(
                    "文档疑似涉及智能化应用，但成效指标识别到 "
                    f"{achievement_count} 个，少于规则要求的 2 个。"
                ),
                evidence=_join_evidence(
                    [line for line in lines if _contains_any(line, INTELLIGENT_KEYWORDS)]
                ),
            )
        )
        suggestions.append("涉及智能化应用时，请明确不少于 2 个成效指标。")

    if issues:
        status = "发现问题"
        severity = "高"
        passed = False
        summary = "项目成效考核目标数量未完全满足规则要求。"
    else:
        status = "通过"
        severity = "通过"
        passed = True
        summary = "项目成效考核目标数量满足规则要求。"

    metrics = {
        "common_benefit_indicator_count": common_benefit_count,
        "business_output_or_benefit_indicator_count": business_output_or_benefit_count,
        "intelligent_project_detected": intelligent_project,
        "achievement_indicator_count": achievement_count,
        "indicator_lines_sample": indicator_lines[:20],
    }

    return RuleResult(
        rule_excel_row=21,
        rule_name="项目成效考核目标数量设置合规性审查规则",
        rule_category="内容合规性审查规则",
        rule_description="指标设置数量应满足要求",
        judgement_condition=(
            "通用指标中至少设定3个效益指标，业务指标中至少设定4个产出指标或效益指标；"
            "涉及智能化应用的需明确不少于2个成效指标"
        ),
        passed=passed,
        status=status,
        severity=severity,
        summary=summary,
        metrics=metrics,
        issues=issues,
        suggestions=suggestions or ["保持通用指标、业务指标和智能化应用成效指标数量满足规则要求。"],
    )


def _build_data_reporting_results(
    content: bytes,
    filename: str,
    selected_rule_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    document = BaseValidator.parse_document(content, filename)
    validation = DataReportingValidator().validate(document)
    grouped_errors = _group_validation_errors_by_rule(validation.errors)
    selected = {str(item).strip() for item in selected_rule_ids or [] if str(item).strip()}

    results: list[dict[str, Any]] = []
    for rule in DATA_REPORTING_RULES:
        if selected and not _rule_selected(rule, selected):
            continue
        issues = grouped_errors.get(str(rule["rule_id"]), [])
        suggestions = _unique_texts(
            [str(issue.get("suggestion") or "").strip() for issue in issues]
            or [str(rule["default_suggestion"])]
        )
        if not suggestions:
            suggestions = [str(rule["default_suggestion"])]

        results.append(
            {
                "rule_id": rule["rule_id"],
                "rule_excel_row": rule["excel_row"],
                "rule_name": rule["rule_name"],
                "rule_category": rule["rule_category"],
                "rule_description": rule["rule_description"],
                "judgement_condition": rule["judgement_condition"],
                "passed": not issues,
                "status": "通过" if not issues else "发现问题",
                "severity": "pass" if not issues else _highest_validation_severity(
                    [str(issue.get("severity") or "warning") for issue in issues]
                ),
                "summary": "未发现问题。" if not issues else f"发现 {len(issues)} 项需复核内容。",
                "metrics": {"error_count": len(issues), "source": "class_file_data_reporting"},
                "issues": issues,
                "suggestions": suggestions,
            }
        )
    return results


def _group_validation_errors_by_rule(errors: list[ValidationError]) -> dict[str, list[dict[str, Any]]]:
    grouped = {str(rule["rule_id"]): [] for rule in DATA_REPORTING_RULES}
    for error in errors:
        rule = _rule_for_error(str(error.code))
        if rule is None:
            continue
        grouped[str(rule["rule_id"])].append(
            {
                "message": str(error.message),
                "section": str(error.section),
                "evidence": str(error.section),
                "severity": str(error.severity),
                "suggestion": error.suggestion,
            }
        )
    return grouped


def _rule_for_error(code: str) -> dict[str, Any] | None:
    for rule in DATA_REPORTING_RULES:
        if any(code.startswith(prefix) for prefix in rule["error_prefixes"]):
            return rule
    return None


def _filter_rule_results(
    results: list[dict[str, Any]],
    selected_rule_ids: list[str] | None,
) -> list[dict[str, Any]]:
    selected = {str(item).strip() for item in selected_rule_ids or [] if str(item).strip()}
    if not selected:
        return results
    return [result for result in results if _result_selected(result, selected)]


def _result_selected(result: dict[str, Any], selected: set[str]) -> bool:
    values = {
        str(result.get("rule_id") or ""),
        str(result.get("rule_excel_row") or ""),
        str(result.get("rule_name") or ""),
    }
    return bool(values & selected)


def _rule_selected(rule: dict[str, Any], selected: set[str]) -> bool:
    values = {
        str(rule.get("rule_id") or ""),
        str(rule.get("excel_row") or ""),
        str(rule.get("rule_name") or ""),
    }
    return bool(values & selected)


def _highest_validation_severity(values: list[str]) -> str:
    if any(value == "risk" for value in values):
        return "risk"
    if any(value == "warning" for value in values):
        return "warning"
    return values[0] if values else "warning"


def _unique_texts(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _result_to_dict(result: RuleResult) -> dict[str, Any]:
    data = asdict(result)
    data["rule_id"] = LEGACY_RULE_IDS.get(result.rule_excel_row, str(result.rule_excel_row))
    data["issues"] = [asdict(issue) for issue in result.issues]
    return data


def _meaningful_lines(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in normalized.split("\n") if line.strip()]


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _extract_amounts_from_line(line: str, full_text: str) -> list[float]:
    value_text = re.sub(r"^(?:表\d+行\d+|第\d+页表\d+行\d+)\t", "", line)
    numeric_matches = list(MONEY_PATTERN.finditer(value_text))
    looks_like_numeric_table_row = "\t" in line and len(numeric_matches) >= 2

    if not _contains_any(line, FINANCIAL_KEYWORDS) and not looks_like_numeric_table_row:
        return []

    default_to_wan = bool(re.search(r"单位\s*[:：]?\s*万元|单位\s*[:：]?\s*万", full_text))
    amounts: list[float] = []
    for match in numeric_matches:
        raw_number = match.group("number")
        unit = match.group("unit")
        value = float(raw_number.replace(",", ""))

        if not unit:
            if not default_to_wan and not looks_like_numeric_table_row and value < 10:
                continue
            if value < 1:
                continue

        if unit == "元":
            value = value / 10000
        amounts.append(round(value, 4))
    return amounts


def _pick_representative_amount(amounts: list[float]) -> float:
    return max(amounts)


def _distinct_amounts(amounts: Iterable[float]) -> list[float]:
    distinct: list[float] = []
    for amount in amounts:
        if not any(_amounts_close(amount, existing) for existing in distinct):
            distinct.append(amount)
    return distinct


def _amounts_close(left: float, right: float) -> bool:
    tolerance = max(0.01, abs(right) * 0.001)
    return abs(left - right) <= tolerance


def _format_amount(amount: float) -> str:
    formatted = f"{amount:.4f}".rstrip("0").rstrip(".")
    return f"{formatted} 万元"


def _is_detail_total_context(lines: list[str], index: int, line: str) -> bool:
    if _contains_any(line, DETAIL_TOTAL_KEYWORDS):
        return True
    if index > 0 and "\t" in line and _contains_any(lines[index - 1], DETAIL_TOTAL_KEYWORDS):
        return True
    return False


def _find_total_row_arithmetic_issues(
    lines: list[str],
    amount_lines: list[tuple[int, str, list[float]]],
) -> list[RuleIssue]:
    issues: list[RuleIssue] = []
    for index, line, amounts in amount_lines:
        if not _is_detail_total_context(lines, index, line) or len(amounts) < 3:
            continue
        expected_total = sum(amounts[:-1])
        actual_total = amounts[-1]
        if not _amounts_close(expected_total, actual_total):
            issues.append(
                RuleIssue(
                    message=(
                        "识别到合计/总计行内部加总不一致："
                        f"前序金额合计为 {_format_amount(expected_total)}，"
                        f"行尾合计为 {_format_amount(actual_total)}。"
                    ),
                    evidence=line,
                )
            )
    return issues


def _collect_indicator_lines(lines: list[str]) -> list[str]:
    selected: list[str] = []
    in_indicator_section = False

    for line in lines:
        if _contains_any(line, ("2.6", "绩效目标", "项目成效", "成效考核", "规划指标")):
            in_indicator_section = True

        has_indicator_keyword = _contains_any(line, ("通用指标", "业务指标", "产出指标", "效益指标", "成效指标", "指标"))
        if (in_indicator_section or has_indicator_keyword) and has_indicator_keyword:
            if _looks_like_indicator_line(line):
                selected.append(line)

        if in_indicator_section and _contains_any(line, ("2.7", "投资概况", "建设内容", "预算编制")):
            in_indicator_section = False

    return _dedupe_preserve_order(selected)


def _looks_like_indicator_line(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    if len(compact) < 5:
        return False

    label_only = compact
    for token in ("通用指标", "业务指标", "产出指标", "效益指标", "成效指标", "指标名称", "指标类型", "一级指标", "二级指标", "三级指标", "目标值", "单位"):
        label_only = label_only.replace(token, "")
    label_only = re.sub(r"[:：|,，;；\-/\\（）()【】\[\]\t]", "", label_only)

    return bool(label_only) or bool(re.search(r"\d|%|≥|≤|>|<", compact))


def _count_indicator_rows(
    lines: list[str],
    scope_keywords: tuple[str, ...],
    type_keywords: tuple[str, ...],
) -> int:
    count = 0
    target_scope = "common" if "通用" in "".join(scope_keywords) else "business"
    target_types = {
        _detect_indicator_type(keyword)
        for keyword in type_keywords
        if _detect_indicator_type(keyword)
    }
    current_scope: str | None = None
    current_type: str | None = None

    for line in lines:
        detected_scope = _detect_indicator_scope(line)
        detected_type = _detect_indicator_type(line)
        if detected_scope:
            current_scope = detected_scope
        if detected_type:
            current_type = detected_type

        scoped = current_scope == target_scope
        typed = current_type in target_types

        if scoped and typed and _looks_like_indicator_data_row(line):
            count += 1

    return count


def _detect_indicator_scope(line: str) -> str | None:
    if "通用指标" in line or "通用" in line:
        return "common"
    if "业务指标" in line or "业务" in line:
        return "business"
    return None


def _detect_indicator_type(line: str) -> str | None:
    if "产出指标" in line or "产出" in line:
        return "output"
    if "效益指标" in line or "效益" in line:
        return "benefit"
    return None


def _looks_like_indicator_data_row(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    header_tokens = ("指标名称", "指标类型", "一级指标", "二级指标", "三级指标", "目标值", "单位")
    if all(token in compact for token in ("指标", "名称")) and not re.search(r"\d|%|率|量|数|提升|降低|满意|覆盖|完成", compact):
        return False
    if compact in header_tokens:
        return False
    return len(compact) >= 8


def _count_achievement_indicator_rows(lines: list[str]) -> int:
    count = 0
    in_achievement_section = False

    for line in lines:
        if "成效指标" in line:
            in_achievement_section = True
        if (in_achievement_section or "成效" in line) and _looks_like_indicator_data_row(line):
            count += 1

    return count


def _join_evidence(lines: list[str], limit: int = 5) -> str | None:
    if not lines:
        return None
    return " | ".join(lines[:limit])


def _dedupe_preserve_order(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = re.sub(r"\s+", "", line)
        if key not in seen:
            seen.add(key)
            output.append(line)
    return output
