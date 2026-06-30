from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional

import pdfplumber
from docx import Document


@dataclass(frozen=True)
class DocumentSection:
    title: str
    content: str


@dataclass(frozen=True)
class DocumentContent:
    format: str  # "docx" | "pdf"
    raw_text: str
    sections: List[DocumentSection]
    filename: str


def parse_document(content: bytes, filename: str) -> DocumentContent:
    """根据文件扩展名选择合适的解析器"""
    if filename.lower().endswith(".docx"):
        return _parse_docx(content, filename)
    elif filename.lower().endswith(".pdf"):
        return _parse_pdf(content, filename)
    else:
        raise ValueError(f"不支持的文件格式: {filename}")


def _parse_docx(content: bytes, filename: str) -> DocumentContent:
    """解析 Word 文档"""
    try:
        doc = Document(BytesIO(content))
    except Exception as e:
        raise ValueError(f"Word 文档解析失败: {e}") from e

    sections: List[DocumentSection] = []
    raw_text_parts = []

    current_section_title = ""
    current_section_content = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        # 简单启发式：检测标题（可配置样式判断）
        if para.style and "heading" in para.style.name.lower():
            # 保存前一个章节
            if current_section_content:
                section_text = "\n".join(current_section_content)
                sections.append(
                    DocumentSection(title=current_section_title, content=section_text)
                )
                current_section_content = []
            current_section_title = text
        else:
            current_section_content.append(text)
            raw_text_parts.append(text)

    # 保存最后一个章节
    if current_section_content:
        section_text = "\n".join(current_section_content)
        sections.append(
            DocumentSection(title=current_section_title, content=section_text)
        )

    raw_text = "\n".join(raw_text_parts)

    return DocumentContent(
        format="docx",
        raw_text=raw_text,
        sections=sections,
        filename=filename,
    )


def _parse_pdf(content: bytes, filename: str) -> DocumentContent:
    """解析 PDF 文档"""
    try:
        pdf = pdfplumber.open(BytesIO(content))
    except Exception as e:
        raise ValueError(f"PDF 文档解析失败: {e}") from e

    sections: List[DocumentSection] = []
    raw_text_parts = []

    for page_num, page in enumerate(pdf.pages, 1):
        text = page.extract_text()
        if text:
            raw_text_parts.append(text.strip())
            sections.append(
                DocumentSection(
                    title=f"第 {page_num} 页",
                    content=text.strip(),
                )
            )

    pdf.close()

    raw_text = "\n".join(raw_text_parts)

    return DocumentContent(
        format="pdf",
        raw_text=raw_text,
        sections=sections,
        filename=filename,
    )
