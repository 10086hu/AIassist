from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from app.modules.resource.maintenance_rules import (
    MAINTENANCE_RULES,
    RULE_BY_ID,
    MaintenanceDocument,
    MaintenanceRule,
    evaluate_maintenance_rules as _legacy_evaluate_maintenance_rules,
    is_maintenance_project_document,
    maintenance_rules_to_public_dicts,
    parse_maintenance_document,
)
from app.modules.resource.rules import ResourceRuleFinding


SOFTWARE_EXCLUSION_TERMS = (
    "页面设计",
    "系统框架搭建",
    "框架搭建",
    "数据对接迁移",
    "数据对接",
    "数据迁移",
    "实施联调测试",
    "联调测试",
    "接口研发",
    "接口运维",
    "国标平台对接",
    "国标设备接入",
)
SOFTWARE_CONDITIONAL_EXCLUSION_TERMS = ("接口", "对接", "接入")
HARDWARE_EXCLUSION_TERMS = (
    "机柜",
    "综合布线",
    "布线",
    "管道",
    "立杆",
    "支架",
    "集成费",
    "办公电脑",
    "PC机",
    "打印机",
    "辅材",
    "门禁卡",
    "卡片",
    "账号租赁",
    "知网",
    "数据服务",
    "跳线",
    "网线",
    "电缆",
)
NEW_ITEM_TERMS = ("新增", "新建", "新购", "新增项", "新增运维项", "新增功能", "功能迭代")
BUSINESS_PC_TERMS = ("业务电脑", "业务终端")
CHINESE_NAME_CONTEXT_PATTERN = re.compile(
    r"(?:负责人|联系人|项目经理|工程师|运维人员|维护人员|姓名)[：:|\s]*([\u4e00-\u9fa5]{2,4})"
)

MONEY_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
MONEY_PATTERN = re.compile(rf"(?P<number>{MONEY_NUMBER})\s*(?P<unit>万元|万|元)?")


@dataclass(frozen=True)
class MaintenanceLineItem:
    row_index: int
    section: str
    name: str
    spec: str
    purchase_amount: float | None
    declared_amount: float | None
    approved_amount: float | None
    raw_text: str


@dataclass(frozen=True)
class MaintenanceMaterialProfile:
    has_plan: bool
    has_self_check: bool
    has_contract: bool
    has_ledger_or_ticket: bool
    has_kajia_table: bool
    has_system_export: bool
    kajia_item_count: int


def evaluate_maintenance_rules(
    content: bytes,
    filename: str,
    selected_rule_ids: Iterable[str] | None = None,
    db: Session | None = None,
) -> list[ResourceRuleFinding]:
    """Evaluate Songjiang maintenance rules through the isolated module.

    The legacy 20-rule engine remains the compatibility baseline. This module
    replaces only mature, deterministic rules whose requirements are explicit
    in the old 20-rule workbook and the newer operation-flow workbook.
    """

    selected = {str(item).strip() for item in selected_rule_ids or [] if str(item).strip()}
    legacy_findings = _legacy_evaluate_maintenance_rules(content, filename, selected_rule_ids=selected, db=db)
    document = parse_maintenance_document(content, filename)
    profile = _profile_material(document)
    replacements: dict[str, ResourceRuleFinding] = {}

    enhanced_rules = {
        "MAINT_OPS_001": lambda doc, rule: _evaluate_total_amount_consistency(doc, rule, profile),
        "MAINT_OPS_002": lambda doc, rule: _evaluate_itemized_quote(doc, rule, profile),
        "MAINT_OPS_003": lambda doc, rule: _evaluate_plan_structure(doc, rule, profile),
        "MAINT_OPS_004": _evaluate_sensitive_terms,
        "MAINT_OPS_010": _evaluate_software_scope_exclusions,
        "MAINT_OPS_011": _evaluate_hardware_scope_exclusions,
        "MAINT_OPS_012": lambda doc, rule: _evaluate_xueliang_outsourcing_fee(doc, rule, profile),
        "MAINT_OPS_013": lambda doc, rule: _evaluate_finance_bureau_outsourcing_fee(doc, rule, profile),
        "MAINT_OPS_014": lambda doc, rule: _evaluate_kajia_rate_rule(doc, rule, "application_software", profile),
        "MAINT_OPS_015": lambda doc, rule: _evaluate_kajia_rate_rule(doc, rule, "product_software", profile),
        "MAINT_OPS_016": lambda doc, rule: _evaluate_kajia_rate_rule(doc, rule, "hardware", profile),
        "MAINT_OPS_017": lambda doc, rule: _evaluate_security_fee_rule(doc, rule, profile, "三级", 4.0, 9.0),
        "MAINT_OPS_018": lambda doc, rule: _evaluate_security_fee_rule(doc, rule, profile, "二级", 3.0, 6.0),
        "MAINT_OPS_019": lambda doc, rule: _evaluate_history_price_with_new_item_exception(doc, rule, profile),
        "MAINT_OPS_020": lambda doc, rule: _evaluate_new_product_reference_guard(doc, rule, profile),
    }
    for rule_id, evaluator in enhanced_rules.items():
        if not _rule_selected(rule_id, selected):
            continue
        enhanced = evaluator(document, RULE_BY_ID[rule_id])
        if enhanced is not None:
            replacements[rule_id] = enhanced

    if not replacements:
        return legacy_findings
    output: list[ResourceRuleFinding] = []
    for finding in legacy_findings:
        output.append(replacements.pop(finding.rule_code, finding))
    output.extend(replacements.values())
    return output


