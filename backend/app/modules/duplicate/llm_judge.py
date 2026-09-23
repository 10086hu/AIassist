from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import settings
from app.core.direct_http import post_direct
from app.core.llm_json import parse_json_object_from_text
from app.modules.duplicate.embeddings import lexical_overlap, normalize_text


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMJudgement:
    label: str
    severity: str
    reason: str
    suggestion: Optional[str] = None
    model_name: str = "deepseek"


def judge_pair_with_llm(
    left_name: str,
    left_description: str,
    right_name: str,
    right_description: str,
    similarity: float,
    context: str = "",
    use_llm: bool = True,
) -> LLMJudgement:
    """调用 DeepSeek 大模型进行重复判定"""
    if not use_llm:
        return _fallback_judge(left_name, left_description, right_name, right_description, similarity)
    if not settings.deepseek_api_key:
        raise ValueError("重复建设检查需要大模型，但后端未配置 DEEPSEEK_API_KEY")

    prompt = _build_judge_prompt(
        left_name, left_description,
        right_name, right_description,
        similarity,
        context,
    )

    try:
        response = _call_deepseek_api(prompt)
        return _parse_llm_response(response)
    except Exception as e:
        logger.exception("重复建设大模型判定失败")
        raise ValueError(f"重复建设大模型判定失败：{e}") from e


def _build_judge_prompt(
    left_name: str,
    left_description: str,
    right_name: str,
    right_description: str,
    similarity: float,
    context: str = "",
) -> str:
    context_text = f"\n【报告关系】\n{context}\n" if context.strip() else ""
    return f"""你是政府信息化项目可研评审专家。请比较以下两个功能点是否重复建设。

【功能点1】
名称: {left_name}
描述: {left_description}

【功能点2】
名称: {right_name}
描述: {right_description}

【参考信息】
语义相似度: {similarity:.2f}
{context_text}

判定口径：
1. 重复：目标对象、业务流程、核心能力基本相同，只是措辞不同。
2. 高度相似：属于同一业务域，能力有明显重叠，但存在上下级、前后置或范围差异。
3. 无关：业务目标或核心能力不同。

请按以下 JSON 格式输出你的判定结果，不要包含其他内容。
reason 控制在 80 个汉字以内，suggestion 控制在 80 个汉字以内：
{{
    "label": "重复|高度相似|无关",
    "reason": "判定理由",
    "suggestion": "建议"
}}"""


def _call_deepseek_api(prompt: str) -> str:
    """调用 DeepSeek API"""
    url = f"{settings.deepseek_api_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.deepseek_api_key}",
    }
    payload = {
        "model": settings.deepseek_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        # Keep enough of the output budget for the required JSON response.
        "reasoning_effort": "low",
        "max_tokens": settings.duplicate_llm_max_tokens,
        "response_format": {"type": "json_object"},
    }

    timeout = settings.duplicate_llm_timeout_seconds
    response = _post_with_retry(url, headers, payload, timeout)
    if response.status_code in {400, 422}:
        payload.pop("response_format", None)
        response = _post_with_retry(url, headers, payload, timeout)
    response.raise_for_status()

    data = response.json()
    return _extract_message_text(data)


def _post_with_retry(url: str, headers: dict[str, str], payload: dict, timeout: int):
    response = None
    for attempt in range(3):
        try:
            response = post_direct(url, headers=headers, json=payload, timeout=timeout)
            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                return response
        except Exception:
            if attempt == 2:
                raise
        if attempt < 2:
            time.sleep(2**attempt)
    assert response is not None
    return response


def _parse_llm_response(response: str) -> LLMJudgement:
    """解析 LLM 响应"""
    try:
        data = parse_json_object_from_text(_strip_think_blocks(response))
        label = _normalize_label(_text_value(data.get("label"), "无关"))
        reason = _text_value(data.get("reason"), "")
        suggestion = _text_value(data.get("suggestion"), "")

        severity_map = {
            "重复": "risk",
            "高度相似": "warning",
            "无关": "pass",
        }

        return LLMJudgement(
            label=label,
            severity=severity_map[label],
            reason=reason or "LLM 判定",
            suggestion=suggestion or None,
            model_name=settings.deepseek_model,
        )
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as e:
        logger.error(f"Failed to parse LLM response: {e}, response: {response}")
        raise ValueError(f"Invalid LLM response format") from e


def _extract_message_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("LLM response missing choices")
    message = (choices[0] or {}).get("message") or {}
    value = message.get("content")
    if value is not None and str(value).strip():
        return str(value).strip()
    finish_reason = (choices[0] or {}).get("finish_reason")
    raise ValueError(f"LLM response missing final content, finish_reason={finish_reason}")


def _strip_think_blocks(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.IGNORECASE | re.DOTALL).strip()


def _text_value(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value).strip() or default


def _normalize_label(value: str) -> str:
    text = value.strip()
    if text in {"重复", "完全重复", "疑似重复"}:
        return "重复"
    if text in {"高度相似", "部分重复", "阶段延续", "需人工确认", "相似"}:
        return "高度相似"
    if text in {"无关", "不重复", "相似但不重复"}:
        return "无关"
    raise ValueError(f"大模型返回了无法识别的重复建设判定：{text}")


def _fallback_judge(
    left_name: str,
    left_description: str,
    right_name: str,
    right_description: str,
    similarity: float,
) -> LLMJudgement:
    """本地规则降级判定"""
    left = f"{left_name} {left_description}"
    right = f"{right_name} {right_description}"
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    overlap = lexical_overlap(left, right)

    if left_norm == right_norm or left_norm in right_norm or right_norm in left_norm:
        return LLMJudgement(
            label="重复",
            severity="risk",
            reason="两个功能点的名称或描述存在完全一致/包含关系，核心建设内容高度重合。",
            suggestion="建议合并描述，或明确拆分后的边界、对象和交付物。",
            model_name="rule-based",
        )

    if similarity >= 0.62 and overlap >= 0.28:
        return LLMJudgement(
            label="重复",
            severity="risk",
            reason=f"语义相似度为 {similarity:.2f}，关键词重合度较高，疑似同一能力的重复表述。",
            suggestion="建议评审人员核对是否属于同一功能，避免重复申报。",
            model_name="rule-based",
        )

    if similarity >= 0.45 or overlap >= 0.22:
        return LLMJudgement(
            label="高度相似",
            severity="warning",
            reason=f"语义相似度为 {similarity:.2f}，两项处于相近业务域，但可能存在范围或层级差异。",
            suggestion="建议补充两项的职责边界、使用对象和数据范围。",
            model_name="rule-based",
        )

    return LLMJudgement(
        label="无关",
        severity="pass",
        reason=f"语义相似度为 {similarity:.2f}，未达到内部重复筛查阈值。",
        model_name="rule-based",
    )
