"""Evidence-based, deliberately coarse report readiness checks.

No city/agency whitelist, prescribed chapter numbers, arithmetic, indicator
quotas or licensing judgments. Unreadable evidence never proves absence.
"""
from __future__ import annotations

from collections import defaultdict
from io import BytesIO
from pathlib import Path
import hashlib
import re

VERSION = "completeness-2.1"
LABELS = {"pass": "通过", "missing": "主要材料缺失", "review": "待核实", "info": "一般提示", "unreadable": "无法检查", "not_applicable": "不适用"}
GROUPS = {
    "basis": ("项目背景与建设依据", ("项目背景", "建设背景", "建设依据", "编制依据", "项目必要性", "建设必要性", "政策依据", "立项依据")),
    "needs": ("需求或现状分析", ("需求分析", "现状分析", "业务需求", "系统现状", "现状与需求", "现状及", "存在问题", "存在的问题", "建设需求", "运维需求")),
    "construction": ("建设或服务主要内容", ("建设内容", "建设方案", "总体方案", "总体设计", "功能设计", "系统设计", "功能需求", "运维内容", "服务内容", "实施方案", "技术方案", "功能清单", "设备采购清单", "设备清单", "采购清单", "服务清单")),
    "budget": ("投资与预算", ("投资估算", "投资概算", "项目预算", "预算编制", "经费预算", "费用估算", "项目投资", "总投资", "投资总额", "费用测算", "报价", "经费测算")),
}
PLACEHOLDER = re.compile(r"^(?:待补充|待编制|待填写|略|暂无|无|见附件|详见附件|此处填写|请填写|填写说明|编制要求|待完善|后续补充|待定|TBD|[-—_/\s])*[。；;]*$", re.I)
TOC = re.compile(r"(?:\.{3,}|…{2,}|\t)\s*\d+\s*$")
MONEY = re.compile(r"(?<![\d.])(?:\d[\d,，]*(?:\.\d+)?|[零〇一二两三四五六七八九十百壹贰叁肆伍陆柒捌玖拾佰仟][零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟]*)\s*[（(]?\s*(?:万元|亿元|元)(?![A-Za-z])")
BUDGET_SECTION = re.compile(r"投资(?:估算|概算|概况)|(?:预算|概算|估算)(?:编制|明细|汇总)|(?:费用|经费)(?:估算|测算|预算)|项目预算$|投资与预算|投资和预算|资金来源")
BUSINESS_SECTION = re.compile(r"预算管理|投资监管|业务需求单位|单位概况|编制依据|政策依据|建设背景|建设依据")

# These are material types, not mandatory forms for every project.
TABLE_TYPES = {
    'functions': ('建设功能清单', re.compile(r'(?:功能|模块|建设内容)(?:清单|一览表|明细表)|功能需求表'), re.compile(r'功能|模块|接口')),
    'equipment': ('设备及采购清单', re.compile(r'(?:设备|软硬件|硬件|软件产品|采购)(?:采购)?(?:清单|配置表|明细表|一览表)'), re.compile(r'设备|采购|服务器|终端|交换机|存储|软件产品')),
    'services': ('服务内容清单', re.compile(r'(?:服务|服务内容|运维服务|购买服务|服务项目)(?:清单|一览表|明细表)'), re.compile(r'服务|运维|巡检|培训')),
}
DEPENDENT_TABLE = re.compile(r'资源申请(?:表|清单)?|数据(?:资源)?目录|接口清单')
TABLE_HEADER = re.compile(r'(?:序号|编号|项目|分项目|名称|系统名称|子系统名称|模块名称|子模块名称|功能描述|功能名称|建设内容|业务/功能|目前现状|预期目标|费用名称|小计|内容|说明|备注|单位|计量单位|数量|单价|金额|总价|合价|费用|预算|合计|设备名称|产品名称|服务名称|服务项|服务内容|服务要求|技术参数|规格|规格型号|配置|采购数量|一级功能|二级功能|三级功能|四级功能|工作量|总金额|对应建设内容中的功能章节|对应子系统|数据治理服务事项|测算单位|治理对象及范围|治理成效及交付物|关联的系统的数据治理功能模块)(?:[（(].*?[）)])?')