def _evaluate_total_amount_consistency(
    doc: MaintenanceDocument,
    rule: MaintenanceRule,
    profile: MaintenanceMaterialProfile,
) -> ResourceRuleFinding:
    source_keywords = {
        "运维方案申报金额": ("运维方案", "方案", "申报金额", "申报总额", "总投资", "预算"),
        "自查表申报金额": ("自查表", "申报金额", "申报总额", "预算", "批复金额"),
        "系统填报申报金额": ("系统填报", "系统填写", "系统导出", "申报系统", "填报金额", "申报金额"),
    }
    values: dict[str, float] = {}
    evidence: list[str] = []
    for source_name, keywords in source_keywords.items():
        for line in doc.lines:
            if _line_matches_amount_source(line, keywords):
                amount = _largest_amount(line)
                if amount is not None:
                    values[source_name] = amount
                    evidence.append(_trim(line))
                    break

    source_count = sum((profile.has_plan, profile.has_self_check, profile.has_system_export))
    if len(values) < 2 or source_count < 2:
        return _manual(
            rule,
            "申报总额",
            "未同时识别到运维方案、自查表及系统填报中的至少两个申报金额来源，无法完成多来源一致性校验。",
            "请提供运维方案、自查表、系统导出/系统填报截图中的申报金额；缺少的来源应标注为资料不足后人工确认。",
            doc,
            evidence or _evidence(doc, ("申报金额", "申报总额", "预算", "金额", "系统填报", "自查表", "方案")),
            values,
        )

    distinct = {round(value, 4) for value in values.values()}
    if len(distinct) == 1:
        return _pass(rule, "申报总额", f"已识别到 {len(values)} 个来源的申报金额，金额一致。", values, evidence)
    return _finding(
        rule,
        "申报总额",
        "高",
        "金额不一致",
        f"识别到不同来源申报金额不一致：{_format_quantities(values)}。",
        "请核对运维方案、自查表和系统填报金额，统一申报总额后重新提交。",
        values,
        evidence,
    )


def _evaluate_itemized_quote(
    doc: MaintenanceDocument,
    rule: MaintenanceRule,
    profile: MaintenanceMaterialProfile,
) -> ResourceRuleFinding:
    items = _parse_kajia_items(doc)
    detail_rows = [
        _trim(" | ".join(row), 320)
        for row in doc.table_rows
        if _contains_any(" ".join(row), ("硬件", "软件", "模块", "明细", "服务项", "申报项", "报价", "金额", "运维"))
        and _largest_amount(" ".join(row)) is not None
    ]
    if items or len(detail_rows) >= 2:
        detail_count = max(len(items), len(detail_rows))
        return _pass(
            rule,
            "申报内容明细报价",
            f"已识别到 {detail_count} 条硬件、软件、服务项或模块级明细报价。",
            {"明细报价条数": float(detail_count)},
            [item.raw_text for item in items[:5]] or detail_rows[:5],
        )

    total_amounts = _amounts_near_keywords(doc.lines, ("申报金额", "申报总额", "合计", "总计", "报价", "预算"))
    if total_amounts and (profile.has_plan or profile.has_system_export or profile.has_kajia_table):
        return _finding(
            rule,
            "申报内容明细报价",
            "中",
            "疑似未拆分报价",
            "识别到项目总金额，但未识别到硬件、软件模块或服务项层面的明细报价。",
            "请将申报内容拆分至硬件、应用软件模块、产品软件或服务项，并分别列示数量、单价和金额。",
            {"总额记录数": float(len(total_amounts))},
            _evidence(doc, ("申报金额", "合计", "总计", "报价", "预算")),
        )
    return _manual(
        rule,
        "申报内容明细报价",
        "未识别到运维方案、系统导出或核价清单中的申报金额/明细报价，无法判断是否已拆分细项报价。",
        "请提供包含申报金额和分项报价的运维方案、系统导出表或核价清单。",
        doc,
        _evidence(doc, ("申报金额", "明细", "报价", "系统导出", "核价清单", "运维方案")),
    )


def _evaluate_plan_structure(
    doc: MaintenanceDocument,
    rule: MaintenanceRule,
    profile: MaintenanceMaterialProfile,
) -> ResourceRuleFinding:
    required_groups = {
        "项目概述": ("项目概述", "项目背景", "项目基本情况"),
        "需求分析": ("需求分析", "运维需求", "服务需求"),
        "现状分析": ("现状分析", "现状情况", "系统现状"),
        "项目运维方案": ("项目运维方案", "运维方案", "运维内容", "运维服务内容", "服务内容"),
        "项目实施进度": ("项目实施进度", "实施进度", "服务周期", "计划安排"),
        "人员安排": ("人员安排", "人员配置", "服务人员", "运维人员"),
        "投资估算": ("投资估算", "投资概算", "预算", "报价", "费用估算"),
    }
    evidence: list[str] = []
    found: list[str] = []
    missing: list[str] = []
    for group_name, aliases in required_groups.items():
        group_evidence = _evidence(doc, aliases, limit=2)
        if group_evidence or _contains_any(doc.raw_text, aliases):
            found.append(group_name)
            evidence.extend(group_evidence[:1])
        else:
            missing.append(group_name)

    if not profile.has_plan:
        return _manual(
            rule,
            "运维方案章节架构",
            "未识别到运维方案材料，无法核查方案章节完整性。",
            "请上传运维方案；若当前材料不是方案，本规则应作为资料不足交由人工确认。",
            doc,
            evidence or _evidence(doc, ("方案", "运维内容", "服务周期", "预算")),
            {"识别章节数": float(len(found))},
        )
    if len(found) >= 5 and "项目运维方案" in found and "投资估算" in found:
        return _pass(
            rule,
            "运维方案章节架构",
            f"已识别到 {len(found)} 类方案关键章节，覆盖运维内容和费用估算。",
            {"识别章节数": float(len(found)), "缺失章节数": float(len(missing))},
            evidence,
        )
    return _finding(
        rule,
        "运维方案章节架构",
        "中",
        "章节不完整",
        f"未充分识别到以下方案关键章节：{'、'.join(missing)}。",
        "请补充项目概述、需求/现状分析、运维服务内容、实施进度、人员安排及费用估算，保持运维方案框架完整。",
        {"识别章节数": float(len(found)), "缺失章节数": float(len(missing))},
        evidence,
    )


