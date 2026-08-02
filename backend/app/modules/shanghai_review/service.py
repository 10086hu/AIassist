# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.modules.shanghai_review import BaseValidator, ConstructionBasisValidator


BASIS_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "BASIS_007",
        "excel_row": 7,
        "rule_name": "投资总额一致性校验规则",
        "rule_category": "一致性校验规则",
        "rule_description": "全文项目总投资应保持一致",
        "judgement_condition": "2.7投资概况与8.2预算编制说明应一致",
        "error_prefixes": ("ICC-",),
        "default_suggestion": "请保持2.7投资概况与8.2预算编制说明中的投资金额、费用明细和二类费用测算口径一致。",
    },
    {
        "rule_id": "BASIS_009",
        "excel_row": 9,
        "rule_name": "指标一致性校验规则",
        "rule_category": "一致性校验规则",
        "rule_description": "本项目验收的指标应与规划指标相一致",
        "judgement_condition": "4.2建设目标需求分析中的本项目验收标准、具体指标及目标值应使用2.6规划指标的绩效目标来罗列",
        "error_prefixes": ("IDC-",),
        "default_suggestion": "请核对2.6规划指标与4.2验收标准，确保指标名称、目标值和验收口径保持一致。",
    },
    {
        "rule_id": "BASIS_012",
        "excel_row": 12,
        "rule_name": "功能模块一致性校验规则",
        "rule_category": "一致性校验规则",
        "rule_description": "功能模块需与预算投资表中的内容做到有效对应",
        "judgement_condition": "6.1与8.2和8.3和8.4的内容一一对应，6.1的三级标题及编号与8.2的软件开发功能清单表中的功能点及编号一一对应",
        "error_prefixes": ("MBC-",),
        "default_suggestion": "请统一6.1建设内容与8.2软件开发功能清单中的功能模块编号和名称。",
    },
    {
        "rule_id": "BASIS_014",
        "excel_row": 14,
        "rule_name": "安全内容一致性校验规则",
        "rule_category": "一致性校验规则",
        "rule_description": "安全需求分析与安全建设内容应对应",
        "judgement_condition": "4.7安全需求分析与6.6安全建设内容应关联对应，项目的系统安全等级目标的定位描述全文一致",
        "error_prefixes": ("SCC-",),
        "default_suggestion": "请核对4.7安全需求分析与6.6安全建设内容，保持等保等级、风险类别和安全措施对应。",
    },
)


def run_basis_check_from_document(
    content: bytes,
    filename: str,
    project_name: str = "未命名可研项目",
    selected_rule_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run rule rows 7, 9, 12 and 14 for construction basis review."""
    document = BaseValidator.parse_document(content, filename)
    if not str(document.get("_full_text") or "").strip():
        raise ValueError("未能从文档中提取到有效文本，请确认文件不是扫描件或加密文件")

    validation = ConstructionBasisValidator().validate(document)
    grouped_errors = _group_errors_by_rule(validation.errors)
    selected = {str(item).strip() for item in selected_rule_ids or [] if str(item).strip()}

    results: list[dict[str, Any]] = []
    for rule in BASIS_RULES:
        if selected and not _rule_selected(rule, selected):
            continue
        issues = grouped_errors.get(rule["rule_id"], [])
        severity = _highest_severity([str(issue.get("severity") or "warning") for issue in issues])
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
                "severity": "pass" if not issues else severity,
                "summary": "未发现问题。" if not issues else f"发现 {len(issues)} 项需复核内容。",
                "metrics": {"error_count": len(issues), "source_excel_row": rule["excel_row"]},
                "issues": issues,
                "suggestions": suggestions,
            }
        )

    passed_count = sum(1 for item in results if item.get("passed"))
    return {
        "project_name": project_name,
        "filename": filename,
        "document_format": Path(filename).suffix.lower().lstrip("."),
        "extracted_characters": len(str(document.get("_full_text") or "")),
        "sections_found": sorted(key for key in document if not key.startswith("_") and len(key) < 30),
        "status": "通过" if passed_count == len(results) else "发现问题",
        "summary": f"建设依据审查完成：{passed_count}/{len(results)} 项通过。",
        "results": results,
    }


def _group_errors_by_rule(errors: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped = {str(rule["rule_id"]): [] for rule in BASIS_RULES}
    for error in errors:
        rule = _rule_for_error(str(getattr(error, "code", "")))
        if rule is None:
            continue
        grouped[str(rule["rule_id"])].append(
            {
                "message": str(getattr(error, "message", "")),
                "section": str(getattr(error, "section", "")),
                "evidence": str(getattr(error, "section", "")),
                "severity": str(getattr(error, "severity", "warning")),
                "suggestion": getattr(error, "suggestion", None),
            }
        )
    return grouped


def _rule_for_error(code: str) -> dict[str, Any] | None:
    for rule in BASIS_RULES:
        if any(code.startswith(prefix) for prefix in rule["error_prefixes"]):
            return rule
    return None


def _rule_selected(rule: dict[str, Any], selected: set[str]) -> bool:
    values = {
        str(rule["rule_id"]),
        str(rule["excel_row"]),
        str(rule["rule_name"]),
        f"名称：{rule['rule_name']}",
    }
    return bool(values & selected)


def _highest_severity(values: list[str]) -> str:
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