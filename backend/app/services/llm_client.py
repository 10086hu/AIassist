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
        }
        if payload.get("_raw_text"):
            result["content"] = payload["_raw_text"]
        else:
            result["result"] = payload
        return result
    except Exception as exc:
        return {
            "success": False,
            "configured": True,
            "model": model,
            "error_type": _error_type(exc),
            "error": _safe_error_message(exc),
        }


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

    content = extract_message_content(data)
    return _parse_model_content(content)


def extract_message_content(response: Any) -> str:
    choices = _get_response_value(response, "choices")
    if not choices:
        raise ValueError("llm_response_missing_choices")

    first_choice = choices[0] if isinstance(choices, (list, tuple)) else _get_response_value(choices, 0)
    message = _get_response_value(first_choice, "message")
    if message is None:
        raise ValueError("llm_response_missing_message")

    content = _get_response_value(message, "content")
    if content is None:
        raise ValueError("llm_response_missing_content")

    return str(content)


def _get_response_value(value: Any, key: Any) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    if isinstance(key, int):
        try:
            return value[key]
        except (TypeError, IndexError, KeyError):
            return None
    return getattr(value, key, None)


def _parse_model_content(content: str) -> dict[str, Any]:
    text = _strip_think_blocks(content)
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


def _normalize_review(payload: dict[str, Any], model: str, finding: dict[str, Any], context: str) -> dict[str, Any]:
    raw_text = _short_text(payload.get("_raw_text") or payload.get("content") or "")
    fallback_suggestion = str(finding.get("suggestion") or "")
    fallback_basis = str(finding.get("evidence") or finding.get("source_section") or context or "")
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
        "judgement": _string_value(payload.get("judgement") if payload.get("judgement") is not None else payload.get("is_valid")) or ("模型返回自然语言复核意见" if raw_text else ""),
        "risk_level_suggestion": _string_value(payload.get("risk_level_suggestion") or payload.get("risk_level")),
        "user_reason": user_reason,
        "user_suggestion": user_suggestion,
        "user_basis": user_basis,
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
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"url_error:{exc.reason.__class__.__name__}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    message = str(exc)
    return f"{exc.__class__.__name__}: {message}" if message else exc.__class__.__name__


def _error_type(exc: Exception) -> str:
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