def _evaluate_sensitive_terms(doc: MaintenanceDocument, rule: MaintenanceRule) -> ResourceRuleFinding:
    vendor_terms = (
        "指定品牌",
        "指定厂商",
        "唯一供应商",
        "原厂唯一",
        "不得替代",
        "仅支持",
        "必须采用",
        "内部口径",
        "特殊处理",
    )
    brand_terms = (
        "华为",
        "H3C",
        "新华三",
        "海康",
        "大华",
        "浪潮",
        "联想",
        "Oracle",
        "Microsoft",
        "Windows Server",
        "SQL Server",
        "VMware",
    )
    hits: list[str] = []
    for line in doc.lines:
        if _contains_any(line, vendor_terms) or _contains_any(line, brand_terms):
            hits.append(_trim(line))
            continue
        if CHINESE_NAME_CONTEXT_PATTERN.search(line):
            hits.append(_trim(line))
    hits = list(dict.fromkeys(hits))[:8]
    if not hits:
        return _pass(rule, "运维敏感词", "未识别到厂商名称、指定性表述或疑似运维人员姓名。", {}, [])
    return _finding(
        rule,
        "运维敏感词",
        "需人工确认",
        "命中敏感表达",
        f"识别到 {len(hits)} 处厂商名称、指定性表述或疑似运维人员姓名，需要结合上下文复核。",
        "请确认是否属于现状描述、合同事实或必要引用；如为拟采购/拟运维要求，应改为客观技术指标并删除个人姓名。",
        {"命中条数": float(len(hits))},
        hits,
    )


def _evaluate_software_scope_exclusions(doc: MaintenanceDocument, rule: MaintenanceRule) -> ResourceRuleFinding | None:
    hits = _find_exclusion_hits(
        doc,
        exact_terms=SOFTWARE_EXCLUSION_TERMS,
        conditional_terms=SOFTWARE_CONDITIONAL_EXCLUSION_TERMS,
        skip_when_new=True,
    )
    if not hits:
        return _pass(rule, "应用/产品软件运维申报范围", "未识别到规则列明的应用软件或产品软件运维负面清单项。", {}, [])
    return _finding(
        rule,
        "应用/产品软件运维申报范围",
        "高",
        "应核定为0",
        "识别到不属于应用软件及产品软件运维申报范围的内容：" + "；".join(hit["term"] for hit in hits[:6]) + "。",
        "请将页面设计、框架搭建、数据对接迁移、实施联调测试及非新增接口运维等申报项核定为0；如属于新增功能开发，请补充新增依据。",
        {"命中申报项数": float(len(hits))},
        [hit["line"] for hit in hits],
    )


def _evaluate_hardware_scope_exclusions(doc: MaintenanceDocument, rule: MaintenanceRule) -> ResourceRuleFinding | None:
    hits = _find_exclusion_hits(
        doc,
        exact_terms=HARDWARE_EXCLUSION_TERMS,
        conditional_terms=(),
        skip_when_new=False,
        skip_terms=BUSINESS_PC_TERMS,
    )
    if not hits:
        return _pass(rule, "硬件运维申报范围", "未识别到规则列明的硬件运维负面清单项。", {}, [])
    return _finding(
        rule,
        "硬件运维申报范围",
        "高",
        "应核定为0",
        "识别到不属于硬件运维预算范畴或需核减的内容：" + "；".join(hit["term"] for hit in hits[:6]) + "。",
        "请核减机柜、综合布线/管道、立杆支架、集成费、办公电脑、打印机、辅材、账号租赁和数据服务等事项；业务电脑或新增硬件采购需补充说明后人工复核。",
        {"命中申报项数": float(len(hits))},
        [hit["line"] for hit in hits],
    )


