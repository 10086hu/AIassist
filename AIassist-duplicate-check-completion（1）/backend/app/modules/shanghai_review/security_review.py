# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Iterable

from app.modules.shanghai_review.base import BaseValidator
from app.services.llm_client import get_llm_model, review_security_document_with_llm


SECURITY_RULE = {
    "rule_id": "SECURITY_REASON_001",
    "rule_name": "安全需求分析合规性审查规则",
    "rule_category": "内容合规性审查规则",
    "rule_detail": "适用范围：市级项目。分析系统的安全风险，对信息系统安全等级给予准确定位，描述系统关于数据的相关安全要求，包括数据分类分级和所需的安全防护措施及密码应用措施。判断条件：4.7安全需求分析是否满足要求。",
    "source": "local_builtin",
}

SECURITY_REQUIRED_POINTS: dict[str, frozenset[str]] = {
    "安全风险分析": frozenset({"风险", "威胁", "漏洞", "隐患", "攻击", "风险评估", "威胁建模"}),
    "数据分类分级": frozenset({"分类分级", "数据分类", "数据分级", "敏感数据", "重要数据", "一般数据"}),
    "安全防护措施": frozenset(
        {
            "访问控制",
            "身份认证",
            "权限",
            "授权",
            "加密",
            "防火墙",
            "审计",
            "日志",
            "备份",
            "恢复",
            "隔离",
            "安全防护",
            "终端安全",
            "漏洞扫描",
        }
    ),
    "密码应用措施": frozenset({"密码", "商用密码", "国密", "SM2", "SM3", "SM4", "密码应用", "密评"}),
}

SECURITY_LEVEL_PATTERNS = (
    re.compile(r"(?:等级保护|等保|安全等级|安全保护等级)\s*(?:要求|为|：|:|达到|定为|按)?\s*([一二三四五])级"),
    re.compile(r"(?:网络安全等级|信息系统安全等级|安全保护等级)\s*[第等]?\s*([2-5])\s*级"),
    re.compile(r"(?:二级|三级|四级|五级)\s*(?:等保|等级保护|安全)"),
)

LEVEL_TEXT_TO_INT = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "二级": 2, "三级": 3, "四级": 4, "五级": 5}


