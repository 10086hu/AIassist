from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


DEFAULT_MODEL = "DeepSeek-V4-Flash"
DEFAULT_API_BASE_URL = "https://api.deepseek.com/v1"


def get_llm_model() -> str:
    return os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def review_finding_with_llm(
    module_code: str,
    rule: dict[str, Any] | None,
    finding: dict[str, Any],
    context: str | None = None,
    timeout: int = 25,
) -> dict[str, Any]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    model = get_llm_model()
    if not api_key:
        return {
            "enabled": False,
            "model": model,
            "llm_available": False,
            "llm_error": "missing_api_key",
        }

    prompt = _build_prompt(module_code, rule or {}, finding, context or "")
    try:
        payload = _call_deepseek(prompt=prompt, api_key=api_key, model=model, timeout=timeout)
        normalized = _normalize_review(payload, model)
        normalized["enabled"] = True
        normalized["llm_available"] = True
        return normalized
    except Exception as exc:
        return {
            "enabled": True,
            "model": model,
            "llm_available": False,
            "llm_error": str(exc),
        }


def _call_deepseek(prompt: str, api_key: str, model: str, timeout: int) -> dict[str, Any]:
    base_url = os.getenv("DEEPSEEK_API_BASE_URL", DEFAULT_API_BASE_URL).strip().rstrip("/")
    if base_url.endswith("/chat/completions"):
        url = base_url
    else:
        url = f"{base_url}/chat/completions"

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是可研报告审查复核助手。只输出合法 JSON，不要输出 Markdown。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
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

    content = data["choices"][0]["message"]["content"]
    return _parse_json_content(content)


def _parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
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
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("llm_response_not_object")
    return parsed


def _normalize_review(payload: dict[str, Any], model: str) -> dict[str, Any]:
    return {
        "enabled": True,
        "model": model,
        "judgement": str(payload.get("judgement") or payload.get("is_valid") or ""),
        "risk_level_suggestion": str(payload.get("risk_level_suggestion") or payload.get("risk_level") or ""),
        "reason": str(payload.get("reason") or ""),
        "rewrite_suggestion": str(payload.get("rewrite_suggestion") or payload.get("suggestion") or ""),
        "confidence": payload.get("confidence"),
        "need_human_review": payload.get("need_human_review"),
        "scene_type": payload.get("scene_type"),
    }


def _build_prompt(module_code: str, rule: dict[str, Any], finding: dict[str, Any], context: str) -> str:
    if module_code == "sensitive_word":
        payload = {
            "任务": "敏感词检查结果语义复核",
            "命中词": finding.get("hit_text"),
            "上下文": finding.get("context") or context,
            "所在章节": finding.get("section"),
            "当前风险等级": finding.get("risk_level"),
            "当前场景类型": finding.get("scene_type"),
            "匹配规则": finding.get("matched_rule") or rule.get("rule_name"),
            "输出JSON字段": {
                "judgement": "是否真实构成敏感风险",
                "scene_type": "拟采用/现状描述/政策引用/历史系统描述/需人工确认",
                "risk_level_suggestion": "高/中/低/需人工确认/通过",
                "reason": "判断理由",
                "rewrite_suggestion": "修改建议",
                "need_human_review": "true/false",
                "confidence": "0到1之间的小数",
            },
        }
    else:
        payload = {
            "任务": "建设功能对应关系检查结果语义复核",
            "规则名称": finding.get("rule_name") or rule.get("rule_name"),
            "规则描述": rule.get("rule_detail") or finding.get("rule_detail"),
            "问题描述": finding.get("description"),
            "原因": finding.get("reason"),
            "证据片段": finding.get("evidence") or finding.get("evidence_examples") or context,
            "所在章节": finding.get("source_section"),
            "当前风险等级": finding.get("risk_level"),
            "输出JSON字段": {
                "judgement": "是否确实存在建设功能对应关系问题",
                "risk_level_suggestion": "高/中/低/需人工确认/通过",
                "reason": "判断理由",
                "rewrite_suggestion": "修改建议",
                "need_human_review": "true/false",
                "confidence": "0到1之间的小数",
            },
        }
    return json.dumps(payload, ensure_ascii=False)