def _evaluate_xueliang_outsourcing_fee(
    doc: MaintenanceDocument,
    rule: MaintenanceRule,
    profile: MaintenanceMaterialProfile,
) -> ResourceRuleFinding:
    if "雪亮工程" not in doc.raw_text:
        return _pass(rule, "雪亮工程外包服务费", "未识别到雪亮工程外包服务费场景。", {}, [])
    evidence = _evidence(doc, ("雪亮工程", "外包服务费", "人员费", "19万", "19 万"))
    amounts = _amounts_near_keywords(doc.lines, ("雪亮工程", "外包服务费", "人员费"))
    if any(abs(amount - 19.0) <= 0.05 for amount in amounts):
        return _pass(rule, "雪亮工程外包服务费", "识别到雪亮工程外包服务费金额为 19 万元。", {"识别金额": 19.0}, evidence)
    if amounts:
        return _finding(
            rule,
            "雪亮工程外包服务费",
            "高",
            "核价不一致",
            f"雪亮工程外包服务费应统一核定为 19 万元，识别金额为：{', '.join(f'{item:g}' for item in amounts[:5])} 万元。",
            "请按规则将雪亮工程外包服务费核定为 19 万元，或补充特殊审批依据。",
            {"规则金额": 19.0, "识别金额": amounts[0]},
            evidence,
        )
    if not profile.has_kajia_table:
        return _manual(
            rule,
            "雪亮工程外包服务费",
            "识别到雪亮工程场景，但未识别到核价清单或外包服务费金额，无法确认是否按 19 万元核定。",
            "请提供核价清单或外包服务费明细；资料缺失时标注为需人工确认。",
            doc,
            evidence,
        )
    return _manual(rule, "雪亮工程外包服务费", "识别到雪亮工程场景，但未提取到外包服务费金额。", "请补充外包服务费金额并核对是否为 19 万元。", doc, evidence)


def _evaluate_finance_bureau_outsourcing_fee(
    doc: MaintenanceDocument,
    rule: MaintenanceRule,
    profile: MaintenanceMaterialProfile,
) -> ResourceRuleFinding:
    if not _contains_any(doc.raw_text, ("财政局", "财政")):
        return _pass(rule, "财政局外包服务费", "未识别到财政局外包服务费场景。", {}, [])
    evidence = _evidence(doc, ("财政局", "财政", "外包服务费", "人员费", "申报金额", "核定价格"))
    if not _contains_any(doc.raw_text, ("外包服务费", "人员费")):
        return _manual(rule, "财政局外包服务费", "识别到财政相关项目，但未识别到外包服务费或人员费申报项。", "如存在外包服务费，请明确申报金额；如不存在，可忽略本规则。", doc, evidence)
    amounts = _amounts_near_keywords(doc.lines, ("财政局", "财政", "外包服务费", "人员费", "申报金额"))
    if amounts:
        return _pass(rule, "财政局外包服务费", "识别到财政局外包服务费申报金额，规则口径为以申报金额为准。", {"申报金额": amounts[0]}, evidence)
    if not profile.has_kajia_table:
        return _manual(
            rule,
            "财政局外包服务费",
            "识别到财政局外包服务费场景，但未识别到核价清单或申报金额。",
            "请提供核价清单或外包服务费申报明细；缺少材料时标注资料不足。",
            doc,
            evidence,
        )
    return _manual(rule, "财政局外包服务费", "识别到财政局外包服务费，但未提取到申报金额。", "请补充财政局外包服务费申报金额。", doc, evidence)


def _evaluate_kajia_rate_rule(
    doc: MaintenanceDocument,
    rule: MaintenanceRule,
    category: str,
    profile: MaintenanceMaterialProfile,
) -> ResourceRuleFinding | None:
    parsed_items = _parse_kajia_items(doc)
    items = [item for item in parsed_items if _item_matches_category(item, category)]
    if not items:
        if profile.has_kajia_table and profile.kajia_item_count == 0:
            return _manual(
                rule,
                _category_label(category),
                "识别到疑似核价清单表头，但未提取到可核价明细行，不能执行结构化费率核价。",
                "请检查核价清单是否包含项目/产品名称、购置金额、申报运维金额或申报金额等列，并保留为可解析表格。",
                doc,
                _evidence(doc, ("核价清单", "购置金额", "申报金额", "运维金额", "去年核定", "应用软件", "产品软件", "硬件")),
                {"核价清单明细项数": float(profile.kajia_item_count)},
            )
        if not profile.has_kajia_table:
            if category != "hardware":
                return _manual(
                    rule,
                    _category_label(category),
                    "未识别到核价清单，不能执行应用软件/产品软件运维的结构化费率核价。",
                    "请提供含购置金额、申报运维金额、核定运维金额等列的核价清单；缺失时仅标注资料不足，不强行测算。",
                    doc,
                    _evidence(doc, ("核价清单", "购置金额", "申报金额", "运维金额", "去年核定", "应用软件", "产品软件")),
                    {"核价清单明细项数": float(profile.kajia_item_count)},
                )
            return _pass(
                rule,
                _category_label(category),
                "未识别到可执行费率核价的明细清单，本规则不因正文或非核价表中出现硬件/软件词汇而触发资料不足。",
                {"核价清单明细项数": float(profile.kajia_item_count)},
                [],
            )
        return _pass(rule, _category_label(category), "核价清单中未识别到该类运维明细场景。", {"核价清单明细项数": float(profile.kajia_item_count)}, [])

    compare_rate = 0.06 if category == "application_software" else 0.04
    approved_rate = 0.04
    issues: list[str] = []
    evidence: list[str] = []
    checked = 0
    over_10_percent = 0
    over_rule_amount = 0
    approved_above_rule = 0

    for item in items:
        if item.purchase_amount is None or item.declared_amount is None or item.purchase_amount <= 0:
            continue
        checked += 1
        declared_rate = item.declared_amount / item.purchase_amount
        threshold = item.purchase_amount * compare_rate
        expected = item.declared_amount if threshold >= item.declared_amount else item.purchase_amount * approved_rate
        if declared_rate > 0.10 + 1e-9:
            over_10_percent += 1
            issues.append(f"{item.name}申报费率{declared_rate:.1%}，超过10%上限")
            evidence.append(item.raw_text)
        if item.declared_amount > expected + max(1.0, expected * 0.005):
            over_rule_amount += 1
            issues.append(f"{item.name}申报运维金额{item.declared_amount:g}元，高于规则测算{expected:g}元")
            evidence.append(item.raw_text)
        if item.approved_amount is not None and item.approved_amount > expected + max(1.0, expected * 0.005):
            approved_above_rule += 1
            issues.append(f"{item.name}核定运维金额{item.approved_amount:g}元，高于规则测算{expected:g}元")
            evidence.append(item.raw_text)

    if checked == 0:
        return _manual(
            rule,
            _category_label(category),
            "识别到该类运维明细，但未同时提取到购置金额和申报运维金额，无法按费率自动核价。",
            "请补齐或规范购置金额、申报运维金额/申报金额列；如该表为租赁、云资源或非硬件运维费，应从本规则适用范围中排除。",
            doc,
            [item.raw_text for item in items[:6]],
            {"明细项数": float(len(items)), "可核价项数": 0.0},
        )
    quantities = {
        "明细项数": float(len(items)),
        "可核价项数": float(checked),
        "申报费率超过10%项数": float(over_10_percent),
        "申报金额高于规则测算项数": float(over_rule_amount),
        "核定金额高于规则测算项数": float(approved_above_rule),
    }
    if issues:
        return _finding(
            rule,
            _category_label(category),
            "中",
            "核价疑似偏高",
            "；".join(dict.fromkeys(issues[:8])),
            "请按5万元阈值、三年以上历史项目、6%/4%费率、10%申报费率上限以及“申报/核定/历史取低”口径复核。",
            quantities,
            list(dict.fromkeys(evidence))[:8],
        )
    return _pass(
        rule,
        _category_label(category),
        f"已按核价清单识别并复核{checked}项，未发现申报费率超过10%或高于规则测算金额的情况。",
        quantities,
        [item.raw_text for item in items[:6]],
    )


