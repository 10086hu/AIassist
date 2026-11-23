from io import BytesIO
from pathlib import Path
import sys

import pytest
from docx import Document
from lxml import html

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.modules.shanghai_review.basis_document import BasisDocument, _investment, _indicators, _security, _modules, project_levels
from app.modules.shanghai_review.service import run_basis_check_from_document
from app.api.evaluate import _normalize_document_rule_result, _attach_finding_locations, _render_rule_evidence_preview


def raw(doc):
    buffer=BytesIO();doc.save(buffer);return buffer.getvalue()


def table(doc, headers, rows):
    t=doc.add_table(rows=1,cols=len(headers))
    for c,v in zip(t.rows[0].cells,headers):c.text=v
    for values in rows:
        for c,v in zip(t.add_row().cells,values):c.text=v
    return t


def test_toc_autonumber_children_and_tables_stay_in_section():
    d=Document()
    p=d.add_paragraph('4.7 安全需求分析\t11');p.style=d.styles.add_style('TOC 1',1)
    d.add_heading('安全需求分析',2)
    d.add_heading('系统安全需求',3)
    d.add_paragraph('本项目初步定级为等保三级。')
    table(d,['需求','值'],[['备份','RTO≤4h']])
    d.add_heading('下一节',2)
    d.add_paragraph('不属于安全需求')
    doc=BasisDocument(raw(d),'report.docx')
    blocks=doc.select('^安全需求分析$')
    assert any('等保三级' in b['quote'] for b in blocks)
    assert any(b.get('table_index') for b in blocks)
    assert not any('不属于安全需求' in b['quote'] or '\t11' in b['quote'] for b in blocks)


def test_investment_uses_actual_money_and_real_budget_title():
    d=Document();d.add_paragraph('报告编制年份2025，标准GBT19488.2-2008。')
    d.add_heading('2.7 投资概况',2);d.add_paragraph('项目总投资5010.4038万元。')
    d.add_heading('7.1 预算编制说明',2);d.add_paragraph('项目总投资：5010.4038（万元）。')
    d.add_heading('8.2 项目招标方案',2);d.add_paragraph('招标依据2025年规定。')
    issues,metrics=_investment(BasisDocument(raw(d),'r.docx'))
    assert metrics['total_amounts']==[5010.4038,5010.4038]
    assert all('金额不一致' not in i['message'] for i in issues)
    assert all(i['manual_review'] for i in issues)


def test_total_conflict_and_component_conflict_keep_both_sources():
    d=Document();d.add_heading('投资概况',2)
    d.add_paragraph('项目总投资120万元，其中软件开发费用100万元，工程建设其他费用20万元。')
    d.add_heading('项目总投资估算',2)
    table(d,['费用名称','金额（万元）'],[['软件开发费用','104'],['工程其他费用','16'],['总投资','121']])
    issues,_=_investment(BasisDocument(raw(d),'r.docx'))
    actual=[i for i in issues if not i['manual_review']]
    assert len(actual)==3
    assert all(any(l.get('table_index') for l in i['source_locations']) for i in actual)
    assert all(any(l.get('paragraph') for l in i['source_locations']) for i in actual)


def test_currency_conversion_and_display_rounding():
    d=Document();d.add_heading('投资概况',2);d.add_paragraph('项目总投资1180.06万元。')
    d.add_heading('项目预算',1)
    table(d,['费用名称','金额（万元）'],[['一类费用','1064'],['二类费用','116.06'],['总计','1180.1']])
    d.add_paragraph('项目总投资11800600元。')
    issues,metrics=_investment(BasisDocument(raw(d),'r.docx'))
    assert len(metrics['total_amounts'])==3
    assert not any('总投资金额不一致' in i['message'] for i in issues)


def test_phase_totals_do_not_become_whole_project_totals():
    d=Document();d.add_heading('投资概况',2)
    d.add_paragraph('一期项目总投资50万元。')
    d.add_paragraph('二期项目总投资70万元。')
    issues,metrics=_investment(BasisDocument(raw(d),'r.docx'))
    assert metrics['total_amounts']==[]
    assert issues[0]['manual_review']


def test_phase_table_does_not_conflict_with_project_total():
    d=Document();d.add_heading('投资概况',2);d.add_paragraph('项目总投资120万元。')
    d.add_heading('附件一期投资估算',2)
    table(d,['费用名称','金额（万元）'],[['软件开发费用','50'],['工程其他费用','10'],['总投资','60']])
    issues,metrics=_investment(BasisDocument(raw(d),'r.docx'))
    assert metrics['total_amounts']==[120]
    assert not any('金额不一致' in i['message'] for i in issues)


