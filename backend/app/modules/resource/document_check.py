"""Document-aware resource checks shared by the API and audit runner."""
from io import BytesIO
from dataclasses import replace
import re
from .quantity_parser import parse_resource_items, _parse_quantity
from .quantity_rules import evaluate_resource_rules, RULE_15_CODE, RULE_16_OS_CODE, RULE_16_DB_CODE


def _coverage_statement(text, field, expected=0):
    """A deployment commitment can cover many hosts; compatibility cannot."""
    compact = re.sub(r'\s+', '', text)
    if re.search(r'例如|示例|模板|参考案例|原批复|上期|历史配置|建议|可选|如需|假如|尚未|待定|未配置|未配备|未全部|不采用|不使用|不安装|不配置|无需|不覆盖|仅支持|兼容', compact):
        return False
    host = r'(?:数据库服务器|数据库主机|数据库节点)' if field == '数据库' else r'(?:服务器|云主机|虚拟机|计算节点)'
    # Avoid mistaking the word 数据库 in 数据库服务器 for the software.
    software = compact if field != '数据库' else re.sub(host, '主机', compact)
    product = r'操作系统|银河麒麟|统信|UOS|Linux|Windows\s*Server|openEuler' if field == '操作系统' else r'数据库|达梦|MySQL|PostgreSQL|Oracle|人大金仓|GaussDB'
    if not re.search(product, software, re.I):
        return False
    action = r'(?:配置|配备|安装|部署|使用|采用|共用|共享|提供|配)'
    # Bind the action to the product, within a clause: configuring a system
    # disk "for the OS" is not the same as configuring the OS itself.
    direct = re.search(action + r'[^，,；;。、]{0,16}(?:' + product + r')', software, re.I)
    reverse = re.search(r'(?:' + product + r')[^，,；;。、]{0,30}(?:统一提供|统一配备|统一安装|统一部署|覆盖所有|覆盖全部)', software, re.I)
    if not (direct or reverse):
        return False
    if re.search(r'(?:支持|能够|可支持|可兼容).{0,18}' + field, compact):
        return False
    if re.search(r'(?:支持|能够).{0,18}' + host, compact):
        return False
    universal = bool(re.search(r'(?:所有|全部|每台|每一台|各台|各个|全部新增|本期新增)' + host, compact)
                     or re.search(r'(?:本项目|本期|本次)(?:的)?(?:多个|多台)?' + host + r'.{0,8}(?:均|统一|共用|共享|配一套|配同一)', compact)
                     or re.search(host + r'(?:均|全部|统一).{0,12}(?:配置|配备|安装|部署|使用|采用|共用|共享|由)', compact))
    if re.search(r'部分|其中|仅为|仅覆盖|只覆盖|除.{0,15}外', compact):
        universal = False
    number = r'[\d零〇一二两三四五六七八九十百]+'
    counts = [_parse_quantity(m.group(1)) for m in re.finditer(r'(' + number + r')[台个](?:新增)?' + host, compact)]
    counts.extend(_parse_quantity(m.group(1)) for m in re.finditer(host + r'[（(]?(?:共|数量为|数量[:：])?(' + number + r')[台个]', compact))
    counts = [n for n in counts if n is not None]
    if expected > 0 and counts and max(counts) < expected:
        universal = False
    # Without an extracted host count, explicit plural deployment still proves
    # coverage of that declared group, not of an unseen separate group.
    declared_group = expected <= 0 and (bool(counts) or bool(re.search(r'(?:多台|多个|若干台)' + host + r'.{0,12}(?:共用|共享|统一|配同一|配一套)', compact)))
    return universal or declared_group or bool(expected > 0 and counts and max(counts) >= expected)


def _apply_document_coverage(findings, paragraphs, items):
    texts = list(dict.fromkeys(paragraphs + [i.raw_text or i.spec for i in items]))
    for index, f in enumerate(findings):
        if f.rule_code not in {RULE_16_OS_CODE, RULE_16_DB_CODE}:
            continue
        field = '操作系统' if f.rule_code == RULE_16_OS_CODE else '数据库'
        expected = f.source_quantities.get('服务器数量' if field == '操作系统' else '数据库服务器数量', 0)
        quotes = [t for t in texts if any(_coverage_statement(s, field, expected) for s in re.split(r'[。；;\n]', t))]
        conflicts = [t for t in texts if re.search(r'(?:服务器|主机|节点).{0,45}(?:未配置|未配备|缺少|待配置).{0,12}' + field + r'|' + field + r'\s*[：:]\s*(?:未配置|未配备|缺少|待配置)', t)]
        if quotes and not conflicts:
            findings[index] = replace(f, result_label='通过', severity='通过', suggestion=None,
                reason=f'原文明确说明相关服务器统一配置或共享使用{field}，按部署覆盖关系核验通过；采购套数与服务器台数不要求完全一致。此结论不代替商业许可条款核验。',
                evidence_examples=quotes[:6], source_locations=[dict(quote=q, precision='text') for q in quotes[:6]],
                source_section='服务器部署与统一配置说明')
        elif quotes and conflicts:
            findings[index] = replace(f, result_label='无法判断', severity='需人工确认',
                reason=f'存在统一配置说明，也存在{field}未配置或待配置的明确记录，需核实是否属于同一阶段和对象。',
                evidence_examples=(quotes + conflicts)[:8],
                source_locations=[dict(quote=q, precision='text') for q in (quotes + conflicts)[:8]])
    return findings


