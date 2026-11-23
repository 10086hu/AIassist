"""Evidence-based project classification, independent of rule-specific policy scope.

An owner's administrative affiliation, the project's budget level and the city
funding it are separate facts. No agency or example-report name is a shortcut.
"""
import re

OWNER = r'(?:项目建设单位|建设单位|申报单位|项目单位|购买主体|建设主体|建设市局|预算单位)'
PUBLIC_UNIT = r'(?:局|委员会|政府|服务中心|管理中心|发展中心|资源中心|办公室)$'


def classify_project(document, full_text=None):
    text = str(full_text if full_text is not None else document.get('_full_text', ''))
    lines = [re.sub(r'\s+', '', x) for x in text.splitlines() if x.strip()]
    evidence = []

    def add(kind, value, quote, line, strength='high'):
        item = dict(kind=kind, value=value, quote=quote, location=f'提取文本第{line}行', strength=strength)
        if not any((x['kind'], x['value'], x['quote']) == (kind, value, quote) for x in evidence):
            evidence.append(item)

    def city(value):
        m = re.match(r'(?:中华人民共和国)?([\u4e00-\u9fff]{2,10}?市)', value)
        return m.group(1) if m else None

    owners = []
    for i, line in enumerate(lines, 1):
        m = re.match(rf'^(?:[\d.．、]+)?{OWNER}[：:|](.{{2,100}})', line)
        if not m and re.fullmatch(rf'(?:[\d.．、]+)?{OWNER}', line) and i < len(lines):
            value = lines[i]
        elif m:
            value = m.group(1)
        else:
            continue
        value = re.split(r'[。；;|]|负责人[：:]', value)[0].strip()
        if not value or len(value) > 80:
            continue
        if value not in owners:
            owners.append(value)
        add('owner', value, line, i)
        region = city(value)
        if region:
            add('owner_region', region, line, i)
        if re.search(r'(?:区|县)(?:人民政府|[^，。]{0,18}(?:局|委员会|中心))$', value):
            add('owner_level', 'district', line, i)
        elif region and re.search(PUBLIC_UNIT, value) and not re.search(r'公司|集团|企业|区|县', value):
            add('owner_level', 'municipal', line, i, 'medium')

    for i, line in enumerate(lines, 1):
        # A paragraph may discuss last year's budget before stating this project's budget.
        for clause in re.split(r'[。；;]', line):
            if not clause:
                continue
            plain = re.sub(r'《[^》]*》', '', clause)
            if re.search(r'原批复|上期|上年度|去年|历史项目|例如|示例|模板|参考案例|政策规定|指引规定', plain):
                continue
            project = bool(re.search(r'本项目|本期|本次|该项目|项目资金|资金来源[：:]|项目级别[：:]|预算级次[：:]', plain))
            if project:
                level = re.search(r'(?:项目级别|预算级次)[：:](市级|市本级|区级|县级|省级|中央级)', plain)
                if not level:
                    level = re.search(r'(?:本项目|本期|该项目).{0,15}(?:属于|纳入|列入|为)(?:[^，]{0,12}?)(市级|市本级|区级|县级|省级|中央级)(?:项目|预算|数字化)', plain)
                if level and not re.search(r'不属于|不纳入|不列入|不是', plain[:level.end()]):
                    v = level.group(1)
                    add('project_level', 'municipal' if v in ('市级', '市本级') else 'district' if v in ('区级', '县级') else 'central' if v == '中央级' else 'provincial', clause, i)
                funding_statement = re.search(r'资金来源|资金筹措|(?:资金|费用|投资|经费|预算).{0,18}(?:申请|安排|来自|来源|拨款|承担|出资|筹集|自筹)|(?:财政|预算).{0,12}(?:拨款|安排|承担|出资|拨付)', plain)
                if funding_statement and not re.search(r'无需|不使用|不申请|未申请|不安排|不得|不涉及|不纳入|尚未确定|待确定|争取|可能', plain):
                    if re.search(r'(?:市(?:本级|级)?财政|市本级预算|市级预算)', plain):
                        add('funding_level', 'municipal', clause, i)
                        for m in re.finditer(r'([\u4e00-\u9fff]{2,10}?市)(?:本级|级)?财政', plain):
                            region = m.group(1)
                            # Strip preceding funding verbs; do not infer a city from just 市财政.
                            region = re.split(r'申请|来自|来源为|来源于|使用|全部为|由|及|和|与|采用', region)[-1]
                            if len(region) >= 3:
                                add('funding_region', region, clause, i)
                    if re.search(r'区(?:本级|级)?财政|县(?:本级|级)?财政|区级预算|县级预算', plain):
                        add('funding_level', 'district', clause, i)
                    if re.search(r'中央财政|中央预算', plain):
                        add('funding_level', 'central', clause, i)
                    if re.search(r'(?:全部|仅|完全).{0,8}(?:自筹|企业资金)|资金来源[：:]企业自筹', plain):
                        add('funding_level', 'private', clause, i)
                    if '国资经营预算' in plain or '国有资本经营预算' in plain:
                        add('public_funding', 'state_capital_budget', clause, i)
                if re.search(r'(?:纳入|列入|按照|按).{0,30}市(?:级|本级).{0,20}(?:预算申报|立项管理|项目管理)', plain) and not re.search(r'不纳入|不列入|参考|参照', plain):
                    add('project_level', 'municipal', clause, i)
            # The grammatical subject must be the owner (or an explicit owner role).
            subject = plain.startswith(('建设单位', '项目单位', '本单位', '申报单位')) or any(plain.startswith(x) or plain.startswith(x.removeprefix('中华人民共和国')) for x in owners)
            if subject and re.search(r'中央垂(?:直管理|管)|隶属中华人民共和国.{1,16}(?:总署|部|总局)', plain):
                add('owner_affiliation', 'central', clause, i)
            if re.search(r'^(?:本单位(?:属于|为|是)|申报单位[：:]|预算单位[：:])?上海市市级预算单位(?:项目)?$', plain):
                add('budget_unit', 'shanghai_municipal', clause, i)
                add('owner_region', '上海市', clause, i)
                add('owner_level', 'municipal', clause, i)

    def vals(kind):
        return set(x['value'] for x in evidence if x['kind'] == kind)

    explicit, funding, owner_levels = vals('project_level'), vals('funding_level'), vals('owner_level')
    conflicts = []
    if len(explicit) > 1:
        level = 'unknown'; conflicts.append('同一材料存在不同项目级别声明，需确认阶段或子项目范围。')
    elif explicit:
        level = next(iter(explicit))
    elif len(funding) > 1:
        level = 'mixed'
    elif owner_levels == {'district'}:
        # Transfers to a district do not by themselves change the budget owner.
        level = 'district'
    elif funding:
        level = next(iter(funding))
    elif vals('owner_affiliation'):
        # Administrative affiliation alone says nothing about the project funding.
        level = 'unknown'
    elif len(owner_levels) == 1:
        level = next(iter(owner_levels))
    else:
        level = 'unknown'
    if len(owner_levels) > 1 and not explicit and not funding:
        conflicts.append('建设或申报主体涉及不同级别，尚未明确预算申报主体。')
    regions = vals('funding_region') or vals('owner_region')
    region = next(iter(regions)) if len(regions) == 1 else 'unknown'
    if len(regions) > 1:
        conflicts.append('存在多个出资地区，需区分各地区预算范围。')
    return dict(level=level, region=region, owners=owners, owner_affiliation='central' if vals('owner_affiliation') else 'unknown',
                municipal_budget_unit='shanghai_municipal' in vals('budget_unit'),
                funding_levels=sorted(funding), evidence=evidence, conflicts=conflicts,
                confidence='high' if explicit or funding else 'medium' if owner_levels else 'low')