def _evaluate_security_fee_rule(
    doc: MaintenanceDocument,
    rule: MaintenanceRule,
    profile: MaintenanceMaterialProfile,
    level: str,
    low_fee: float,
    high_fee: float,
) -> ResourceRuleFinding:
    text = doc.raw_text
    level_keywords = (f"等保{level}", f"{level}等保", f"{level}等级保护", f"等级保护{level}")
    evidence = _evidence(doc, level_keywords + ("密测费", "密码测评", "密码应用安全性评估", "安全测试费", "安全测评", "首次安测", "等保测评", "系统金额", "测评费"))
    if level == "三级" and _contains_any(text, ("密测费", "密码测评费", "密码应用安全性评估费")):
        return _finding(
            rule,
            "密测费",
            "高",
            "应核定为0",
            "新规则要求密测费用不支持纳入运维申报。",
            "请将密测费核减为0；如另有主管部门专项依据，应单独提交人工复核。",
            {"命中申报项数": 1.0},
            evidence,
        )
    if level == "三级" and "安全测试费" in text and not _contains_any(text, ("等保测评", "等级保护测评", "已完成等保")):
        return _finding(
            rule,
            "安全测试费",
            "需人工确认",
            "安全测试费需调研",
            "新规则要求安全测试费可参考等保测评费核价，但需调研是否实际进行了等保测评；首次安测费用不支持。",
            "请补充安全测试与等保测评的对应关系、是否首次安测以及调研依据。",
            {"命中申报项数": 1.0},
            evidence,
        )
    if not _contains_any(text, level_keywords):
        if profile.has_kajia_table:
            return _pass(rule, f"等保{level}测评费", f"核价清单中未识别到等保{level}测评费场景。", {}, [])
        return _manual(
            rule,
            f"等保{level}测评费",
            f"未识别到核价清单或等保{level}测评费明细，无法按固定金额规则核价。",
            "请提供核价清单或明确系统金额、等保级别和测评费金额；缺失材料时标注资料不足。",
            doc,
            evidence,
        )

    if _contains_any(text, ("数据局已立项", "已立项审核", "数据局核定")):
        return _manual(rule, f"等保{level}测评费", "识别到数据局已立项或核定口径，应按数据局核定金额复核。", "请录入或核对数据局核定金额。", doc, evidence)

    system_amount = _first_labeled_amount(doc.lines, ("系统金额", "系统", "申报金额", "建设金额", "项目金额", "购置金额", "立项金额"))
    fee_amount = _first_labeled_amount(doc.lines, ("测评费", "等保测评", "等级保护测评", f"等保{level}"))
    quantities = {key: value for key, value in {"系统金额": system_amount, "测评费": fee_amount}.items() if value is not None}
    if system_amount is None or fee_amount is None:
        return _manual(
            rule,
            f"等保{level}测评费",
            f"已识别到等保{level}场景，但未同时识别到系统金额和测评费金额，无法自动核价。",
            "请明确系统金额和等保测评费金额；如果相关金额仅在核价清单中，请上传结构化核价清单。",
            doc,
            evidence,
            quantities,
        )

    expected = high_fee if system_amount >= 100 else low_fee if system_amount >= 10 else 0.0
    quantities["规则核定金额"] = expected
    if expected <= 0:
        return _manual(rule, f"等保{level}测评费", "系统金额低于 10 万元，规则表未给出明确测评费标准。", "请人工确认该系统是否应申报等保测评费。", doc, evidence, quantities)
    if abs(fee_amount - expected) <= 0.05:
        return _pass(rule, f"等保{level}测评费", f"测评费与规则核定金额 {expected:g} 万元一致。", quantities, evidence)
    return _finding(
        rule,
        f"等保{level}测评费",
        "中",
        "测评费不一致",
        f"系统金额 {system_amount:g} 万元，对应等保{level}测评费应为 {expected:g} 万元，识别金额为 {fee_amount:g} 万元。",
        "请按系统金额区间调整等保测评费，或补充数据局核定依据。",
        quantities,
        evidence,
    )