def _check_resource_document_without_locations(content, filename, items=None):
    items = parse_resource_items(content, filename) if items is None else items
    findings = _apply_document_coverage(evaluate_resource_rules(items), [], items)
    if not filename.lower().endswith('.docx'):
        return findings
    from docx import Document
    paragraphs = [p.text for p in Document(BytesIO(content)).paragraphs]
    findings = _apply_document_coverage(findings, paragraphs, items)
    from app.modules.data_rules.project_scope import assess_project_applicability
    scope = assess_project_applicability({'_full_text': '\n'.join(paragraphs)}, 21)
    if not scope['applies'] and not scope.get('indeterminate'):
        return [replace(f, result_label='不适用', severity='不适用', reason='项目级别有明确依据，不属于第15、16条市级项目范围。' + scope['reason'], suggestion=None) for f in findings]
    # A local/off-cloud project can still receive a useful reference quantity
    # check. Do not infer "no cloud" merely from "not on the municipal cloud".
    off_cloud = [p for p in paragraphs if re.search(r'(?:本项目|本期).{0,30}(?:不上云|不使用云资源|全部本地部署)', p)
                 and not re.search(r'例如|示例|模板|引用|历史|上期|不再|并非|不是', p)]
    if off_cloud:
        findings = [replace(f, result_label='不适用', severity='不适用', reason='正文明确本项目不上云，第16条市级上云项目规则不适用。', evidence_examples=off_cloud, source_locations=[dict(quote=q, precision='text') for q in off_cloud], suggestion=None)
                    if f.rule_code in {RULE_16_OS_CODE, RULE_16_DB_CODE} else f for f in findings]
    elif scope.get('indeterminate') or scope.get('mode') == 'reference_check':
        findings = [replace(f, reason='项目级别或预算申报范围尚待确认，以下为现有材料的配置关系参考核验。' + f.reason,
                    severity='需人工确认' if f.result_label == '不一致' else f.severity,
                    result_label='无法判断' if f.result_label == '不一致' else f.result_label) for f in findings]
    exclusions = [p for p in paragraphs if re.search(r'(?:本项目|本期)[^。；\n]{0,60}(?:不涉及|不使用|无需申请)\s*PaaS', p, re.I)
                  and not any(t in p for t in ('例如', '示例', '模板', '参照', '引用'))]
    if not exclusions:
        return findings
    has_paas_items = any('paas' in (i.source or '').lower() and '正文' not in i.sheet_name for i in items)
    for index, f in enumerate(findings):
        if f.rule_code != RULE_15_CODE:
            continue
        # Keep the surviving security/crypto comparison, including a proven
        # mismatch. Exclusion of one list never suppresses the whole rule.
        findings[index] = replace(f,
            reason=('正文声明不涉及PaaS，但同时识别到PaaS数量清单，需核实阶段。' if has_paas_items else
                    '正文声明不涉及PaaS，仅排除该类清单；继续核验安全与密码服务之间的关联项。') + f.reason,
            evidence_examples=list(dict.fromkeys(exclusions + f.evidence_examples)),
            source_locations=f.source_locations + [dict(sheet_name='DOCX正文', quote=quote, precision='text') for quote in exclusions])
    return findings


def check_resource_document(content, filename, items=None):
    findings = _check_resource_document_without_locations(content, filename, items)
    if not filename.lower().endswith('.docx'):
        return findings
    from .quantity_parser import DocumentIndex, build_evidence_location
    try:
        index = DocumentIndex(content, filename)
    except Exception:
        return findings
    return [replace(f, evidence_location=build_evidence_location(
        index, f.evidence_examples, f.source_section, f.source_locations, f.suggestion or ''))
        for f in findings]