def test_unit_only_summary_table_has_actual_total_and_components():
    d=Document();d.add_heading('投资概况',2);d.add_paragraph('项目总投资120万元，其中建设费用100万元，其他费用20万元。')
    d.add_heading('项目预算',1)
    t=table(d,['投资估算总表（单位：万元）','',''],[['一、系统建设费','104',''],['二、其他费用','16',''],['总计','120','']])
    t.cell(0,0).merge(t.cell(0,2))
    issues,metrics=_investment(BasisDocument(raw(d),'r.docx'))
    assert metrics['total_amounts']==[120,120]
    assert any('建设费用' in i['message'] and '104' in i['message'] for i in issues)


def test_missing_fee_standard_cannot_generate_invented_amounts(monkeypatch):
    from app.modules.price import standards
    def unavailable(*args,**kwargs):raise FileNotFoundError('standard')
    monkeypatch.setattr(standards,'evaluate_class2_fees',unavailable)
    d=Document();d.add_heading('投资概况',2)
    d.add_paragraph('项目总投资120万元，其中建设费用100万元。')
    d.add_heading('项目预算',1)
    table(d,['费用名称','金额（万元）'],[['一类费用','100'],['咨询费','3'],['二类费用','20'],['总投资','120']])
    issues,metrics=_investment(BasisDocument(raw(d),'上海市建设项目.docx'))
    assert metrics['total_consistency_status']=='通过'
    fee_issue=next(i for i in issues if '标准数据不可用' in i['message'])
    assert fee_issue['manual_review']
    assert all('按标准应计' not in i['message'] and '超标' not in i['message'] for i in issues)


def test_configured_fee_upper_limit_is_not_changed(monkeypatch):
    from app.modules.price import standards
    from app.modules.price.standards import FeeEvaluation
    captured=[]
    def evaluate(items,context,level):
        captured.append((items,context,level))
        return [FeeEvaluation('consulting','咨询费',30000,40000,'pass','低于上限','')],{}
    monkeypatch.setattr(standards,'evaluate_class2_fees',evaluate)
    d=Document();d.add_heading('投资概况',2)
    d.add_paragraph('项目总投资120万元，其中建设费用100万元。')
    d.add_heading('项目预算',1)
    table(d,['费用名称','金额（万元）'],[['一类费用','100'],['咨询费','3'],['二类费用','20'],['总投资','120']])
    issues,_=_investment(BasisDocument(raw(d),'上海市建设项目.docx'))
    assert not issues
    assert captured[0][0][0].total_price==30000
    assert captured[0][1].direct_construction_cost==1000000
    assert captured[0][2]=='市级'


def test_merged_phase_total_counted_once_and_real_component_mismatch_found():
    d=Document();d.add_heading('项目总投资估算',2)
    t=table(d,['分项目','建设内容','单价（万元）','总价（万元）'],[
        ['一期','软件内容','426','481.746'],['','硬件内容','55.746',''],
        ['二期','软件内容','150','195.74'],['','硬件内容','34.74',''],['','等保测评','8','']])
    t.cell(1,0).merge(t.cell(2,0));t.cell(1,3).merge(t.cell(2,3))
    t.cell(3,0).merge(t.cell(5,0));t.cell(3,3).merge(t.cell(5,3))
    issues,metrics=_investment(BasisDocument(raw(d),'r.docx'))
    assert metrics['derived_phase_totals']==[677.486]
    conflict=next(i for i in issues if '二期投资总价' in i['message'])
    assert '192.74' in conflict['message'] and '差额3' in conflict['message']
    assert len(conflict['source_locations'])==3
    assert not conflict['manual_review']


def metric_doc(left='100%',right='90%'):
    d=Document();d.add_heading('2.6 规划指标',2)
    table(d,['指标名称','目标值'],[['软件开发完成率',left]])
    d.add_heading('4.2 建设目标需求分析',2)
    d.add_heading('4.2.2 验收标准、具体指标和内容',3)
    table(d,['指标名称','目标值'],[['软件开发完成率',right]])
    return BasisDocument(raw(d),'r.docx')


def test_indicator_compares_values_not_similar_words():
    issues,metrics=_indicators(metric_doc())
    assert metrics['matched_count']==1
    assert any('100%' in i['message'] and '90%' in i['message'] for i in issues)
    assert len(issues[0]['source_locations'])==2
    assert _indicators(metric_doc(right='100%'))[0]==[]


def test_missing_sections_never_compare_full_document_with_itself():
    d=Document();d.add_paragraph('本项目总投资120万元，等保三级。')
    result=run_basis_check_from_document(raw(d),'r.docx')
    assert all(not r['passed'] and r['status']=='需人工复核' for r in result['results'])


