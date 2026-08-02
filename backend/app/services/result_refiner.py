from __future__ import annotations

import os
import time
from typing import Any

from app.services.llm_client import get_llm_model, review_finding_with_llm


LLM_MODULE_BUDGET_SECONDS = 60
LLM_ITEM_TIMEOUT_SECONDS = 15
LLM_GROUP_LIMIT = 5


def refine_findings(
    module_code: str,
    findings: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    use_llm: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    refined = [_template_refine(module_code, finding) for finding in findings]
    model = get_llm_model()
    meta: dict[str, Any] = {
        "llm_enabled": bool(use_llm and os.getenv("DEEPSEEK_API_KEY")),
        "llm_model": model,
        "llm_status": "disabled",
        "llm_reviewed_count": 0,
        "llm_error_count": 0,
        "llm_skipped_count": len(refined),
        "llm_display_mode": "business_review",
    }

    if not use_llm:
        return refined, meta
    if not os.getenv("DEEPSEEK_API_KEY"):
        meta["llm_error"] = "missing_api_key"
        return refined, meta

    rule_by_id = {
        str(rule.get("rule_id") or ""): rule
        for rule in rules
        if isinstance(rule, dict)
    }
    candidates = sorted(refined, key=_candidate_sort_key)[:LLM_GROUP_LIMIT]
    start = time.monotonic()
    error_count = 0
    reviewed_count = 0
    timed_out = False

    for finding in candidates:
        remaining = LLM_MODULE_BUDGET_SECONDS - (time.monotonic() - start)
        if remaining <= 1:
            timed_out = True
            break
        rule = rule_by_id.get(str(finding.get("rule_id") or ""), {})
        review = review_finding_with_llm(
            module_code=module_code,
            rule=rule,
            finding=_llm_group_payload(finding),
            context=_llm_group_context(finding),
            timeout=max(1, min(LLM_ITEM_TIMEOUT_SECONDS, int(remaining))),
        )
        if review.get("llm_available") is False:
            error_count += 1
            if str(review.get("llm_error_type") or "") == "timeout":
                timed_out = True
                break
            continue

        _apply_llm_review_to_business_finding(finding, review)
        reviewed_count += 1

    skipped_count = max(0, len(refined) - reviewed_count)
    if timed_out:
        status = "timeout" if reviewed_count == 0 else "partial"
    elif error_count:
        status = "partial" if reviewed_count else "timeout"
    else:
        status = "completed"

    meta.update(
        {
            "llm_status": status,
            "llm_reviewed_count": reviewed_count,
            "llm_error_count": error_count,
            "llm_skipped_count": skipped_count,
        }
    )
    return refined, meta


def document_metrics(report_file_path: str) -> dict[str, Any]:
    try:
        from app.modules.sensitive_word.sensitive_word_checker import parse_report

        segments = parse_report(report_file_path)
        texts = [str(getattr(segment, "text", "") or "") for segment in segments]
        sections = {
            str(getattr(segment, "section", "") or "")
            for segment in segments
            if str(getattr(segment, "section", "") or "").strip()
        }
        char_count = sum(len(text) for text in texts)
        paragraph_count = len([text for text in texts if text.strip()])
        chunk_count = max(1, (char_count + 3999) // 4000)
        return {
            "char_count": char_count,
            "paragraph_count": paragraph_count,
            "section_count": len(sections),
            "chunk_count": chunk_count,
            "large_document": char_count > 120000,
            "processing_mode": "chunked_async" if char_count > 120000 else "standard",
        }
    except Exception:
        return {
            "char_count": 0,
            "paragraph_count": 0,
            "section_count": 0,
            "chunk_count": 0,
            "large_document": False,
            "processing_mode": "standard",
        }


def _template_refine(module_code: str, finding: dict[str, Any]) -> dict[str, Any]:
    evidence_examples = _evidence_examples(finding)
    source_section = _first_text(
        finding.get("source_section"),
        finding.get("section"),
        "全文",
    )
    risk_level = _first_text(finding.get("risk_level"), "需人工确认")
    merged_count = _int_value(finding.get("merged_count"), 1)
    raw_issue_type = _first_text(finding.get("issue_type"), finding.get("matched_rule"), finding.get("hit_text"), "问题项")
    rule_basis = _first_text(finding.get("rule_name"), finding.get("matched_rule"), finding.get("rule_category"), "规则审查")

    if module_code == "basis":
        display_title = _first_text(finding.get("display_title"), raw_issue_type, "建设依据审查问题")
        review_opinion = _first_text(
            finding.get("review_opinion"),
            f"规则检查发现“{rule_basis}”存在需复核事项，可能影响可研报告前后章节依据、指标、投资或安全内容的一致性。",
        )
        evidence_summary = _first_text(
            finding.get("evidence_summary"),
            f"系统依据“{source_section}”及相关章节内容形成该项判断。",
        )
        revision_advice = _first_text(
            finding.get("revision_advice"),
            "请按规则要求核对对应章节，统一投资总额、规划指标、功能模块编号和安全内容表述。",
        )
    elif module_code == "data_reasonableness":
        display_title = _first_text(finding.get("display_title"), raw_issue_type, "数据填报合理性问题")
        review_opinion = _first_text(
            finding.get("review_opinion"),
            "规则检查发现预算金额、合计关系、指标数量或数据填报依据存在需复核事项。",
        )
        evidence_summary = _first_text(
            finding.get("evidence_summary"),
            "系统依据报告正文中的预算、指标或相关章节内容形成该项判断。",
        )
        revision_advice = _first_text(
            finding.get("revision_advice"),
            "请核对项目总投资、分项预算合计、成效指标数量和相关章节说明，确保数据口径一致且满足规则要求。",
        )
    elif module_code == "resource":
        display_title = _first_text(finding.get("display_title"), raw_issue_type, "资源申请合理性问题")
        review_opinion = _first_text(
            finding.get("review_opinion"),
            "规则检查发现资源申请数量、资源清单来源或关键配置关系存在需复核事项。",
        )
        evidence_summary = _first_text(
            finding.get("evidence_summary"),
            "系统依据资源清单中的服务名称、来源清单、规格和数量字段形成该项判断。",
        )
        revision_advice = _first_text(
            finding.get("revision_advice"),
            "请核对安全服务、PaaS、密码服务、服务器、操作系统、数据库服务器和数据库等资源数量是否一致。",
        )
    elif module_code == "sensitive_word":
        hit_text = _first_text(finding.get("hit_text"), raw_issue_type)
        scene = _first_text(finding.get("scene_type"), "需结合上下文核验")
        display_title = _sensitive_title(hit_text, risk_level)
        review_opinion = (
            f"报告中出现“{hit_text}”，该表述可能涉及非信创产品、限制性表述或需进一步说明的技术选型，"
            "建议结合项目建设目标、采购范围和信创要求进行核验。"
        )
        evidence_summary = f"命中内容位于“{source_section}”，上下文主要描述“{scene}”。"
        revision_advice = (
            "建议补充选型依据、国产化适配说明、替代路径或专家确认意见；"
            "如该表述仅为现状描述，应明确说明其不属于新增采购或建设内容。"
        )
    else:
        feature = _feature_name(finding)
        display_title = _function_title(raw_issue_type)
        review_opinion = (
            f"报告中提到了“{feature}”，但在建设内容、业务需求或投资预算中未看到充分对应说明，"
            "可能导致建设必要性和投入合理性支撑不足。"
        )
        evidence_summary = f"系统在“{source_section}”中发现相关功能表述，但未在前后章节识别到充分的需求来源或预算支撑。"
        revision_advice = (
            "建议在业务需求分析或建设内容章节中补充该功能点的建设依据、应用场景、服务对象和预期成效；"
            "如涉及预算，应同步说明其与投资项的对应关系。"
        )

    return {
        **finding,
        "display_title": display_title,
        "risk_level": risk_level,
        "review_opinion": review_opinion,
        "evidence_summary": evidence_summary,
        "revision_advice": revision_advice,
        "rule_basis": rule_basis,
        "source_section": source_section,
        "evidence_examples": evidence_examples,
        "merged_count": merged_count,
        "llm_refined": False,
        "raw_issue_type": raw_issue_type,
    }


def _llm_group_payload(finding: dict[str, Any]) -> dict[str, Any]:
    return {
        "description": finding.get("display_title"),
        "issue_type": finding.get("raw_issue_type"),
        "risk_level": finding.get("risk_level"),
        "rule_name": finding.get("rule_basis"),
        "source_section": finding.get("source_section"),
        "suggestion": finding.get("revision_advice"),
        "evidence_examples": finding.get("evidence_examples") or [],
        "merged_count": finding.get("merged_count"),
    }


def _llm_group_context(finding: dict[str, Any]) -> str:
    examples = finding.get("evidence_examples") or []
    text = "\n".join(str(item) for item in examples[:3])
    return _truncate(text, 800)


def _apply_llm_review_to_business_finding(finding: dict[str, Any], review: dict[str, Any]) -> None:
    opinion = _first_text(review.get("review_opinion"), review.get("user_reason"), review.get("reason"))
    advice = _first_text(review.get("revision_advice"), review.get("user_suggestion"), review.get("rewrite_suggestion"))
    basis = _first_text(review.get("evidence_summary"), review.get("user_basis"))
    title = _first_text(review.get("display_title"))

    if title:
        finding["display_title"] = _truncate(title, 80)
    if opinion:
        finding["review_opinion"] = _truncate(opinion, 260)
    if advice:
        finding["revision_advice"] = _truncate(advice, 260)
    if basis:
        finding["evidence_summary"] = _truncate(basis, 260)
    if review.get("risk_level_suggestion"):
        finding["llm_risk_level_suggestion"] = review["risk_level_suggestion"]
    finding["llm_refined"] = True
    finding["llm_review"] = review


def _candidate_sort_key(finding: dict[str, Any]) -> tuple[int, int]:
    return (_risk_rank(str(finding.get("risk_level") or "")), -_int_value(finding.get("merged_count"), 1))


def _risk_rank(risk_level: str) -> int:
    return {"高": 0, "中": 1, "需人工确认": 2, "需确认": 2, "低": 3}.get(risk_level, 4)


def _evidence_examples(finding: dict[str, Any]) -> list[str]:
    examples = finding.get("evidence_examples") or finding.get("context_examples") or []
    if not isinstance(examples, list):
        examples = [examples]
    if not examples:
        examples = [finding.get("evidence") or finding.get("context") or finding.get("source_section") or finding.get("section")]
    result: list[str] = []
    for item in examples:
        text = _truncate(item, 300)
        if text and text not in result:
            result.append(text)
        if len(result) >= 3:
            break
    return result


def _feature_name(finding: dict[str, Any]) -> str:
    text = _first_text(finding.get("description"), finding.get("feature"), finding.get("issue_type"), "相关功能点")
    for marker in ("“", "【"):
        if marker in text:
            end_marker = "”" if marker == "“" else "】"
            start = text.find(marker) + 1
            end = text.find(end_marker, start)
            if end > start:
                return _truncate(text[start:end], 40)
    return _truncate(text, 40)


def _function_title(issue_type: str) -> str:
    text = issue_type or ""
    if "预算" in text:
        return "建设内容与预算支撑关系不足"
    if "需求" in text:
        return "建设功能缺少需求依据"
    if "建设内容" in text:
        return "功能表述与建设内容对应不足"
    return "建设功能对应关系需补充说明"


def _sensitive_title(hit_text: str, risk_level: str) -> str:
    if risk_level in {"高", "中"}:
        return f"疑似敏感表述需核验：{_truncate(hit_text, 24)}"
    return f"需人工确认的表述：{_truncate(hit_text, 24)}"


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _truncate(value: Any, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