def _clean(value):
    return re.sub(r"\s+", "", str(value or ""))


def _location(block):
    result = {k: v for k, v in block.items() if k in {"filename", "section", "paragraph", "table_index", "table_path", "row_index", "page", "line", "sheet"}}
    result["quote"] = block["quote"][:700]
    return result


def _text_blocks(text, **position):
    section = ""
    aliases = tuple(t for _, terms in GROUPS.values() for t in terms)
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or TOC.search(line):
            continue
        title = re.sub(r"^(?:第[一二三四五六七八九十\d]+[章节]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[.、\s]*)\s*", "", line)
        is_heading = len(line) < 65 and (title in aliases or title in {"投资与预算", "立项依据与背景", "现状与需求分析"})
        if is_heading:
            section = title
        yield {"quote": line, "line": number, "section": section, "is_heading": is_heading, **position}


def _read(content, filename):
    ext = Path(filename).suffix.lower()
    blocks, limitations = [], []
    if ext == ".docx":
        from app.modules.resource.quantity_parser import DocumentIndex
        index = DocumentIndex(content, filename)
        blocks = index.blocks
        # An image in a normal report may be a diagram, logo or seal. Only
        # record the uncertainty; do not make every illustrated report fail.
        images = len(index.document.element.xpath('.//*[local-name()="drawing" or local-name()="pict"]'))
        # Preserve the local scope of unreadable pictures, including otherwise
        # empty paragraphs. A cover logo must not excuse a missing budget.
        paragraphs = {b['paragraph']: b for b in blocks if b.get('paragraph')}
        rows = {(b.get('table_index'), b.get('row_index')): b for b in blocks if b.get('table_index')}
        paragraph_number = table_number = 0
        section = ''
        markers = []
        for child in index.body_children(index.document.element.body):
            tag = child.tag.rsplit('}', 1)[-1]
            if tag == 'p':
                paragraph_number += 1
                existing = paragraphs.get(paragraph_number, {})
                section = existing.get('section', section)
                if child.xpath('.//*[local-name()="drawing" or local-name()="pict"]'):
                    markers.append({'quote': '此段含图片，图片内容尚未识别。', 'paragraph': paragraph_number, 'section': section, '_unreadable': True})
            elif tag == 'tbl':
                table_number += 1
                for row_number, row in enumerate(child.findall('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tr'), 1):
                    if row.xpath('.//*[local-name()="drawing" or local-name()="pict"]'):
                        markers.append({'quote': '此表格行含图片，图片内容尚未识别。', 'table_index': table_number, 'row_index': row_number,
                                        'section': rows.get((table_number, row_number), {}).get('section', section), '_unreadable': True})
        blocks.extend(markers)
        return blocks, limitations, images
    if ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(BytesIO(content)) as pdf:
            images = 0
            for n, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                if not text.strip():
                    limitations.append(f"第{n}页未提取到正文，可能为空白页或扫描页")
                    blocks.append({'quote': limitations[-1], 'page': n, 'section': '', '_unreadable': True})
                images += len(page.images)
                blocks.extend(_text_blocks(text, page=n))
        return blocks, limitations, images
    if ext == ".txt":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("gb18030")
        blocks = list(_text_blocks(text))
        return blocks, limitations, 0
    if ext in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook
        book = load_workbook(BytesIO(content), read_only=True, data_only=True)
        try:
            for sheet in book:
                for n, row in enumerate(sheet.values, 1):
                    text = " | ".join(str(v) if v is not None else "" for v in row)
                    if text.strip(" |"):
                        blocks.append({"quote": text, "sheet": sheet.title, "row_index": n, "section": sheet.title, "is_heading": False})
        finally:
            book.close()
        return blocks, limitations, 0
    raise ValueError("暂不支持此格式，请提供DOCX、可读取PDF、TXT或Excel材料")


