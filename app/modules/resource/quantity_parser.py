from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from io import BytesIO, StringIO
from typing import Iterable, Optional
from dataclasses import replace
from app.modules.docx_grid import table_grid


# 表头别名映射。
# 资源清单来自不同人员或不同模板时，列名可能不统一；这里把常见列名统一映射成
# source/name/quantity/unit/spec 五个内部字段，后续规则引擎只处理标准字段。
HEADER_ALIASES = {
    "source": {"清单", "来源", "表名", "章节", "所属章节", "资源清单", "资源来源", "表单", "类别", "分类", "所属清单", "资源类别"},
    "name": {"资源名称", "资源类型", "服务名称", "产品名称", "产品小类", "产品类别", "名称", "项目名称", "服务内容", "内容", "安全服务名称", "资源项", "服务项", "申请内容", "软件名称", "系统名称", "数据库名称"},
    "quantity": {"数量", "申请数量", "配置数量", "服务数量", "资源数量", "台数", "套数", "个数", "授权数量", "申请台数", "申请套数", "采购数量", "配套数量"},
    "unit": {"单位", "计量单位"},
    "spec": {"规格", "规格型号", "配置", "参数", "描述", "说明", "备注", "配置说明", "用途", "型号"},
}

# Word/PDF 通常不是标准表格。这里定义文档文本中可能出现的资源名称别名，
# 解析器会把这些别名统一映射成规则引擎能识别的标准资源名称。
DOCUMENT_RESOURCE_ALIASES = {
    "安全防病毒服务": ("安全防病毒服务", "防病毒服务", "病毒防护服务", "杀毒服务"),
    "安全认证网关服务": ("安全认证网关服务", "安全认证网关", "认证网关服务", "认证网关", "安全网关服务"),
    "时间戳服务": ("时间戳服务",),
    "签名验签服务": ("签名验签服务", "签名验签服务器", "电子签名服务", "验签服务"),
    "可信密码服务（数据加解密服务）": ("可信密码服务", "服务器密码机", "数据加解密服务", "加解密服务", "加密服务", "解密服务"),
    "身份认证服务": ("身份认证服务", "统一身份认证服务", "统一认证服务"),
    "数字证书服务": ("数字证书服务", "证书服务"),
    "数据库服务器": ("数据库服务器", "数据库主机", "DB服务器", "db服务器", "数据库云主机"),
    "服务器操作系统": ("服务器操作系统", "操作系统", "国产操作系统", "OS", "os", "麒麟", "kylin", "统信", "uos", "欧拉", "openEuler", "openeuler", "服务器OS"),
    "数据库软件": ("数据库软件", "数据库授权", "数据库产品", "数据库管理系统", "国产数据库", "数据库", "达梦数据库", "人大金仓", "GaussDB", "MySQL", "PostgreSQL", "Oracle"),
    "服务器": ("云服务器", "应用服务器", "服务器", "虚拟机", "计算资源", "主机", "云主机", "计算实例"),
}

# 文档章节/上下文到第 15 行“三类清单”的映射。
# 解析 Word/PDF 时，会根据当前行或章节标题推断资源来自哪一类依据。
DOCUMENT_SOURCE_ALIASES = {
    "安全服务需求表": ("安全服务需求表", "安全服务需求", "安全建设内容", "6.6", "六.六", "第六章安全建设"),
    "PaaS服务清单": ("PaaS服务清单", "PaaS服务", "PaaS 服务", "服务工具箱", "同行业成熟产品", "6.7", "六.七"),
    "密码服务资源内容清单": ("密码服务资源内容清单", "密码服务资源", "密码资源清单", "密码服务清单"),
}

QUANTITY_PATTERN = re.compile(
    r"(?<![\d.])(-?\d+(?:\.\d+)?)\s*"
    r"(项|套|台|个|份|张|次|年|月|人|核|线程|块|颗|路|副本|G|GB|T|TB|GHz|MHz|QPS|ms|毫秒|秒)?",
    re.IGNORECASE,
)

SECTION_NUMBER_PATTERN = re.compile(r"(?<!\d)\d+(?:\.\d+){1,3}(?!\d)")

RESOURCE_CONTEXT_KEYWORDS = (
    "资源申请",
    "资源清单",
    "资源内容清单",
    "云资源",
    "计算资源",
    "安全服务需求",
    "安全服务清单",
    "PaaS服务",
    "PaaS 服务",
    "PaaS服务清单",
    "服务工具箱",
    "密码服务资源",
    "密码资源清单",
    "密码服务清单",
    "三大件",
    "资源项",
    "服务项",
    "服务器操作系统",
    "操作系统",
    "数据库服务器",
    "数据库软件",
    "数据库管理系统",
    "云服务器",
    "云主机",
)

RESOURCE_TABLE_HEADER_KEYWORDS = (
    "资源名称",
    "资源类型",
    "服务名称",
    "申请数量",
    "配置数量",
    "资源数量",
    "服务数量",
    "申请台数",
    "申请套数",
    "采购数量",
    "配套数量",
)

NON_RESOURCE_TABLE_KEYWORDS = (
    "视频监控",
    "摄像机",
    "防水箱",
    "立杆",
    "网线",
    "辅材",
    "硬盘",
    "显示屏",
    "解码器",
    "品牌",
    "型号",
    "单价",
    "合价",
    "报价",
    "预算",
    "概算",
    "万元",
)

QUANTITY_FIELD_NAMES = {"quantity", "数量", "申请数量", "配置数量", "服务数量", "资源数量", "台数", "套数", "个数", "授权数量", "申请台数", "申请套数", "采购数量", "配套数量"}
MAX_REASONABLE_RESOURCE_QUANTITY = 10000.0
RESOURCE_COUNT_UNITS = {"项", "套", "台", "个", "份", "张", "次", "套/台"}
SPEC_UNITS = {"核", "G", "GB", "T", "TB", "年", "月", "人"}
QUANTITY_CONTEXT_KEYWORDS = ("数量", "台数", "套数", "个数", "申请", "配置", "采购", "配套", "共", "合计")
NON_RESOURCE_COUNT_UNITS = {
    "核", "线程", "块", "颗", "路", "副本",
    "g", "gb", "t", "tb", "ghz", "mhz", "qps", "ms", "毫秒", "秒",
    "年", "月", "人",
}
SPEC_CONTEXT_KEYWORDS = (
    "raid", "ssd", "hdd", "cpu", "gpu", "内存", "硬盘", "系统盘", "数据盘",
    "主频", "线程", "副本", "qps", "响应时间", "延迟", "数据量", "容量",
)
GENERIC_RESOURCE_NAMES = {"物理机", "虚拟机", "主机", "服务器", "PC服务器", "硬件产品", "产品软件"}


