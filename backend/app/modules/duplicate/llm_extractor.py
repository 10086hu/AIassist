from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import replace
from typing import List, Optional

from app.core.config import settings
from app.core.direct_http import post_direct
from app.core.llm_json import parse_json_object_from_text
from app.modules.duplicate.document_parser import DocumentContent, DocumentSection
from app.modules.duplicate.excel_parser import ParsedFunctionPoint
from app.modules.duplicate.execution import parallel_model_work, remaining_timeout


logger = logging.getLogger(__name__)

# The gateway routes Flash to V4.1 and includes reasoning in the output budget.
# Smaller blocks leave room for both reasoning and the evidence-bearing JSON.
MAX_SECTION_CHARS = 1200


def extract_function_points(
    doc_content: DocumentContent,
    project_context: str = "",
) -> List[ParsedFunctionPoint]:
    """
    从文档中智能提取功能点
    - 文档过长时分块处理
    - 保留不同位置出现的同名功能点，以便识别内部重复
    - 返回结构化数据
    """
    if not settings.deepseek_api_key:
        raise ValueError("DEEPSEEK_API_KEY 未配置，无法进行功能点提取")

    extracted_points: List[ParsedFunctionPoint] = []

    chunks = (
        [("完整文档", doc_content.raw_text)]
        if len(doc_content.raw_text) <= MAX_SECTION_CHARS else _build_extraction_chunks(doc_content)
    )

    def extract_segment(chunk_title, chunk, depth=0):
        try:
            return _extract_from_text(chunk, section_title=chunk_title, project_context=project_context)
        except ValueError as exc:
            if "finish_reason=length" not in str(exc) or depth >= 3 or len(chunk) <= 300:
                raise ValueError(f"文档分块提取不完整：{chunk_title}: {exc}") from exc
            logger.warning("文档块输出截断，继续拆分（层级 %s）：%s", depth + 1, chunk_title)
            recovered = []
            for part_index, part in enumerate(_split_chunk(chunk), 1):
                recovered.extend(extract_segment(f"{chunk_title}（片段 {part_index}）", part, depth + 1))
            return recovered

    def extract_chunk(item):
        idx, (chunk_title, chunk) = item
        logger.info("提取合并文档块 %s/%s: %s", idx, len(chunks), chunk_title)
        return extract_segment(chunk_title, chunk)

    for points in parallel_model_work(
        enumerate(chunks, 1), extract_chunk,
        message="大模型抽取功能点", start=0.02, end=0.35,
    ):
        extracted_points.extend(points)

    # 保留报告不同位置重复出现的功能点，供后续重复建设判定。
    final_points: List[ParsedFunctionPoint] = []
    for idx, point in enumerate(extracted_points, 1):
        final_points.append(
            ParsedFunctionPoint(
                row_index=idx,
                name=point.name,
                description=point.description,
                category=point.category,
                source_location=point.source_location,
            )
        )

    logger.info(f"从文档中提取了 {len(final_points)} 个功能点")
    return final_points


def _build_extraction_chunks(doc_content: DocumentContent) -> list[tuple[str, str]]:
    sections = doc_content.sections or [DocumentSection(title="完整文档", content=doc_content.raw_text)]
    chunks: list[tuple[str, str]] = []
    current_blocks: list[str] = []
    current_titles: list[str] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current_blocks, current_titles, current_chars
        if not current_blocks:
            return
        title = "、".join(current_titles[:3])
        if len(current_titles) > 3:
            title += f"等{len(current_titles)}节"
        chunks.append((title or "文档片段", "\n\n".join(current_blocks)))
        current_blocks, current_titles, current_chars = [], [], 0

    for section in sections:
        content = section.content.strip()
        if not content:
            continue
        title = section.title.strip() or "未命名章节"
        content_limit = max(300, MAX_SECTION_CHARS - len(title) - 20)
        for offset in range(0, len(content), content_limit):
            segment = content[offset : offset + content_limit]
            part_number = offset // content_limit + 1
            part_title = title if len(content) <= content_limit else f"{title}（片段 {part_number}）"
            block = f"【章节：{part_title}】\n{segment}"
            if current_blocks and current_chars + len(block) + 2 > MAX_SECTION_CHARS:
                flush()
            current_blocks.append(block)
            current_titles.append(part_title)
            current_chars += len(block) + 2
    flush()
    return chunks