def _body(block):
    text = block["quote"].strip()
    if block.get('_unreadable') or block.get("is_heading") or block.get("is_caption") or TOC.search(text):
        return False
    if len(_clean(text)) <= 16 and re.search(r"(?:如下|见下表)[：:。]*$", _clean(text)):
        return False
    if PLACEHOLDER.fullmatch(_clean(text)) or re.match(r"^(?:填写说明|编制要求|请在此|此处填写)", text):
        return False
    if len(_clean(text)) < 100 and re.search(r'(?:详见|参见|见下表|见附表|见附件)', text) and not re.search(r'[。；;].+\S', text):
        return False
    # A label alone is not content; real short table rows remain usable.
    plain = _clean(text).strip("|：:。")
    if block.get("table_index") or block.get("sheet"):
        cells = [c.strip() for c in re.split(r"[|｜]", text) if c.strip()]
        # A merged caption row does not make an otherwise empty main table
        # complete. Preserve actual prose/amount rows, including single cells.
        if len(cells) == 1 and len(cells[0]) < 120 and not MONEY.search(cells[0]) and re.search(r'(?:预算总表|估算表|概算表|明细表|汇总表|建设内容及报价|预算明细|清单|一览表|配置表|需求表)(?:[（(].*?[）)])?$', cells[0]):
            return False
        if cells and all(TABLE_HEADER.fullmatch(c) or PLACEHOLDER.fullmatch(_clean(c)) or re.fullmatch(r'\d+[.、]?', c) for c in cells):
            return False
    if any(plain == _clean(term) for _, terms in GROUPS.values() for term in terms):
        return False
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z0-9]", plain)) and len(plain) >= 4


def _group_evidence(blocks, aliases, key=""):
    results = []
    for block in blocks:
        if not _body(block):
            continue
        text = block["quote"]
        section = str(block.get("section", ""))
        # Remove policy titles before looking for report-content aliases.
        narrative = re.sub(r"《[^》]*》", "", text)
        if re.search(r"(?:详见|参见|见附件|另附|后续补充|另行编制)", text) and len(_clean(text)) < 65:
            continue
        section_match = any(term in section for term in aliases)
        text_match = any(term in narrative for term in aliases)
        if key == "construction" and re.search(r"(?:本项目|本次).{0,12}(?:建设|开发|改造|升级|提供).{2,}(?:系统|模块|平台|服务|功能|接口)", narrative):
            text_match = True
        if key in {"needs", "construction"}:
            if key == "construction" and re.search(r"原则|统筹规划|先进适用|安全可靠", narrative) and not re.search(r"模块|功能|接口|系统建设|本次开发|本次建设|服务包括|建设内容包括", narrative):
                continue
            if BUSINESS_SECTION.search(section) and not re.search(r"(?:现状|需求分析|主要建设内容|项目建设内容|系统功能)", section):
                section_match = False
                # Policy background can mention an implementation plan; only
                # explicit current-project prose is eligible as a fallback.
                text_match = text_match and bool(re.search(r"(?:本项目|本次).{0,20}(?:建设|需求|实现|开发|提供)", narrative))
            if "业务需求单位" in section and key == "needs":
                section_match = False
            if key == "needs" and re.search(r"(?:计划|周期|里程碑|进度)", section):
                section_match = text_match = False
        if key == "budget":
            section_match = bool(BUDGET_SECTION.search(section)) and not bool(BUSINESS_SECTION.search(section))
            text_match = bool(MONEY.search(narrative)) and bool(re.search(r"(?:本项目|本次|项目总|总投资|投资总额|建设费用|开发费|预算为|估算为|投资与预算)", narrative))
            # Only the existence of budget material is checked here. Amount
            # extraction and unit/field sufficiency belong to specialist rules.
            if not MONEY.search(narrative) and not section_match:
                continue
        if not section_match and not text_match:
            continue
        score = 100 if section_match else 30
        if key == "needs" and re.search(r"现状|存在问题|业务需求分析|系统功能和性能需求分析", section):
            score += 30
        if key == "construction" and re.search(r"项目建设内容|系统功能需求分析|应用系统建设|功能设计", section):
            score += 30
        if key in {"needs", "construction"} and re.search(r"背景|依据", section):
            score -= 50
        results.append((score, _location(block)))
    results.sort(key=lambda pair: -pair[0])
    return [b for _, b in results[:3]]