@dataclass(frozen=True)
class ParsedResourceItem:
    """解析后的单条资源清单记录。

    这个对象是“解析层”和“规则层”的边界：
    - 解析层负责把 Excel/CSV 的各种格式转成 ParsedResourceItem。
    - 规则层只依赖这个标准对象，不再关心原始文件格式。
    """

    # 原始 Excel/CSV 中的数据行号，用于返回问题定位。
    row_index: int
    # Excel 工作表名；CSV 统一记为 "CSV"。
    sheet_name: str
    # 清单来源，例如“安全服务需求表”“PaaS服务清单”“密码服务资源内容清单”。
    source: str
    # 资源或服务名称，例如“安全防病毒服务”“云服务器”“数据库软件”。
    name: str
    # 数量统一转成 float，方便规则层做加总和比较。
    quantity: float
    # 计量单位，例如“项”“台”“套”。
    unit: str = "项"
    # 规格、配置、备注等辅助描述。
    spec: str = ""
    # 原始行文本拼接，用于写入数据库便于追溯。
    raw_text: str = ""


def parse_resource_items(content: bytes, filename: str) -> list[ParsedResourceItem]:
    """根据文件扩展名选择解析器，并返回标准化资源项列表。"""

    lower_name = filename.lower()
    if lower_name.endswith(".csv"):
        return _dedupe_items(_parse_csv(content))
    if lower_name.endswith(".xlsx"):
        return _dedupe_items(_parse_xlsx(content))
    if lower_name.endswith(".docx"):
        return _dedupe_items(_parse_docx(content))
    if lower_name.endswith(".pdf"):
        return _dedupe_items(_parse_pdf(content))
    raise ValueError("资源申请合理性检查支持 .xlsx、.csv、.docx 或 .pdf 文件")


def _parse_xlsx(content: bytes) -> list[ParsedResourceItem]:
    """解析 Excel 文件。

    一个 Excel 可能有多个工作表，所以逐个 sheet 解析后合并结果。
    data_only=True 表示如果单元格是公式，优先读取公式计算后的显示值。
    """

    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(content), data_only=True)
    items: list[ParsedResourceItem] = []
    for sheet in workbook.worksheets:
        rows = list(sheet.iter_rows(values_only=True))
        items.extend(_parse_rows(rows, sheet.title))
    return items


def _parse_csv(content: bytes) -> list[ParsedResourceItem]:
    """解析 CSV 文件。

    utf-8-sig 可以兼容 Excel 导出的带 BOM 的 CSV，避免第一列表头出现隐藏字符。
    """

    text = content.decode("utf-8-sig")
    rows = list(csv.reader(StringIO(text)))
    return _parse_rows(rows, "CSV")


def _parse_docx(content: bytes) -> list[ParsedResourceItem]:
    """解析 Word 文档中的资源项。

    处理两类内容：
    - 表格：按 Excel/CSV 的二维表逻辑解析，适合资源清单表格。
    - 段落：按文本行扫描，适合报告正文里写“云服务器4台”等描述。
    """

    from docx import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.text.paragraph import Paragraph

    document = Document(BytesIO(content))
    items: list[ParsedResourceItem] = []

    paragraph_lines = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    items.extend(_parse_text_lines(paragraph_lines, "DOCX正文", require_resource_context=True))

    caption = ""
    table_index = 0
    previous_header = None
    previous_scope = ""
    continuation = False
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            text = Paragraph(child, document).text.strip()
            if text:
                continuation = "续表" in text or "（续）" in text
                if len(text) < 150:
                    caption = text
                if not continuation:
                    previous_header = None
            continue
        if not isinstance(child, CT_Tbl):
            continue
        table_index += 1
        rows = table_grid(child)
        sheet_name = f"DOCX表格{table_index}"
        if continuation and previous_header and rows and len(rows[0]) == len(previous_header):
            if not {"name", "quantity"}.issubset(_detect_explicit_header_fields(rows[:1])):
                rows = [previous_header, *rows]
                sheet_name = previous_scope
        if not _is_resource_related_table(rows):
            continue
        table_items = _parse_rows(rows, sheet_name, require_resource_context=True)
        bucket = _infer_document_source(caption)
        if bucket:
            table_items = [replace(x, source=bucket) for x in table_items]
        hi, _ = _find_header(rows)
        previous_header, previous_scope = rows[hi], sheet_name
        continuation = False
        if table_items:
            items.extend(table_items)
        else:
            table_lines = [" ".join(cell for cell in row if cell) for row in rows]
            items.extend(_parse_text_lines(table_lines, sheet_name, require_resource_context=True))

    return items


