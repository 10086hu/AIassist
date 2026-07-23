from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from app.core.config import settings
from app.modules.function_correspondence.construction_function_correspondence_checker import (
    DEFAULT_RULES as FUNCTION_CORRESPONDENCE_DEFAULT_RULES,
    check_construction_function_correspondence,
    load_rules as load_function_correspondence_rules,
)
from app.modules.sensitive_word.sensitive_word_checker import (
    DEFAULT_NON_XINCHUANG_TERMS,
    DEFAULT_RESTRICTIVE_PATTERNS,
    check_sensitive_words,
    load_rules_from_api as load_sensitive_word_rules_from_api,
)


RuleRunner = Callable[[Optional[str]], Dict[str, Any]]


def run_function_correspondence_check(
    report_file_path: str,
    rule_source: Optional[str] = "api",
    project_level: str = "市级项目",
    use_llm: bool = False,
    selected_rule_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    rule_config = _resolve_rule_source(rule_source)
    # The current checker engine does not expose rule-id filtering yet.
    # selected_rule_ids is accepted here so the WPF/client contract can stay stable.
    _ = selected_rule_ids

    def runner(rule_api_base: Optional[str]) -> Dict[str, Any]:
        return check_construction_function_correspondence(
            report_path=report_file_path,
            rules_xlsx_path=rule_config["rules_xlsx_path"],
            rule_api_base=rule_api_base,
            project_level=project_level,
            use_llm=use_llm,
            deepseek_api_key=settings.deepseek_api_key or None,
        )

    raw = _run_with_rule_api_fallback(runner, rule_config)
    return _normalize_function_correspondence_result(raw)


def run_sensitive_word_check(
    report_file_path: str,
    rule_source: Optional[str] = "api",
    project_level: str = "通用",
    use_llm: bool = False,
    selected_rule_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    rule_config = _resolve_rule_source(rule_source)
    # The current checker engine does not expose rule-id filtering yet.
    # selected_rule_ids is accepted here so the WPF/client contract can stay stable.
    _ = selected_rule_ids

    def runner(rule_api_url: Optional[str]) -> Dict[str, Any]:
        return check_sensitive_words(
            report_path=report_file_path,
            rules_xlsx_path=rule_config["rules_xlsx_path"],
            rule_api_url=rule_api_url,
            rule_api_keyword="敏感词",
            use_llm=use_llm,
            project_level=project_level,
        )

    raw = _run_with_rule_api_fallback(runner, rule_config)
    return _normalize_sensitive_word_result(raw)


def list_check_rules(module: str, rule_source: Optional[str] = "api") -> Dict[str, Any]:
    module_code = module.strip().lower()
    rule_config = _resolve_rule_source(rule_source)
    rule_api_url = rule_config["rule_api_url"]

    if module_code == "function_correspondence":
        try:
            rules = load_function_correspondence_rules(
                rules_xlsx_path=rule_config["rules_xlsx_path"],
                rule_api_base=rule_api_url,
            )
            source = "rule_api" if any(rule.source == "api" for rule in rules) else "local_default"
        except Exception:
            rules = list(FUNCTION_CORRESPONDENCE_DEFAULT_RULES)
            source = "local_default"
        return {
            "module_code": "function_correspondence",
            "module_name": "建设功能的对应关系检查",
            "source": source,
            "rules": [_rule_to_public_dict(asdict(rule)) for rule in rules],
        }

    if module_code == "sensitive_word":
        rules = load_sensitive_word_rules_from_api(rule_api_url, keyword="敏感词") if rule_api_url else []
        if rules:
            return {
                "module_code": "sensitive_word",
                "module_name": "敏感词检查",
                "source": "rule_api",
                "rules": [_rule_to_public_dict(asdict(rule)) for rule in rules],
            }
        return {
            "module_code": "sensitive_word",
            "module_name": "敏感词检查",
            "source": "local_default",
            "rules": [_sensitive_term_to_rule_dict(index, term) for index, term in enumerate(
                DEFAULT_NON_XINCHUANG_TERMS + DEFAULT_RESTRICTIVE_PATTERNS,
                start=1,
            )],
        }

    raise ValueError("不支持的规则模块")


def _resolve_rule_source(rule_source: Optional[str]) -> Dict[str, Optional[str]]:
    source = (rule_source or "api").strip()
    if source.lower() in {"", "api", "rule_api", "rules_api"}:
        return {
            "rules_xlsx_path": None,
            "rule_api_url": _normalize_rule_api_base(settings.rule_api_base_url),
        }
    if source.lower() in {"local", "default", "builtin", "none"}:
        return {"rules_xlsx_path": None, "rule_api_url": None}
    if source.startswith(("http://", "https://")):
        return {"rules_xlsx_path": None, "rule_api_url": _normalize_rule_api_base(source)}
    if Path(source).exists():
        return {"rules_xlsx_path": source, "rule_api_url": None}
    return {
        "rules_xlsx_path": None,
        "rule_api_url": _normalize_rule_api_base(settings.rule_api_base_url),
    }


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
        return _with_rule_source_meta(result, "local_default", None)

    try:
        result = runner(rule_api_url)
        if _uses_api_rules(result):
            return _with_rule_source_meta(result, "rule_api", None)
        return _with_rule_source_meta(result, "local_default", "规则库未返回可用规则")
    except Exception as exc:
        result = runner(None)
        return _with_rule_source_meta(result, "local_default", str(exc))


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


def _normalize_function_correspondence_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    summary = dict(raw.get("summary") or {})
    summary["rule_source"] = raw.get("_rule_source")
    if raw.get("_rule_api_error"):
        summary["rule_api_error"] = raw["_rule_api_error"]

    return {
        "module_code": "function_correspondence",
        "module_name": raw.get("module_name", "建设功能的对应关系检查"),
        "status": raw.get("status", "完成"),
        "summary": summary,
        "findings": raw.get("findings", []),
        "rules_used": raw.get("rules_used", []),
        "elapsed_seconds": raw.get("elapsed_seconds", 0),
    }


def _normalize_sensitive_word_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    findings = raw.get("findings", [])
    risk_summary = dict(raw.get("summary") or {})
    risk_summary["rule_source"] = raw.get("_rule_source")
    if raw.get("_rule_api_error"):
        risk_summary["rule_api_error"] = raw["_rule_api_error"]

    suggestions = _collect_suggestions(findings)

    return {
        "module_code": "sensitive_word",
        "module_name": raw.get("module_name", "敏感词检查"),
        "status": "通过" if not findings else "发现问题",
        "findings": findings,
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


def _collect_suggestions(findings: list[dict[str, Any]]) -> list[str]:
    suggestions: list[str] = []
    seen: set[str] = set()
    for item in findings:
        suggestion = str(item.get("suggestion") or "").strip()
        if suggestion and suggestion not in seen:
            suggestions.append(suggestion)
            seen.add(suggestion)
    return suggestions