def assess_project_applicability(document, rule, full_text=None):
    facts = classify_project(document, full_text)
    level, region = facts['level'], facts['region']
    result = dict(applies=False, scope=level, confidence=facts['confidence'], project_classification=facts,
                  evidence=facts['evidence'], mode='undetermined')

    def done(applies, reason, mode, indeterminate=False):
        return {**result, 'applies': applies, 'reason': reason, 'mode': mode, 'indeterminate': indeterminate,
                'review_required': mode == 'reference_check'}

    if facts['conflicts']:
        return done(True, '项目范围证据冲突，执行参考核验，结果需按申报范围确认。', 'reference_check')
    if rule == 21:
        if level == 'municipal':
            return done(True, '建设/申报主体、项目级别或资金来源支持市级项目；按规则分工第21条“市级项目”标签执行，不额外限定上海。', 'applicable')
        if level == 'mixed' and 'municipal' in facts['funding_levels']:
            return done(True, '含市级财政资金的共同出资项目，执行指标参考核验并保留范围说明。', 'reference_check')
        if level in {'district', 'central', 'provincial', 'private'}:
            return done(False, '项目预算级别不属于本条市级项目范围。', 'not_applicable')
        return done(False, '未提取到可靠项目级别证据，不能按文件名或单位名称猜测。', 'undetermined', True)
    if region not in {'上海市', 'unknown'}:
        return done(False, '项目建设/申报主体或出资地区明确在上海以外；这不等于该项目不是当地市级项目。', 'not_applicable')
    if level in {'district', 'central', 'provincial', 'private'}:
        return done(False, '明确的项目预算级别不在上海市市级数字化项目范围。', 'not_applicable')
    if region == '上海市' and level in {'municipal', 'mixed'}:
        municipal_owner = any(e['kind'] == 'owner_level' and e['value'] == 'municipal' for e in facts['evidence'])
        if facts['owner_affiliation'] == 'central' or level == 'mixed' or not municipal_owner and not facts['municipal_budget_unit']:
            return done(True, '项目有上海市级资金依据，单位隶属或共同出资情况需与预算申报口径分开；执行第24条内容对标，PDF所列预算单位适用条件仍需核实，不能直接跳过或认定违规。', 'reference_check')
        return done(True, '项目的上海市级主体/预算证据支持执行第24条内容核验。', 'applicable')
    return done(False, '缺少项目资金、预算级别或地区证据，尚不能确认第24条适用范围。', 'undetermined', True)