def _evaluate_history_price_with_new_item_exception(
    doc: MaintenanceDocument,
    rule: MaintenanceRule,
    profile: MaintenanceMaterialProfile,
) -> ResourceRuleFinding:
    current = _first_labeled_amount(doc.lines, ("本年度核定", "今年核定", "本年核定", "核定价格", "今年审核价格", "审核价格"))
    previous = _first_labeled_amount(doc.lines, ("去年核定", "上年核定", "历史价格", "去年价格", "历史审核金额"))
    if current is None or previous is None:
        return _manual(
            rule,
            "历史价格比较",
            "未同时识别到本年度核定价格和去年/历史核定价格，无法完成历史价格取低校验。",
            "请提供核价清单或历史审核金额字段；无新增项时今年审核价格、申报金额、历史审核金额应取低。",
            doc,
            _evidence(doc, ("本年度核定", "今年核定", "去年核定", "历史价格", "新增", "核定价格", "审核价格")),
            {"核价清单明细项数": float(profile.kajia_item_count)},
        )
    evidence = _evidence(doc, ("本年度核定", "今年核定", "去年核定", "历史价格", "新增", "新增项", "核定价格", "审核价格"))
    quantities = {"本年度核定价格": current, "去年核定价格": previous}
    if current <= previous + 0.05:
        return _pass(rule, "历史价格比较", "本年度核定价格未高于去年核定价格，符合取低口径。", quantities, evidence)
    if _contains_any(doc.raw_text, NEW_ITEM_TERMS):
        return _finding(
            rule,
            "历史价格比较",
            "需人工确认",
            "新增项例外需确认",
            f"本年度核定价格{current:g}万元高于去年核定价格{previous:g}万元，但文本中识别到新增项表述，新规则允许新增项导致审核金额大于历史金额。",
            "请拆分历史运维项与新增项，历史部分仍按申报金额、规则测算金额、历史审核金额取低；新增项按产品库询价或功能点核价。",
            quantities,
            evidence,
        )
    return _finding(
        rule,
        "历史价格比较",
        "中",
        "高于去年核定价格",
        f"本年度核定价格{current:g}万元高于去年核定价格{previous:g}万元，不符合无新增项不核增、三种价格取低口径。",
        "请按今年审核价格、申报金额、历史审核金额取低；确需调增时补充新增项或调研依据。",
        quantities,
        evidence,
    )


def _evaluate_new_product_reference_guard(
    doc: MaintenanceDocument,
    rule: MaintenanceRule,
    profile: MaintenanceMaterialProfile,
) -> ResourceRuleFinding | None:
    if not profile.has_kajia_table:
        return _manual(
            rule,
            "新增软硬件产品核价",
            "未识别到核价清单，不能执行新增软硬件产品的产品库/参考价匹配。",
            "请提供包含新增项、产品名称、品牌型号、规格、单价的核价清单；缺失时仅提示资料不足，不按正文片段强行匹配。",
            doc,
            _evidence(doc, ("新增", "新购", "产品库", "参考价", "上海市产品库", "松江区产品库", "硬件", "产品软件")),
            {"核价清单明细项数": float(profile.kajia_item_count)},
        )
    return None


def _parse_kajia_items(doc: MaintenanceDocument) -> list[MaintenanceLineItem]:
    rows = doc.table_rows
    if not rows:
        return []
    items: list[MaintenanceLineItem] = []
    current_section = ""
    header_map: dict[str, int] | None = None
    for row_index, row in enumerate(rows, start=1):
        row_text = " | ".join(str(cell).strip() for cell in row if str(cell).strip())
        if not row_text:
            continue
        maybe_header = _header_map(row)
        if maybe_header:
            header_map = maybe_header
            continue
        if _looks_like_section(row):
            current_section = row_text
            continue
        if not header_map:
            continue
        name = _value(row, header_map.get("name"))
        if not name:
            continue
        purchase = _number(_value(row, header_map.get("purchase_amount")))
        declared = _number(_value(row, header_map.get("declared_amount")))
        approved = _number(_value(row, header_map.get("approved_amount")))
        if purchase is None and declared is None and approved is None:
            continue
        spec = _value(row, header_map.get("spec"))
        items.append(
            MaintenanceLineItem(
                row_index=row_index,
                section=current_section,
                name=name,
                spec=spec,
                purchase_amount=purchase,
                declared_amount=declared,
                approved_amount=approved,
                raw_text=_trim(row_text, 320),
            )
        )
    return items


