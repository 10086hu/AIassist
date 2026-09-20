"""Compare explicitly named investment components without summing unrelated tables."""
from io import BytesIO
import re
from docx import Document
from app.modules.docx_grid import table_grid

NAMES = {'软件开发费': ('应用软件开发费', '软件开发费用', '应用软件开发', '软件开发费', '软件开发'),
         '工程其他费用': ('工程建设其他费用', '工程其他费用', '工程建设其他费')}


def find_component_differences(content):
    doc = Document(BytesIO(content))
    prose = []
    for p in doc.paragraphs:
        text = re.sub(r'\s+', '', p.text)
        if '本项目总投资' not in text or any(t in text for t in ('上期', '去年', '历史', '原方案', '原批复', '一期', '二期')):
            continue
        for canonical, names in NAMES.items():
            match = re.search('(?:' + '|'.join(names) + r')[为：:]?([\d,.]+)万元', text)
            if match: prose.append((canonical, float(match[1].replace(',', '')), text))
    tables = []
    for ti, t in enumerate(doc.element.body.findall('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tbl'), 1):
        rows = table_grid(t, repeat_vertical=False)
        if not any(r and r[0].strip() == '一' for r in rows): continue
        col = next((i for r in rows[:3] for i, c in enumerate(r) if '万元' in c and any(w in c for w in ('金额', '小计', '费用'))), None)
        if col is None: continue
        for ri, row in enumerate(rows, 1):
            if col >= len(row) or not re.fullmatch(r'[\d,.]+', row[col].strip()): continue
            for canonical, names in NAMES.items():
                if any(re.sub(r'\s+', '', c) in names for c in row[:col]):
                    tables.append((canonical, float(row[col].replace(',', '')), f'DOCX表格{ti}行{ri}：' + ' | '.join(row)))
    return [{'message': f'资料不足：{name}正文为 {a:g} 万元，投资汇总表为 {b:g} 万元，分项口径不一致，需核实版本或费用范围。', 'evidence': line + ' | ' + row, 'section': '正文与投资汇总分项'}
            for name, a, line in prose for kind, b, row in tables if kind == name and abs(a-b) > .05]