def _parse_pdf(content: bytes) -> list[ParsedResourceItem]:
    """解析 PDF 文档中的资源项。

    PDF 的表格结构经常丢失，所以这里同时尝试：
    - extract_tables：能识别出表格时按二维表解析。
    - extract_text：无法稳定识别表格时按文本行解析。
    """

    import pdfplumber

    items: list[ParsedResourceItem] = []
    with pdfplumber.open(BytesIO(content)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            sheet_name = f"PDF第{page_index}页"

            for table_index, table in enumerate(page.extract_tables() or [], start=1):
                if not _is_resource_related_table(table):
                    continue
                table_items = _parse_rows(table, f"{sheet_name}表格{table_index}", require_resource_context=True)
                items.extend(table_items)

            text = page.extract_text() or ""
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            items.extend(_parse_text_lines(lines, sheet_name, require_resource_context=True))

    return items


def _parse_rows(
    rows: Iterable[Iterable[object]],
    sheet_name: str,
    require_resource_context: bool = False,
) -> list[ParsedResourceItem]:
    """把二维表格行解析为资源项。

    处理策略：
    1. 删除完全空行。
    2. 在前 12 行中自动识别表头。
    3. 根据表头映射提取来源、资源名称、数量、单位和规格。
    4. 如果数量列为空，尝试从资源名称或规格文本中兜底提取数字。
    """

    # materialized 是去掉空行后的二维数组，便于多次扫描和按索引访问。
    materialized = [list(row) for row in rows if any(_cell_text(cell) for cell in row)]
    if not materialized:
        return []
    if require_resource_context and not _is_resource_related_table(materialized):
        return []

    # 自动识别表头位置和字段列号。
    header_index, mapping = _find_header(materialized)
    has_explicit_quantity_column = "quantity" in mapping and _looks_like_quantity_header(_value_at(materialized[header_index], mapping.get("quantity")))
    items: list[ParsedResourceItem] = []
    for offset, row in enumerate(materialized[header_index + 1 :], start=header_index + 2):
        name = _value_at(row, mapping.get("name"))
        raw_text = " ".join(_cell_text(cell) for cell in row if _cell_text(cell))
        name = _refine_resource_name_from_row(row, name)
        if not name:
            # 没有资源名称时无法参与规则匹配，直接跳过该行。
            continue

        source = _value_at(row, mapping.get("source")) or sheet_name
        quantity_text = _value_at(row, mapping.get("quantity"))
        spec = _value_at(row, mapping.get("spec"))
        if require_resource_context:
            header = materialized[header_index]
            for title in ("资源名称", "服务项", "服务名称", "产品小类", "货物名称", "设备名称", "产品名称", "名称"):
                if title in header:
                    name = _value_at(row, header.index(title)) or name
                    break
            if "服务项" in header:
                parts = [_value_at(row, header.index(h)) for h in ("服务子类", "服务项", "服务子项") if h in header]
                name = ' '.join(dict.fromkeys(p for p in parts if p)) or name
            spec = " | ".join(f"{h}：{row[i]}" for i, h in enumerate(header) if h and i < len(row) and row[i] and i != mapping.get("quantity"))
        if require_resource_context and not _is_resource_related_row(f"{source} {name} {spec} {raw_text}"):
            continue
        unit = _value_at(row, mapping.get("unit")) or _guess_unit(quantity_text) or "项"
        if re.search(r'人\s*[/／]?\s*(?:月|天|日|年)', quantity_text):
            unit = '人月'
        if require_resource_context:
            # Word/PDF 表格经常包含序号、单价、合价和规格参数。只有明确数量列才读取数量，
            # 避免把预算金额、章节号或规格指标误当成资源申请数量。若 PDF/Word 表格丢失表头，
            # 再在资源名称附近做一次严格兜底，只接受“台/套/个/项”等数量单位或数量上下文。
            if not has_explicit_quantity_column and not re.search(r'\d+(?:\.\d+)?\s*(?:台|套|个|项|节点|实例)', raw_text):
                continue
            quantity = _parse_quantity(quantity_text) if has_explicit_quantity_column else None
            if has_explicit_quantity_column and quantity_text.strip() in {"", "/", "-", "—", "不涉及"}:
                continue
            if quantity is None:
                quantity, guessed_unit = _parse_quantity_for_resource_row(f"{name} {spec} {raw_text}", name)
                if guessed_unit and not _value_at(row, mapping.get("unit")):
                    unit = guessed_unit
            if quantity is None or not _is_reasonable_resource_quantity(quantity):
                continue
        else:
            # Excel/CSV 通常是用户明确上传的资源清单，保留原有兜底能力。
            quantity = _parse_quantity(quantity_text) or _parse_quantity(f"{name} {spec}") or 0.0

        items.append(
            ParsedResourceItem(
                row_index=offset,
                sheet_name=sheet_name,
                source=source,
                name=name,
                quantity=quantity,
                unit=unit,
                spec=spec,
                raw_text=raw_text,
            )
        )
    return items


def _parse_text_lines(
    lines: list[str],
    sheet_name: str,
    require_resource_context: bool = False,
) -> list[ParsedResourceItem]:
    """从 Word/PDF 的自然语言文本中抽取资源项。

    文本解析是启发式的，不要求固定表头。它主要服务于第 15/16 行规则：
    - 先根据章节或行内容识别来源，例如 6.6、6.7、密码服务资源内容清单。
    - 再识别资源名称，例如安全防病毒服务、云服务器、操作系统、数据库。
    - 最后在资源名称附近提取数量。
    """

    items: list[ParsedResourceItem] = []
    current_source = ""
    resource_context_active = False

    for line_index, original_line in enumerate(lines, start=1):
        line = _normalize_line(original_line)
        if not line:
            continue

        inferred_source = _infer_document_source(line)
        if inferred_source:
            current_source = inferred_source
            resource_context_active = True
        elif require_resource_context and _is_context_heading(line):
            resource_context_active = _is_resource_context(line)
            current_source = ""

        if require_resource_context and not (resource_context_active or _is_resource_context(line)):
            continue

        for canonical_name, alias, start, end in _find_document_resources(line):
            quantity, unit = _parse_quantity_near_alias(line, alias, start, end, strict=require_resource_context)
            if quantity is None:
                continue
            unit = _normalize_resource_count_unit(unit)
            if require_resource_context and not unit:
                continue
            if unit and unit not in RESOURCE_COUNT_UNITS:
                continue
            if require_resource_context and not _is_reasonable_resource_quantity(quantity):
                continue

            items.append(
                ParsedResourceItem(
                    row_index=line_index,
                    sheet_name=sheet_name,
                    source=inferred_source or current_source or sheet_name,
                    name=canonical_name,
                    quantity=quantity,
                    unit=unit or "项",
                    spec=alias,
                    raw_text=original_line.strip(),
                )
            )

    return items


def _find_header(rows: list[list[object]]) -> tuple[int, dict[str, int]]:
    """在前 12 行中寻找最像表头的行。

    评分方法很简单：某一行匹配到的标准字段越多，越可能是表头。
    如果没有找到必要字段，会用常见列位置做兜底，保证简单清单也能解析。
    """

    best_index = 0
    best_mapping: dict[str, int] = {}
    best_score = -1

    # 预先标准化别名，避免在循环里重复处理。
    normalized_aliases = {
        field: {_normalize_header(alias) for alias in aliases}
        for field, aliases in HEADER_ALIASES.items()
    }

    for index, row in enumerate(rows[:12]):
        mapping: dict[str, int] = {}
        for col_index, cell in enumerate(row):
            normalized = _normalize_header(_cell_text(cell))
            for field, aliases in normalized_aliases.items():
                if normalized in aliases or any(alias and alias in normalized for alias in aliases):
                    mapping[field] = col_index
        score = len(mapping)
        if score > best_score:
            best_index = index
            best_mapping = mapping
            best_score = score

    # 兜底规则：如果没有表头或表头不标准，默认第 1/2/3 列是来源/名称/数量。
    if "name" not in best_mapping:
        best_mapping["name"] = 1 if _row_width(rows[best_index]) > 2 else 0
    if "quantity" not in best_mapping:
        best_mapping["quantity"] = 2 if _row_width(rows[best_index]) > 2 else best_mapping["name"]
    if "source" not in best_mapping and _row_width(rows[best_index]) > 2:
        best_mapping["source"] = 0
    return best_index, best_mapping


def _value_at(row: list[object], index: Optional[int]) -> str:
    """安全读取某一列，越界或缺失时返回空字符串。"""

    if index is None or index >= len(row):
        return ""
    return _cell_text(row[index])


def _cell_text(value: object) -> str:
    """把 Excel/CSV 单元格值转成干净字符串。"""

    if value is None:
        return ""
    return str(value).strip()


def _normalize_header(value: str) -> str:
    """标准化表头，忽略空格、换行和大小写差异。"""

    return value.replace(" ", "").replace("\n", "").replace("\t", "").strip().lower()


def _row_width(row: list[object]) -> int:
    """统计一行中非空单元格数量，用于判断是否有足够列做兜底映射。"""

    return len([cell for cell in row if _cell_text(cell)])


def _parse_quantity(value: str) -> Optional[float]:
    """从文本中提取数量。

    支持：
    - 阿拉伯数字：2、2.5、1,000
    - 简单中文数字：一、二、两、十等
    """

    if not value:
        return None
    compact = value.replace(",", "")
    match = re.search(r"(?<![\d.])-?\d+(?:\.\d+)?", compact)
    if match:
        return float(match.group(0))

    return _parse_chinese_quantity(value)


def _guess_unit(value: str) -> str:
    """从“2台”“3套”这类文本中提取单位。"""

    if not value:
        return ""
    match = re.search(r"\d+(?:\.\d+)?\s*([\u4e00-\u9fff]{1,3})", value)
    return match.group(1) if match else ""


def _normalize_line(value: str) -> str:
    """标准化 Word/PDF 文本行，减少换行、空格和标点差异。"""

    return re.sub(r"\s+", " ", value).strip()


def _infer_document_source(value: str) -> str:
    """根据章节或上下文文本判断资源来自哪类清单。"""

    normalized = re.sub(r"\s+", "", value.lower())
    if "paas安全服务需求" in normalized:
        return "安全服务需求表"
    if "paas服务项清单" in normalized:
        return "PaaS服务清单"
    for source, aliases in DOCUMENT_SOURCE_ALIASES.items():
        if any(alias.lower() in normalized for alias in aliases if not re.fullmatch(r"\d+\.\d+", alias)):
            return source
    return ""


def _find_document_resources(value: str) -> list[tuple[str, str, int, int]]:
    """识别一行文本里出现的资源名称。

    为避免“数据库服务器”同时被识别成“数据库”和“服务器”，别名按长度降序匹配，
    并跳过已经被更长别名占用的文本范围。
    """

    candidates: list[tuple[str, str, int, int]] = []
    for canonical, aliases in DOCUMENT_RESOURCE_ALIASES.items():
        for alias in aliases:
            for match in re.finditer(re.escape(alias), value, flags=re.IGNORECASE):
                candidates.append((canonical, alias, match.start(), match.end()))

    candidates.sort(key=lambda item: (item[3] - item[2]), reverse=True)

    selected: list[tuple[str, str, int, int]] = []
    occupied: list[tuple[int, int]] = []
    for candidate in candidates:
        _, _, start, end = candidate
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        selected.append(candidate)
        occupied.append((start, end))

    return sorted(selected, key=lambda item: item[2])


def _parse_quantity_near_alias(
    value: str,
    alias: str,
    start: int,
    end: int,
    strict: bool = False,
) -> tuple[Optional[float], str]:
    """在资源名称附近提取数量和单位。

    优先读取资源名称后面的数字，例如“云服务器4台”；
    再读取资源名称前面的数字，例如“4台云服务器”；
    最后才在整行去掉章节号后兜底提取数字。
    """

    after = _quantity_window_after_alias(value, end)
    before = _quantity_window_before_alias(value, start)

    # Prefer the number directly adjacent to this alias.  This matters for
    # compact rows such as "12套操作系统、2套数据库、2套中间件": a broad
    # fallback window can otherwise reuse the first quantity for every item.
    adjacent_before = re.search(
        r"(\d+(?:\.\d+)?)\s*(项|套|台|个|份|张|次)?\s*$",
        before[-24:],
        flags=re.IGNORECASE,
    )
    if adjacent_before and (adjacent_before.group(2) or not strict):
        return float(adjacent_before.group(1)), adjacent_before.group(2) or _guess_unit(before[-24:])
    adjacent_after = re.match(
        r"\s*(\d+(?:\.\d+)?)\s*(项|套|台|个|份|张|次)?",
        after,
        flags=re.IGNORECASE,
    )
    if adjacent_after and (adjacent_after.group(2) or not strict) and not re.match(r"\s*\d+\.\d+\.\d+", after):
        return float(adjacent_after.group(1)), adjacent_after.group(2) or _guess_unit(after[:24])

    before_matches = list(_iter_resource_quantity_matches(before, strict=strict))
    if before_matches:
        match = before_matches[-1]
        if (not strict or _has_explicit_resource_quantity_context(before, match)) and (
            not strict or len(before) - match.end() <= 4
        ):
            return float(match.group(1)), match.group(2) or _guess_unit(before)

    after_match = _select_resource_quantity_match(after, strict=strict)
    if after_match and (not strict or _has_explicit_resource_quantity_context(after, after_match)):
        return float(after_match.group(1)), after_match.group(2) or _guess_unit(after)

    if before_matches and (not strict or _has_explicit_resource_quantity_context(before, before_matches[-1])):
        match = before_matches[-1]
        return float(match.group(1)), match.group(2) or _guess_unit(before)

    if strict:
        return None, ""

    value_without_sections = SECTION_NUMBER_PATTERN.sub("", value)
    fallback_match = _select_resource_quantity_match(value_without_sections, strict=False)
    if fallback_match:
        return float(fallback_match.group(1)), fallback_match.group(2) or _guess_unit(value_without_sections)

    quantity = _parse_quantity(value_without_sections)
    return quantity, _guess_unit(value_without_sections)


def _is_resource_related_table(rows: Iterable[Iterable[object]]) -> bool:
    materialized = [list(row) for row in rows if any(_cell_text(cell) for cell in row)]
    if not materialized:
        return False
    first_rows_text = " ".join(" ".join(_cell_text(cell) for cell in row) for row in materialized[:8])
    all_text = " ".join(" ".join(_cell_text(cell) for cell in row) for row in materialized[:20])
    explicit_fields = _detect_explicit_header_fields(materialized)
    if {"name", "quantity"}.issubset(explicit_fields) and (
        _is_resource_context(all_text) or bool(_find_document_resources(all_text))
    ):
        return True
    if {"name", "quantity", "spec"}.issubset(explicit_fields):
        return True
    if any(keyword.lower() in first_rows_text.lower() for keyword in RESOURCE_CONTEXT_KEYWORDS):
        return True
    if any(keyword in first_rows_text for keyword in RESOURCE_TABLE_HEADER_KEYWORDS) and any(
        keyword.lower() in all_text.lower() for keyword in RESOURCE_CONTEXT_KEYWORDS
    ):
        return True
    if any(keyword in all_text for keyword in NON_RESOURCE_TABLE_KEYWORDS) and not any(
        keyword.lower() in all_text.lower() for keyword in RESOURCE_CONTEXT_KEYWORDS
    ):
        return False
    return False


def _is_resource_related_row(value: str) -> bool:
    return _is_resource_context(value) or bool(_find_document_resources(value))


def _is_resource_context(value: str) -> bool:
    normalized = value.lower()
    return any(keyword.lower() in normalized for keyword in RESOURCE_CONTEXT_KEYWORDS)


def _is_context_heading(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) <= 45 and re.match(r"^(第?[一二三四五六七八九十\d]+[章节条、.．)]|\d+(?:\.\d+){0,3})", stripped):
        return True
    return any(keyword in stripped for keyword in ("表", "清单", "章节", "建设内容", "资源"))


def _looks_like_quantity_header(value: str) -> bool:
    normalized = _normalize_header(value)
    normalized_names = {_normalize_header(item) for item in QUANTITY_FIELD_NAMES}
    return normalized in normalized_names or any(name and name in normalized for name in normalized_names)


def _is_reasonable_resource_quantity(value: float) -> bool:
    return 0 <= float(value) <= MAX_REASONABLE_RESOURCE_QUANTITY


def _dedupe_items(items: list[ParsedResourceItem]) -> list[ParsedResourceItem]:
    """去除重复解析结果。

    Word/PDF 可能同时从表格和文本层读到同一行内容；这里按核心字段去重，
    避免同一资源数量被重复累计。
    """

    deduped: list[ParsedResourceItem] = []
    seen: set[tuple[str, str, float, str]] = set()
    for item in items:
        key = (
            item.sheet_name,
            item.row_index,
            _normalize_header(item.source),
            _normalize_header(item.name),
            round(item.quantity, 4),
            _normalize_header(item.raw_text or item.spec),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _detect_explicit_header_fields(rows: list[list[object]]) -> set[str]:
    """识别真实表头字段，不使用兜底列位，供 Word/PDF 表格范围判断使用。"""

    normalized_aliases = {
        field: {_normalize_header(alias) for alias in aliases}
        for field, aliases in HEADER_ALIASES.items()
    }
    fields: set[str] = set()
    for row in rows[:12]:
        for cell in row:
            normalized = _normalize_header(_cell_text(cell))
            if not normalized:
                continue
            for field, aliases in normalized_aliases.items():
                if normalized in aliases or any(alias and alias in normalized for alias in aliases):
                    fields.add(field)
    return fields


def _parse_quantity_for_resource_row(value: str, resource_name: str) -> tuple[Optional[float], str]:
    """在表格表头丢失时，从资源行里严格提取数量。"""

    line = _normalize_line(value)
    resource_matches = _find_document_resources(line)
    for canonical_name, alias, start, end in resource_matches:
        if canonical_name in resource_name or resource_name in canonical_name or alias in resource_name:
            quantity, unit = _parse_quantity_near_alias(line, alias, start, end, strict=True)
            if quantity is not None:
                unit = _normalize_resource_count_unit(unit)
                if unit and unit not in RESOURCE_COUNT_UNITS:
                    continue
                return quantity, unit
    # A row without a quantity column may contain IP addresses, versions, years,
    # capacities, or other numbers belonging to the specification.  Do not scan
    # the whole row unless an explicit quantity cue is close to the number.
    match = _select_resource_quantity_match(line, strict=True) if not resource_matches else None
    if match and _has_explicit_resource_quantity_context(line, match):
        return float(match.group(1)), match.group(2) or _guess_unit(line)
    return None, ""


def _normalize_resource_count_unit(unit: str) -> str:
    value = str(unit or "").strip()
    for count_unit in RESOURCE_COUNT_UNITS:
        if value == count_unit or value.startswith(count_unit):
            return count_unit
    return value


def _has_explicit_resource_quantity_context(value: str, match: re.Match[str]) -> bool:
    unit = (match.group(2) or "").strip().lower()
    suffix = value[match.end() : match.end() + 8].strip().lower()
    if re.match(r"^(?:年|月|人月|小时|天|秒|毫秒)", suffix):
        return False
    if unit in RESOURCE_COUNT_UNITS:
        return True
    left = value[max(0, match.start() - 10) : match.start()]
    right = value[match.end() : min(len(value), match.end() + 10)]
    context = f"{left}{right}"
    return any(keyword in context for keyword in ("数量", "台数", "套数", "个数", "申请", "配置", "采购", "配套", "合计"))


def _refine_resource_name_from_row(row: list[object], current_name: str) -> str:
    """把“物理机/虚拟机/产品软件”等泛化名称替换为同一行里的具体资源名。"""

    current = _cell_text(current_name)
    if current and current not in GENERIC_RESOURCE_NAMES and _find_document_resources(current):
        return current

    candidates: list[str] = []
    for cell in row:
        text = _cell_text(cell)
        if not text or text == current or text in GENERIC_RESOURCE_NAMES:
            continue
        if len(text) <= 8 and _parse_quantity(text) is not None:
            continue
        if _find_document_resources(text):
            candidates.append(text)

    if not candidates:
        return current
    concise = [item for item in candidates if len(item) <= 32]
    concise.sort(key=lambda item: (len(item), item))
    if concise:
        return concise[0]
    candidates.sort(key=lambda item: (len(item), item))
    return candidates[0]


def _quantity_window_after_alias(value: str, end: int) -> str:
    """截取资源名后的同一描述片段，避免跨到下一个资源项取数量。"""

    window = value[end : min(len(value), end + 48)]
    delimiter_match = re.search(r"[；;。。\n\r]", window)
    if delimiter_match:
        window = window[: delimiter_match.start()]

    next_resource_starts = [
        start
        for _, _, start, _ in _find_document_resources(window)
        if start > 0
    ]
    if next_resource_starts:
        window = window[: min(next_resource_starts)]
    return window


def _quantity_window_before_alias(value: str, start: int) -> str:
    """截取资源名前的同一描述片段，避免跨上一资源项取数量。"""

    window = value[max(0, start - 32) : start]
    delimiter_positions = [
        window.rfind(delimiter)
        for delimiter in ("；", ";", "。", "\n", "\r")
    ]
    delimiter_positions = [position for position in delimiter_positions if position >= 0]
    if delimiter_positions:
        window = window[max(delimiter_positions) + 1 :]
    return window


def _iter_resource_quantity_matches(value: str, strict: bool) -> Iterable[re.Match[str]]:
    for match in QUANTITY_PATTERN.finditer(value):
        if not strict or _is_resource_quantity_match(value, match):
            yield match


def _select_resource_quantity_match(value: str, strict: bool) -> Optional[re.Match[str]]:
    matches = list(_iter_resource_quantity_matches(value, strict=strict))
    if not matches:
        return None
    if not strict:
        return matches[0]

    def score(match: re.Match[str]) -> tuple[int, int]:
        unit = match.group(2) or ""
        left = value[max(0, match.start() - 8) : match.start()]
        right = value[match.end() : min(len(value), match.end() + 8)]
        context = f"{left}{right}"
        unit_score = 2 if unit in RESOURCE_COUNT_UNITS else 0
        keyword_score = 1 if any(keyword in context for keyword in QUANTITY_CONTEXT_KEYWORDS) else 0
        return unit_score + keyword_score, -match.start()

    return max(matches, key=score)


def _is_resource_quantity_match(value: str, match: re.Match[str]) -> bool:
    unit = match.group(2) or ""
    unit_lower = unit.lower()
    left = value[max(0, match.start() - 12) : match.start()]
    right = value[match.end() : min(len(value), match.end() + 12)]
    local_context = f"{left}{right}".lower()
    if unit_lower in NON_RESOURCE_COUNT_UNITS:
        return False
    if not unit and _starts_with_non_resource_unit(right):
        return False
    if any(keyword in local_context for keyword in SPEC_CONTEXT_KEYWORDS):
        return False
    if re.search(r"每\s*$|单\s*$", left):
        return False
    if unit in RESOURCE_COUNT_UNITS:
        return True
    if unit in SPEC_UNITS:
        return False
    return any(keyword in f"{left}{right}" for keyword in QUANTITY_CONTEXT_KEYWORDS)


def _starts_with_non_resource_unit(value: str) -> bool:
    stripped = value.strip().lower()
    return any(stripped.startswith(unit) for unit in NON_RESOURCE_COUNT_UNITS)


def _parse_chinese_quantity(value: str) -> Optional[float]:
    """解析常见中文数量，覆盖一、两、十、十五、二十、二十一、一百等轻量场景。"""

    digit_map = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    match = re.search(r"[零〇一二两三四五六七八九十百]+", value)
    if not match:
        return None
    token = match.group(0)
    total = 0
    current = 0
    for char in token:
        if char in digit_map:
            current = digit_map[char]
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        elif char == "百":
            total += (current or 1) * 100
            current = 0
    total += current
    return float(total) if total >= 0 else None


# Evidence coordinates are part of document parsing, independent of quantities.
from collections import Counter
import unicodedata
from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

MAX_LOCATIONS = 40
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

def compact(text):
    return re.sub(r'[\s|｜]+', '', unicodedata.normalize('NFKC', str(text or '')))


def heading_level(paragraph):
    """Prefer Word outline metadata; numbering is a fallback for plain text."""
    text = paragraph.text.strip()
    if not text or len(text) > 160:
        return None
    nodes = [paragraph._p]
    direct = paragraph._p.find(W+'pPr/'+W+'outlineLvl')
    if direct is not None:
        value = int(direct.get(qn('w:val'), '9'))
        return value + 1 if value < 9 else None
    style = paragraph.style
    visited = set()
    while style is not None and style.style_id not in visited:
        visited.add(style.style_id)
        match = re.search(r'^(?:Heading|标题)\s*([1-9])$', style.name, re.I)
        if match:
            return int(match[1])
        nodes.append(style.element)
        style = style.base_style
    for node in nodes:
        outline = node.find(W+'pPr/'+W+'outlineLvl')
        if outline is not None:
            value = int(outline.get(qn('w:val'), '9'))
            return value + 1 if value < 9 else None
    text = unicodedata.normalize('NFKC', text)
    if len(text) > 80 or re.search(r'[。；;!?！？]$', text):
        return None
    if re.match(r'^第[一二三四五六七八九十百\d]+[章节篇]', text):
        return 1
    numbered = re.match(r'^(\d+(?:\.\d+){1,8})(?:[.、\s]+|(?=[\u4e00-\u9fff]))(.+)$', text)
    if numbered and not re.match(r'^(?:万元|元|%|％|倍|人月|台|套)(?:\W|$)', numbered[2]):
        return numbered[1].count('.') + 1
    if re.match(r'^[一二三四五六七八九十]+、', text):
        return 2
    if re.match(r'^\([一二三四五六七八九十]+\)', text):
        return 3
    if re.match(r'^\d+[.、]\s*[^\d]', text) and len(text) < 45:
        return 3
    if re.match(r'^\(\d+\)', text) and len(text) < 45:
        return 4
    return None


def is_toc(paragraph):
    style = paragraph.style.name.lower()
    text = paragraph.text.strip()
    if re.search(r'^(?:toc|目录)\s*\d*$', style):
        return True
    return bool(re.search(r'(?:\.{3,}|…{2,}|\t)\s*\d+\s*$', text))


def has_heading_style(paragraph):
    node = paragraph._p.find(W+'pPr/'+W+'outlineLvl')
    if node is not None:
        return int(node.get(qn('w:val'), '9')) < 9
    style, seen = paragraph.style, set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        if re.fullmatch(r'(?:Heading|标题)\s*[1-9]', style.name, re.I):
            return True
        outline = style.element.find(W+'pPr/'+W+'outlineLvl')
        if outline is not None:
            return int(outline.get(qn('w:val'), '9')) < 9
        style = style.base_style
    return False


class DocumentIndex:
    def __init__(self, content, filename):
        self.filename = filename
        self.blocks = []
        self.document = Document(BytesIO(content))
        formal_roots = sum(1 for p in self.document.paragraphs if has_heading_style(p) and heading_level(p) == 1)
        headings = []
        numbered_titles = {}
        paragraph_index = table_index = 0
        caption = ''
        for child in self.body_children(self.document.element.body):
            if child.tag == W+'p':
                paragraph_index += 1
                paragraph = Paragraph(child, self.document)
                text = paragraph.text.strip()
                if is_toc(paragraph) or not text:
                    continue
                level = heading_level(paragraph)
                structured = has_heading_style(paragraph)
                if formal_roots >= 2 and level == 1 and not structured:
                    # Quoted regulations contain plain-text '第一章/第二章'.
                    # They do not replace a report's explicit Word chapter tree.
                    level = None
                if level:
                    if level == 1:
                        numbered_titles.clear()
                    number = re.match(r'^(\d+(?:\.\d+)+)', unicodedata.normalize('NFKC', text))
                    if number:
                        parts = tuple(number[1].split('.'))
                        # Numbered siblings must not inherit an intervening
                        # Chinese sub-list as their parent chapter.
                        parents = [(len(key), title) for key, title in numbered_titles.items()
                                   if len(key) < len(parts) and parts[:len(key)] == key]
                        if not structured:
                            headings = [h for h in headings if h[0] == 1] + sorted(parents)
                        numbered_titles[parts] = text
                    headings = [(n, t) for n, t in headings if n < level]
                    headings.append((level, text))
                caption = text if re.match(r'^表\s*\d', text) and len(text) < 160 else ''
                self.add(text, headings, paragraph=paragraph_index, is_heading=bool(level), is_caption=bool(caption))
            elif child.tag == W+'tbl':
                table_index += 1
                self.table(child, headings, table_index, caption)
                caption = ''

    @classmethod
    def body_children(cls, element):
        for child in element:
            if child.tag == W+'sdt':
                instructions = ' '.join(t.text or '' for t in child.iter(W+'instrText'))
                gallery = child.find('.//'+W+'docPartGallery')
                if re.search(r'\bTOC\b', instructions) or gallery is not None and 'contents' in gallery.get(qn('w:val'), '').lower():
                    continue
                content = child.find(W+'sdtContent')
                if content is not None:
                    yield from cls.body_children(content)
            elif child.tag != W+'del':
                yield child

    def add(self, text, headings, **location):
        if not text.strip():
            return
        self.blocks.append({'quote': text, '_compact': compact(text),
                            'heading_path': [t for _, t in headings],
                            'section': ' → '.join(t for _, t in headings), **location})

    def table(self, table, headings, number, caption='', nested_path=''):
        previous = {}
        for row_index, row in enumerate(table.findall(W+'tr'), 1):
            before = row.find(W+'trPr/'+W+'gridBefore')
            column = int(before.get(qn('w:val'), '0')) + 1 if before is not None else 1
            cells, current = [], {}
            for cell in row.findall(W+'tc'):
                span = cell.find(W+'tcPr/'+W+'gridSpan')
                width = int(span.get(qn('w:val'), '1')) if span is not None else 1
                merge = cell.find(W+'tcPr/'+W+'vMerge')
                continuation = merge is not None and merge.get(qn('w:val')) != 'restart'
                text = ' '.join(''.join(t.text or '' for t in p.iter(W+'t')) for p in cell.findall(W+'p'))
                text = ' '.join(text.split())
                origin = previous.get(column) if continuation else None
                info = {'column_start': column, 'column_end': column+width-1,
                        'origin_row': origin['origin_row'] if origin else row_index,
                        'quote': origin['quote'] if origin else text}
                cells.append(info)
                for c in range(column, column+width):
                    current[c] = info
                for nested_index, nested in enumerate(cell.findall(W+'tbl'), 1):
                    path = f'{nested_path + "/" if nested_path else ""}{row_index}/{column}/{nested_index}'
                    self.table(nested, headings, number, caption, path)
                column += width
            previous = current
            self.add(' | '.join(c['quote'] for c in cells), headings,
                     table_index=number, table_path=nested_path, row_index=row_index,
                     table_caption=caption, cells=cells, is_heading=False)

    def candidates(self, quote, scope='', table=None, row=None):
        query = compact(quote)
        pool = [b for b in self.blocks if (table is None or b.get('table_index') == table and not b.get('table_path'))
                and (row is None or b.get('row_index') == row)]
        if scope:
            scoped = [b for b in pool if compact(scope) in compact(b['section']+' '+b.get('table_caption', ''))]
            if scoped:
                pool = scoped
            elif row is not None and table is None:
                return []  # A caption-relative row number is unsafe without its caption.
        found = []
        for block in pool:
            text = block['_compact']
            if len(query) >= 6 and query in text:
                found.append(block)
            elif len(text) >= (12 if block.get('table_index') else 24) and text in query and not block.get('is_heading'):
                found.append(block)
            elif row is not None and (table is not None or scope) and block.get('cells'):
                # Rule parsers may repeat a horizontally merged value across
                # grid columns. Verify physical cells at the explicit address.
                cells = [compact(c['quote']) for c in block['cells']
                         if compact(c['quote']) and c['origin_row'] == block['row_index']]
                if len(cells) >= 2 and all(cell in query for cell in cells):
                    found.append(block)
        return found

    def locate(self, evidence, scope=''):
        text = str(evidence or '').strip()
        matches = []
        first = text.split('|', 1)[0].strip()
        if len(first) <= 80 and any(b.get('is_heading') and compact(first) == b['_compact'] for b in self.blocks):
            scope = first
        # One issue can cite both prose and several table rows. Keep each source.
        chunks = re.split(r'(?=DOCX表格\d+行\d+[:：])|；(?=[^；]*表内行\d+)', text)
        for chunk in chunks:
            chunk = chunk.strip(' |；')
            if not chunk:
                continue
            explicit = re.match(r'DOCX表格(\d+)行(\d+)[:：](.*)', chunk, re.S)
            caption_row = re.match(r'(.+?)\s*表内行(\d+)(?:[:：](.*))?$', chunk, re.S)
            resource = re.match(r'.*?(DOCX表格(\d+)|DOCX正文)[:：](.*)', chunk, re.S)
            if explicit:
                matches.extend(self.candidates(explicit[3], table=int(explicit[1]), row=int(explicit[2])))
            elif caption_row:
                if caption_row[3]:
                    matches.extend(self.candidates(caption_row[3], scope=caption_row[1], row=int(caption_row[2])))
                else:
                    # A row address is evidence of a checked location, not of absent text.
                    matches.extend(b for b in self.blocks if b.get('row_index') == int(caption_row[2])
                                   and compact(caption_row[1]) in compact(b['section']+' '+b.get('table_caption', '')))
            elif resource:
                raw = re.sub(r'；识别数量=.*$', '', resource[3])
                matches.extend(self.candidates(raw, table=int(resource[2]) if resource[2] else None))
            else:
                matches.extend(self.candidates(chunk, scope=scope))
        return matches

    def scopes(self, label):
        terms = [t.strip() for t in re.split(r'\s+/\s+|；', label) if len(t.strip()) >= 4]
        matches = []
        for term in terms:
            for block in self.blocks:
                if block.get('is_heading') and compact(term) in block['_compact']:
                    matches.append(block)
                elif block.get('row_index') == 1 and compact(term) in compact(block.get('table_caption', '')+' '+block['section']):
                    matches.append(block)
                elif compact(term) == '验收要求' and block.get('is_heading') and re.search(r'验收(?:标准|要求|方案|条件|交付|阶段)', block['quote']):
                    matches.append(block)
        return matches


def _key(block):
    return (block.get('paragraph'), block.get('table_index'), block.get('table_path'), block.get('row_index'))


def _public_location(block, filename, status='matched', role='evidence'):
    location = {k: v for k, v in block.items() if not k.startswith('_') and k not in {'is_heading', 'is_caption'}}
    location.update(file_name=filename, file_type='docx', match_status=status, role=role,
                    precision='table_row' if 'table_index' in block else 'paragraph', highlight=True)
    if 'table_index' in block:
        location['sheet_name'] = f"表格{block['table_index']}" + (f"（{block['table_caption']}）" if block.get('table_caption') else '')
        if block.get('table_path'):
            location['sheet_name'] += f" / 嵌套表{block['table_path']}"
    return location


def _hint(location):
    parts = [location.get('section', ''), location.get('sheet_name', '')]
    if location.get('row_index'):
        parts.append(f"第{location['row_index']}行")
    elif location.get('paragraph'):
        parts.append(f"正文第{location['paragraph']}段")
    return '，'.join(p for p in parts if p)


def _highlights(locations):
    output = []
    for loc in locations[:MAX_LOCATIONS]:
        output.append({**loc, 'quote': loc['quote'][:500]})
        offset = 0
        for cell in loc.get('cells', []):
            quote = cell['quote']
            if offset >= 500 and 0 < len(quote) <= 50 and re.search(r'\d|人月|万条|万个', quote):
                # Long specifications must not push quantity/unit cells out of
                # the preview. Each extra excerpt is an actual addressed cell.
                output.append({**loc, 'quote': quote, 'precision': 'cell',
                               'row_index': cell['origin_row'],
                               'column_start': cell['column_start'], 'column_end': cell['column_end'],
                               'section': f"{loc['section']} → 第{cell['column_start']}列"})
            offset += len(quote) + 3
    return output



def build_evidence_location(index, evidence, section='', sources=None, advice=''):
    filename = index.filename
    finding = {}
    blocks = []
    if sources:
        for source in sources:
            table = re.fullmatch(r'DOCX表格(\d+)', source.get('sheet_name', ''))
            candidates = index.candidates(source.get('quote', ''), table=int(table[1]) if table else None)
            exact_rows = [b for b in candidates if b.get('row_index') == source.get('row_index')] if table else []
            blocks.extend(exact_rows or candidates)
    else:
        for quote in evidence:
            blocks.extend(index.locate(quote, section))
    # Do not highlight a made-up 'missing' sentence; identify the checked scope.
    scope_only = bool(blocks) and all(b.get('is_heading') or b.get('is_caption') for b in blocks)
    if scope_only:
        blocks = []
    if not blocks:
        scope_only = True
        for label in evidence + [section]:
            if isinstance(label, str) and len(label) < 250:
                blocks.extend(index.scopes(label))
    unique = {_key(b): b for b in blocks}
    blocks = list(unique.values())
    counts = Counter(b['_compact'] for b in blocks)
    ambiguous = any(n > 1 for n in counts.values())
    status = 'ambiguous' if ambiguous else ('scope' if scope_only and blocks else 'matched' if blocks else 'unresolved')
    locations = [_public_location(b, filename, status, 'scope' if scope_only else 'evidence') for b in blocks]
    highlights = _highlights(locations)
    hint = '；'.join(dict.fromkeys(_hint(loc) for loc in locations[:MAX_LOCATIONS]))
    prefix = '核查范围：' if scope_only else ''
    if ambiguous:
        prefix = '原文存在多处匹配，以下为候选位置：'
    if not hint:
        hint = '未匹配到可核验的原文位置；未生成推测性高亮。'
    elif len(locations) > MAX_LOCATIONS:
        hint += f'；共{len(locations)}处，展示前{MAX_LOCATIONS}处，完整位置保留在明细中'
    finding['location_hint'] = prefix + hint
    finding['source_locations'] = locations
    finding['source_location'] = {'file_name': filename, 'entries': locations, 'match_status': status,
                                  'precision': 'scope' if scope_only else 'document_blocks',
                                  'location_count': len(locations)}
    finding['source_highlights'] = highlights
    finding['revision_target'] = {'location_hint': finding['location_hint'],
                                  'source_location': finding['source_location'], 'highlights': highlights,
                                  'target_type': 'section_or_document' if scope_only or not locations else 'source_text',
                                  'advice': advice}
    return finding