def _split_chunk(text: str) -> list[str]:
    """Split close to the midpoint while preserving paragraph boundaries."""
    midpoint = len(text) // 2
    newline = text.rfind("\n", 0, midpoint)
    if newline < max(1, midpoint // 2):
        newline = text.find("\n", midpoint)
    if newline < 1 or newline >= len(text) - 1:
        newline = midpoint
    return [text[:newline].strip(), text[newline:].strip()]


def _extract_from_text(
    text: str,
    section_title: str = "",
    project_context: str = "",
    previous_points: Optional[List[ParsedFunctionPoint]] = None,
) -> List[ParsedFunctionPoint]:
    """从文本中提取功能点"""
    prompt = _build_extraction_prompt(
        text,
        section_title=section_title,
        project_context=project_context,
        previous_points=previous_points,
    )

    try:
        response = _call_deepseek_api(prompt)
        try:
            points = _parse_extraction_response(response)
            return _attach_extraction_location(points, section_title)
        except ValueError:
            retry_prompt = (
                prompt
                + "\n\n"
                + "The previous response could not be parsed. Return only one JSON object with a top-level function_points array."
            )
            response = _call_deepseek_api(retry_prompt)
            points = _parse_extraction_response(response)
            return _attach_extraction_location(points, section_title)
    except Exception as e:
        logger.error(f"从文本提取功能点失败: {e}")
        raise ValueError(f"LLM 提取失败: {e}") from e


def _build_extraction_prompt(
    text: str,
    section_title: str = "",
    project_context: str = "",
    previous_points: Optional[List[ParsedFunctionPoint]] = None,
) -> str:
    """Extract explicit software capabilities without expanding background prose."""
    return f"""从下面原文中摘录明确提出要建设的软件功能或模块。只输出 JSON。
要求：
1. 只摘录具体建设内容、功能设计、独立交付清单或软件开发预算中的待建项。项目概况、目标、必要性、效益、总架构、总结性功能清单对这些项的复述不另提取，即使写有“建设”也不单独输出。不推导隐含功能。
2. 原文明确系统/平台/模块时，以该建设单元为一项，将它的子功能归入description，不能将每个子功能再拆成独立建设项；无明确模块时才按可独立交付的软件功能提取。同一位置的同名功能只输出一次。
3. 两个不同的明确待建模块即使功能相同也分别保留，并保留各自原文位置。只是输出结果、业务原则、复用已有能力或对已介绍模块的回顾时，不视为新的待建项。
4. name 使用原文的简短功能名称，30字以内；description 用120字以内保留该建设项的服务对象、范围和核心子功能，不把整条流程赋给每个步骤；source_quote 逐字复制原文依据，120字以内。
5. 无明确软件功能时返回空数组。不需要分类、提取说明或分析过程。
格式：{{"function_points": [{{"name": "功能名称", "description": "该功能自己的建设范围", "source_quote": "原文依据"}}]}}
章节：{section_title}
原文：
{text}"""


def _call_deepseek_api(prompt: str) -> str:
    """调用 DeepSeek API 提取功能点"""
    url = f"{settings.deepseek_api_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.deepseek_api_key}",
    }
    payload = {
        "model": settings.deepseek_model,
        # The Tongji V4-Flash route can exhaust its output budget without
        # emitting content when a system message is present.  The complete
        # role and JSON constraints already live in the user prompt.
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        # V4-Flash defaults to a large reasoning budget on this gateway.  For
        # extraction that can consume the whole token budget before any JSON
        # is emitted (finish_reason=length, empty content).
        "reasoning_effort": "low",
        "max_tokens": getattr(settings, "duplicate_extraction_max_tokens", settings.duplicate_llm_max_tokens),
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
            response = post_direct(url, headers=headers, json=payload, timeout=remaining_timeout(timeout))
            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                return response
        except Exception:
            if attempt == 2:
                raise
        if attempt < 2:
            time.sleep(2**attempt)
    assert response is not None
    return response


def _parse_extraction_response(response: str) -> List[ParsedFunctionPoint]:
    """解析 LLM 响应中的功能点"""
    try:
        data = parse_json_object_from_text(_strip_think_blocks(response))
        function_points = data.get("function_points")
        if not isinstance(function_points, list):
            raise ValueError("LLM response missing function_points array")

        parsed_points: List[ParsedFunctionPoint] = []
        for idx, item in enumerate(function_points, 1):
            if not isinstance(item, dict):
                continue
            name = _text_value(item.get("name"), "")
            description = _text_value(item.get("description"), "") or _text_value(item.get("source_quote"), "")
            category = _text_value(item.get("category"), "")
            source_quote = _text_value(item.get("source_quote"), "")

            if name:  # 只有名称非空才有效
                parsed_points.append(
                    ParsedFunctionPoint(
                        row_index=idx,
                        name=name,
                        description=description,
                        category=category or None,
                        source_location=(
                            {"quote": source_quote, "precision": "quote"}
                            if source_quote else None
                        ),
                    )
                )

        return parsed_points
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as e:
        logger.error(f"解析 LLM 响应失败: {e}, 响应内容: {response[:500]}")
        raise ValueError(f"无法解析 LLM 响应") from e


def _attach_extraction_location(
    points: list[ParsedFunctionPoint],
    section_title: str,
) -> list[ParsedFunctionPoint]:
    output: list[ParsedFunctionPoint] = []
    for point in points:
        location = dict(point.source_location or {})
        if section_title:
            location.setdefault("extraction_section", section_title)
        output.append(replace(point, source_location=location or None))
    return output


def _extract_message_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("LLM response missing choices")
    message = (choices[0] or {}).get("message") or {}
    finish_reason = (choices[0] or {}).get("finish_reason")
    if finish_reason == "length":
        raise ValueError("LLM response truncated, finish_reason=length")
    value = message.get("content")
    if value is not None and str(value).strip():
        return str(value).strip()
    raise ValueError(f"LLM response missing final content, finish_reason={finish_reason}")


def _strip_think_blocks(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.IGNORECASE | re.DOTALL).strip()


def _text_value(value: object, default: str) -> str:
    if value is None:
        return default
    return str(value).strip() or default
