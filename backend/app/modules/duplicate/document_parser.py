from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from typing import List, Optional
from docx import Document


@dataclass(frozen=True)
class DocumentSection:
    title: str
    content: str
    start_line: int = 0
    end_line: int = 0
    page_no: Optional[int] = None


@dataclass(frozen=True)
class DocumentSourceBlock:
    text: str
    paragraph: int
    line: int
    section: str = ""


@dataclass(frozen=True)
class DocumentContent:
    format: str  # "docx"
    raw_text: str
    sections: List[DocumentSection]
    filename: str
    source_blocks: List[DocumentSourceBlock] = field(default_factory=list)


def parse_document(content: bytes, filename: str) -> DocumentContent:
    """根据文件扩展名选择合适的解析器"""
    if filename.lower().endswith(".docx"):
        return _parse_docx(content, filename)
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

    table_text_parts = _extract_docx_table_text(doc)
    if table_text_parts:
        raw_text_parts.extend(table_text_parts)
        current_section_content.extend(table_text_parts)

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
        source_blocks=_extract_docx_source_blocks(doc),
    )


def _extract_docx_source_blocks(doc: Document) -> list[DocumentSourceBlock]:
    """Build locations with the same paragraph/line numbering as the preview."""
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph

    blocks: list[DocumentSourceBlock] = []
    paragraph_index = 0
    line_index = 0
    current_section = ""

    def register(text: str) -> tuple[int, int]:
        nonlocal paragraph_index, line_index
        paragraph_index += 1
        if text.strip():
            line_index += 1
        return paragraph_index, line_index if text.strip() else 0

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            paragraph = Paragraph(child, doc)
            text = paragraph.text.strip()
            location = register(text)
            style_name = str(getattr(paragraph.style, "name", "") or "").lower()
            if text and ("heading" in style_name or "标题" in style_name):
                current_section = text
            if text:
                blocks.append(DocumentSourceBlock(text, location[0], location[1], current_section))
        elif isinstance(child, CT_Tbl):
            table = Table(child, doc)
            for row_xml in child.tr_lst:
                row_texts: list[str] = []
                row_locations: list[tuple[int, int]] = []
                for cell_xml in row_xml.tc_lst:
                    cell = _Cell(cell_xml, table)
                    cell_parts: list[str] = []
                    for paragraph in cell.paragraphs:
                        text = paragraph.text.strip()
                        row_locations.append(register(text))
                        if text:
                            cell_parts.append(text)
                    if cell_parts:
                        row_texts.append(" ".join(cell_parts))
                if row_texts:
                    first = next((item for item in row_locations if item[1] > 0), row_locations[0])
                    blocks.append(DocumentSourceBlock(" ".join(row_texts), first[0], first[1], current_section))
    return blocks


def _extract_docx_table_text(doc: Document) -> list[str]:
    rows: list[str] = []
    for table in doc.tables:
        for row in table.rows:
            values = [
                cell.text.strip().replace("\n", " ")
                for cell in row.cells
                if cell.text and cell.text.strip()
            ]
            if values:
                rows.append(" | ".join(values))
    return rows
