from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request
from typing import Any


DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_API_BASE_URL = "https://api.deepseek.com"
DEFAULT_TIMEOUT_SECONDS = 12
TRANSPORT = "openai_chat_completions"


class LlmProviderError(Exception):
    def __init__(self, detail: dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(str(detail))


class MissingContentError(ValueError):
    def __init__(self, debug: dict[str, Any]) -> None:
        self.debug = debug
        super().__init__("llm_response_missing_content")


class ExtractedContent:
    def __init__(self, content: str, source: str) -> None:
        self.content = content
        self.source = source


def get_llm_model() -> str:
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if model.lower() == "deepseek-v4-flash":
        return DEFAULT_MODEL
    return model


def get_llm_base_url() -> str:
    return (os.getenv("DEEPSEEK_API_BASE_URL", DEFAULT_API_BASE_URL).strip() or DEFAULT_API_BASE_URL).rstrip("/")


def get_llm_status() -> dict[str, Any]:
    api_key_present = bool(os.getenv("DEEPSEEK_API_KEY"))
    return {
        "configured": api_key_present,
        "model": get_llm_model(),
        "base_url": get_llm_base_url(),
        "api_key_present": api_key_present,
    }


def ping_llm(text: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    model = get_llm_model()
    if not api_key:
        return {
            "success": False,
            "configured": False,
            "model": model,
            "error_type": "missing_api_key",
        }

    prompt = {
        "task": "连通性测试",
        "text": text[:500],
        "output_json_schema": {
            "judgement": "一句话说明是否理解输入",
            "user_reason": "20字以内的简短说明",
        },
    }
    try:
        payload = _call_deepseek(
            prompt=json.dumps(prompt, ensure_ascii=False),
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_tokens=180,
        )
        result = {
            "success": True,
            "configured": True,
            "model": model,
            "transport": TRANSPORT,
            "content_source": str(payload.get("_content_source") or "unknown"),
        }
        if payload.get("_raw_text"):
            result["content"] = payload["_raw_text"]
        else:
            result["result"] = payload
            result["content"] = json.dumps(payload, ensure_ascii=False)
        return result
    except Exception as exc:
        result = {
            "success": False,
            "configured": True,
            "model": model,
            "error_type": _error_type(exc),
            "error": _safe_error_message(exc),
            "transport": TRANSPORT,
        }
        if isinstance(exc, LlmProviderError):
            result["error_detail"] = exc.detail
        if isinstance(exc, MissingContentError):
            result.update(exc.debug)
        return result


def review_finding_with_llm(
    module_code: str,
    rule: dict[str, Any] | None,
    finding: dict[str, Any],
    context: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    model = get_llm_model()
    if not api_key:
        return {
            "enabled": False,
            "model": model,
            "llm_available": False,
            "llm_error": "missing_api_key",
            "llm_error_type": "missing_api_key",
        }

    prompt = _build_prompt(module_code, rule or {}, finding, context or "")
    try:
        payload = _call_deepseek(
            prompt=prompt,
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_tokens=700,
        )
        normalized = _normalize_review(payload, model, finding, context or "")
        normalized["enabled"] = True
        normalized["llm_available"] = True
        return normalized
    except Exception as exc:
        return {
            "enabled": True,
            "model": model,
            "llm_available": False,
            "llm_error": _safe_error_message(exc),
            "llm_error_type": _error_type(exc),
        }


def _call_deepseek(
    prompt: str,
    api_key: str,
    model: str,
    timeout: int,
    max_tokens: int,
) -> dict[str, Any]:
    try:
        return _post_chat_completion(
            prompt=prompt,
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_tokens=max_tokens,
            use_response_format=True,
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 422):
            return _post_chat_completion(
                prompt=prompt,
                api_key=api_key,
                model=model,
                timeout=timeout,
                max_tokens=max_tokens,
                use_response_format=False,
            )
        raise


def _post_chat_completion(
    prompt: str,
    api_key: str,
    model: str,
    timeout: int,
    max_tokens: int,
    use_response_format: bool,
) -> dict[str, Any]:
    url = f"{get_llm_base_url()}/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是可研报告审查复核助手。只输出合法 JSON，不输出 Markdown。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    if use_response_format:
        body["response_format"] = {"type": "json_object"}

    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))

    provider_error = _provider_error_detail(data)
    if provider_error:
        raise LlmProviderError(provider_error)

    extracted = _extract_message_content(data)
    parsed = _parse_model_content(extracted.content)
    parsed["_content_source"] = extracted.source
    return parsed


def extract_message_content(response: Any) -> str:
    return _extract_message_content(response).content


def _extract_message_content(response: Any) -> ExtractedContent:
    for key in ("output_text", "content"):
        content = _get_response_value(response, key)
        if _has_text(content):
            return ExtractedContent(_safe_content_text(content), key)

    choices = _get_response_value(response, "choices")
    if not choices:
        raise ValueError("llm_response_missing_choices")

    first_choice = choices[0] if isinstance(choices, (list, tuple)) else _get_response_value(choices, 0)
    for path in (
        ("message", "content"),
        ("message", "reasoning"),
        ("message", "reasoning_content"),
        ("message", "refusal"),
        ("delta", "content"),
        ("text",),
    ):
        content = _get_nested_response_value(first_choice, path)
        if _has_text(content):
            return ExtractedContent(_safe_content_text(content), ".".join(str(item) for item in path))

    raise MissingContentError(_missing_content_debug(response, first_choice))


def _get_response_value(value: Any, key: Any) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    if isinstance(key, int):
        try:
            return value[key]
        except (TypeError, IndexError, KeyError):
            return None
    return getattr(value, key, None)


def _get_nested_response_value(value: Any, path: tuple[Any, ...]) -> Any:
    current = value
    for key in path:
        current = _get_response_value(current, key)
        if current is None:
            return None
    return current


def _has_text(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _safe_content_text(value: Any) -> str:
    raw_text = str(value)
    stripped = _strip_think_blocks(raw_text)
    if stripped:
        return stripped
    return _short_text(raw_text.strip(), 500)


def _parse_model_content(content: str) -> dict[str, Any]:
    raw_text = str(content or "")
    text = _strip_think_blocks(raw_text)
    if not text and raw_text.strip():
        text = _short_text(raw_text.strip(), 300)
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {"content": text, "_raw_text": text}
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        return {"content": text, "_raw_text": text}
    return parsed


def _strip_think_blocks(content: str) -> str:
    return re.sub(r"<think>.*?</think>", "", content or "", flags=re.IGNORECASE | re.DOTALL).strip()


def _provider_error_detail(response: Any) -> dict[str, Any]:
    error = _get_response_value(response, "error")
    if not error:
        return {}
    return {
        key: _short_text(_get_response_value(error, key), 300)
        for key in ("message", "type", "code")
        if _has_text(_get_response_value(error, key))
    } or {"message": _short_text(error, 300)}


def _missing_content_debug(response: Any, first_choice: Any) -> dict[str, Any]:
    message = _get_response_value(first_choice, "message")
    return {
        "response_keys": _response_keys(response),
        "choice_keys": _response_keys(first_choice),
        "message_keys": _response_keys(message),
        "raw_preview": _short_text(_safe_json_preview(response), 500),
    }


def _response_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value.keys()]
    if value is None:
        return []
    return [key for key in ("choices", "message", "delta", "content", "reasoning", "reasoning_content", "refusal", "text", "output_text", "error") if hasattr(value, key)]


def _safe_json_preview(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _normalize_review(payload: dict[str, Any], model: str, finding: dict[str, Any], context: str) -> dict[str, Any]:
    raw_text = _short_text(payload.get("_raw_text") or payload.get("content") or "")
    fallback_suggestion = str(finding.get("suggestion") or "")
    fallback_basis = str(finding.get("evidence") or finding.get("source_section") or context or "")
    display_title = _short_text(payload.get("display_title") or payload.get("title") or payload.get("issue_title"), 80)
    feature_label = _short_text(payload.get("feature_label") or payload.get("item_label"), 80)
    user_reason = _short_text(payload.get("user_reason") or payload.get("reason") or raw_text)
    user_suggestion = _short_text(
        payload.get("user_suggestion")
        or payload.get("rewrite_suggestion")
        or payload.get("suggestion")
        or _extract_suggestion_from_text(raw_text)
        or fallback_suggestion
    )
    user_basis = _short_text(payload.get("user_basis") or payload.get("basis") or payload.get("evidence_basis") or fallback_basis)
    return {
        "enabled": True,
        "model": model,
        "display_title": display_title,
        "feature_label": feature_label,
        "judgement": _string_value(payload.get("judgement") if payload.get("judgement") is not None else payload.get("is_valid")) or ("模型返回自然语言复核意见" if raw_text else ""),
        "risk_level_suggestion": _string_value(payload.get("risk_level_suggestion") or payload.get("risk_level")),
        "user_reason": user_reason,
        "user_suggestion": user_suggestion,
        "user_basis": user_basis,
        "evidence_summary": _short_text(payload.get("evidence_summary") or user_basis),
        "revision_advice": _short_text(payload.get("revision_advice") or user_suggestion),
        "reason": user_reason,
        "rewrite_suggestion": user_suggestion,
        "confidence": payload.get("confidence"),
        "need_human_review": payload.get("need_human_review"),
        "scene_type": payload.get("scene_type"),
    }


def _extract_suggestion_from_text(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"(建议|修改建议|处理建议)[:：]\s*(.+)", text)
    return match.group(2).strip() if match else ""


def _build_prompt(module_code: str, rule: dict[str, Any], finding: dict[str, Any], context: str) -> str:
    if module_code == "sensitive_word":
        payload = {
            "任务": "敏感词检查结果语义复核，并整理成用户可读文本",
            "命中词": finding.get("hit_text"),
            "上下文": finding.get("context") or context,
            "所在章节": finding.get("section"),
            "当前风险等级": finding.get("risk_level"),
            "当前场景类型": finding.get("scene_type"),
            "匹配规则": finding.get("matched_rule") or rule.get("rule_name"),
            "要求": [
                "判断该命中是拟采用、现状描述、政策引用、历史系统描述，还是需人工确认。",
                "判断是否真实构成敏感风险，不要覆盖规则检查结论。",
                "把原因、建议、依据整理成自然、凝练、适合最终用户阅读的中文。",
                "user_reason、user_suggestion、user_basis 每项控制在80到150字。",
            ],
            "输出JSON字段": {
                "judgement": "是否真实构成敏感风险",
                "scene_type": "拟采用/现状描述/政策引用/历史系统描述/需人工确认",
                "risk_level_suggestion": "高/中/低/需人工确认/通过",
                "user_reason": "用户可读原因",
                "user_suggestion": "用户可执行修改建议",
                "user_basis": "规则、章节、上下文或证据依据概括",
                "need_human_review": True,
                "confidence": 0.8,
            },
        }
    elif module_code == "content_consistency":
        payload = {
            "任务": "建设内容一致性第十条结果语义复核，并整理成类似其他小规则的简洁问题结果",
            "规则名称": finding.get("rule_name") or rule.get("rule_name"),
            "规则描述": rule.get("rule_detail") or finding.get("rule_detail"),
            "疑似功能点": finding.get("item") or finding.get("feature"),
            "原始功能点": finding.get("raw_item"),
            "问题标题": finding.get("description"),
            "原始原因": finding.get("reason"),
            "原始建议": finding.get("suggestion"),
            "证据片段": finding.get("evidence") or finding.get("evidence_examples") or context,
            "所在章节": finding.get("source_section"),
            "当前风险等级": finding.get("risk_level"),
            "要求": [
                "先判断疑似功能点是否为具体建设功能；如果原始功能点包含逗号、顿号或“数据中台-”等父级前缀，只提炼最短的具体功能点名称。",
                "display_title 要像其他小规则一样简洁，不要罗列多个前缀或整段原文；优先使用“功能点对应说明不足：具体功能点”。",
                "复核是否确实缺少需求分析、建设内容、功能点设计或投资概算之间的对应说明，不要覆盖规则检查结论。",
                "user_reason、user_suggestion、user_basis 每项控制在60到120字。",
            ],
            "输出JSON字段": {
                "display_title": "功能点对应说明不足：具体功能点",
                "feature_label": "最短具体功能点名称",
                "judgement": "是否确实存在建设内容一致性问题",
                "risk_level_suggestion": "高/中/低/需人工确认/通过",
                "user_reason": "用户可读原因",
                "user_suggestion": "用户可执行修改建议",
                "user_basis": "规则、章节、上下文或证据依据概括",
                "need_human_review": True,
                "confidence": 0.8,
            },
        }
    else:
        payload = {
            "任务": "建设功能对应关系检查结果语义复核，并整理成用户可读文本",
            "规则名称": finding.get("rule_name") or rule.get("rule_name"),
            "规则描述": rule.get("rule_detail") or finding.get("rule_detail"),
            "问题描述": finding.get("description"),
            "原始原因": finding.get("reason"),
            "原始建议": finding.get("suggestion"),
            "证据片段": finding.get("evidence") or finding.get("evidence_examples") or context,
            "所在章节": finding.get("source_section"),
            "当前风险等级": finding.get("risk_level"),
            "要求": [
                "判断是否确实存在建设功能、建设内容、预算或需求之间的对应关系问题。",
                "判断问题是否可能重复或可合并，不要覆盖规则检查结论。",
                "把原因、建议、依据整理成自然、凝练、适合最终用户阅读的中文。",
                "user_reason、user_suggestion、user_basis 每项控制在80到150字。",
            ],
            "输出JSON字段": {
                "judgement": "是否确实存在问题",
                "risk_level_suggestion": "高/中/低/需人工确认/通过",
                "user_reason": "用户可读原因",
                "user_suggestion": "用户可执行修改建议",
                "user_basis": "规则、章节、上下文或证据依据概括",
                "need_human_review": True,
                "confidence": 0.8,
            },
        }
    return json.dumps(payload, ensure_ascii=False)


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, LlmProviderError):
        return "provider_error"
    if isinstance(exc, MissingContentError):
        return "ValueError: llm_response_missing_content"
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"url_error:{exc.reason.__class__.__name__}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    message = str(exc)
    return f"{exc.__class__.__name__}: {message}" if message else exc.__class__.__name__


def _error_type(exc: Exception) -> str:
    if isinstance(exc, LlmProviderError):
        return "provider_error"
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "url_error"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    return exc.__class__.__name__


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _short_text(value: Any, max_chars: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."
