from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import requests

from app.core.config import settings
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
) -> LLMJudgement:
    """调用 DeepSeek 大模型进行重复判定"""
    if not settings.deepseek_api_key:
        logger.warning("DEEPSEEK_API_KEY not set, falling back to rule-based judge")
        return _fallback_judge(left_name, left_description, right_name, right_description, similarity)

    prompt = _build_judge_prompt(
        left_name, left_description,
        right_name, right_description,
        similarity
    )

    try:
        response = _call_deepseek_api(prompt)
        return _parse_llm_response(response)
    except Exception as e:
        logger.error(f"LLM judgement failed: {e}, falling back to rule-based judge")
        return _fallback_judge(left_name, left_description, right_name, right_description, similarity)


def _build_judge_prompt(
    left_name: str,
    left_description: str,
    right_name: str,
    right_description: str,
    similarity: float,
) -> str:
    return f"""你是政府信息化项目可研评审专家。请比较以下两个功能点是否重复建设。

【功能点1】
名称: {left_name}
描述: {left_description}

【功能点2】
名称: {right_name}
描述: {right_description}

【参考信息】
语义相似度: {similarity:.2f}

判定口径：
1. 重复：目标对象、业务流程、核心能力基本相同，只是措辞不同。
2. 高度相似：属于同一业务域，能力有明显重叠，但存在上下级、前后置或范围差异。
3. 无关：业务目标或核心能力不同。

请按以下 JSON 格式输出你的判定结果，不要包含其他内容：
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
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": 0.3,
        "max_tokens": 500,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return content.strip()


def _parse_llm_response(response: str) -> LLMJudgement:
    """解析 LLM 响应"""
    try:
        json_str = response
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0]

        data = json.loads(json_str)
        label = data.get("label", "无关").strip()
        reason = data.get("reason", "").strip()
        suggestion = data.get("suggestion", "").strip()

        if label not in ("重复", "高度相似", "无关"):
            label = "无关"

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
            model_name="deepseek",
        )
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error(f"Failed to parse LLM response: {e}, response: {response}")
        raise ValueError(f"Invalid LLM response format") from e


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