def run_security_check_from_document(
    content: bytes,
    filename: str,
    project_name: str = "未命名可研项目",
    selected_rule_ids: list[str] | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    document = BaseValidator.parse_document(content, filename)
    full_text = str(document.get("_full_text") or "")
    if not full_text.strip():
        raise ValueError("未能从文档中提取到有效文本，请确认文件不是扫描件或加密文件")

    if selected_rule_ids:
        selected = {str(item).strip() for item in selected_rule_ids if str(item).strip()}
        if not _rule_selected(SECURITY_RULE, selected):
            return _build_result(
                packet=_build_security_packet(document, filename, project_name),
                review={
                    "passed": True,
                    "judgement": "通过",
                    "risk_level_suggestion": "通过",
                    "issue_type": "已跳过",
                    "user_reason": "当前未勾选安全内容合理性规则，已跳过该项检查。",
                    "user_suggestion": "",
                    "user_basis": "",
                    "sections_used": [],
                    "missing_points": [],
                    "matched_points": [],
                    "need_human_review": False,
                    "confidence": 1.0,
                    "llm_available": False,
                    "model": get_llm_model(),
                    "skipped": True,
                },
            )

    packet = _build_security_packet(document, filename, project_name)
    if use_llm:
        review = review_security_document_with_llm(SECURITY_RULE, packet)
        if review.get("llm_available") is not True:
            review = dict(packet.get("local_review") or {})
            review["llm_available"] = False
            review["llm_error"] = str(review.get("llm_error") or "missing_api_key")
            review["llm_error_type"] = str(review.get("llm_error_type") or review.get("llm_error") or "missing_api_key")
            review["model"] = get_llm_model()
    else:
        review = dict(packet.get("local_review") or {})
        review["llm_available"] = False
        review["llm_error"] = "llm_disabled"
        review["llm_error_type"] = "llm_disabled"
        review["llm_disabled"] = True
        review["model"] = get_llm_model()

    return _build_result(packet=packet, review=review)


def _build_security_packet(document: dict[str, Any], filename: str, project_name: str) -> dict[str, Any]:
    section_4_7 = _first_section_text(document, "4.7", "安全需求分析")
    section_6_6 = _first_section_text(document, "6.6", "安全建设内容")
    level_4_7 = _extract_security_level(section_4_7)
    level_6_6 = _extract_security_level(section_6_6)
    coverage_4_7 = sorted(_match_security_points(section_4_7))
    coverage_6_6 = sorted(_match_security_points(section_6_6))
    matched_points = _coverage_points(level_4_7, coverage_4_7)
    missing_points = [point for point in ("安全风险分析", "安全等级定位", "数据分类分级", "安全防护措施", "密码应用措施") if point not in matched_points]
    level_conflict = bool(level_4_7 is not None and level_6_6 is not None and level_4_7 != level_6_6)
    level_text_4_7 = _level_text(level_4_7)
    level_text_6_6 = _level_text(level_6_6)

    packet = {
        "project_name": project_name,
        "filename": filename,
        "section_4_7_present": bool(section_4_7.strip()),
        "section_6_6_present": bool(section_6_6.strip()),
        "section_4_7_heading": _heading_from_section(section_4_7, "4.7安全需求分析"),
        "section_6_6_heading": _heading_from_section(section_6_6, "6.6安全建设内容"),
        "section_4_7_excerpt": _excerpt(section_4_7),
        "section_6_6_excerpt": _excerpt(section_6_6),
        "security_level_4_7": level_text_4_7,
        "security_level_6_6": level_text_6_6,
        "coverage_4_7": coverage_4_7,
        "coverage_6_6": coverage_6_6,
        "matched_points": matched_points,
        "missing_points": missing_points,
        "sections_used": [section for section, text in (("4.7", section_4_7), ("6.6", section_6_6)) if str(text or "").strip()],
        "level_conflict": level_conflict,
        "evidence_summary": _evidence_summary(section_4_7, section_6_6, level_text_4_7, level_text_6_6, matched_points, missing_points),
        "local_issue_type": _local_issue_type(section_4_7, level_conflict, missing_points),
        "local_reason": _local_reason(section_4_7, level_text_4_7, level_text_6_6, matched_points, missing_points, level_conflict),
        "local_suggestion": _local_suggestion(missing_points, level_conflict),
        "local_passed": bool(section_4_7.strip()) and not level_conflict and not missing_points,
    }
    packet["local_review"] = {
        "passed": packet["local_passed"],
        "judgement": "通过" if packet["local_passed"] else "不通过",
        "risk_level_suggestion": "通过" if packet["local_passed"] else _local_risk_level(section_4_7, level_conflict, missing_points),
        "issue_type": packet["local_issue_type"],
        "user_reason": packet["local_reason"],
        "user_suggestion": packet["local_suggestion"],
        "user_basis": packet["evidence_summary"],
        "sections_used": packet["sections_used"],
        "missing_points": missing_points,
        "matched_points": matched_points,
        "need_human_review": not packet["local_passed"],
        "confidence": 0.72 if packet["local_passed"] else 0.84,
        "llm_available": False,
        "model": get_llm_model(),
    }
    return packet


def _build_result(packet: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    passed = _review_passed(review)
    model = str(review.get("model") or get_llm_model())
    findings: list[dict[str, Any]] = []
    if not passed:
        findings.append(
            {
                "display_title": SECURITY_RULE["rule_name"],
                "risk_level": _normalize_risk_level(review.get("risk_level_suggestion") or ("高" if review.get("issue_type") == "章节缺失" else "需人工确认")),
                "review_opinion": str(review.get("user_reason") or packet.get("local_reason") or "安全需求分析存在需复核事项。"),
                "evidence_summary": str(review.get("user_basis") or packet.get("evidence_summary") or ""),
                "revision_advice": str(review.get("user_suggestion") or packet.get("local_suggestion") or "请补充安全风险分析、安全等级定位、数据分类分级、安全防护措施和密码应用措施。"),
                "rule_basis": SECURITY_RULE["rule_name"],
                "rule_id": SECURITY_RULE["rule_id"],
                "rule_name": SECURITY_RULE["rule_name"],
                "rule_category": SECURITY_RULE["rule_category"],
                "source_section": "4.7安全需求分析",
                "evidence_examples": [item for item in [packet.get("section_4_7_excerpt"), packet.get("section_6_6_excerpt")] if item],
                "merged_count": 1,
                "llm_refined": bool(review.get("llm_available")),
                "llm_review": review,
                "raw_issue_type": review.get("issue_type") or packet.get("local_issue_type"),
            }
        )

    summary = {
        "total_findings": len(findings),
        "risk_count": _risk_count(findings),
        "llm_enabled": bool(review.get("llm_available")) and not bool(review.get("skipped")),
        "llm_model": model,
        "llm_reviewed_count": 1 if review.get("llm_available") else 0,
        "llm_error_count": 0 if review.get("llm_available") or review.get("skipped") or review.get("llm_disabled") else 1,
        "llm_status": (
            "skipped"
            if review.get("skipped")
            else "disabled"
            if review.get("llm_disabled")
            else ("completed" if review.get("llm_available") else "fallback")
        ),
        "section_4_7_present": packet.get("section_4_7_present"),
        "section_6_6_present": packet.get("section_6_6_present"),
        "section_4_7_level": packet.get("security_level_4_7") or "",
        "section_6_6_level": packet.get("security_level_6_6") or "",
        "coverage_4_7": packet.get("coverage_4_7") or [],
        "coverage_6_6": packet.get("coverage_6_6") or [],
        "missing_points": packet.get("missing_points") or [],
        "matched_points": packet.get("matched_points") or [],
        "rule_source": "deepseek" if review.get("llm_available") else ("skipped" if review.get("skipped") else "local_fallback"),
    }
    return {
        "module_code": "security",
        "module_name": "安全内容的合理性",
        "status": "通过" if not findings else "发现问题",
        "summary": summary,
        "findings": findings,
        "rules_used": [_rule_to_public_dict(SECURITY_RULE)],
        "raw_result": {
            "packet": packet,
            "llm_review": review,
        },
        "model_name": model,
    }


def _review_passed(review: dict[str, Any]) -> bool:
    passed = review.get("passed")
    if isinstance(passed, bool):
        return passed
    if isinstance(passed, str):
        return passed.strip().lower() in {"true", "1", "yes", "pass", "passed"}
    judgement = str(review.get("judgement") or "").strip()
    if judgement in {"通过", "合规", "满足要求"}:
        return True
    risk = str(review.get("risk_level_suggestion") or "").strip()
    return risk in {"通过", "低"}


def _local_issue_type(section_4_7: str, level_conflict: bool, missing_points: list[str]) -> str:
    if not section_4_7.strip():
        return "章节缺失"
    if level_conflict:
        return "安全等级不一致"
    if missing_points:
        return "、".join(missing_points)
    return "通过"


def _local_reason(
    section_4_7: str,
    level_4_7: str,
    level_6_6: str,
    matched_points: list[str],
    missing_points: list[str],
    level_conflict: bool,
) -> str:
    if not section_4_7.strip():
        return "未找到4.7安全需求分析章节，无法判断系统安全风险、等级定位及安全措施是否满足要求。"
    parts = []
    if matched_points:
        parts.append(f"4.7已覆盖{_join_points(matched_points)}")
    if level_conflict:
        parts.append(f"4.7安全等级为{level_4_7}，6.6安全等级为{level_6_6}，两处表述不一致")
    if missing_points:
        parts.append(f"仍缺少{_join_points(missing_points)}")
    if not parts:
        return "4.7安全需求分析已明确安全风险、等级定位和关键安全要求，整体满足审查要求。"
    return "；".join(parts) + "。"


def _local_suggestion(missing_points: list[str], level_conflict: bool) -> str:
    if level_conflict:
        return "请统一4.7与6.6中的安全保护等级表述，并补充等级确定依据。"
    if not missing_points:
        return "建议保留现有表述，并确认安全等级、分类分级和密码应用措施在前后章节保持一致。"
    return f"建议在4.7中补充{_join_points(missing_points)}，并用具体措施说明如何落实安全需求。"


def _local_risk_level(section_4_7: str, level_conflict: bool, missing_points: list[str]) -> str:
    if not section_4_7.strip() or level_conflict:
        return "高"
    if len(missing_points) >= 2:
        return "高"
    if missing_points:
        return "中"
    return "低"


def _evidence_summary(
    section_4_7: str,
    section_6_6: str,
    level_4_7: str,
    level_6_6: str,
    matched_points: list[str],
    missing_points: list[str],
) -> str:
    parts = []
    if section_4_7.strip():
        parts.append(f"4.7：{_excerpt(section_4_7, 160)}")
    if section_6_6.strip():
        parts.append(f"6.6：{_excerpt(section_6_6, 160)}")
    if level_4_7 or level_6_6:
        parts.append(f"安全等级：4.7={level_4_7 or '未识别'}，6.6={level_6_6 or '未识别'}")
    if matched_points:
        parts.append(f"已识别：{_join_points(matched_points)}")
    if missing_points:
        parts.append(f"待补充：{_join_points(missing_points)}")
    return "；".join(parts)


def _first_section_text(document: dict[str, Any], key: str, alias: str) -> str:
    for item in (key, alias):
        value = document.get(item)
        if value is not None:
            return _section_to_text(value)
    return ""


def _section_to_text(section: Any) -> str:
    if isinstance(section, str):
        return section
    if isinstance(section, list):
        return "\n".join(str(item) for item in section if str(item).strip())
    if isinstance(section, dict):
        parts: list[str] = []
        for value in section.values():
            if isinstance(value, list):
                parts.extend(str(item) for item in value if str(item).strip())
            elif str(value or "").strip():
                parts.append(str(value))
        return "\n".join(parts)
    return str(section or "")


def _extract_security_level(text: str) -> int | None:
    for pattern in SECURITY_LEVEL_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        raw = match.group(1)
        if raw in LEVEL_TEXT_TO_INT:
            return LEVEL_TEXT_TO_INT[raw]
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def _match_security_points(text: str) -> set[str]:
    matched: set[str] = set()
    lowered = text.lower()
    for name, keywords in SECURITY_REQUIRED_POINTS.items():
        if any(keyword.lower() in lowered for keyword in keywords):
            matched.add(name)
    return matched


def _coverage_points(level_4_7: int | None, coverage_4_7: list[str]) -> list[str]:
    points = list(coverage_4_7)
    if level_4_7 is not None:
        points.append("安全等级定位")
    return points


def _heading_from_section(text: str, fallback: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_line:
        return fallback
    return first_line[:80]


def _excerpt(text: str, max_chars: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rstrip() + "..."


def _level_text(level: int | None) -> str:
    if level is None:
        return ""
    return f"等保{level}级"


def _normalize_risk_level(value: Any) -> str:
    text = str(value or "").strip()
    mapping = {
        "pass": "通过",
        "passed": "通过",
        "通过": "通过",
        "low": "低",
        "low risk": "低",
        "低": "低",
        "medium": "中",
        "中": "中",
        "high": "高",
        "高": "高",
        "warning": "需人工确认",
        "需人工确认": "需人工确认",
        "需确认": "需人工确认",
    }
    return mapping.get(text, text or "需人工确认")


def _risk_count(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in findings:
        risk = _normalize_risk_level(item.get("risk_level") or "需人工确认")
        counts[risk] = counts.get(risk, 0) + 1
    return counts


def _rule_selected(rule: dict[str, Any], selected: set[str]) -> bool:
    values = {
        str(rule.get("rule_id") or ""),
        str(rule.get("rule_name") or ""),
        f"名称：{rule.get('rule_name') or ''}",
    }
    return bool(values & selected)


def _rule_to_public_dict(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": str(rule.get("rule_id") or ""),
        "rule_name": str(rule.get("rule_name") or ""),
        "rule_category": str(rule.get("rule_category") or ""),
        "rule_detail": str(rule.get("rule_detail") or ""),
        "source": str(rule.get("source") or "local_builtin"),
    }


def _join_points(points: Iterable[str]) -> str:
    items = [str(item).strip() for item in points if str(item).strip()]
    return "、".join(items)
