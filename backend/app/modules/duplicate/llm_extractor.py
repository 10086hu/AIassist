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


logger = logging.getLogger(__name__)

# V4-Flash reliably completes structured extraction for blocks around 4k
# characters.  Larger blocks can consume the gateway output budget and return
# finish_reason=length with no final content.
MAX_SECTION_CHARS = 4000


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

    # 短文档一次性提取；长文档将相邻小章节合并，避免每个章节各发一次请求。
    if len(doc_content.raw_text) < 5000:
        # 一次性提取
        points = _extract_from_text(
            doc_content.raw_text,
            section_title="完整文档",
            project_context=project_context,
        )
        extracted_points.extend(points)
    else:
        errors: list[str] = []
        chunks = _build_extraction_chunks(doc_content)
        for idx, (chunk_title, chunk) in enumerate(chunks, 1):
            logger.info("提取合并文档块 %s/%s: %s", idx, len(chunks), chunk_title)
            try:
                points = _extract_from_text(
                    chunk,
                    section_title=chunk_title,
                    project_context=project_context,
                )
                extracted_points.extend(points)
            except ValueError as exc:
                # V4-Flash can occasionally spend its whole output budget on a
                # dense block. Retry that block as two smaller requests before
                # giving up, so one transient truncation does not erase the
                # duplicate module from the review.
                logger.warning("文档块 %s/%s 提取失败，拆分后重试: %s", idx, len(chunks), exc)
                recovered = False
                for part_idx, part in enumerate(_split_chunk(chunk), 1):
                    try:
                        part_points = _extract_from_text(
                            part,
                            section_title=f"{chunk_title}（重试片段 {part_idx}）",
                            project_context=project_context,
                        )
                        extracted_points.extend(part_points)
                        recovered = recovered or bool(part_points)
                    except ValueError as part_exc:
                        errors.append(f"{chunk_title}（片段 {part_idx}）: {part_exc}")
                        logger.warning("跳过提取失败的重试片段 %s/%s: %s", part_idx, 2, part_exc)
                if not recovered:
                    errors.append(f"{chunk_title}: {exc}")
        if not extracted_points and errors:
            raise ValueError("所有文档分块的大模型功能点提取均失败：" + "; ".join(errors[:3]))

    # 保留报告不同位置重复出现的功能点，供后续重复建设判定。
    final_points: List[ParsedFunctionPoint] = []
    for idx, point in enumerate(extracted_points, 1):
        final_points.append(
            ParsedFunctionPoint(
                row_index=idx,
                name=point.name,
                description=point.description,
                category=point.category,
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
        content_limit = max(1000, MAX_SECTION_CHARS - len(title) - 20)
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
    """构建功能点提取提示词"""
    previous_context = ""
    if previous_points:
        prev_names = [p.name for p in previous_points[-5:]]  # 最近 5 个
        previous_context = f"""

已提取的功能点（避免重复）：
{json.dumps(prev_names, ensure_ascii=False, indent=2)}

注意：不要重复提取上面已提取的功能点。"""

    return f"""你是政府信息化项目可研报告的分析专家。请从以下文本中提取所有功能点。

功能点定义：指系统或应用需要实现的具体功能，包括但不限于：
- 用户认证、权限管理、数据交换等核心业务功能
- 报表、监控、日志等支撑功能
- 不包括硬件配置、采购清单等非功能项
- 不包括技术指标或性能要求
- 同一功能在不同位置重复申报时，请分别输出，不要合并或去重

{f"项目上下文：{project_context}" if project_context else ""}

{f"章节标题：{section_title}" if section_title else ""}{previous_context}

请返回严格的 JSON 格式，包含以下字段：
- function_points: 功能点列表
  - name: 功能点名称（简洁，20 字以内）
  - description: 详细描述（1-2 句话）
  - category: 所属模块或分类
  - source_quote: 能直接证明该功能点的原文句子或表格行，必须从输入文本逐字复制，不得改写，120 字以内

响应格式示例：
{{
  "function_points": [
    {{
      "name": "统一身份认证",
      "description": "实现用户账号集中管理和单点登录功能",
      "category": "基础设施",
      "source_quote": "建设统一身份认证能力，实现用户账号集中管理和单点登录。"
    }},
    {{
      "name": "权限管理",
      "description": "提供基于角色的访问控制机制",
      "category": "基础设施"
    }}
  ],
  "extraction_notes": "提取时的说明（如有特殊情况）"
}}

文本内容：
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


def _parse_extraction_response(response: str) -> List[ParsedFunctionPoint]:
    """解析 LLM 响应中的功能点"""
    try:
        data = parse_json_object_from_text(_strip_think_blocks(response))
        function_points = data.get("function_points", [])

        parsed_points: List[ParsedFunctionPoint] = []
        for idx, item in enumerate(function_points, 1):
            if not isinstance(item, dict):
                continue
            name = _text_value(item.get("name"), "")
            description = _text_value(item.get("description"), "")
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
    value = message.get("content")
    if value is not None and str(value).strip():
        return str(value).strip()
    finish_reason = (choices[0] or {}).get("finish_reason")
    raise ValueError(f"LLM response missing final content, finish_reason={finish_reason}")


def _strip_think_blocks(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.IGNORECASE | re.DOTALL).strip()


def _text_value(value: object, default: str) -> str:
    if value is None:
        return default
    return str(value).strip() or default
