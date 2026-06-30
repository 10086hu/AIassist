from __future__ import annotations

import json
import logging
from typing import List, Optional

import requests

from app.core.config import settings
from app.modules.duplicate.document_parser import DocumentContent
from app.modules.duplicate.excel_parser import ParsedFunctionPoint


logger = logging.getLogger(__name__)


def extract_function_points(
    doc_content: DocumentContent,
    project_context: str = "",
) -> List[ParsedFunctionPoint]:
    """
    从文档中智能提取功能点
    - 文档过长时分块处理
    - 自动去重
    - 返回结构化数据
    """
    if not settings.deepseek_api_key:
        raise ValueError("DEEPSEEK_API_KEY 未配置，无法进行功能点提取")

    extracted_points: List[ParsedFunctionPoint] = []
    seen_names = set()

    # 如果文档较短，一次性提取；否则分章节提取
    if len(doc_content.raw_text) < 5000:
        # 一次性提取
        points = _extract_from_text(
            doc_content.raw_text,
            section_title="完整文档",
            project_context=project_context,
        )
        extracted_points.extend(points)
    else:
        # 分章节提取
        for idx, section in enumerate(doc_content.sections, 1):
            if not section.content.strip():
                continue

            logger.info(f"提取第 {idx} 个章节: {section.title}")

            points = _extract_from_text(
                section.content,
                section_title=section.title,
                project_context=project_context,
                previous_points=extracted_points,
            )
            extracted_points.extend(points)

    # 后处理：去重和验证
    final_points: List[ParsedFunctionPoint] = []
    for idx, point in enumerate(extracted_points, 1):
        name_lower = point.name.lower().strip()
        if name_lower not in seen_names:
            seen_names.add(name_lower)
            # 更新行号
            final_points.append(
                ParsedFunctionPoint(
                    row_index=idx,
                    name=point.name,
                    description=point.description,
                    category=point.category,
                )
            )

    logger.info(f"从文档中提取了 {len(final_points)} 个功能点 (去重前: {len(extracted_points)})")
    return final_points


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
        points = _parse_extraction_response(response)
        return points
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

{f"项目上下文：{project_context}" if project_context else ""}

{f"章节标题：{section_title}" if section_title else ""}{previous_context}

请返回严格的 JSON 格式，包含以下字段：
- function_points: 功能点列表
  - name: 功能点名称（简洁，20 字以内）
  - description: 详细描述（1-2 句话）
  - category: 所属模块或分类

响应格式示例：
{{
  "function_points": [
    {{
      "name": "统一身份认证",
      "description": "实现用户账号集中管理和单点登录功能",
      "category": "基础设施"
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
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 2000,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return content.strip()


def _parse_extraction_response(response: str) -> List[ParsedFunctionPoint]:
    """解析 LLM 响应中的功能点"""
    try:
        # 尝试提取 JSON
        json_str = response
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0]

        data = json.loads(json_str)
        function_points = data.get("function_points", [])

        parsed_points: List[ParsedFunctionPoint] = []
        for idx, item in enumerate(function_points, 1):
            name = item.get("name", "").strip()
            description = item.get("description", "").strip()
            category = item.get("category", "").strip()

            if name:  # 只有名称非空才有效
                parsed_points.append(
                    ParsedFunctionPoint(
                        row_index=idx,
                        name=name,
                        description=description,
                        category=category or None,
                    )
                )

        return parsed_points
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error(f"解析 LLM 响应失败: {e}, 响应内容: {response[:500]}")
        raise ValueError(f"无法解析 LLM 响应") from e