@pytest.mark.parametrize('text,expected',[
    ('本项目网络安全等级保护定级为三级。',3),
    ('本系统的安全保护等级为第三级即监督保护级。',3),
    ('安全防护能力不低于网络安全等级保护制度二级要求。',2),
    ('本系统要达到等保2.0三级要求。',3),
    ('本项目按三级等保要求建设。',3),
    ('满足第三级信息系统商用密码技术实施要求。',None),
    ('采用《三级等保建设标准》开展设计。',None),
])
def test_level_expressions_do_not_confuse_crypto_or_standard_titles(text,expected):
    levels=project_levels([{'quote':text}])
    assert ({v for v,_ in levels} if levels else set())==({expected} if expected else set())


def test_security_child_level_and_backup_in_adjacent_chapter():
    d=Document();d.add_heading('4.7 安全需求分析',2)
    d.add_heading('4.7.3 系统安全需求',3);d.add_paragraph('本项目初步定级为等保三级，需备份和容灾。')
    d.add_heading('6.6 安全建设内容',2);d.add_paragraph('本项目按三级等保要求建设。')
    d.add_heading('6.9 PaaS安全服务',2)
    d.add_heading('6.9.2 数据备份与恢复',3);d.add_paragraph('提供备份和容灾措施。')
    issues,metrics=_security(BasisDocument(raw(d),'r.docx'))
    assert metrics['needs_levels']==metrics['construction_levels']==[3]
    assert not issues


def module_doc(reference='6.1.1.1',name='用户管理'):
    d=Document();d.add_heading('6.1 应用系统建设',2)
    d.add_heading('6.1.1 业务子系统',3)
    d.add_heading('6.1.1.1 用户管理',4)
    d.add_heading('7.2 应用软件开发投资估算',2)
    table(d,['功能名称','总金额（万元）','对应建设内容中的功能章节'],[[name,'10',reference]])
    return BasisDocument(raw(d),'r.docx')


def test_function_reference_matches_exact_heading_and_name():
    issues,metrics=_modules(module_doc())
    assert metrics['matched_items']==1 and not issues
    issues,metrics=_modules(module_doc('6.1.1.10'))
    assert metrics['unresolved_items']==1
    assert any('6.1.1.10' in i['evidence'] for i in issues)
    issues,metrics=_modules(module_doc(name='虚构功能'))
    assert metrics['unresolved_items']==1


def test_numbered_parent_titles_and_tender_are_not_functions():
    d=Document();d.add_heading('6.1 应用系统建设',2)
    d.add_heading('8.2 项目招标方案',2)
    issues,metrics=_modules(BasisDocument(raw(d),'r.docx'))
    assert metrics['budget_items']==0 and metrics['construction_headings']==0
    assert issues[0]['manual_review']


def test_merged_table_and_repeated_text_addresses_survive_normalization():
    d=Document();d.add_paragraph('前文标准GBT19488.2-2008')
    d.add_heading('投资概况',2);d.add_paragraph('项目总投资100万元。')
    table(d,['内容','数量'],[['前置表格','1']])
    d.add_heading('项目预算',1)
    table(d,['费用名称','金额（万元）'],[['软件开发费用','100'],['工程其他费用','20'],['总投资','120']])
    content=raw(d)
    result=_attach_finding_locations(_normalize_document_rule_result(run_basis_check_from_document(content,'r.docx'),'basis'),content,'r.docx')
    dom=html.fromstring(_render_rule_evidence_preview(content,'r.docx'))
    f=next(f for f in result['findings'] if '总投资金额不一致' in f['review_opinion'])
    assert f['source_location']['verified_basis']
    for loc in f['source_locations']:
        if loc.get('paragraph'):
            nodes=dom.xpath('//*[@data-e-kind="paragraph" and @data-e-paragraph="%s"]'%loc['paragraph'])
        else:
            nodes=dom.xpath('//*[@data-e-kind="row" and @data-e-table="%s" and @data-e-row="%s"]'%(loc['table_index'],loc['row_index']))
        assert len(nodes)==1 and nodes[0].get('data-e-text')==loc['quote']
        assert '19488.2' not in loc['quote']


def test_txt_keeps_original_line_positions():
    result=run_basis_check_from_document('年份2025\n\n2.7 投资概况\n项目总投资100万元。\n7.1 预算编制说明\n项目总投资120万元。'.encode(),'r.txt')
    investment=result['results'][0]
    issue=next(i for i in investment['issues'] if '总投资金额不一致' in i['message'])
    assert [l['line_start'] for l in issue['source_locations']]==[4,6]
    assert issue['source_location']['file_type']=='txt'
