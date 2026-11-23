from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from io import BytesIO
from typing import Any, Iterable

from app.modules.duplicate.document_parser import DocumentContent, parse_document
from app.modules.data_rules.data_governance_service import validate_data_governance_service_design
from app.modules.shanghai_review import BaseValidator, DataReportingValidator, ValidationError


@dataclass(frozen=True)
class RuleIssue:
    message: str
    evidence: str | None = None
    section: str | None = None


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
    {
        "rule_id": "DATA_REASON_024",
        "excel_row": 24,
        "rule_name": "数据治理服务内容设计合规性审查规则",
        "rule_category": "内容合规性审查规则",
        "rule_description": "涉及数据治理服务的项目，数据服务事项内容参照《市级数字化项目数据治理服务配置指引（试行）》开展编制。",
        "judgement_condition": "第6.3节数据治理内容附表应符合指引的4类6项服务范围、计量单位和负面清单要求。",
        "error_prefixes": ("DGS-",),
        "default_suggestion": "请按指引附表补充或调整6.3数据治理服务内容，确保服务事项属于4类6项，计量单位正确，且不包含负面清单事项。",
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


NUMBER_TEXT = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
MONEY_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_.])(?P<currency>人民币|RMB|¥|￥)?\s*"
    rf"(?P<number>{NUMBER_TEXT})\s*"
    r"(?:(?:[（(]\s*)?(?P<unit>万元|万\s*元|元|万)(?:\s*[）)])?)?"
    r"(?!\s*(?:\+|个|台|套|块|条|项|人|次|年|月|日|天|小时|页|张|路|颗|份|类|核|票|字节|"
    r"GB|MB|TB|KB|G|M|GHz|MHz|%|％|[A-Za-z]*B))",
    re.IGNORECASE,
)
NUMBER_PATTERN = re.compile(rf"(?<![A-Za-z0-9_.])(?P<number>{NUMBER_TEXT})(?![A-Za-z0-9_.])")
NON_MONEY_UNIT_RE = re.compile(
    r"^\s*(?:个|台|套|块|条|项|人|次|年|月|日|天|小时|页|张|路|颗|份|类|核|"
    r"GB|MB|TB|KB|G|M|GHz|MHz|%|％)",
    re.IGNORECASE,
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

FINANCIAL_CONTEXT_KEYWORDS = (
    "投资",
    "预算",
    "概算",
    "估算",
    "金额",
    "费用",
    "经费",
    "资金",
    "成本",
    "造价",
    "单价",
    "总价",
    "报价",
    "购置费",
    "开发费",
    "服务费",
    "建设费",
    "总计",
    "合计",
    "小计",
)

AMOUNT_COLUMN_KEYWORDS = (
    "金额",
    "投资",
    "预算",
    "概算",
    "估算",
    "费用",
    "经费",
    "资金",
    "成本",
    "造价",
    "单价",
    "总价",
    "合价",
    "报价",
    "小计",
    "合计",
    "总计",
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

PROJECT_TOTAL_ALIASES = (
    *PROJECT_TOTAL_KEYWORDS,
    "总投资额",
    "项目总预算",
    "工程总投资",
)

DETAIL_TOTAL_KEYWORDS = ("总计", "合计")

NON_AMOUNT_COLUMN_KEYWORDS = (
    "数量",
    "单位",
    "规格",
    "配置",
    "容量",
    "内存",
    "硬盘",
    "CPU",
    "工作量",
    "人月",
    "服务期",
    "建设周期",
    "周期",
    "月份",
    "条数",
    "页数",
)

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

BUDGET_CONTEXT_ALIASES = (
    *FINANCIAL_CONTEXT_KEYWORDS,
    "申报金额",
    "申报总额",
    "总金额",
    "金额小计",
    "投资估算额",
    "概算金额",
)

BUDGET_AMOUNT_COLUMN_ALIASES = (
    *AMOUNT_COLUMN_KEYWORDS,
    "申报金额",
    "申报总额",
    "总金额",
    "合计金额",
)

DETAIL_TOTAL_ALIASES = (
    *DETAIL_TOTAL_KEYWORDS,
    "汇总",
    "共计",
    "总额",
    "总和",
    "合计金额",
    "总计金额",
)

INDICATOR_SCOPE_ALIASES = {
    "common": ("通用指标", "通用", "共性指标", "通用绩效指标"),
    "business": ("业务指标", "业务", "行业指标", "业务绩效指标"),
}

INDICATOR_TYPE_ALIASES = {
    "output": ("产出指标", "产出", "输出指标", "建设产出"),
    "benefit": ("效益指标", "效益", "效果指标", "应用效益", "使用效益"),
}

ACHIEVEMENT_INDICATOR_ALIASES = (
    "成效指标",
    "应用成效",
    "智能化成效",
    "算法成效",
    "模型成效",
    "准确率",
    "召回率",
    "响应时间",
    "识别率",
)

def run_data_rules_check_from_document(
    content: bytes,
    filename: str,
    project_name: str = "未命名可研项目",
    selected_rule_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run all data reasonableness rules against an uploaded Word report."""
    selected = {str(item).strip() for item in selected_rule_ids or [] if str(item).strip()}
    budget_rule_only = _selected_is_budget_rule_only(selected)
    if budget_rule_only and filename.lower().endswith(".docx"):
        document = _parse_docx_paragraphs_only(content, filename)
    else:
        document = parse_document(content, filename)
    if not document.raw_text.strip() and not budget_rule_only:
        raise ValueError("未能从文档中提取到有效文本，请确认文件不是扫描件或加密文件")

    result_dicts: list[dict[str, Any]] = []

    if not selected or _selected_matches_rule(selected, "DATA_REASON_001", 18, "项目预算一致性校验规则"):
        result_dicts.append(
            _result_to_dict(_evaluate_budget_consistency(document, content=content, filename=filename))
        )
    if not selected or _selected_matches_rule(selected, "DATA_REASON_002", 21, "项目成效考核目标数量设置合规性审查规则"):
        result_dicts.append(
            _result_to_dict(
                _evaluate_indicator_quantity(
                    document,
                    content=content,
                    filename=filename,
                    project_name=project_name,
                )
            )
        )
    if not selected or any(_rule_selected(rule, selected) for rule in DATA_REPORTING_RULES):
        result_dicts.extend(
            _build_data_reporting_results(
                content=content,
                filename=filename,
                selected_rule_ids=selected_rule_ids,
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


def _evaluate_budget_consistency(
    document: DocumentContent,
    content: bytes | None = None,
    filename: str | None = None,
) -> RuleResult:
    from app.modules.data_rules.document_rules import _evaluate_budget_consistency as revised
    return revised(document, content=content, filename=filename)




def _evaluate_indicator_quantity(
    document: DocumentContent,
    content: bytes | None = None,
    filename: str | None = None,
    project_name: str | None = None,
) -> RuleResult:
    from app.modules.data_rules.document_rules import _evaluate_indicator_quantity as revised
    return revised(document, content=content, filename=filename, project_name=project_name)




def _build_data_reporting_results(
    content: bytes,
    filename: str,
    selected_rule_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    selected_governance = {str(item).strip() for item in selected_rule_ids or [] if str(item).strip()}
    governance_rule = next(rule for rule in DATA_REPORTING_RULES if str(rule["rule_id"]) == "DATA_REASON_024")
    if selected_governance and all(_rule_selected(governance_rule, {item}) for item in selected_governance):
        from app.modules.data_rules.document_rules import _build_data_reporting_results as revised_governance
        return revised_governance(content, filename, selected_rule_ids=["24"])
    document = BaseValidator.parse_document(content, filename)
    validation = DataReportingValidator().validate(document)
    validation_errors = list(validation.errors)
    grouped_errors = _group_validation_errors_by_rule(validation_errors)
    selected = {str(item).strip() for item in selected_rule_ids or [] if str(item).strip()}

    results: list[dict[str, Any]] = []
    for rule in DATA_REPORTING_RULES:
        if selected and not _rule_selected(rule, selected):
            continue
        if str(rule["rule_id"]) == "DATA_REASON_024":
            from app.modules.data_rules.document_rules import _build_data_reporting_results as revised_governance
            results.extend(revised_governance(content, filename, selected_rule_ids=["24"]))
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


def _selected_matches_rule(selected: set[str], rule_id: str, excel_row: int, rule_name: str) -> bool:
    return bool({rule_id, str(excel_row), rule_name} & selected)


def _rule_selected(rule: dict[str, Any], selected: set[str]) -> bool:
    values = {
        str(rule.get("rule_id") or ""),
        str(rule.get("excel_row") or ""),
        str(rule.get("rule_name") or ""),
    }
    return bool(values & selected)


def _selected_is_budget_rule_only(selected: set[str]) -> bool:
    if not selected:
        return False
    if not _selected_matches_rule(selected, "DATA_REASON_001", 18, "项目预算一致性校验规则"):
        return False
    if _selected_matches_rule(selected, "DATA_REASON_002", 21, "项目成效考核目标数量设置合规性审查规则"):
        return False
    if any(_rule_selected(rule, selected) for rule in DATA_REPORTING_RULES):
        return False
    allowed_values = {"DATA_REASON_001", "18", "项目预算一致性校验规则"}
    return selected <= allowed_values


def _parse_docx_paragraphs_only(content: bytes, filename: str) -> DocumentContent:
    try:
        from docx import Document
    except Exception as exc:
        raise ValueError(f"Word 文档解析失败: {exc}") from exc

    try:
        doc = Document(BytesIO(content))
    except Exception as exc:
        raise ValueError(f"Word 文档解析失败: {exc}") from exc

    lines = [paragraph.text.strip() for paragraph in doc.paragraphs if paragraph.text and paragraph.text.strip()]
    return DocumentContent(format="docx", raw_text="\n".join(lines), sections=[], filename=filename)


def _rule_21_project_applicability(
    document: DocumentContent,
    filename: str | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Return whether rule 21 should run.

    规则分工第21条标签是“市级项目”。当前接口没有结构化项目级别字段，因此先使用
    文件名/项目名的强信号判断；项目级别不明确时保守执行，避免漏检可能的市级项目。
    """

    name_context = f"{project_name or ''} {filename or ''}"
    full_context = f"{name_context} {document.raw_text[:3000]}"
    normalized_name = name_context.lower()
    normalized_full = full_context.lower()

    municipal_terms = ("市级项目", "市级数字化", "上海市", "上海海关")
    non_municipal_terms = (
        "区级项目",
        "松江区",
        "运维项目",
        "运维方案",
        "维护方案",
        "系统维护",
        "云托管方案",
        "(运维)",
        "（运维）",
    )

    if any(term.lower() in normalized_name for term in municipal_terms):
        return {"applies": True, "scope": "municipal", "confidence": "high", "reason": "文件名或项目名明确指向市级项目。"}

    if any(term.lower() in normalized_name for term in non_municipal_terms):
        return {"applies": False, "scope": "non_municipal", "confidence": "high", "reason": "文件名或项目名明确指向区级/运维/维护项目。"}

    if any(term.lower() in normalized_full for term in municipal_terms):
        return {"applies": True, "scope": "municipal", "confidence": "medium", "reason": "正文前部出现市级项目相关表述。"}

    if any(term.lower() in normalized_full for term in non_municipal_terms):
        return {"applies": False, "scope": "non_municipal", "confidence": "medium", "reason": "正文前部出现区级/运维/维护项目相关表述。"}

    return {"applies": True, "scope": "unknown_assume_applicable", "confidence": "low", "reason": "项目级别未明确，保守执行第21条。"}


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


def _contains_fuzzy_keyword(text: str, keywords: Iterable[str], threshold: float = 0.75) -> bool:
    """Return True when text exactly contains a keyword or covers most keyword 2-grams.

    This is intentionally a recall helper for noun/header recognition only. Rule pass/fail
    decisions still use parsed amounts, quantities and indicator counts.
    """

    keyword_tuple = tuple(str(keyword or "") for keyword in keywords if str(keyword or ""))
    if _contains_any(text, keyword_tuple):
        return True
    return _best_keyword_bigram_recall(text, keyword_tuple) >= threshold


def _contains_project_total_keyword(text: str) -> bool:
    return _contains_fuzzy_keyword(text, PROJECT_TOTAL_ALIASES, threshold=0.78)


def _contains_detail_total_keyword(text: str) -> bool:
    return _contains_fuzzy_keyword(text, DETAIL_TOTAL_ALIASES, threshold=0.72)


def _contains_intelligent_keyword(text: str) -> bool:
    return _contains_fuzzy_keyword(text, INTELLIGENT_KEYWORDS, threshold=0.68)


def _contains_indicator_section_keyword(text: str) -> bool:
    return _contains_fuzzy_keyword(text, ("2.6", "绩效目标", "项目成效", "成效考核", "规划指标"), threshold=0.68)


def _contains_indicator_any_keyword(text: str) -> bool:
    return _contains_fuzzy_keyword(
        text,
        (
            "通用指标",
            "业务指标",
            "产出指标",
            "效益指标",
            "成效指标",
            "指标",
        ),
        threshold=0.68,
    )


def _best_keyword_bigram_recall(text: str, keywords: Iterable[str]) -> float:
    text_grams = _char_bigrams(text)
    if not text_grams:
        return 0.0
    best = 0.0
    for keyword in keywords:
        keyword_grams = _char_bigrams(keyword)
        if not keyword_grams:
            continue
        best = max(best, len(text_grams & keyword_grams) / len(keyword_grams))
    return best


def _char_bigrams(value: str) -> set[str]:
    compact = re.sub(r"[\s,，.。;；:：|/\\()（）\[\]【】《》<>_\-－—、]+", "", str(value or "")).lower()
    if len(compact) < 2:
        return set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def _extract_amounts_from_line(lines: list[str], index: int, full_text: str) -> list[float]:
    line = lines[index]
    value_text = _strip_table_prefix(line)
    explicit_matches = [
        match
        for match in MONEY_PATTERN.finditer(value_text)
        if match.group("currency") or match.group("unit")
    ]

    context_text = _line_context(lines, index)
    budget_context = _is_budget_amount_context(value_text, context_text)

    amounts: list[float] = []
    for match in explicit_matches:
        if not _money_match_is_budget_amount(value_text, match, budget_context):
            continue
        unit = (match.group("unit") or "").replace(" ", "")
        if unit == "万" and not _bare_wan_looks_like_money(value_text, match, budget_context):
            continue
        amount = _money_match_to_wan(match)
        if amount is not None and _amount_is_reasonable(amount):
            amounts.append(amount)

    if budget_context:
        amounts.extend(_extract_unitless_budget_amounts(lines, index, value_text, context_text))

    return _dedupe_amounts(amounts)


def _strip_table_prefix(line: str) -> str:
    return re.sub(r"^(?:表\d+行\d+|第\d+页表\d+行\d+)\t", "", line)


def _line_context(lines: list[str], index: int, window: int = 4) -> str:
    start = max(0, index - window)
    return "\n".join(_strip_table_prefix(line) for line in lines[start : index + 1])


def _is_budget_amount_context(value_text: str, context_text: str) -> bool:
    compact_context = re.sub(r"\s+", "", context_text)
    has_financial_context = _contains_fuzzy_keyword(compact_context, BUDGET_CONTEXT_ALIASES, threshold=0.70)
    has_local_money_unit = _has_amount_unit_text(compact_context)
    has_amount_column = _contains_fuzzy_keyword(compact_context, BUDGET_AMOUNT_COLUMN_ALIASES, threshold=0.70)

    # 单纯出现在资源清单、配置清单中的数字，即使附近有“单位”，也不能按预算金额处理。
    if _looks_like_resource_quantity_context(value_text) and not has_financial_context:
        return False

    return has_financial_context and (has_local_money_unit or has_amount_column)


def _has_amount_unit_text(text: str) -> bool:
    return bool(
        re.search(
            r"(单位|金额|投资|预算|概算|估算|费用|经费|资金|成本|造价|总价|合计|总计).*?"
            r"(万元|元(?![\u4e00-\u9fffA-Za-z]))",
            text,
        )
    )


def _looks_like_resource_quantity_context(text: str) -> bool:
    return bool(
        re.search(
            r"(?:数量|配置|规格|容量|内存|硬盘|CPU|服务器|数据库|操作系统|交换机|防火墙|存储|"
            r"GB|MB|TB|KB|GHz|MHz|个|台|套|块|条|核)",
            text,
            flags=re.IGNORECASE,
        )
    )


def _extract_unitless_budget_amounts(
    lines: list[str],
    index: int,
    value_text: str,
    context_text: str,
) -> list[float]:
    if not _has_local_wan_unit(context_text):
        return []

    amounts: list[float] = []
    table_cells = value_text.split("\t") if "\t" in value_text else []
    if table_cells:
        header_cells = _nearest_table_header_cells(lines, index, len(table_cells))
        if not header_cells:
            return []
        for cell_index, cell in enumerate(table_cells):
            if _cell_has_explicit_money(cell):
                continue
            if not _table_cell_is_amount_column(cell_index, cell, header_cells, value_text):
                continue
            unit = _amount_unit_for_cell(cell_index, header_cells, context_text)
            for match in NUMBER_PATTERN.finditer(cell):
                if _number_has_non_money_unit(cell, match):
                    continue
                if _looks_like_ordinal_or_year(cell, match, cell_index):
                    continue
                value = float(match.group("number").replace(",", ""))
                if unit == "元":
                    value = value / 10000
                if _amount_is_reasonable(value):
                    amounts.append(round(value, 4))
        return amounts

    return []


def _has_local_wan_unit(context_text: str) -> bool:
    compact_context = re.sub(r"\s+", "", context_text)
    return _has_amount_unit_text(compact_context)


def _nearest_table_header_cells(lines: list[str], index: int, cell_count: int) -> list[str]:
    current_table = _table_id(lines[index])
    if not current_table:
        return []

    for previous in range(index - 1, max(-1, index - 10), -1):
        if _table_id(lines[previous]) != current_table:
            continue
        cells = _strip_table_prefix(lines[previous]).split("\t")
        if len(cells) != cell_count:
            continue
        if _contains_fuzzy_keyword("".join(cells), (*BUDGET_AMOUNT_COLUMN_ALIASES, "数量", "单位", "规格"), threshold=0.70):
            return cells
    return []


def _table_id(line: str) -> str | None:
    match = re.match(r"^(表\d+|第\d+页表\d+)行\d+\t", line)
    return match.group(1) if match else None


def _cell_has_explicit_money(cell: str) -> bool:
    return any(match.group("currency") or match.group("unit") for match in MONEY_PATTERN.finditer(cell))


def _money_match_is_budget_amount(text: str, match: re.Match[str], budget_context: bool) -> bool:
    if _number_has_non_money_unit(text, match):
        return False

    prefix = text[max(0, match.start() - 12) : match.start()]
    suffix = text[match.end() : match.end() + 12]
    local_context = prefix + suffix
    if _looks_like_resource_quantity_context(local_context) and not _contains_fuzzy_keyword(text, BUDGET_CONTEXT_ALIASES, threshold=0.72):
        return False

    currency = bool(match.group("currency"))
    unit = (match.group("unit") or "").replace(" ", "")
    if unit == "万":
        return _bare_wan_looks_like_money(text, match, budget_context)
    if unit in {"万元", "元"}:
        return currency or budget_context or _contains_fuzzy_keyword(text, BUDGET_CONTEXT_ALIASES, threshold=0.72)
    return currency and not _looks_like_resource_quantity_context(local_context)


def _table_cell_is_amount_column(
    cell_index: int,
    cell: str,
    header_cells: list[str],
    row_text: str,
) -> bool:
    if cell_index == 0 and re.fullmatch(r"\d+(?:\.\d+)?", cell.strip()):
        return False

    if header_cells and cell_index < len(header_cells):
        header = header_cells[cell_index]
        if _contains_any(header, ("数量", "规格", "配置", "单位", "容量")):
            return False
        if _contains_any(header, NON_AMOUNT_COLUMN_KEYWORDS):
            return False
        if _contains_fuzzy_keyword(header, BUDGET_AMOUNT_COLUMN_ALIASES, threshold=0.70):
            return True
        return False

    return False


def _amount_unit_for_cell(cell_index: int, header_cells: list[str], context_text: str) -> str:
    header = header_cells[cell_index] if header_cells and cell_index < len(header_cells) else ""
    compact_header = re.sub(r"\s+", "", header)
    if "万元" in compact_header:
        return "万元"
    if "元" in compact_header:
        return "元"

    compact_context = re.sub(r"\s+", "", context_text)
    if "单位:万元" in compact_context or "单位：万元" in compact_context or "（万元）" in compact_context:
        return "万元"
    if "单位:元" in compact_context or "单位：元" in compact_context or "（元）" in compact_context:
        return "元"
    return "万元"


def _money_match_to_wan(match: re.Match[str]) -> float | None:
    raw_number = match.group("number")
    unit = (match.group("unit") or "").replace(" ", "")
    value = float(raw_number.replace(",", ""))

    if unit == "元":
        value = value / 10000
    return round(value, 4)


def _bare_wan_looks_like_money(text: str, match: re.Match[str], budget_context: bool) -> bool:
    suffix = text[match.end() : match.end() + 8]
    if re.match(r"\s*(?:\+|票|条|个|台|套|块|项|人|次|年|月|日|样本|记录|数据)", suffix):
        return False
    if _looks_like_resource_quantity_context(text):
        return False
    return budget_context and _contains_fuzzy_keyword(text, ("金额", "费用", "经费", "投资", "预算", "概算", "估算", "单价", "总价", "采购", "服务费"), threshold=0.72)


def _number_has_non_money_unit(text: str, match: re.Match[str]) -> bool:
    suffix = text[match.end() : match.end() + 8]
    return bool(NON_MONEY_UNIT_RE.match(suffix))


def _looks_like_ordinal_or_year(text: str, match: re.Match[str], cell_index: int) -> bool:
    number_text = match.group("number").replace(",", "")
    try:
        value = float(number_text)
    except ValueError:
        return True

    if cell_index == 0 and value < 1000 and re.fullmatch(r"\s*" + re.escape(match.group("number")) + r"\s*", text):
        return True
    if value < 1:
        return True
    if 1900 <= value <= 2100 and re.search(r"年|年度", text):
        return True
    if cell_index == 0 and value < 10 and not _contains_any(text, ("合计", "总计", "小计", "金额", "费用", "投资", "预算", "概算", "估算")):
        return True
    return False


def _amount_is_reasonable(amount_wan: float) -> bool:
    return amount_wan >= 0.0001


def _dedupe_amounts(amounts: list[float]) -> list[float]:
    deduped: list[float] = []
    for amount in amounts:
        if not any(_amounts_close(amount, existing) for existing in deduped):
            deduped.append(amount)
    return deduped


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
    if _contains_detail_total_keyword(line):
        return True
    if index > 0 and "\t" in line and _contains_detail_total_keyword(lines[index - 1]):
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


def _count_total_row_arithmetic_checks(
    lines: list[str],
    amount_lines: list[tuple[int, str, list[float]]],
) -> int:
    return sum(
        1
        for index, _line, amounts in amount_lines
        if _is_detail_total_context(lines, index, lines[index]) and len(amounts) >= 3
    )


def _empty_budget_table_checks() -> dict[str, Any]:
    return {
        "detail_total_candidates": [],
        "arithmetic_issues": [],
        "tables_checked": 0,
        "evidence_examples": [],
        "table_internal_checks": 0,
        "summary_internal_checks": 0,
    }


def _budget_candidate_is_component_total(candidate: dict[str, Any]) -> bool:
    return str(candidate.get("kind") or "") == "detail_table_total"


def _budget_candidate_is_grand_total(candidate: dict[str, Any]) -> bool:
    if str(candidate.get("kind") or "") == "investment_summary_total":
        return True
    line = str(candidate.get("line") or "")
    return _contains_fuzzy_keyword(
        line,
        (
            "投资估算总表",
            "投资估算汇总表",
            "项目投资估算",
            "总投资估算",
            "项目总投资",
            "项目投资总预算",
            "预算总额",
            "总预算",
        ),
        threshold=0.70,
    )


def _sum_candidate_amounts(candidates: list[dict[str, Any]]) -> float | None:
    amounts = [
        float(candidate["amount_wan"])
        for candidate in candidates
        if candidate.get("amount_wan") is not None
    ]
    if not amounts:
        return None
    return round(sum(amounts), 4)


def _amount_exceeds(left: float, right: float) -> bool:
    tolerance = max(0.01, abs(right) * 0.001)
    return left - right > tolerance


def _iter_docx_table_rows_fast(doc: Any) -> Iterable[list[list[str]]]:
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    table_tag = namespace + "tbl"
    row_tag = namespace + "tr"
    cell_tag = namespace + "tc"
    text_tag = namespace + "t"

    for child in doc.element.body.iterchildren():
        if child.tag != table_tag:
            continue
        rows: list[list[str]] = []
        for row_element in child.findall(row_tag):
            cells: list[str] = []
            for cell_element in row_element.findall(cell_tag):
                cell_text = "".join(
                    text_node.text or ""
                    for text_node in cell_element.iter(text_tag)
                    if text_node.text
                )
                cells.append(_clean_budget_cell(cell_text))
            rows.append(cells)
        yield rows


def _extract_docx_budget_table_checks(content: bytes | None, filename: str | None) -> dict[str, Any]:
    """解析 Word 预算表，校验明细合计与投资估算总表。

    parse_document 的纯文本会丢失表格列定位，容易漏掉“总金额/申报金额”列。这里直接读 DOCX
    表格，保留 DOCX表格X行Y 作为前端可展示的文章依据。
    """

    if not content or not (filename or "").lower().endswith(".docx"):
        return _empty_budget_table_checks()

    try:
        from docx import Document
    except Exception:
        return _empty_budget_table_checks()

    try:
        doc = Document(BytesIO(content))
    except Exception:
        return _empty_budget_table_checks()

    detail_total_candidates: list[dict[str, Any]] = []
    arithmetic_issues: list[RuleIssue] = []
    evidence_examples: list[str] = []
    tables_checked = 0
    table_internal_checks = 0
    summary_internal_checks = 0

    for table_index, rows in enumerate(_iter_docx_table_rows_fast(doc), start=1):
        rows = [row for row in rows if any(row)]
        if not rows:
            continue

        table_text = " ".join(" ".join(row) for row in rows)
        if not _looks_like_budget_table(table_text):
            continue

        location = f"DOCX表格{table_index}"
        if any(keyword in table_text for keyword in ("投资估算总表", "投资估算表", "投资估算汇总表", "项目投资估算", "总投资估算")):
            tables_checked += 1
            summary_result = _check_investment_summary_table(rows, location)
            detail_total_candidates.extend(summary_result["detail_total_candidates"])
            arithmetic_issues.extend(summary_result["arithmetic_issues"])
            evidence_examples.extend(summary_result["evidence_examples"])
            summary_internal_checks += summary_result["summary_internal_checks"]
            continue

        amount_info = _budget_amount_column(rows)
        if amount_info is None:
            continue
        header_index, amount_col, unit = amount_info
        total_rows = [
            (row_index, row)
            for row_index, row in enumerate(rows[header_index + 1 :], start=header_index + 2)
            if _budget_row_is_total(row)
        ]
        if not total_rows:
            continue

        tables_checked += 1
        total_row_index, total_row = total_rows[-1]
        total_amount = _budget_row_amount(total_row, amount_col, unit)
        data_amounts = [
            _budget_row_amount(row, amount_col, unit)
            for row_index, row in enumerate(rows[header_index + 1 :], start=header_index + 2)
            if row_index != total_row_index and not _budget_row_is_total(row)
        ]
        data_amounts = [amount for amount in data_amounts if amount is not None]
        evidence_line = f"{location}行{total_row_index}：{_budget_row_preview(total_row)}"

        if total_amount is not None:
            detail_total_candidates.append(
                {
                    "line": evidence_line,
                    "amount_wan": total_amount,
                    "source": location,
                    "kind": "detail_table_total",
                }
            )
            evidence_examples.append(evidence_line)

        if total_amount is not None and data_amounts and _budget_table_allows_simple_sum(rows, header_index, total_row_index):
            table_internal_checks += 1
            expected = round(sum(data_amounts), 4)
            if not _amounts_close(expected, total_amount):
                arithmetic_issues.append(
                    RuleIssue(
                        message=(
                            "预算明细表合计行内部加总不一致："
                            f"明细金额合计为 {_format_amount(expected)}，"
                            f"合计行金额为 {_format_amount(total_amount)}。"
                        ),
                        evidence=evidence_line,
                        section=location,
                    )
                )

    return {
        "detail_total_candidates": detail_total_candidates,
        "arithmetic_issues": arithmetic_issues,
        "tables_checked": tables_checked,
        "evidence_examples": _dedupe_preserve_order(evidence_examples),
        "table_internal_checks": table_internal_checks,
        "summary_internal_checks": summary_internal_checks,
    }


def _check_investment_summary_table(rows: list[list[str]], location: str) -> dict[str, Any]:
    amount_col = _numeric_column_with_most_values(rows[1:])
    if amount_col is None:
        return {"detail_total_candidates": [], "arithmetic_issues": [], "evidence_examples": [], "summary_internal_checks": 0}

    system_row = _find_budget_amount_row_any(rows, ("系统建设费", "工程建设费", "建设费"), amount_col, "万元")
    other_row = _find_budget_amount_row_any(rows, ("其他费用", "工程建设其他费", "其他费"), amount_col, "万元")
    total_row = _find_budget_amount_row_any(rows, ("总计", "总投资估算", "项目总投资", "投资估算合计", "合计"), amount_col, "万元")
    candidates: list[dict[str, Any]] = []
    issues: list[RuleIssue] = []
    evidence: list[str] = []
    summary_internal_checks = 0

    if total_row:
        total_index, total_cells = total_row
        total_amount = _budget_row_amount(total_cells, amount_col, "万元")
        line = f"{location}行{total_index}：{_budget_row_preview(total_cells)}"
        if total_amount is not None:
            candidates.append({"line": line, "amount_wan": total_amount, "source": location, "kind": "investment_summary_total"})
            evidence.append(line)

        if system_row and other_row and total_amount is not None:
            system_amount = _budget_row_amount(system_row[1], amount_col, "万元")
            other_amount = _budget_row_amount(other_row[1], amount_col, "万元")
            if system_amount is not None and other_amount is not None:
                summary_internal_checks += 1
                expected = round(system_amount + other_amount, 4)
                if not _amounts_close(expected, total_amount):
                    issues.append(
                        RuleIssue(
                            message=(
                                "投资估算总表总计与一级费用合计不一致："
                                f"建设费+其他费用为 {_format_amount(expected)}，"
                                f"总投资为 {_format_amount(total_amount)}。"
                            ),
                            evidence=" | ".join(
                                [
                                    f"{location}行{system_row[0]}：{_budget_row_preview(system_row[1])}",
                                    f"{location}行{other_row[0]}：{_budget_row_preview(other_row[1])}",
                                    line,
                                ]
                            ),
                            section=location,
                        )
                    )

    hierarchy_result = _check_hierarchical_budget_rows(rows, amount_col, "万元", location)
    issues.extend(hierarchy_result["arithmetic_issues"])
    evidence.extend(hierarchy_result["evidence_examples"])
    summary_internal_checks += hierarchy_result["checks"]

    return {
        "detail_total_candidates": candidates,
        "arithmetic_issues": issues,
        "evidence_examples": evidence,
        "summary_internal_checks": summary_internal_checks,
    }


def _looks_like_budget_table(text: str) -> bool:
    return _contains_any(
        text,
        (
            "投资估算",
            "预算",
            "总金额",
            "申报金额",
            "总价",
            "单价",
            "工作量",
            "系统建设费",
            "其他费用",
        ),
    )


def _budget_table_allows_simple_sum(rows: list[list[str]], header_index: int, total_row_index: int) -> bool:
    if total_row_index <= header_index + 1:
        return False

    body_rows = rows[header_index + 1 : total_row_index - 1]
    if not body_rows:
        return False

    return True


def _budget_amount_column(rows: list[list[str]]) -> tuple[int, int, str] | None:
    for row_index, row in enumerate(rows[:8]):
        for col_index, cell in enumerate(row):
            compact = re.sub(r"\s+", "", cell)
            if any(keyword in compact for keyword in ("总金额", "申报金额", "总价", "合价")):
                unit = "元" if "元" in compact and "万元" not in compact else "万元"
                return row_index, col_index, unit
    return None


def _numeric_column_with_most_values(rows: list[list[str]]) -> int | None:
    max_cols = max((len(row) for row in rows), default=0)
    best_col: int | None = None
    best_count = 0
    for col in range(max_cols):
        count = 0
        for row in rows:
            if col < len(row) and _parse_budget_number(row[col]) is not None:
                count += 1
        if count > best_count:
            best_col = col
            best_count = count
    return best_col if best_count else None


def _find_budget_row(rows: list[list[str]], keyword: str) -> tuple[int, list[str]] | None:
    for row_index, row in enumerate(rows, start=1):
        row_text = " ".join(row)
        if keyword in row_text or _contains_fuzzy_keyword(row_text, (keyword,), threshold=0.75):
            return row_index, row
    return None


def _find_budget_row_any(rows: list[list[str]], keywords: Iterable[str]) -> tuple[int, list[str]] | None:
    for keyword in keywords:
        row = _find_budget_row(rows, keyword)
        if row:
            return row
    return None


def _find_budget_amount_row_any(
    rows: list[list[str]],
    keywords: Iterable[str],
    amount_col: int,
    unit: str,
) -> tuple[int, list[str]] | None:
    for keyword in keywords:
        for row_index, row in enumerate(rows, start=1):
            row_text = " ".join(row)
            if keyword in row_text or _contains_fuzzy_keyword(row_text, (keyword,), threshold=0.75):
                if _budget_row_amount(row, amount_col, unit) is not None:
                    return row_index, row
    return None


def _check_hierarchical_budget_rows(
    rows: list[list[str]],
    amount_col: int,
    unit: str,
    location: str,
) -> dict[str, Any]:
    issues: list[RuleIssue] = []
    evidence: list[str] = []
    checks = 0
    parsed_rows: list[dict[str, Any]] = []

    for row_index, row in enumerate(rows, start=1):
        level = _budget_hierarchy_level(row)
        amount = _budget_row_amount(row, amount_col, unit)
        parsed_rows.append({"row_index": row_index, "row": row, "level": level, "amount": amount})

    for index, current in enumerate(parsed_rows):
        current_level = current["level"]
        current_amount = current["amount"]
        if current_level is None or current_amount is None:
            continue

        descendants: list[dict[str, Any]] = []
        for candidate in parsed_rows[index + 1 :]:
            candidate_level = candidate["level"]
            if candidate_level is None:
                continue
            if candidate_level <= current_level:
                break
            descendants.append(candidate)

        if not descendants:
            continue
        direct_level = min(item["level"] for item in descendants if item["level"] is not None)
        direct_amounts = [
            item["amount"]
            for item in descendants
            if item["level"] == direct_level and item["amount"] is not None
        ]
        if len(direct_amounts) < 2:
            continue

        checks += 1
        expected = round(sum(direct_amounts), 4)
        if not _amounts_close(expected, current_amount):
            evidence_line = f"{location}行{current['row_index']}：{_budget_row_preview(current['row'])}"
            issues.append(
                RuleIssue(
                    message=(
                        "投资估算表层级加总不一致："
                        f"下级分项合计为 {_format_amount(expected)}，"
                        f"本级金额为 {_format_amount(current_amount)}。"
                    ),
                    evidence=evidence_line,
                    section=location,
                )
            )
            evidence.append(evidence_line)

    return {"arithmetic_issues": issues, "evidence_examples": evidence, "checks": checks}


def _budget_hierarchy_level(row: list[str]) -> int | None:
    first_cell = re.sub(r"\s+", "", row[0] if row else "")
    if not first_cell:
        return None

    chinese_number = r"一二三四五六七八九十"
    if re.match(rf"^[{chinese_number}]+(?:[、.．]|$)", first_cell):
        return 1
    if re.match(rf"^[（(][{chinese_number}]+[）)]", first_cell):
        return 2

    number_match = re.match(r"^(\d+(?:\.\d+)*)", first_cell)
    if number_match:
        token = number_match.group(1)
        return 3 + token.count(".")

    return None


def _budget_row_is_total(row: list[str]) -> bool:
    leading = " ".join(row[:3])
    return "合计" in leading or "总计" in leading or "总投资估算" in leading or "项目总投资" in leading


def _budget_row_amount(row: list[str], amount_col: int, unit: str) -> float | None:
    amount = _parse_budget_number(row[amount_col] if amount_col < len(row) else "")
    if amount is None:
        numeric_values = [_parse_budget_number(cell) for cell in row]
        numeric_values = [value for value in numeric_values if value is not None]
        amount = numeric_values[-1] if numeric_values else None
    if amount is None:
        return None
    return round(amount / 10000, 4) if unit == "元" else round(amount, 4)


def _parse_budget_number(value: str) -> float | None:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    match = re.fullmatch(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return float(text)


def _clean_budget_cell(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\r", "\n").replace("\n", " ")).strip()


def _budget_row_preview(row: list[str]) -> str:
    return " | ".join(cell for cell in row if cell)[:260]


def _distinct_budget_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, float]] = set()
    for candidate in candidates:
        line = str(candidate.get("line") or "")
        amount = round(float(candidate.get("amount_wan") or 0), 4)
        key = (line, amount)
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return output



def _extract_indicator_quantity_source(
    document: DocumentContent,
    content: bytes | None = None,
    filename: str | None = None,
) -> tuple[list[str], list[list[str]], str]:
    actual_filename = filename or document.filename
    if content and actual_filename.lower().endswith(".docx"):
        docx_lines, docx_table_rows = _extract_docx_2_6_indicator_source(content)
        if docx_lines or docx_table_rows:
            return docx_lines, docx_table_rows, "docx_2_6_section"

    raw_lines = _extract_text_2_6_section_lines(_meaningful_lines(document.raw_text))
    if raw_lines:
        return raw_lines, [], "raw_text_2_6_section"

    return [], [], "2_6_section_not_found"


def _extract_docx_2_6_indicator_source(content: bytes) -> tuple[list[str], list[list[str]]]:
    try:
        from docx import Document
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except Exception:
        return [], []

    try:
        doc = Document(BytesIO(content))
    except Exception:
        return [], []

    candidates: list[tuple[list[str], list[list[str]]]] = []
    current_lines: list[str] | None = None
    current_table_rows: list[list[str]] = []

    def close_current() -> None:
        nonlocal current_lines, current_table_rows
        if current_lines is not None:
            candidates.append((current_lines, current_table_rows))
        current_lines = None
        current_table_rows = []

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            text = Paragraph(child, doc).text.strip()
            if not text:
                continue
            if _is_2_6_indicator_heading(text):
                close_current()
                current_lines = [text]
                current_table_rows = []
                continue
            if current_lines is not None and _is_after_2_6_heading(text):
                close_current()
                continue
            if current_lines is not None:
                current_lines.append(text)
        elif isinstance(child, CT_Tbl) and current_lines is not None:
            table = Table(child, doc)
            for row in table.rows:
                cells = [
                    _clean_indicator_cell(cell.text)
                    for cell in row.cells
                    if cell.text and cell.text.strip()
                ]
                if not cells:
                    continue
                current_table_rows.append(cells)
                current_lines.append(_indicator_cells_to_line(cells))

    close_current()
    return _select_best_indicator_candidate(candidates)


def _select_best_indicator_candidate(
    candidates: list[tuple[list[str], list[list[str]]]]
) -> tuple[list[str], list[list[str]]]:
    if not candidates:
        return [], []

    def score(candidate: tuple[list[str], list[list[str]]]) -> tuple[int, int, int]:
        lines, rows = candidate
        table_indicator_rows = sum(1 for row in rows if _looks_like_indicator_table_data_cells(row))
        indicator_line_count = sum(1 for line in lines if _looks_like_indicator_line(line))
        return table_indicator_rows, indicator_line_count, len(lines)

    return max(candidates, key=score)


def _extract_text_2_6_section_lines(lines: list[str]) -> list[str]:
    selected: list[str] = []
    in_section = False

    for line in lines:
        if _is_2_6_indicator_heading(line):
            selected = [line]
            in_section = True
            continue
        if in_section and _is_after_2_6_heading(line):
            break
        if in_section:
            selected.append(line)

    return selected


def _is_2_6_indicator_heading(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if re.match(r"^2[\.．]6(?:[\.．、:：]|$)", compact):
        return True
    return "项目成效考核目标" in compact or compact.startswith("规划指标")


def _is_after_2_6_heading(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(
        re.match(r"^2[\.．](?:7|8|9|[1-9]\d)(?:[\.．、:：]|$)", compact)
        or re.match(r"^[3-9][\.．]\d*", compact)
    )


def _clean_indicator_cell(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\r", "\n").replace("\n", " ")).strip()


def _indicator_cells_to_line(cells: list[str]) -> str:
    return " | ".join(cell for cell in cells if cell)


def _count_indicator_table_rows(
    table_rows: list[list[str]],
) -> tuple[int, int, int, list[str]]:
    common_benefit_count = 0
    business_output_or_benefit_count = 0
    achievement_count = 0
    counted_lines: list[str] = []
    header_map: dict[str, int] = {}

    for cells in table_rows:
        normalized_cells = [_clean_indicator_cell(cell) for cell in cells if cell.strip()]
        if not normalized_cells or _is_indicator_table_title_row(normalized_cells):
            continue
        current_header_map = _indicator_table_header_map(normalized_cells)
        if current_header_map:
            header_map = current_header_map
            continue
        if not _looks_like_indicator_table_data_cells(normalized_cells):
            continue

        line = _indicator_cells_to_line(normalized_cells)
        scope = _detect_indicator_table_scope(normalized_cells, header_map)
        indicator_type = _detect_indicator_table_type(normalized_cells, header_map)
        if not scope or not indicator_type:
            continue

        counted = False
        if scope == "common" and indicator_type == "benefit":
            common_benefit_count += 1
            counted = True
        if scope == "business" and indicator_type in {"output", "benefit"}:
            business_output_or_benefit_count += 1
            counted = True
        if _contains_intelligent_keyword(line) and indicator_type in {"output", "benefit"}:
            achievement_count += 1
            counted = True
        if counted:
            counted_lines.append(line)

    return (
        common_benefit_count,
        business_output_or_benefit_count,
        achievement_count,
        _dedupe_preserve_order(counted_lines),
    )


def _indicator_table_header_map(cells: list[str]) -> dict[str, int]:
    header_map: dict[str, int] = {}
    for index, cell in enumerate(cells):
        compact = re.sub(r"\s+", "", cell)
        if "一级指标" in compact:
            header_map["scope"] = index
        elif "二级指标" in compact:
            header_map["type"] = index
        elif "三级指标" in compact:
            header_map["level3"] = index
        elif "四级指标" in compact or "指标名称" in compact:
            header_map["name"] = index
        elif "指标值" in compact or "目标值" in compact:
            header_map["value"] = index
    if {"scope", "type"} <= set(header_map):
        return header_map
    return {}


def _is_indicator_table_title_row(cells: list[str]) -> bool:
    unique_cells = {re.sub(r"\s+", "", cell) for cell in cells if cell}
    return len(unique_cells) == 1 and any(
        keyword in next(iter(unique_cells), "")
        for keyword in ("规划指标参数", "项目成效考核目标", "绩效目标")
    )


def _looks_like_indicator_table_data_cells(cells: list[str]) -> bool:
    compact_line = re.sub(r"\s+", "", _indicator_cells_to_line(cells))
    if not compact_line or _indicator_table_header_map(cells):
        return False
    if "序号" in compact_line and ("一级指标" in compact_line or "二级指标" in compact_line):
        return False
    return bool(_detect_indicator_scope(compact_line) and _detect_indicator_type(compact_line))


def _detect_indicator_table_scope(cells: list[str], header_map: dict[str, int]) -> str | None:
    scope_cell = _get_indicator_table_cell(cells, header_map, "scope", 1)
    return _detect_indicator_scope(scope_cell) or _detect_indicator_scope(_indicator_cells_to_line(cells))


def _detect_indicator_table_type(cells: list[str], header_map: dict[str, int]) -> str | None:
    type_cell = _get_indicator_table_cell(cells, header_map, "type", 2)
    return _detect_indicator_type(type_cell) or _detect_indicator_type(_indicator_cells_to_line(cells))


def _get_indicator_table_cell(
    cells: list[str],
    header_map: dict[str, int],
    key: str,
    fallback_index: int,
) -> str:
    index = header_map.get(key, fallback_index)
    if 0 <= index < len(cells):
        return cells[index]
    return ""


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
