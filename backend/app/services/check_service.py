from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from pathlib import Path
import os
import re
from typing import Any, Callable, Dict, Optional, Sequence

from app.core.config import settings
from app.modules.function_correspondence.construction_function_correspondence_checker import (
    check_construction_function_correspondence,
    load_rules as load_function_correspondence_rules,
)
from app.modules.sensitive_word.sensitive_word_checker import (
    DEFAULT_NON_XINCHUANG_TERMS,
    DEFAULT_RESTRICTIVE_PATTERNS,
    check_sensitive_words,
    load_rules_from_api as load_sensitive_word_rules_from_api,
    load_rules_from_xlsx as load_sensitive_word_rules_from_xlsx,
)
from app.modules.data_rules.service import DATA_REASONABLENESS_RULES
from app.modules.shanghai_review.service import BASIS_RULES
from app.services.llm_client import get_llm_model, review_finding_with_llm
from app.services.result_refiner import document_metrics, refine_findings


RuleRunner = Callable[[Optional[str]], Dict[str, Any]]


def run_function_correspondence_check(
    report_file_path: str,
    rule_source: Optional[str] = "api",
    project_level: str = "市级项目",
    use_llm: bool = False,
    selected_rule_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    rule_config = _resolve_rule_source(rule_source, "function_correspondence")

    def runner(rule_api_base: Optional[str]) -> Dict[str, Any]:
        return check_construction_function_correspondence(
            report_path=report_file_path,
            rules_xlsx_path=rule_config["rules_xlsx_path"],
            rule_api_base=rule_api_base,
            project_level=project_level,
            use_llm=False,
            deepseek_api_key=None,
            selected_rule_ids=selected_rule_ids,
        )

    raw = _run_with_rule_api_fallback(runner, rule_config)
    return _normalize_function_correspondence_result(raw, report_file_path=report_file_path, use_llm=use_llm)


def run_sensitive_word_check(
    report_file_path: str,
    rule_source: Optional[str] = "api",
    project_level: str = "通用",
    use_llm: bool = False,
    selected_rule_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    rule_config = _resolve_rule_source(rule_source, "sensitive_word")

    def runner(rule_api_url: Optional[str]) -> Dict[str, Any]:
        return check_sensitive_words(
            report_path=report_file_path,
            rules_xlsx_path=rule_config["rules_xlsx_path"],
            rule_api_url=rule_api_url,
            rule_api_keyword="敏感词",
            use_llm=False,
            project_level=project_level,
            selected_rule_ids=selected_rule_ids,
        )

    raw = _run_with_rule_api_fallback(runner, rule_config)
    return _normalize_sensitive_word_result(raw, report_file_path=report_file_path, use_llm=use_llm)


def list_check_rules(module: str, rule_source: Optional[str] = "api") -> Dict[str, Any]:
    module_code = module.strip().lower()
    if module_code == "basis":
        return {
            "module_code": "basis",
            "module_name": "建设依据审查",
            "source": "local_builtin",
            "rules": [_basis_rule_to_public_dict(rule) for rule in BASIS_RULES],
        }

    if module_code == "data_reasonableness":
        return {
            "module_code": "data_reasonableness",
            "module_name": "数据填报合理性检查",
            "source": "local_builtin",
            "rules": [_local_rule_to_public_dict(rule) for rule in DATA_REASONABLENESS_RULES],
        }

    rule_config = _resolve_rule_source(rule_source, module_code)
    rule_api_url = rule_config["rule_api_url"]

    if module_code == "function_correspondence":
        source = "local_xlsx"
        try:
            rules = load_function_correspondence_rules(
                rules_xlsx_path=rule_config["rules_xlsx_path"],
                rule_api_base=rule_api_url,
            )
            if any(rule.source == "api" for rule in rules):
                source = "rule_api"
        except Exception:
            rules = load_function_correspondence_rules(
                rules_xlsx_path=_local_rules_xlsx_path("function_correspondence"),
                rule_api_base=None,
            )

        public_rules = [
            rule
            for rule in (_rule_to_public_dict(asdict(item)) for item in rules)
            if _rule_matches_module(rule, "function_correspondence")
        ]
        return {
            "module_code": "function_correspondence",
            "module_name": "建设功能的对应关系检查",
            "source": source,
            "rules": public_rules,
        }

    if module_code == "sensitive_word":
        rules = []
        if rule_api_url:
            try:
                rules = load_sensitive_word_rules_from_api(rule_api_url, keyword="敏感词")
            except Exception:
                rules = []
        if rules:
            public_rules = [
                rule
                for rule in (_rule_to_public_dict(asdict(item)) for item in rules)
                if _rule_matches_module(rule, "sensitive_word")
            ]
            return {
                "module_code": "sensitive_word",
                "module_name": "敏感词检查",
                "source": "rule_api",
                "rules": public_rules,
            }

        local_rules = load_sensitive_word_rules_from_xlsx(_local_rules_xlsx_path("sensitive_word"))
        if local_rules:
            public_rules = [
                rule
                for rule in (_rule_to_public_dict(asdict(item)) for item in local_rules)
                if _rule_matches_module(rule, "sensitive_word")
            ]
            return {
                "module_code": "sensitive_word",
                "module_name": "敏感词检查",
                "source": "local_xlsx",
                "rules": public_rules,
            }

        return {
            "module_code": "sensitive_word",
            "module_name": "敏感词检查",
            "source": "local_default",
            "rules": [
                _sensitive_term_to_rule_dict(index, term)
                for index, term in enumerate(
                    DEFAULT_NON_XINCHUANG_TERMS + DEFAULT_RESTRICTIVE_PATTERNS,
                    start=1,
                )
            ],
        }

    raise ValueError("不支持的规则模块")


def _resolve_rule_source(rule_source: Optional[str], module_code: str) -> Dict[str, Optional[str]]:
    source = (rule_source or "api").strip()
    if source.lower() in {"", "api", "rule_api", "rules_api"}:
        return {
            "rules_xlsx_path": _local_rules_xlsx_path(module_code),
            "rule_api_url": _normalize_rule_api_base(settings.rule_api_base_url),
        }
    if source.lower() in {"local", "default", "builtin", "none"}:
        return {"rules_xlsx_path": _local_rules_xlsx_path(module_code), "rule_api_url": None}
    if source.startswith(("http://", "https://")):
        return {
            "rules_xlsx_path": _local_rules_xlsx_path(module_code),
            "rule_api_url": _normalize_rule_api_base(source),
        }
    if Path(source).exists():
        return {"rules_xlsx_path": source, "rule_api_url": None}
    return {
        "rules_xlsx_path": _local_rules_xlsx_path(module_code),
        "rule_api_url": _normalize_rule_api_base(settings.rule_api_base_url),
    }


def _local_rules_xlsx_path(module_code: str) -> str:
    filename = {
        "function_correspondence": "规则库-建设功能的对应关系检查.xlsx",
        "sensitive_word": "规则库-敏感词检查.xlsx",
    }.get(module_code)
    if not filename:
        raise ValueError("unsupported rule module")
    path = Path(__file__).resolve().parents[1] / "rules" / filename
    if not path.exists():
        raise FileNotFoundError(f"missing local rules fallback: {path}")
    return str(path)


def _normalize_rule_api_base(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    normalized = url.strip().rstrip("/")
    if normalized.endswith("/rules"):
        normalized = normalized[: -len("/rules")]
    if normalized.endswith("/api/rule-library/list"):
        normalized = normalized[: -len("/api/rule-library/list")]
    return normalized or None


def _run_with_rule_api_fallback(
    runner: RuleRunner,
    rule_config: Dict[str, Optional[str]],
) -> Dict[str, Any]:
    rule_api_url = rule_config["rule_api_url"]
    if not rule_api_url:
        result = runner(None)
        return _with_rule_source_meta(result, "local_xlsx", None)

    try:
        result = runner(rule_api_url)
        if _uses_api_rules(result):
            return _with_rule_source_meta(result, "rule_api", None)
        return _with_rule_source_meta(result, "local_xlsx", "规则库未返回可用规则，已使用本地 Excel fallback")
    except Exception as exc:
        result = runner(None)
        return _with_rule_source_meta(result, "local_xlsx", str(exc))


def _with_rule_source_meta(
    result: Dict[str, Any],
    rule_source: str,
    rule_api_error: Optional[str],
) -> Dict[str, Any]:
    result = dict(result)
    result["_rule_source"] = rule_source
    if rule_api_error:
        result["_rule_api_error"] = rule_api_error
    return result


def _normalize_function_correspondence_result(raw: Dict[str, Any], report_file_path: str, use_llm: bool = False) -> Dict[str, Any]:
    raw_findings = list(raw.get("findings") or [])
    findings = _merge_function_findings(raw_findings)
    displayed_findings = findings[:50]
    refined_findings, llm_meta = refine_findings(
        module_code="function_correspondence",
        findings=displayed_findings,
        rules=raw.get("rules_used") or [],
        use_llm=use_llm,
    )

    summary = dict(raw.get("summary") or {})
    metrics = document_metrics(report_file_path)
    summary["raw_findings_count"] = len(raw_findings)
    summary["merged_findings_count"] = len(findings)
    summary["displayed_findings_count"] = len(refined_findings)
    summary["risk_summary"] = dict(Counter(item.get("risk_level") for item in refined_findings if item.get("risk_level")))
    summary["total_findings"] = len(refined_findings)
    summary.update(metrics)
    summary.update(llm_meta)
    summary["rule_source"] = raw.get("_rule_source")
    if raw.get("_rule_api_error"):
        summary["rule_api_error"] = raw["_rule_api_error"]

    return {
        "module_code": "function_correspondence",
        "module_name": raw.get("module_name", "建设功能的对应关系检查"),
        "status": raw.get("status", "完成"),
        "summary": summary,
        "findings": refined_findings,
        "rules_used": raw.get("rules_used", []),
        "elapsed_seconds": raw.get("elapsed_seconds", 0),
    }


def _normalize_sensitive_word_result(raw: Dict[str, Any], report_file_path: str, use_llm: bool = False) -> Dict[str, Any]:
    raw_findings = list(raw.get("findings", []))
    findings = _merge_sensitive_findings(raw_findings)
    displayed_findings = findings[:100]
    refined_findings, llm_meta = refine_findings(
        module_code="sensitive_word",
        findings=displayed_findings,
        rules=raw.get("rules") or [],
        use_llm=use_llm,
    )

    risk_summary = dict(raw.get("summary") or {})
    metrics = document_metrics(report_file_path)
    risk_summary["raw_findings_count"] = len(raw_findings)
    risk_summary["merged_findings_count"] = len(findings)
    risk_summary["displayed_findings_count"] = len(refined_findings)
    risk_summary["total_findings"] = len(refined_findings)
    risk_summary.update(metrics)
    risk_summary.update(llm_meta)
    risk_summary["rule_source"] = raw.get("_rule_source")
    if raw.get("_rule_api_error"):
        risk_summary["rule_api_error"] = raw["_rule_api_error"]

    suggestions = _collect_suggestions(refined_findings)

    return {
        "module_code": "sensitive_word",
        "module_name": raw.get("module_name", "敏感词检查"),
        "status": "通过" if not refined_findings else "发现问题",
        "findings": refined_findings,
        "risk_summary": risk_summary,
        "suggestions": suggestions,
        "elapsed_seconds": raw.get("elapsed_seconds", 0),
    }


def _rule_to_public_dict(rule: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "rule_id": str(rule.get("rule_id") or ""),
        "rule_name": str(rule.get("rule_name") or ""),
        "rule_category": str(rule.get("rule_category") or ""),
        "rule_detail": str(rule.get("rule_detail") or rule.get("judgement_condition") or ""),
        "source": str(rule.get("source") or ""),
    }


def _basis_rule_to_public_dict(rule: Dict[str, Any]) -> Dict[str, Any]:
    return _local_rule_to_public_dict(rule)


def _local_rule_to_public_dict(rule: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "rule_id": str(rule.get("rule_id") or ""),
        "rule_name": str(rule.get("rule_name") or ""),
        "rule_category": str(rule.get("rule_category") or ""),
        "rule_detail": str(rule.get("rule_description") or rule.get("judgement_condition") or ""),
        "source": "local_builtin",
    }


def _sensitive_term_to_rule_dict(index: int, term: Any) -> Dict[str, Any]:
    name = term.matched_rule_name or term.term
    detail = term.suggestion or f"检测命中内容：{term.term}"
    return {
        "rule_id": f"local_sensitive_{index:03d}",
        "rule_name": name,
        "rule_category": term.category,
        "rule_detail": detail,
        "source": term.source,
    }


def _uses_api_rules(result: Dict[str, Any]) -> bool:
    for key in ("rules_used", "rules"):
        rules = result.get(key)
        if isinstance(rules, list):
            return any(isinstance(rule, dict) and rule.get("source") == "api" for rule in rules)
    return False


def _apply_llm_reviews(
    module_code: str,
    findings: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    enabled: bool,
    limit: int,
) -> dict[str, Any]:
    model = get_llm_model()
    if not enabled:
        return {
            "llm_enabled": False,
            "llm_model": model,
            "llm_reviewed_count": 0,
            "llm_error_count": 0,
            "llm_display_mode": "rule_text",
        }

    if not os.getenv("DEEPSEEK_API_KEY"):
        return {
            "llm_enabled": False,
            "llm_model": model,
            "llm_reviewed_count": 0,
            "llm_error_count": 0,
            "llm_reason": "missing_api_key",
            "llm_error": "missing_api_key",
            "llm_display_mode": "rule_text",
        }

    rule_by_id = {
        str(rule.get("rule_id") or ""): rule
        for rule in rules
        if isinstance(rule, dict)
    }
    candidates = sorted(
        [finding for finding in findings if _should_llm_review(module_code, finding)],
        key=_llm_candidate_sort_key,
    )[:limit]
    reviewed_count = 0
    error_count = 0
    errors: list[str] = []
    for finding in candidates:
        rule = rule_by_id.get(str(finding.get("rule_id") or ""), {})
        review = review_finding_with_llm(
            module_code=module_code,
            rule=rule,
            finding=finding,
            context=_finding_context(finding),
            timeout=12,
        )
        finding["llm_review"] = review
        if review.get("risk_level_suggestion"):
            finding["llm_risk_level_suggestion"] = review["risk_level_suggestion"]
        if review.get("llm_available") is False:
            error_count += 1
            error = str(review.get("llm_error_type") or review.get("llm_error") or "llm_call_failed")
            if error not in errors:
                errors.append(error)
        else:
            reviewed_count += 1

    meta = {
        "llm_enabled": True,
        "llm_model": model,
        "llm_reviewed_count": reviewed_count,
        "llm_error_count": error_count,
        "llm_display_mode": "llm_refined_text",
    }
    if errors:
        meta["llm_error"] = ", ".join(errors[:3])
    return meta


def _should_llm_review(module_code: str, finding: dict[str, Any]) -> bool:
    risk_level = str(finding.get("risk_level") or "")
    if module_code == "function_correspondence":
        return risk_level in {"高", "中"}
    if module_code == "sensitive_word":
        return True
    return True


def _llm_candidate_sort_key(finding: dict[str, Any]) -> tuple[int, int]:
    return (
        _risk_rank(str(finding.get("risk_level") or "")),
        -int(finding.get("merged_count") or 1),
    )


def _finding_context(finding: dict[str, Any]) -> str:
    examples = finding.get("evidence_examples") or finding.get("context_examples") or []
    if isinstance(examples, list) and examples:
        return "\n".join(str(item) for item in examples[:3])
    return str(finding.get("evidence") or finding.get("context") or finding.get("source_section") or "")


def _rule_matches_module(rule: Dict[str, Any], module_code: str) -> bool:
    text = " ".join(
        str(rule.get(key) or "")
        for key in ("rule_id", "rule_name", "rule_category", "rule_detail")
    )
    if module_code == "function_correspondence":
        keywords = (
            "建设功能", "功能对应", "对应关系", "建设内容一致性",
            "需求分析", "建设内容", "功能点", "预算对应",
        )
        sensitive_keywords = ("敏感词", "限制性", "不规范用语", "非信创", "信创")
        return any(keyword in text for keyword in keywords) and not any(keyword in text for keyword in sensitive_keywords)
    if module_code == "sensitive_word":
        keywords = ("敏感词", "敏感词检测", "限制性", "不规范", "信创", "非信创", "风险词")
        return any(keyword in text for keyword in keywords)
    return False


def _merge_function_findings(findings: list[dict[str, Any]], limit: int = 50) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for item in findings:
        key = (
            _clean_key(item.get("issue_type")),
            _clean_key(item.get("risk_level")),
            _clean_key(item.get("source_section"))[:80],
            _clean_key(item.get("description"))[:80],
            _clean_key(item.get("suggestion"))[:120],
        )
        evidence = str(item.get("evidence") or item.get("source_section") or "").strip()
        if key not in groups:
            merged = dict(item)
            merged["evidence_examples"] = [evidence] if evidence else []
            merged["merged_count"] = 1
            groups[key] = merged
            continue

        existing = groups[key]
        existing["merged_count"] = int(existing.get("merged_count") or 1) + 1
        examples = list(existing.get("evidence_examples") or [])
        if evidence and evidence not in examples and len(examples) < 3:
            examples.append(evidence)
        existing["evidence_examples"] = examples

    merged_findings = list(groups.values())
    merged_findings.sort(
        key=lambda item: (
            _risk_rank(str(item.get("risk_level") or "")),
            -int(item.get("merged_count") or 1),
        )
    )
    return merged_findings[:limit]


def _merge_sensitive_findings(findings: list[dict[str, Any]], limit: int = 100) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in findings:
        key = (
            _clean_key(item.get("hit_text")),
            _clean_key(item.get("section")),
            _clean_key(item.get("suggestion"))[:120],
        )
        context = str(item.get("context") or "").strip()
        if key not in groups:
            merged = dict(item)
            merged["context_examples"] = [context] if context else []
            merged["merged_count"] = 1
            groups[key] = merged
            continue

        existing = groups[key]
        existing["merged_count"] = int(existing.get("merged_count") or 1) + 1
        examples = list(existing.get("context_examples") or [])
        if context and context not in examples and len(examples) < 3:
            examples.append(context)
        existing["context_examples"] = examples

    merged_findings = list(groups.values())
    merged_findings.sort(
        key=lambda item: (
            _risk_rank(str(item.get("risk_level") or "")),
            -int(item.get("merged_count") or 1),
        )
    )
    return merged_findings[:limit]


def _clean_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", "", text)


def _risk_rank(risk_level: str) -> int:
    return {"高": 0, "中": 1, "需人工确认": 2, "需确认": 2, "低": 3}.get(risk_level, 4)


def _collect_suggestions(findings: list[dict[str, Any]]) -> list[str]:
    suggestions: list[str] = []
    seen: set[str] = set()
    for item in findings:
        suggestion = str(item.get("suggestion") or "").strip()
        if suggestion and suggestion not in seen:
            suggestions.append(suggestion)
            seen.add(suggestion)
    return suggestions