def _header_map(row: list[str]) -> dict[str, int] | None:
    mapping: dict[str, int] = {}
    for index, cell in enumerate(row):
        value = str(cell).strip()
        header = _normalize_header(value)
        if header in {"产品名称", "项目名称", "申报项", "费用名称", "硬件描述", "软件描述", "硬件名称", "软件名称", "名称"}:
            mapping["name"] = index
        elif header in {"配置", "配置要求", "规格", "规格型号", "服务内容", "功能描述", "模块描述"}:
            mapping["spec"] = index
        elif "购置金额" in header or "购买金额" in header or "原建设费" in header or "原核定开发费" in header:
            mapping["purchase_amount"] = index
        elif "申报运维金额" in header or "申报运维费" in header or header in {"运维金额", "申报金额"}:
            mapping["declared_amount"] = index
        elif "核定运维金额" in header or header in {"核定金额", "审核金额", "核定价", "审核价"}:
            mapping["approved_amount"] = index
    required = {"name", "purchase_amount", "declared_amount"}
    return mapping if required <= mapping.keys() else None


def _normalize_header(value: str) -> str:
    text = re.sub(r"\s+", "", str(value).strip())
    text = re.sub(r"[（(].*?[）)]", "", text)
    return text


def _looks_like_section(row: list[str]) -> bool:
    row_text = " ".join(str(cell).strip() for cell in row if str(cell).strip())
    if not row_text:
        return False
    if _contains_any(row_text, ("硬件运维", "硬件购置", "应用软件", "产品软件", "成品软件", "安全产品", "购买服务", "等保")):
        return _number(row_text) is None
    return False


def _item_matches_category(item: MaintenanceLineItem, category: str) -> bool:
    text = f"{item.section} {item.name} {item.spec}"
    if category == "application_software":
        return _contains_any(text, ("应用软件", "软件开发", "功能开发", "功能迭代")) and not _contains_any(text, ("产品软件", "成品软件"))
    if category == "product_software":
        return _contains_any(text, ("产品软件", "成品软件", "软件产品", "数据库", "中间件", "授权"))
    if category == "hardware":
        return _contains_any(text, ("硬件", "服务器", "网络设备", "交换机", "存储", "终端", "摄像机", "监控设备", "安全产品", "PC服务器"))
    return False


def _find_exclusion_hits(
    doc: MaintenanceDocument,
    exact_terms: Iterable[str],
    conditional_terms: Iterable[str],
    skip_when_new: bool,
    skip_terms: Iterable[str] = (),
) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    terms = [term for term in exact_terms if term]
    conditional = [term for term in conditional_terms if term]
    for line in doc.lines:
        if _contains_any(line, skip_terms):
            continue
        line_is_new = _contains_any(line, NEW_ITEM_TERMS)
        for term in terms:
            if term in line or _fuzzy_contains(line, term, 0.78):
                key = (term, _trim(line))
                if key not in seen:
                    hits.append({"term": term, "line": _trim(line)})
                    seen.add(key)
        if skip_when_new and line_is_new:
            continue
        for term in conditional:
            if term in line or _fuzzy_contains(line, term, 0.85):
                key = (term, _trim(line))
                if key not in seen:
                    hits.append({"term": f"非新增{term}", "line": _trim(line)})
                    seen.add(key)
    return hits


def _profile_material(doc: MaintenanceDocument) -> MaintenanceMaterialProfile:
    text = _material_text(doc)
    filename = doc.filename
    kajia_items = _parse_kajia_items(doc)
    has_kajia_table = bool(kajia_items) or _has_kajia_headers(doc)
    return MaintenanceMaterialProfile(
        has_plan=_contains_any(filename, ("运维方案", "维护方案", "方案")) or _contains_any(text, ("项目运维方案", "运维服务方案", "运维内容", "服务方案")),
        has_self_check=_contains_any(filename, ("自查表", "评估自查")) or _contains_any(text, ("自查表", "评估自查")),
        has_contract=_contains_any(filename, ("合同", "协议")) or _contains_any(text, ("合同", "协议", "甲方", "乙方")),
        has_ledger_or_ticket=_contains_any(filename, ("台账", "工单", "巡检", "工作记录", "记录表")) or _contains_any(text, ("运维台账", "工单", "巡检记录", "工作记录", "业务时间")),
        has_kajia_table=has_kajia_table,
        has_system_export=_contains_any(filename, ("系统导出", "申报导出", "系统填报")) or _contains_any(text, ("系统填报", "系统填写", "系统导出", "申报系统")),
        kajia_item_count=len(kajia_items),
    )


def _has_kajia_headers(doc: MaintenanceDocument) -> bool:
    for row in doc.table_rows:
        cells = {_normalize_header(str(cell).strip()) for cell in row if str(cell).strip()}
        joined = " ".join(cells)
        header_hits = sum(
            1
            for token in (
                "产品名称",
                "项目名称",
                "申报项",
                "硬件描述",
                "软件描述",
                "购置金额",
                "运维金额",
                "申报金额",
                "核定运维金额",
                "核定金额",
                "购置单价",
                "运维单价",
                "品牌",
                "型号",
                "类别",
                "单价",
                "总金额",
            )
            if token in joined
        )
        if header_hits >= 3:
            return True
    return False