def _item(key, title, status, reason, evidence=None, advice=""):
    return {"key": key, "title": title, "status": status, "label": LABELS[status],
            "reason": reason, "evidence": evidence or [], "advice": advice}


def _combine(items):
    for status in ("unreadable", "missing", "review"):
        if any(i["status"] == status for i in items):
            return status
    return "pass"


def _table_type(block):
    """Classify narrowly by table title, leaf heading or characteristic headers."""
    section = str(block.get('section', ''))
    if BUSINESS_SECTION.search(section) or BUDGET_SECTION.search(section):
        return None
    title = section.split(' → ')[-1] + ' ' + block.get('table_caption', '')
    text = block['quote']
    cells = [_clean(c) for c in re.split(r'[|｜]', text) if c.strip()]
    for key, (_, names, _) in TABLE_TYPES.items():
        if names.search(title) or len(cells) == 1 and len(text) < 120 and names.search(text):
            return key
    if set(cells) & {'功能名称', '模块名称'} and set(cells) & {'功能描述', '建设内容', '一级功能', '二级功能'}:
        return 'functions'
    if set(cells) & {'设备名称', '产品名称'} and any(re.fullmatch(r'(?:数量|采购数量|规格|规格型号|型号)(?:[（(].*?[）)])?', c) for c in cells):
        return 'equipment'
    if '服务名称' in cells and set(cells) & {'服务内容', '服务要求'}:
        return 'services'
    return None


def _equivalent_table_prose(blocks, kind):
    """A same-topic explanation can replace a form, but not a bare pointer."""
    topic = TABLE_TYPES[kind][2]
    found = []
    for b in blocks:
        text = b['quote']
        if b.get('table_index') or b.get('sheet') or not _body(b):
            continue
        if BUSINESS_SECTION.search(str(b.get('section', ''))) or re.search(r'详见|参见|见附|见下表|填写|待补充|待完善', text):
            continue
        if BUDGET_SECTION.search(str(b.get('section', ''))) and not re.search(r'(?:本项目|本次)(?:拟|将)?(?:采购|配置|提供|开展|部署|不涉及|无需|不新增|不采购|沿用|利旧)', text):
            continue
        if not topic.search(text):
            continue
        # Accept explicit non-applicability or substantive project descriptions.
        if re.search(r'(?:本项目|本次).{0,20}(?:不涉及|无需|不新增|不采购|不包含|沿用|利旧)', text) or (
            len(_clean(text)) >= 20 and re.search(r'本项目|本次|建设|功能|服务|采购', text)
            and re.search(r'包括|包含|提供|支持|实现|配置|采购|负责|开展|部署|开发', text)
        ):
            found.append(_location(b))
    return found[:3]


def _additional_table_checks(table_blocks, all_blocks, construction):
    classified = defaultdict(list)
    for rows in table_blocks.values():
        kind = next((k for b in rows[:3] if (k := _table_type(b))), None)
        if kind:
            classified[kind].extend(rows)
    results = []
    for kind, rows in classified.items():
        valid = [b for b in rows if _body(b)]
        prose = _equivalent_table_prose(all_blocks, kind)
        marker = next((b for b in rows if b.get('_unreadable')), None)
        status = 'pass' if valid or prose else 'review' if marker else 'missing'
        item = _item('table_' + kind, TABLE_TYPES[kind][0], status,
                     '已找到该类清单的实际内容；不逐字段检查或核对数量。' if valid else
                     '表格未提供有效数据，但已找到同类正文说明或明确的不涉及说明，接受等效表达。' if prose else
                     '该类主要清单含未识别图片，尚不能确认内容。' if marker else
                     '已发现该类主要清单，但只有表头或占位内容，且未找到同类正文说明。',
                     [_location(b) for b in valid[:3]] or prose or [_location(marker or rows[0])],
                     '' if status == 'pass' else '请核对主要清单内容；可以提供已填写表格或同类正文说明。')
        if status != 'pass' and construction['status'] != 'pass':
            item['counted'] = False
            item['reason'] += '同一建设内容缺口统一计入主体材料项。'
        results.append(item)
    return results


