"""Construction-basis checks over ordered, addressable document evidence.

Only the basis service uses this reader. Other modules retain BaseValidator.
DocumentIndex is also used by the precise preview, so coordinates and quotes
refer to the same paragraphs/physical table rows, including merged cells.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.modules.resource.quantity_parser import (
    DocumentIndex, _public_location, compact, heading_level, has_heading_style,
)
from app.modules.shanghai_review.construction_basis_validator import _SECURITY_RISK_MAP

NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
MONEY = re.compile(rf"({NUMBER})\s*[（(]?\s*(亿元|万元|元)\s*[）)]?")
TOTAL = re.compile(rf"(?:本(?:期)?项目|项目)?总投资(?:估算|额|金额)?\s*(?:为|是|：|:|约|共|合计|人民币)*\s*({NUMBER})\s*[（(]?\s*(亿元|万元|元)")
TITLE_NUMBER = re.compile(r"^(\d+(?:\.\d+)+)(?:[.．、\s]+|(?=[\u4e00-\u9fff]))(.+)$")
CANONICAL_COSTS = {
    '软件开发': r'(?:应用)?软件开发(?:费(?:用)?)?',
    '建设费用': r'(?<!其他)(?:直接|系统)?建设费(?:用)?|一类费用',
    '其他费用': r'(?:工程)?(?:建设)?其他(?:建设)?费(?:用)?|二类费用',
    '软件产品': r'软件产品(?:购置)?(?:费(?:用)?)?',
    '硬件购置': r'硬件(?:购置|设备)(?:费(?:用)?)?',
}
FEE_NAMES = {'咨询费': '咨询费', '监理费': '监理费', '测试费': '软件测试费|软件测评费|软测费|测试费',
             '安全测评': '等保(?:[一二三四五1-5]级)?测评|安全测评', '密码测评': '密码测评|密评|密测费', '集成费': '系统集成费|集成费'}


def money(value: str, unit: str) -> Decimal:
    amount = Decimal(value.replace(',', ''))
    return amount * (Decimal(10000) if unit == '亿元' else Decimal('0.0001') if unit == '元' else Decimal(1))


def same_amount(a: Decimal, b: Decimal, left: dict | None = None, right: dict | None = None) -> bool:
    # A chapter number or year never reaches this comparison. Allow only the
    # displayed monetary rounding difference, not a percentage of the budget.
    quanta = [(block or {}).get('_money_precision', {}).get(str(value), Decimal('.01'))
              for value, block in [(a,left),(b,right)]]
    quantum = max(quanta)
    return a.quantize(quantum, rounding=ROUND_HALF_UP) == b.quantize(quantum, rounding=ROUND_HALF_UP)


def record_precision(block: dict, value: Decimal, number: str, unit: str) -> None:
    digits = len(number.split('.')[1]) if '.' in number else 0
    quantum = money(str(Decimal(10) ** -digits),unit)
    block.setdefault('_money_precision', {})[str(value)] = quantum


def clean_title(value: str) -> str:
    value = unicodedata.normalize('NFKC', value).strip()
    m = TITLE_NUMBER.match(value)
    if m:
        value = m[2]
    return re.sub(r'^第[一二三四五六七八九十百\d]+[章节篇]\s*', '', value).strip(' .、：:')


class BasisDocument:
    def __init__(self, content: bytes, filename: str):
        self.filename = filename
        self.is_docx = filename.lower().endswith(('.docx','.doc'))
        if not self.is_docx:
            # TXT remains supported; preserve every original line (including
            # blank lines) so the legacy text preview can use line addresses.
            from docx import Document
            from io import BytesIO
            document=Document()
            for line in content.decode('utf-8-sig',errors='replace').splitlines():
                document.add_paragraph(line)
            stream=BytesIO(); document.save(stream); content=stream.getvalue()
        self.index = DocumentIndex(content, filename)
        self.blocks = self.index.blocks
        self.headings: list[tuple[int, int, dict]] = []
        # Resolve boundaries using report headings, rather than the next child
        # heading. Auto-numbered headings keep their Word outline level.
        from docx.text.paragraph import Paragraph
        from app.modules.resource.quantity_parser import W
        paragraphs = [c for c in self.index.body_children(self.index.document.element.body) if c.tag == W+'p']
        for i, b in enumerate(self.blocks):
            if not b.get('is_heading') or not b.get('paragraph'):
                continue
            p = Paragraph(paragraphs[b['paragraph']-1], self.index.document)
            n = TITLE_NUMBER.match(unicodedata.normalize('NFKC', b['quote']))
            level = len(n[1].split('.')) if n else heading_level(p)
            if has_heading_style(p) or n or level == 1:
                self.headings.append((i, level or 1, b))
        self.tables: dict[tuple, list[dict]] = defaultdict(list)
        for b in self.blocks:
            if b.get('table_index'):
                self.tables[(b['table_index'], b.get('table_path', ''))].append(b)
        for rows in self.tables.values():
            rows.sort(key=lambda b: b['row_index'])

    def sections(self, pattern: str, exclude: str = '') -> list[list[dict]]:
        groups = []
        for position, (start, level, heading) in enumerate(self.headings):
            title = clean_title(heading['quote'])
            if not re.search(pattern, title) or exclude and re.search(exclude, title):
                continue
            end = next((i for i, n, _ in self.headings[position+1:] if n <= level), len(self.blocks))
            groups.append(self.blocks[start:end])
        return groups

    def select(self, pattern: str, exclude: str = '') -> list[dict]:
        return unique_blocks([b for group in self.sections(pattern, exclude) for b in group])

    def locations(self, blocks: list[dict], scope: bool = False) -> list[dict]:
        locations=[_public_location(b, self.filename, 'scope' if scope else 'matched', 'scope' if scope else 'evidence')
                   for b in unique_blocks(blocks)]
        if not self.is_docx:
            for loc in locations:
                loc.update(file_type='txt',line_start=loc.get('paragraph'),line_end=loc.get('paragraph'),precision='line')
        return locations

    def issue(self, message: str, blocks: list[dict], *, manual: bool = False, scope: bool = False,
              suggestion: str = '请核对所列原文，统一相同范围、相同口径的内容。', severity: str = 'risk') -> dict:
        locations = self.locations(blocks, scope)
        return {'message': message, 'section': '；'.join(dict.fromkeys(b.get('section', '') for b in blocks if b.get('section'))),
                'evidence': '\n'.join(b['quote'] for b in unique_blocks(blocks)),
                'severity': 'warning' if manual else severity, 'suggestion': suggestion,
                'manual_review': manual, 'source_locations': locations,
                'source_location': {'file_name': self.filename, 'file_type': 'docx' if self.is_docx else 'txt', 'precision': 'document_blocks' if locations else 'unknown',
                                    'entries': locations, 'verified_basis': True}}


def unique_blocks(blocks: list[dict]) -> list[dict]:
    seen = set(); result = []
    for b in blocks:
        k = (b.get('paragraph'), b.get('table_index'), b.get('table_path'), b.get('row_index'))
        if k not in seen:
            seen.add(k); result.append(b)
    return result


def table_cells(b: dict) -> list[dict]:
    return b.get('cells', [])


def cell_at(b: dict, column: int) -> str:
    return next((c['quote'] for c in table_cells(b) if c['column_start'] <= column <= c['column_end']), '')


def column(rows: list[dict], pattern: str) -> tuple[int, int, str] | None:
    for i, row in enumerate(rows[:6]):
        for c in table_cells(row):
            if re.search(pattern, compact(c['quote'])):
                return i, c['column_start'], c['quote']
    return None


def budget_tables(doc: BasisDocument):
    budget_blocks = doc.select(r'^(?:项目预算|投资估算和资金来源|项目投资估算|项目总投资估算|应用系统软件开发分项投资估算|应用软件开发投资估算|购买服务项目投资估算表.*|附件.*投资估算.*|预算编制说明|项目软硬件购置及应用系统开发投资估算|软件开发功能清单|软件开发预算.*|预算投资表.*)$')
    keys = {(b.get('table_index'), b.get('table_path','')) for b in budget_blocks if b.get('table_index')}
    for rows in doc.tables.values():
        header = column(rows, r'^(?:总金额|总价|报价|小计|金额|合价|申报金额|费用)(?:\(|$)')
        if not header and rows and re.search(r'投资估算总表.*单位[:：](亿元|万元|元)',compact(rows[0]['quote'])):
            # Some summary tables state their unit in a merged title instead
            # of a conventional column header. Infer only an unambiguous
            # numeric column within this explicitly labelled budget table.
            unit=re.search(r'单位[:：](亿元|万元|元)',compact(rows[0]['quote']))[1]
            counts=defaultdict(int)
            for b in rows[1:]:
                for c in table_cells(b):
                    if c['column_start']>1 and re.fullmatch(NUMBER,compact(c['quote'])):
                        counts[c['column_start']]+=1
            ordered=sorted(counts,key=counts.get,reverse=True)
            if ordered and (len(ordered)==1 or counts[ordered[0]]>counts[ordered[1]]):
                header=(0,ordered[0],f'金额（{unit}）')
        if header and (rows[0]['table_index'],rows[0].get('table_path','')) in keys:
            yield rows, header


def numeric_cell(text: str, header: str) -> Decimal | None:
    text = compact(text)
    explicit = MONEY.fullmatch(text)
    if explicit:
        return money(*explicit.groups())
    if re.fullmatch(NUMBER, text):
        unit = '亿元' if '亿元' in header else '万元' if '万元' in header else '元' if '元' in header else None
        return money(text, unit) if unit else None
    return None


def staged_totals(doc: BasisDocument, issues: list[dict]) -> list[tuple[Decimal,dict]]:
    totals=[]
    for rows, (hi,total_col,total_header) in budget_tables(doc):
        phase=column(rows,r'^(?:分项目|建设阶段|阶段|分期)$')
        component=column(rows,r'^(?:建设内容|分项名称|费用名称)$')
        amount=column(rows,r'^(?:分项金额|金额|单价)(?:\(|$)')
        if not phase or not component or not amount or amount[1]==total_col:
            continue
        if column(rows,r'^数量(?:\(|$)'):
            continue  # Unit price cannot be summed when quantities are present.
        groups=defaultdict(list)
        for b in rows[hi+1:]:
            label=cell_at(b,phase[1]).strip()
            if re.fullmatch(r'(?:第?[一二三四五六七八九十\d]+期|第?[一二三四五六七八九十\d]+阶段)',label):
                groups[label].append(b)
        if len(groups)<2 or len(groups)!=len({cell_at(b,phase[1]).strip() for b in rows[hi+1:]}):
            continue
        aggregate=Decimal(0);total_sources=[];complete=True
        for label, members in groups.items():
            total_cells=[c for b in members for c in table_cells(b) if c['column_start']<=total_col<=c['column_end']]
            origins={(c['origin_row'],c['quote']) for c in total_cells}
            values={numeric_cell(c['quote'],total_header) for c in total_cells}
            if len(origins)!=1 or len(values)!=1 or None in values:
                complete=False;break
            total=next(iter(values));aggregate+=total;total_sources.extend(members)
            parts=[numeric_cell(cell_at(b,amount[1]),amount[2]) for b in members]
            if None in parts:
                continue
            if not all(re.search(r'软件|硬件|测评|服务|开发|设备',cell_at(b,component[1])) for b in members):
                continue
            calculated=sum(parts,Decimal(0))
            # The merged total has an explicit phase, so repeated cells must
            # be counted once; component rows have independent stated fees.
            if not same_amount(total,calculated):
                message=f'{label}投资总价{total:g}万元，与该分期各建设内容金额之和{calculated:g}万元不一致，差额{abs(total-calculated):g}万元。'
                existing=next((i for i in issues if i['message']==message),None)
                if existing:
                    additions=doc.locations(members)
                    existing['source_locations'].extend(additions)
                    existing['source_location']['entries']=existing['source_locations']
                    existing['evidence']+='\n'+'\n'.join(b['quote'] for b in members)
                else:
                    issues.append(doc.issue(message,members,suggestion='请核对该分期的软件、硬件及测评等金额，与分期合计和附件预算保持一致。'))
        if complete:
            # Preserve the real rows supporting a derived whole-project total.
            representative=dict(rows[hi],_derived_sources=total_sources)
            totals.append((aggregate,representative))
    return totals


def _investment(doc: BasisDocument) -> tuple[list[dict], dict]:
    issues = []; totals: list[tuple[Decimal, dict]] = []
    components = defaultdict(list)
    for b in doc.blocks:
        if b.get('is_heading'):
            continue
        if b.get('paragraph'):
            for m in TOTAL.finditer(b['quote']):
                # A stated phase/subsystem total has a different comparison
                # scope and cannot be compared with the whole project's total.
                prefix = b['quote'][max(0, m.start()-30):m.start()]
                if re.search(r'(?:一期|二期|第一阶段|第二阶段|子系统)\s*$', prefix) or re.search(r'(?:一期|二期|第一阶段|第二阶段)',b.get('section','')):
                    continue
                value=money(m[1],m[2]); record_precision(b,value,m[1],m[2])
                totals.append((value, b))
            if TOTAL.search(b['quote']):
                for name, alias in CANONICAL_COSTS.items():
                    for m in re.finditer(rf'(?:{alias})\s*(?:为|是|：|:|约)?\s*({NUMBER})\s*[（(]?\s*(亿元|万元|元)', b['quote']):
                        value=money(m[1],m[2]); record_precision(b,value,m[1],m[2])
                        components[name].append((value, b))
    table_fees = {}; fee_blocks = []; fee_records = []; hardware = []
    for rows, (hi, col, title) in budget_tables(doc):
        is_summary=any(re.search('一类费用|软件开发费|系统建设费',b['quote']) for b in rows) and any(re.search('二类费用|其他费用|工程其他费用|工程建设其他费用',b['quote']) for b in rows)
        for b in rows[hi+1:]:
            # Keep the physical amount-column origin to avoid counting a
            # vertically merged stage total once per child row.
            value = numeric_cell(cell_at(b, col), title)
            if value is None:
                # Some total rows are horizontally merged, so the labelled
                # total amount can move left of the usual amount column.
                if any(re.fullmatch(r'(?:项目)?总投资(?:估算|额)?|总计|合计', compact(c['quote'])) for c in table_cells(b)):
                    values = [numeric_cell(c['quote'], title) for c in table_cells(b)]
                    values = [v for v in values if v is not None]
                    value = values[0] if len(values) == 1 else None
            if value is None:
                continue
            for c in table_cells(b):
                if numeric_cell(c['quote'],title)==value:
                    m=MONEY.fullmatch(compact(c['quote']))
                    number,unit=m.groups() if m else (compact(c['quote']),'亿元' if '亿元' in title else '万元' if '万元' in title else '元')
                    record_precision(b,value,number,unit)
            labels = [compact(c['quote']) for c in table_cells(b) if c['column_start'] != col]
            labels = [re.sub(r'^(?:[一二三四五六七八九十]+[、.]|\d+(?:\.\d+)*[.、]?)(?=[^\d.])','',x) for x in labels]
            label = ' '.join(labels)
            phase_scope=re.search(r'一期|二期|第一阶段|第二阶段',b.get('section',''))
            if not phase_scope and (any(re.fullmatch(r'(?:项目)?总投资(?:估算|额)?', x) for x in labels) or is_summary and any(x in {'总计','合计'} for x in labels)):
                totals.append((value, b))
            for name, alias in CANONICAL_COSTS.items():
                if not phase_scope and any(re.fullmatch(alias, x) for x in labels):
                    components[name].append((value, b))
            for name, alias in FEE_NAMES.items():
                if any(re.fullmatch(rf'(?:{alias})(?:费)?(?:\([^)]*\))?', x) for x in labels):
                    table_fees[name] = value; fee_blocks.append(b)
                    fee_records.append((name,value,b,next(x for x in labels if re.fullmatch(rf'(?:{alias})(?:费)?(?:\([^)]*\))?',x))))
            if any(re.fullmatch(r'(?:软硬件购置费|软硬件购置费用)', x) for x in labels):
                hardware.append((value, b))
    derived=staged_totals(doc,issues)
    totals.extend(derived)
    if len(totals) < 2:
        scopes = doc.select(r'投资概况|总投资|项目投资估算|投资估算和资金来源|预算编制说明')
        issues.append(doc.issue('未取得至少两处可比较的项目总投资金额，无法完成总额一致性核对；分期、分项金额没有作为项目总额使用。',
                                [b for b in scopes if b.get('is_heading')][:4], manual=True, scope=True))
    else:
        first = totals[0]
        different = [(a, b) for a, b in totals[1:] if not same_amount(a, first[0],b,first[1])]
        if different:
            values = sorted(set(a for a, _ in totals))
            issues.append(doc.issue('项目总投资金额不一致：'+ '、'.join(f'{a:g}万元' for a in values)+'。',
                                    [source for b in [first[1]]+[b for _,b in different] for source in b.get('_derived_sources',[b])]))
    for name, entries in components.items():
        if len(entries) >= 2 and any(not same_amount(entries[0][0], a,entries[0][1],b) for a, b in entries[1:]):
            issues.append(doc.issue(f'投资分项“{name}”金额不一致：'+ '、'.join(f'{a:g}万元' for a in sorted(set(a for a, _ in entries)))+'；请核对正文与预算表的计算口径。',
                                    [b for _, b in entries]))
    # Use the project's authoritative configured fee evaluator when available.
    # Its applicability and upper-limit semantics must not be replaced by the
    # old inlined estimate (which also treated a fee below the cap as an error).
    bases = components.get('建设费用', [])
    complex_base = any(re.search(r'运维.*基数|基数.*运维|等比例划分', b['quote']) for b in doc.blocks if b.get('paragraph'))
    _fee_checks(doc,issues,totals,components,fee_records,bases,hardware,complex_base)
    total_status='需人工复核' if len(totals)<2 else '发现问题' if any('总投资金额不一致' in i['message'] for i in issues) else '通过'
    return issues, {'total_amounts': [float(a) for a, _ in totals], 'derived_phase_totals':[float(a) for a,_ in derived], 'total_evidence_count': len(totals), 'total_consistency_status':total_status,'fee_fields': list(table_fees)}


def _fee_checks(doc,issues,totals,components,records,bases,hardware,complex_base):
    from app.modules.price.parser import ParsedPriceItem, PriceProjectContext
    from app.modules.price.standards import evaluate_class2_fees
    if complex_base or not records or not bases or len({a for a,_ in bases})!=1:
        issues.append(doc.issue('二类费用或系统集成费尚未取得完整、同口径的申报明细及计费基数，需人工复核。',
                                [b for _,_,b,_ in records] or [b for _,b in totals[-2:]],manual=True,
                                suggestion='请核对实际费用明细、适用标准和建设与运维费用的计算口径。'))
        return
    # Location is read from substantive report prose, never inferred from a
    # cited standard or an incidental policy reference to Shanghai.
    identity=' '.join(b['quote'] for b in doc.blocks if b.get('paragraph') and b['paragraph']<=40)
    municipal=bool(re.search(r'上海(?:市|海关)',doc.filename+' '+identity))
    levels={v for v,_ in project_levels(doc.select(r'^(?:网络)?安全需求分析(?:\(|$)'))}
    def amount(key):
        vals={a for a,_ in components.get(key,[])}
        return float(next(iter(vals))*10000) if len(vals)==1 else None
    development=amount('软件开发');direct=float(bases[0][0]*10000)
    items=[ParsedPriceItem(item_name=label,total_price=float(value*10000),source_section=b.get('section',''),source_row=b.get('row_index'),evidence=b['quote'])
           for _,value,b,label in records]
    context=PriceProjectContext(project_type='construction' if re.search(r'建设|升级改造|可行性',doc.filename) else 'unknown',
        total_investment=float(totals[0][0]*10000) if totals else None,direct_construction_cost=direct,
        software_development_cost=development,hardware_software_purchase_cost=float(hardware[0][0]*10000) if len(hardware)==1 else None,
        application_development_ratio=development/direct if development is not None and direct else None,
        security_level=next(iter(levels)) if len(levels)==1 else None)
    try:
        evaluations,_=evaluate_class2_fees(items,context,'市级' if municipal else '未明确适用层级')
    except (OSError,ValueError,KeyError,TypeError):
        issues.append(doc.issue('费用明细已识别，但项目配置的二类费用标准数据不可用，无法完成标准计费核验；需人工复核。',
                                [bases[0][1]]+[b for _,_,b,_ in records],manual=True,
                                suggestion='请确认适用费用标准及其配置数据，按实际申报金额和计费基数复核。'))
        return
    for evaluation in evaluations:
        if evaluation.status=='pass':
            continue
        relevant=[b for _,_,b,_ in records if evaluation.source_item is None or b['quote']==evaluation.source_item.evidence]
        issues.append(doc.issue(evaluation.fee_name+'：'+evaluation.reason,[bases[0][1]]+relevant,
                                manual=evaluation.status!='fail',suggestion=evaluation.suggestion))


def indicator_items(doc: BasisDocument, blocks: list[dict]) -> dict[str, list[tuple[str, dict]]]:
    items = defaultdict(list)
    keys = {(b.get('table_index'), b.get('table_path', '')) for b in blocks if b.get('table_index')}
    for key in keys:
        rows = doc.tables[key]
        target = column(rows, r'^(?:指标值|目标值|指标目标值|预期目标|目标|验收值|验收标准)(?:\(|$)')
        if not target:
            continue
        hi, vc, _ = target
        names = [c['column_start'] for c in table_cells(rows[hi]) if re.search(r'(?:指标名称|[一二三四五]级指标|指标内容|考核指标)', compact(c['quote'])) and c['column_start'] != vc]
        if not names:
            continue
        for b in rows[hi+1:]:
            name = next((cell_at(b,c).strip() for c in reversed(names) if cell_at(b,c).strip()), '')
            value = cell_at(b,vc).strip()
            if name and value:
                items[compact(name)].append((value, b))
    for b in blocks:
        if b.get('paragraph') and not b.get('is_heading'):
            m = re.match(r'^\s*([^：:；;]{2,70})[：:]\s*([≥≤<>≧≦]?\s*\d+(?:\.\d+)?\s*(?:%|％|秒|毫秒|个|项|次|天|小时|万元|元|人|台|套))\s*[。；;]?$', b['quote'])
            if m:
                items[compact(m[1])].append((m[2], b))
    return items


def _indicators(doc: BasisDocument) -> tuple[list[dict], dict]:
    planned = doc.select(r'规划指标|成效考核目标|绩效目标|绩效指标', r'登记|管理|评价')
    acceptance = doc.select(r'验收标准|验收指标|验收要求|具体指标和内容')
    left = indicator_items(doc, planned); right = indicator_items(doc, acceptance)
    issues = []; matched = 0
    if not left or not acceptance:
        issues.append(doc.issue('未取得可逐项比较的规划指标及验收指标，无法判定指标一致性；未以全文关键词相似度代替核对。',
                                [b for b in planned+acceptance if b.get('is_heading')][:4], manual=True, scope=True,
                                suggestion='请明确规划指标和验收指标的名称、目标值、单位及验收口径。'))
        return issues, {'planned_count':len(left), 'acceptance_count':len(right), 'matched_count':0}
    unresolved = []; evidence = []
    for name, entries in left.items():
        equivalents = right.get(name)
        if equivalents:
            matched += 1
            normalize = lambda v: compact(v).replace('≧','≥').replace('≦','≤').replace('>=','≥').replace('<=','≤')
            lv = {normalize(v) for v,_ in entries}; rv = {normalize(v) for v,_ in equivalents}
            if lv != rv:
                issues.append(doc.issue(f'指标“{name}”的规划目标值与验收目标值不一致：规划'+ '、'.join(sorted(lv))+'；验收'+'、'.join(sorted(rv))+'。',
                                        [b for _,b in entries+equivalents]))
        else:
            # Free-form acceptance descriptions require explicit target values
            # next to the same name. Mere mention is not a verified match.
            values = [v for v,_ in entries]
            lines = [b for b in acceptance if name in compact(b['quote']) and not b.get('is_heading')]
            if lines and all(any(compact(v) in compact(b['quote']) for b in lines) for v in values):
                matched += 1
            else:
                unresolved.append(name); evidence.extend(b for _,b in entries)
    for name, entries in right.items():
        if name not in left:
            unresolved.append('验收侧：'+name); evidence.extend(b for _,b in entries)
    if unresolved:
        scopes = [b for b in acceptance if b.get('is_heading')][:2]
        issues.append(doc.issue(f'{len(unresolved)}项指标未取得对应名称及明确目标值，需核对验收口径：'+'、'.join(unresolved)+'。',
                                evidence+scopes, manual=True,
                                suggestion='请将规划指标逐项对应至验收指标，并明确数值、单位和验收方式。'))
        # Headings cited to explain the search range are scope, not faulty text.
        for loc in issues[-1]['source_locations']:
            if any(loc.get('paragraph')==b.get('paragraph') for b in scopes):
                loc.update(role='scope',match_status='scope')
    return issues, {'planned_count':len(left), 'acceptance_count':len(right), 'matched_count':matched, 'unresolved_count':len(unresolved)}


LEVEL = re.compile(r'(?:等保(?:2\.0)?|(?:网络|信息系统)?安全(?:等级保护|保护等级|等级)|等级保护)\s*(?:制度|的)?\s*(?:定级为|定为|等级为|级别为|要求为|要求|为|：|:|达到|按)?\s*第?\s*([一二三四五1-5])\s*级|([一二三四五1-5])\s*级\s*(?:等保|等级保护|安全保护)')


def project_levels(blocks: list[dict]) -> list[tuple[int, dict]]:
    found = []
    mapping = dict(zip('一二三四五',[1,2,3,4,5]))
    for b in blocks:
        if b.get('is_heading'):
            continue
        text = b['quote']
        if not re.search(r'本项目|本系统|本平台|本安全方案|按照|按.{0,8}级|满足|达到|定级|定为|需满足|需符合|不低于|等级.{0,12}(?:为|：|:)',text):
            continue
        for m in LEVEL.finditer(text):
            # A quoted standard title is not the system's stated target.
            prefix=text[:m.start()]
            if prefix.count('《')>prefix.count('》'):
                continue
            raw = m[1] or m[2]
            found.append((mapping.get(raw, int(raw) if raw.isdigit() else 0), b))
    return found


def risk_blocks(blocks: list[dict]) -> dict[str,list[dict]]:
    result=defaultdict(list)
    for b in blocks:
        if b.get('is_heading'):
            continue
        for category, keywords in _SECURITY_RISK_MAP.items():
            terms = keywords - {'恢复', '异地'} if category=='备份恢复' else keywords
            if any(k.lower() in b['quote'].lower() for k in terms):
                result[category].append(b)
    return result


def _security(doc: BasisDocument) -> tuple[list[dict],dict]:
    needs=doc.select(r'^(?:网络)?安全需求分析(?:\(|$)')
    build=doc.select(r'^(?:网络安全及备份系统方案|安全建设内容|网络安全方案|备份系统方案|安全技术方案|数据备份(?:及|与)?(?:恢复|容灾).*)$', r'现状')
    # Security support can be described in the adjacent cloud/backup chapter.
    build=unique_blocks(build+doc.select(r'^(?:PaaS安全服务|数据备份与恢复|备份及恢复|灾备方案|备份恢复方案)'))
    issues=[]
    if not needs or not build:
        issues.append(doc.issue('未完整识别安全需求与安全建设方案，无法判定对应关系；请核对实际章节标题。',
                                [b for b in needs+build if b.get('is_heading')][:4],manual=True,scope=True))
        return issues, {'needs_blocks':len(needs),'construction_blocks':len(build)}
    left=project_levels(needs); right=project_levels(build)
    ls={v for v,_ in left}; rs={v for v,_ in right}
    if len(ls)==len(rs)==1 and ls!=rs:
        issues.append(doc.issue(f'安全需求与建设方案的系统等保目标不一致：需求为{next(iter(ls))}级，建设为{next(iter(rs))}级。',
                                [b for _,b in left+right]))
    elif not ls or not rs or len(ls)>1 or len(rs)>1:
        issues.append(doc.issue('安全需求或建设方案中的项目等保等级未能唯一确认，需结合系统、子系统范围复核；未将通用标准描述直接作为项目等级。',
                                [b for _,b in left+right] or [needs[0],build[0]],manual=True))
    nr=risk_blocks(needs); br=risk_blocks(build)
    missing=sorted(set(nr)-set(br))
    if missing:
        blocks=[b for k in missing for b in nr[k]]
        issues.append(doc.issue('安全需求中的“'+'、'.join(missing)+'”尚未识别到明确对应措施，请结合政务云、PaaS及其他安全章节复核；关键词未命中不直接认定方案缺失。',
                                blocks+[build[0]],manual=True,suggestion='请说明这些需求由本项目或现有平台的哪些措施承接，并核对相关参数。'))
        issues[-1]['source_locations'][-1].update(role='scope',match_status='scope')
    return issues, {'needs_blocks':len(needs),'construction_blocks':len(build),'needs_levels':sorted(ls),'construction_levels':sorted(rs),'unmatched_risks':missing}


def normal_name(value: str) -> str:
    value=clean_title(value)
    return re.sub(r'[\W_]|\u200b','',value).replace('系统','').replace('模块','').lower()


def name_matches(a: str,b: str) -> bool:
    a=normal_name(a); b=normal_name(b)
    if not a or not b:
        return False
    if a==b:
        return True
    return bool(min(len(a),len(b))>=4 and (a in b or b in a))


def referenced_name_matches(name: str,title: str) -> bool:
    if name_matches(name,title):
        return True
    # Explicit references already fix the subsystem/section. Hierarchical
    # paths may have different middle labels while identifying the same leaf.
    leaf=lambda v: normal_name(re.split(r'[-－—→]',clean_title(v))[-1])
    a,b=leaf(name),leaf(title)
    return len(a)>=2 and a==b


def _modules(doc: BasisDocument) -> tuple[list[dict],dict]:
    groups=doc.sections(r'^(?:应用系统建设|软件开发服务方案|应用系统软件开发方案|应用软件建设|应用系统建设方案)$')
    construction=unique_blocks([b for g in groups for b in g])
    addresses={b.get('paragraph') for b in construction if b.get('paragraph')}
    roots={g[0].get('paragraph') for g in groups}
    headings=[b for _,_,b in doc.headings if b.get('paragraph') in addresses-roots]
    levels={b.get('paragraph'):level for _,level,b in doc.headings}
    all_headings={}
    for _,_,b in doc.headings:
        m=TITLE_NUMBER.match(unicodedata.normalize('NFKC',b['quote']))
        if m: all_headings.setdefault(m[1],[]).append(b)
    items=[]; scopes=[]
    for rows, (hi,_,_) in budget_tables(doc):
        header=rows[hi]
        names=[c['column_start'] for c in table_cells(header) if re.search(r'^(?:子系统名称|系统名称|模块名称|子模块名称|功能名称|[一二三四五]级功能)$',compact(c['quote']))]
        ref=next((c['column_start'] for c in table_cells(header) if re.search('对应建设内容.*章节',c['quote'])),None)
        if not names:
            continue
        scopes.append(header)
        for b in rows[hi+1:]:
            values=[cell_at(b,c).strip() for c in names if cell_at(b,c).strip()]
            if not values or any(re.search(r'合计|总计|总投资',v) for v in values):
                continue
            if all(re.search(r'测评|测试费|咨询费|监理费',v) for v in values):
                continue
            references=re.findall(r'(?<![\d.])\d+(?:\.\d+)+(?![\d.])',cell_at(b,ref)) if ref else []
            items.append({'name':'-'.join(values),'names':values,'references':references,'block':b,'explicit_ref':ref is not None})
    issues=[]
    if not headings or not items:
        issues.append(doc.issue('未取得完整的建设功能目录或预算软件功能清单，无法完成对应核对；没有把招标章节或父标题当作功能项。',
                                [g[0] for g in groups]+scopes,manual=True,scope=True,
                                suggestion='请核对实际软件建设章节及软件开发预算明细表，明确功能名称或对应建设章节。'))
        return issues, {'construction_headings':len(headings),'budget_items':len(items),'matched_items':0}
    matched_items=0; unresolved=[]; covered=set()
    for item in items:
        matches=[]
        if item['references']:
            for ref in item['references']:
                matches.extend(all_headings.get(ref,[]))
            item['related_blocks']=matches
            if len({TITLE_NUMBER.match(unicodedata.normalize('NFKC',b['quote']))[1] for b in matches}) != len(set(item['references'])):
                unresolved.append(item)
                continue
            # Duplicate section numbers require human review, not first match.
            if any(len(all_headings.get(ref,[]))!=1 for ref in item['references']):
                unresolved.append(item); continue
        elif item['explicit_ref']:
            unresolved.append(item); continue
        else:
            matches=[b for b in headings if any(name_matches(n,b['quote']) for n in item['names'])]
        if matches:
            if item['references'] and not any(referenced_name_matches(n,b['quote']) for n in item['names'] for b in matches):
                # A reference to a parent may legitimately aggregate many
                # child functions. Verify names within that exact subtree.
                descendants=[h for ref in item['references'] for number,hs in all_headings.items() if number.startswith(ref+'.') for h in hs]
                if not any(referenced_name_matches(n,b['quote']) for n in item['names'] for b in descendants):
                    unresolved.append(item); continue
            matched_items+=1
            covered.update(b.get('paragraph') for b in matches)
        else:
            unresolved.append(item)
    if unresolved:
        issues.append(doc.issue(f'{len(unresolved)}条预算功能的名称或引用章节需核对，尚未取得唯一、可核验的建设内容对应关系：'+'；'.join(i['name'] for i in unresolved)+'。',
                                [b for i in unresolved for b in [i['block'],*i.get('related_blocks',[])]]+[g[0] for g in groups],manual=True,
                                suggestion='请核对预算表功能名称及对应章节；编号应精确匹配，不能以编号包含关系替代。'))
        for loc in issues[-1]['source_locations']:
            if loc.get('paragraph') in {g[0].get('paragraph') for g in groups}:loc.update(role='scope',match_status='scope')
    # Check the reverse direction at the budget's granularity. An explicit
    # reference to a parent covers its children; numbering alone is never a
    # cross-chapter fuzzy match.
    uncovered=[]
    refs={r for i in items for r in i['references']}
    for b in headings:
        if b.get('paragraph') in covered:
            continue
        m=TITLE_NUMBER.match(unicodedata.normalize('NFKC',b['quote']))
        if refs and m:
            num=m[1]
            if any(num==r or num.startswith(r+'.') or r.startswith(num+'.') for r in refs):
                continue
            # Only report leaves, not every ancestor of the same missing item.
            if any(n!=num and n.startswith(num+'.') for n in all_headings):
                continue
            uncovered.append(b)
        elif not refs:
            # Non-numbered templates: use top-level modules of each software
            # scheme, avoiding sub-functions below that budget granularity.
            top=next((g[0] for g in groups if b in g),None)
            if top and levels.get(b.get('paragraph'))==levels.get(top.get('paragraph'),0)+1 and not any(name_matches(b['quote'],n) for i in items for n in i['names']):
                # Parent names and child names often differ in non-numbered
                # templates. A matched child provides a budget link for its
                # enclosing module without guessing aliases for the parent.
                start=next(i for i,x in enumerate(construction) if x is b)
                level=levels[b['paragraph']]
                end=next((i for i in range(start+1,len(construction)) if construction[i].get('paragraph') in levels and levels[construction[i]['paragraph']]<=level),len(construction))
                if not any(x.get('paragraph') in covered for x in construction[start:end]):
                    uncovered.append(b)
    if uncovered:
        issues.append(doc.issue(f'{len(uncovered)}项建设功能尚未识别到预算对应项：'+'；'.join(clean_title(b['quote']) for b in uncovered)+'。',
                                uncovered+scopes,manual=True,
                                suggestion='请核对功能是否由其他预算项合并计费、复用现有能力，或需要补充预算对应说明。'))
        for loc in issues[-1]['source_locations']:
            if loc.get('row_index') in {b.get('row_index') for b in scopes} and loc.get('table_index') in {b.get('table_index') for b in scopes}:loc.update(role='scope',match_status='scope')
    return issues, {'construction_headings':len(headings),'budget_items':len(items),'matched_items':matched_items,'unresolved_items':len(unresolved),'uncovered_headings':len(uncovered)}


def review_basis_document(content: bytes, filename: str) -> tuple[BasisDocument, dict[str,tuple[list[dict],dict]]]:
    doc=BasisDocument(content,filename)
    if not doc.blocks:
        raise ValueError('未能提取有效正文，请确认报告不是扫描件或加密文件')
    return doc, {'BASIS_007':_investment(doc), 'BASIS_009':_indicators(doc),
                 'BASIS_012':_modules(doc), 'BASIS_014':_security(doc)}