def _material_text(doc: MaintenanceDocument) -> str:
    return f"{doc.filename}\n{doc.raw_text}"


def _line_matches_amount_source(line: str, keywords: Iterable[str]) -> bool:
    return _contains_any(line, keywords) and _contains_any(line, ("金额", "预算", "报价", "申报", "总额", "合计", "总计"))


def _largest_amount(line: str) -> float | None:
    amounts = _extract_amounts(line)
    return max(amounts) if amounts else None


def _extract_amounts(line: str) -> list[float]:
    values: list[float] = []
    for match in MONEY_PATTERN.finditer(line):
        value = _amount_from_match(match)
        if value is not None:
            values.append(value)
    return values


def _amounts_near_keywords(lines: list[str], keywords: Iterable[str]) -> list[float]:
    values: list[float] = []
    for line in lines:
        if _contains_any(line, keywords):
            values.extend(_extract_amounts(line))
    return values


def _format_quantities(values: dict[str, float]) -> str:
    return "；".join(f"{key}={value:g}万元" for key, value in values.items())


def _rule_selected(rule_id: str, selected: set[str]) -> bool:
    if not selected or "MAINT_OPS_ALL" in selected or rule_id in selected:
        return True
    rule = RULE_BY_ID.get(rule_id)
    return bool(rule and ({str(rule.rule_number), str(rule.excel_row), rule.rule_name} & selected))


def _finding(
    rule: MaintenanceRule,
    resource_name: str,
    severity: str,
    result_label: str,
    reason: str,
    suggestion: str | None,
    source_quantities: dict[str, float] | None,
    evidence_examples: list[str] | None,
) -> ResourceRuleFinding:
    return ResourceRuleFinding(
        rule_code=rule.rule_id,
        rule_name=rule.rule_name,
        resource_name=resource_name,
        severity=severity,
        result_label=result_label,
        reason=reason,
        suggestion=suggestion,
        source_quantities=source_quantities or {},
        row_indexes=[],
        evidence_examples=(evidence_examples or [])[:8],
        source_section="运维项目规则 / 独立模块",
    )


def _pass(
    rule: MaintenanceRule,
    resource_name: str,
    reason: str,
    source_quantities: dict[str, float] | None,
    evidence_examples: list[str] | None,
) -> ResourceRuleFinding:
    return _finding(rule, resource_name, "通过", "通过", reason, None, source_quantities, evidence_examples)


def _manual(
    rule: MaintenanceRule,
    resource_name: str,
    reason: str,
    suggestion: str,
    doc: MaintenanceDocument,
    evidence_examples: list[str] | None = None,
    source_quantities: dict[str, float] | None = None,
) -> ResourceRuleFinding:
    evidence = evidence_examples if evidence_examples is not None else _evidence(doc, tuple(rule.rule_name.split()))
    return _finding(rule, resource_name, "需人工确认", "资料不足", reason, suggestion, source_quantities, evidence)


def _contains_any(value: str, keywords: Iterable[str]) -> bool:
    normalized = str(value).lower()
    return any(str(keyword).lower() in normalized for keyword in keywords if str(keyword))


def _evidence(doc: MaintenanceDocument, keywords: Iterable[str], limit: int = 8) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for line in doc.lines:
        if not _contains_any(line, keywords):
            continue
        text = _trim(line)
        if text in seen:
            continue
        seen.add(text)
        output.append(text)
        if len(output) >= limit:
            break
    return output


def _value(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return str(row[index]).strip()


def _number(value: str) -> float | None:
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _first_labeled_amount(lines: list[str], labels: Iterable[str]) -> float | None:
    labels = [str(label).strip() for label in labels if str(label).strip()]
    for line in lines:
        lower_line = line.lower()
        label_ends: list[int] = []
        for label in labels:
            start = lower_line.find(label.lower())
            while start >= 0:
                label_ends.append(start + len(label))
                start = lower_line.find(label.lower(), start + len(label))
        if not label_ends:
            continue
        matches = list(MONEY_PATTERN.finditer(line))
        for label_end in sorted(label_ends):
            for match in matches:
                if match.start() < label_end:
                    continue
                if match.start() - label_end > 36:
                    break
                value = _amount_from_match(match)
                if value is not None:
                    return value
    return None


def _amount_from_match(match: re.Match[str]) -> float | None:
    raw = match.group("number")
    unit = match.group("unit") or ""
    try:
        number = float(raw.replace(",", ""))
    except ValueError:
        return None
    if number <= 0:
        return None
    if unit == "元":
        return number / 10000
    return number


def _char_ngrams(value: str, n: int = 2) -> set[str]:
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value).lower())
    if not normalized:
        return set()
    if len(normalized) <= n:
        return {normalized}
    return {normalized[index : index + n] for index in range(len(normalized) - n + 1)}


def _fuzzy_contains(value: str, keyword: str, threshold: float) -> bool:
    keyword_grams = _char_ngrams(keyword)
    if not keyword_grams:
        return False
    for segment in re.split(r"[\r\n。；;，,！!？?]+", str(value)):
        grams = _char_ngrams(segment)
        if grams and len(grams & keyword_grams) / len(keyword_grams) >= threshold:
            return True
    return False


def _trim(value: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _category_label(category: str) -> str:
    return {
        "application_software": "应用软件运维核价",
        "product_software": "产品软件运维核价",
        "hardware": "硬件运维核价",
    }.get(category, category)