def check_completeness(content: bytes, filename: str, attachments=()):
    """attachments: (filename, bytes) pairs. All results are JSON-serializable."""
    inventory = [{"filename": filename, "role": "report", "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}]
    for name, raw in attachments:
        inventory.append({"filename": name, "role": "attachment", "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)})
    try:
        blocks, limitations, images = _read(content, filename)
    except Exception as exc:
        items = [_item("text", "报告文本", "unreadable", f"文件无法可靠解析：{type(exc).__name__}。请检查格式、加密或文件损坏情况。", advice="请提供可读取的DOCX或PDF重新检查。") ]
        items.extend(_item(k, t, "review", "正文不可用，暂不判断材料是否缺失。") for k, t in (("attachments", "附件材料"), ("tables", "表格数据"), ("chapters", "关键章节")))
        return _report(inventory, items, [], [], [str(exc)[:200]])
    for b in blocks:
        b["filename"] = filename
    main_body = [b for b in blocks if _body(b)]
    if not main_body:
        state = "review" if images or limitations else "missing"
        items = [_item("text", "报告文本", state, "未获取到有效正文；图片内容尚未识别。" if state == "review" else "只识别到空白、标题或模板提示，未发现有效正文。", advice="请核对正文是否随文件提交。")]
        items.extend(_item(k, t, "review", "正文不可用，暂不判断材料是否缺失。") for k, t in (("attachments", "附件材料"), ("tables", "表格数据"), ("chapters", "关键章节")))
        return _report(inventory, items, [], [], limitations)

    all_blocks = list(blocks)
    attachment_readable = {}
    for name, raw in attachments:
        try:
            extra, extra_limits, _ = _read(raw, name)
            for b in extra:
                b["filename"] = name
            all_blocks.extend(extra)
            attachment_readable[name] = any(_body(b) for b in extra) and not extra_limits
        except Exception:
            attachment_readable[name] = False
    attachment_items = _attachments(blocks, all_blocks, attachment_readable)
    groups = []
    for key in ("construction", "budget"):
        title, aliases = GROUPS[key]
        ev = _group_evidence(all_blocks, aliases, key)
        related_refs = [i for i in attachment_items if key in i.get("core_groups", []) and i["status"] in {"missing", "review"}]
        local_limits = [b for b in all_blocks if b.get('_unreadable') and (
            any(term in str(b.get('section', '')) for term in aliases)
            or b.get('page') and not b.get('section'))]
        unreadable_files = [name for name, readable in attachment_readable.items() if not readable and any(t in Path(name).stem for t in aliases)]
        uncertain = bool(local_limits or unreadable_files or related_refs and any(i['status'] == 'review' for i in related_refs))
        status = "pass" if ev else "review" if uncertain else "missing"
        item = _item(key, title, status,
                     "已找到主要材料；仅确认内容存在，充分性和正确性由专项规则检查。" if ev else
                     "未确认该部分主体内容。" + ("相关位置或材料无法可靠读取，需人工核实。" if uncertain else "本次材料中未找到有效正文或等效说明。"),
                     ev or [_location(b) for b in local_limits[:2]],
                     "" if ev else f"请核对是否提交了{title}的主体材料。")
        if related_refs and not ev:
            item['counted'] = False
            item['reason'] += "该缺口统一计入对应附件项，避免重复计数。"
        groups.append(item)

    # A table is not mandatory just because arithmetic checking will need it.
    # Detect only wholly empty main budget tables; no row/amount-count quotas.
    table_blocks = defaultdict(list)
    for block in all_blocks:
        if block.get('table_index') or block.get('sheet'):
            table_blocks[(block.get('filename'), block.get('table_index'), block.get('table_path', ''), block.get('sheet'))].append(block)
    budget_tables = []
    for rows in table_blocks.values():
        context = str(rows[0].get('section', ''))
        header = ' '.join(b['quote'] for b in rows[:3])
        if (BUDGET_SECTION.search(context) and not BUSINESS_SECTION.search(context)) or re.search(r'(?:金额|单价|总价|小计)[（(](?:万|亿)?元[）)]', header):
            valid = [b for b in rows if _body(b)]
            budget_tables.append((rows, valid))
    budget = next(i for i in groups if i['key'] == 'budget')
    valid_rows = [b for _, valid in budget_tables for b in valid]
    empty_tables = [rows for rows, valid in budget_tables if not valid]
    if valid_rows:
        tables = _item('tables', '表格数据', 'pass', '已发现可读取的预算表内容；不评价明细深度、字段或计算。', [_location(b) for b in valid_rows[:3]])
    elif empty_tables:
        marker = next((b for rows in empty_tables for b in rows if b.get('_unreadable')), None)
        tables = _item('tables', '表格数据', 'review' if marker else 'missing',
                       '主要预算表内容为图片，尚未识别。' if marker else '发现主要预算表，但仅有表头或空白模板。',
                       [_location(marker or empty_tables[0][0])], '请核对主要预算表是否实际填入内容。')
        if budget['status'] != 'pass':
            tables['counted'] = False
            tables['reason'] += '同一预算缺口统一计入主体材料项。'
    else:
        tables = _item('tables', '表格数据', 'pass' if budget['status'] == 'pass' else 'not_applicable',
                       '未发现明确缺失的主要附表；接受正文中的预算说明，明细充分性由专项检查。' if budget['status'] == 'pass' else '预算主体缺口已统一列示，此项不再重复提示。',
                       budget['evidence'][:1])
    table_details = _additional_table_checks(table_blocks, all_blocks, groups[0])
    if table_details:
        budget_table = {**tables, 'key': 'table_budget', 'title': '预算表格'}
        table_details.insert(0, budget_table)
        tables = _item('tables', '表格数据', _combine(table_details),
                       '检查已识别的预算、建设功能、设备采购及服务内容清单；接受同类正文，不要求固定表格或逐字段齐全。',
                       [e for i in table_details if i['status'] in {'missing', 'review'} for e in i['evidence']][:3])
        tables['counted'] = False  # Count leaf findings, not their UI summary.
    attachment_status = _combine(attachment_items)
    items = [
        _item('text', '报告文本', 'pass', '已读取有效正文；局部解析限制按相关材料范围处理。', [_location(main_body[0])]),
        _item('attachments', '附件材料', attachment_status,
              '未发现承载核心内容或明确必须提交的附件缺口；普通引用仅作一般提示。' if attachment_status == 'pass' else '存在核心或明确必要附件缺失、无法读取的情况。'),
        tables,
        _item('chapters', '关键章节', _combine(groups), '仅检查建设方案与投资预算主体是否明显残缺，不逐项审查背景、需求、指标、安全或资源等内容。'),
    ]
    for n, limitation in enumerate(limitations):
        attachment_items.append(_item('parse_notice_' + str(n), '局部读取说明', 'info', limitation + '；若核心材料已具备，不影响总体结论。'))
    return _report(inventory, items, groups + table_details + attachment_items, [], limitations)


CORE_TERMS = re.compile(r"投资估算|投资概算|预算(?:明细|总表|附表|汇总|方案)?|建设方案|主要建设内容|总体方案|技术方案|设计方案")


def _core_groups(text):
    return [key for key in ('construction', 'budget') if any(t in text for t in GROUPS[key][1]) or key == 'budget' and '预算' in text
            or key == 'construction' and any(names.search(text) for _, names, _ in TABLE_TYPES.values())]


def _attachments(main, all_blocks, supplied):
    result, seen = [], set()
    pattern = re.compile(r'(?:详见|参见|见)\s*(?:本报告)?(?P<kind>附件|附表)\s*(?:(?P<number>[一二三四五六七八九十百\d]+)|[《“"](?P<title>[^》”"。；\n]{1,60})[》”"])?')
    for b in main:
        if b.get('_unreadable') or b.get('is_heading') or TOC.search(b['quote']):
            continue
        for m in pattern.finditer(b['quote']):
            token = m['kind'] + (m['number'] or ('《' + m['title'] + '》' if m['title'] else ''))
            canonical = _clean(token)
            # Use the local sentence. A policy's attachment is not automatically
            # a submission requirement for the current project.
            before = re.split(r'[。；;\n]', b['quote'][:m.start()])[-1]
            context = before + b['quote'][m.start():]
            section = str(b.get('section', ''))
            explicit = bool(re.search(r'(?:本次|本项目|本报告).{0,15}(?:须|必须|应当|需).{0,12}(?:随附|提交|提供)', context))
            policy = bool(re.search(r'政策|规定|通知|指引|办法|规程|依据|内部文件|《', before)) and not bool(re.search(r'(?:本报告|本次提交|本项目的).{0,20}(?:预算|建设方案|设计方案)', before))
            scoped_pointer = bool(re.fullmatch(r'(?:具体|详细|有关|相关|全部)?(?:内容|材料|方案|说明|明细)?[，,:：\s]*(?:详见|参见|见).+', context.strip()))
            scope = context + (section if scoped_pointer else '')
            table_kind = next((k for k, (_, names, _) in TABLE_TYPES.items() if names.search(scope)), None)
            dependent_table = bool(DEPENDENT_TABLE.search(scope) and re.search(r'全部|完整内容|仅在|仅见|均见|均详见', scope))
            core = not policy and bool(CORE_TERMS.search(context) or scoped_pointer and CORE_TERMS.search(section) or table_kind)
            required = explicit or core or (not policy and dependent_table)
            # Repeated references to the same attachment may have different
            # importance. Process the strongest one rather than the first one.
            priority = 2 if required else 1
            old = next((i for i in result if i['key'] == 'attachment_' + canonical), None)
            if old and old.get('_priority', 0) >= priority:
                continue
            if old:
                result.remove(old)
            ev = []
            has_identifier = bool(m['number'] or m['title'])
            internal_reference = not has_identifier and bool(re.match(r'(?:章节|第)\s*\d+(?:\.\d+)+', b['quote'][m.end():]))
            equivalent_internal = False
            for n, candidate in enumerate(all_blocks):
                t = _clean(candidate['quote'])
                numbered_match = m['number'] and re.match(re.escape(canonical) + r'(?:[^\d一二三四五六七八九十百]|$)', t)
                named_match = m['title'] and _clean(m['title']) == t.strip('《》“”')
                if has_identifier and (numbered_match or named_match) and len(t) < 100:
                    following = []
                    for next_block in all_blocks[n+1:]:
                        if next_block.get('filename') != candidate.get('filename') or next_block.get('is_heading'):
                            break
                        following.append(next_block)
                        if len(following) >= 12:
                            break
                    body = next((p for p in following if _body(p) and not pattern.search(p['quote'])), None)
                    if body:
                        ev = [_location(candidate), _location(body)]
                        break
            if internal_reference and core and not ev:
                referenced_numbers = re.findall(r'\d+(?:\.\d+)+', b['quote'][m.end():])
                for candidate in all_blocks:
                    candidate_scope = str(candidate.get('section', ''))
                    in_appendix = bool(re.search(r'附件|附表', candidate_scope)) or any(re.search(r'(?<![\d.])' + re.escape(n) + r'(?![\d.])', candidate_scope) for n in referenced_numbers)
                    related = any(term in candidate_scope for key in _core_groups(context + section) for term in GROUPS[key][1])
                    if candidate.get('filename') == b.get('filename') and in_appendix and related and _body(candidate):
                        if 'budget' in _core_groups(context + section) and not (MONEY.search(candidate['quote']) or re.search(r'(?:^|[|｜])\s*\d+(?:\.\d+)?\s*(?:[|｜]|$)', candidate['quote'])):
                            continue
                        ev.append(_location(candidate))
                        equivalent_internal = True
                        if len(ev) == 2:
                            break
            matched_files = [name for name in supplied if (
                m['number'] and re.search(re.escape(canonical) + r'(?:[^\d一二三四五六七八九十百]|$)', _clean(Path(name).stem))
                or m['title'] and _clean(m['title']) == _clean(Path(name).stem))]
            readable_file = next((name for name in matched_files if supplied[name]), None)
            if readable_file:
                ev.append({'filename': readable_file, 'quote': '已提交并可读取的对应附件文件'})
            equivalent_table = False
            if not ev and table_kind and not policy and not explicit:
                ev = _equivalent_table_prose(all_blocks, table_kind)
                candidates = defaultdict(list)
                for candidate in all_blocks:
                    if candidate.get('table_index') or candidate.get('sheet'):
                        candidates[(candidate.get('filename'), candidate.get('table_index'), candidate.get('table_path', ''), candidate.get('sheet'))].append(candidate)
                if not ev:
                    for rows in candidates.values():
                        if any(_table_type(row) == table_kind for row in rows[:3]):
                            ev = [_location(row) for row in rows if _body(row)][:3]
                            if ev:
                                break
                equivalent_table = bool(ev)
            status = 'pass' if ev else 'review' if required and matched_files else 'missing' if required and has_identifier else 'review' if required else 'info'
            item = _item('attachment_' + canonical, token, status,
                         '已找到同类清单内容或正文说明，接受等效表达；未核验引用编号。' if equivalent_table else
                         '已找到文内承载同类核心内容的附表；前置检查不校验引用章节编号是否一一对应。' if equivalent_internal else '已找到对应附件内容。' if ev else
                         '正文将核心材料置于此附件或明确要求本次提交，但尚未确认对应材料。' if required else
                         '普通引用的附件尚未对应；可能属于被引用政策。仅作一般提示，不影响完整性结论。',
                         ev or [_location(b)], '' if ev else '请核对附件指向及本次是否需要随附。')
            if matched_files and not ev:
                item['evidence'].extend({'filename': name, 'quote': '已提交，但未能可靠读取对应附件。'} for name in matched_files)
            item['core_groups'] = _core_groups(context + section) if core else []
            item['_priority'] = priority
            result.append(item)
    for item in result:
        item.pop('_priority', None)
    for name, readable in supplied.items():
        if not readable and not any(i['status'] == 'review' and any(e.get('filename') == name for e in i['evidence']) for i in result):
            core = bool(CORE_TERMS.search(Path(name).stem))
            result.append(_item('attachment_file_' + name, name, 'review' if core else 'info',
                                '附件已提交但未能可靠读取；' + ('名称表明可能承载核心材料，需核实。' if core else '未确认其影响核心材料，仅作一般提示。'),
                                advice='可提供可读取版本或人工核对。'))
    return result


def _report(inventory, items, details, observations, limitations):
    leaves = [i for i in items if i['key'] not in {'chapters', 'attachments'}] + details
    counted = [i for i in leaves if i.get('counted', True)]
    missing = [i for i in counted if i['status'] == 'missing']
    review = [i for i in counted if i['status'] == 'review']
    info = [i for i in counted if i['status'] == 'info']
    status = _combine(items)
    return {'version': VERSION, 'status': status, 'label': LABELS[status], 'filename': inventory[0]['filename'],
            'inventory': inventory, 'items': items, 'details': details, 'observations': observations,
            'missing_count': len(missing), 'review_count': len(review), 'info_count': len(info), 'limitations': limitations,
            'scope': '前置材料检查：文件可读性、建设方案与投资预算主体明显残缺、预算及建设功能/设备采购/服务内容主要空表、明确依赖的主要附表与必要附件。接受同类正文说明，专项内容的充分性与正确性不在本次范围内。',
            'conclusion': '未发现明显材料残缺。' if status == 'pass' else LABELS[status],
            'disclaimer': '通过仅表示未发现明显材料残缺，不代表九大专项审查通过。一般提示不影响总体结论。'}
