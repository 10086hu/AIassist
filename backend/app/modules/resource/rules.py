from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from app.modules.resource.parser import ParsedResourceItem


# 规则编码用于写入数据库和前端展示，避免只靠中文规则名定位结果。
RULE_15_CODE = "R15_SECURITY_PAAS_CRYPTO_QUANTITY"
RULE_15_NAME = "安全服务需求表、PaaS服务清单、密码服务资源内容清单的关联内容一致性校验规则"
RULE_16_OS_CODE = "R16_SERVER_OS_QUANTITY"
RULE_16_DB_CODE = "R16_DB_SERVER_DATABASE_QUANTITY"
RULE_16_NAME = "三大件数量一致性校验规则"

# 第 15 行规则关注的安全/密码/PaaS 相关服务。
# key 是系统内部统一名称，tuple 中是原始清单里可能出现的别名。
SECURITY_SERVICE_ALIASES = {
    "安全防病毒服务": ("安全防病毒服务", "防病毒", "杀毒", "病毒防护"),
    "安全认证网关服务": ("安全认证网关服务", "安全认证网关", "认证网关", "安全网关"),
    "时间戳服务": ("时间戳服务", "时间戳"),
    "签名验签服务": ("签名验签服务", "签名验签服务器", "签名验签", "电子签名", "验签"),
    "可信密码服务（数据加解密服务）": ("可信密码服务", "服务器密码机", "数据加解密服务", "加解密", "加密服务", "解密服务"),
    "身份认证服务": ("身份认证服务", "身份认证", "统一认证"),
    "数字证书服务": ("数字证书服务", "数字证书", "证书服务"),
}

# 第 15 行规则要求三个来源之间数量保持一致。
# 这里用关键词把原始 source/sheet/spec 归类到三类标准来源。
SOURCE_BUCKET_ALIASES = {
    "安全服务需求表": ("安全服务需求", "安全建设内容", "安全服务清单", "安全服务"),
    "PaaS服务清单": ("paas", "PaaS", "服务工具箱", "成熟产品"),
    "密码服务资源内容清单": ("密码服务", "密码资源", "密码服务资源", "密码"),
}
RULE_15_EXPECTED_BUCKETS = tuple(SOURCE_BUCKET_ALIASES.keys())
RULE_15_FIREWALL_FEATURE_KEYWORDS = (
    "防火墙",
    "边界安全",
    "入侵防御",
    "Anti-DDoS",
    "URL过滤",
    "反垃圾邮件",
    "反邮件",
)

# 第 16 行规则关注“服务器 -> 操作系统”“数据库服务器 -> 数据库”的一一配置关系。
# 这些别名用于从资源名称和规格中识别相关资源。
SERVER_ALIASES = ("服务器", "云服务器", "虚拟机", "计算资源", "应用服务器", "主机")
DATABASE_SERVER_ALIASES = ("数据库服务器", "数据库主机", "db服务器", "DB服务器")
OS_ALIASES = ("操作系统", "服务器操作系统", "os", "OS", "麒麟", "kylin", "统信", "uos", "欧拉", "openEuler", "openeuler")
DATABASE_ALIASES = ("数据库软件", "数据库", "国产数据库", "mysql", "postgresql", "oracle", "达梦", "人大金仓", "gaussdb")
MAX_REASONABLE_COMPARE_QUANTITY = 10000.0
RULE_16_COVERAGE_KEYWORDS = ("授权", "许可", "license", "覆盖", "适配", "配套", "部署", "支撑", "支持", "可用")
RULE_16_COVERAGE_UNITS = ("套", "台", "个", "份", "项", "实例", "instance")
RULE_16_NON_COVERAGE_UNITS = ("核", "g", "gb", "t", "tb", "年", "月", "人", "种", "类", "元", "万元")
RULE_16_NUMBER_PATTERN = re.compile(r"(?<![\d.])(-?\d+(?:\.\d+)?)\s*([\u4e00-\u9fffA-Za-z/]{0,8})")
RULE_16_SUMMARY_KEYWORDS = ("合计", "总计", "小计", "汇总", "共计", "总共", "累计", "本项目", "本次")
RULE_16_STRONG_SERVER_ALIASES = (
    "云服务器",
    "应用服务器",
    "数据库服务器",
    "裸金属服务器",
    "物理服务器",
    "pc服务器",
    "机架式服务器",
    "虚拟机",
    "云主机",
    "计算资源",
    "计算实例",
)
RULE_16_WEAK_SERVER_ALIASES = ("服务器",)
RULE_16_OS_PRODUCT_ALIASES = (
    "服务器操作系统",
    "操作系统",
    "国产操作系统",
    "麒麟",
    "kylin",
    "统信",
    "uos",
    "欧拉",
    "openeuler",
    "openEuler",
)
RULE_16_DATABASE_PRODUCT_ALIASES = (
    "数据库软件",
    "数据库授权",
    "数据库产品",
    "数据库管理系统",
    "国产数据库",
    "gbase",
    "kingbase",
    "人大金仓",
    "达梦",
    "gaussdb",
    "mysql",
    "postgresql",
    "oracle",
)
RULE_16_PLAIN_DATABASE_PRODUCT_CONTEXT_KEYWORDS = (
    "国产数据库",
    "数据库授权",
    "数据库产品",
    "数据库软件",
    "数据库管理系统",
    "永久授权",
    "license",
    "产品软件",
    "gbase",
    "kingbase",
    "达梦",
    "人大金仓",
    "gaussdb",
    "mysql",
    "postgresql",
    "oracle",
)
RULE_16_SERVER_EXCLUDE_KEYWORDS = (
    "服务器端授权",
    "服务器数字证书",
    "数字证书",
    "证书",
    "防病毒",
    "杀毒",
    "安全产品",
    "安全网关",
    "认证网关",
    "签名验签",
    "服务器密码机",
    "密码机",
    "堡垒机",
    "审计",
    "数据库安全",
    "数据静态脱敏",
    "交换机",
    "摄像机",
    "话筒",
    "调音台",
    "控制器",
    "中控主机",
    "传屏器",
    "工作站",
    "终端",
    "pos机",
    "模块",
    "管理系统控制软件",
)
RULE_16_DEVICE_SERVER_KEYWORDS = (
    "视频",
    "图像",
    "人脸",
    "抓拍",
    "监控",
    "录像",
    "nvr",
    "NVR",
    "诊断",
    "分析一体机",
    "一体机",
    "音频",
    "音视频",
    "会议",
    "智能终端",
    "专用设备",
    "成套设备",
)
RULE_16_OS_EXCLUDE_KEYWORDS = (
    "支持安装在",
    "支持系统类型",
    "支持windows",
    "支持 windows",
    "支持linux",
    "支持 linux",
    "支持国产操作系统",
    "兼容操作系统",
    "适配操作系统",
    "运行环境",
    "客户端软件",
    "安卓操作系统",
    "安全操作系统",
    "审计节点",
    "主机/设备审计",
    "服务器端授权",
)
RULE_16_DATABASE_EXCLUDE_KEYWORDS = (
    "数据库安全",
    "数据库审计",
    "数据库脱敏",
    "数据静态脱敏",
    "数据库访问行为",
    "数据治理",
    "数据处理",
    "数据采集",
    "知识库",
    "承载数据库",
    "用于承载数据库",
    "数据库等",
)
RULE_16_DATABASE_WORKLOAD_KEYWORDS = ("承载数据库", "用于承载数据库", "数据库等")


@dataclass(frozen=True)
class ResourceRuleFinding:
    """单条规则检查结果。

    规则层只返回这个轻量对象；是否落库、如何返回给 API，由 service 层负责。
    """

    # 稳定规则编码，例如 R15_SECURITY_PAAS_CRYPTO_QUANTITY。
    rule_code: str
    # 中文规则名称，来自“规则分工.xlsx”的第 15/16 行。
    rule_name: str
    # 本次发现对应的资源或服务名称。
    resource_name: str
    # 严重程度与敏感词/建设功能对应关系保持一致：通过、需人工确认、中。
    severity: str
    # 面向用户展示的短标签：通过、不一致、资料不足、无法判断。
    result_label: str
    # 详细判定原因。
    reason: str
    # 修正建议；通过项可以为空。
    suggestion: str | None
    # 参与比较的数量明细，例如 {"安全服务需求表": 2, "PaaS服务清单": 3}。
    source_quantities: dict[str, float]
    # 相关原始行号，方便用户回到 Excel/CSV 定位。
    row_indexes: list[int]
    # 原文依据片段，供检测页面“文章依据/文档依据”直接展示定位。
    evidence_examples: list[str] = field(default_factory=list)
    # 原文依据所在章节、表格或来源。
    source_section: str = "资源申请清单"
    source_locations: list[dict[str, object]] | None = None


def evaluate_resource_rules(items: list[ParsedResourceItem]) -> list[ResourceRuleFinding]:
    """执行资源申请合理性检查的总入口。

    当前只实现“规则分工.xlsx”第 15、16 行：
    - 第 15 行：安全服务需求表、PaaS服务清单、密码服务资源内容清单数量一致。
    - 第 16 行：服务器与操作系统、数据库服务器与数据库数量一致。
    """

    findings: list[ResourceRuleFinding] = []
    findings.extend(check_security_service_quantities(items))
    findings.extend(check_major_component_quantities(items))
    return findings


def check_security_service_quantities(items: list[ParsedResourceItem]) -> list[ResourceRuleFinding]:
    """执行第 15 行规则。

    对每一种安全服务：
    1. 根据服务名称别名识别服务类型。
    2. 根据来源字段识别属于哪一类清单。
    3. 按“服务类型 + 清单类型”汇总数量。
    4. 比较不同清单中的数量是否一致。
    """

    # grouped 示例：
    # {
    #   "安全防病毒服务": {"安全服务需求表": 2, "PaaS服务清单": 2, "密码服务资源内容清单": 3}
    # }
    grouped: dict[str, dict[str, float]] = {}
    # rows 保存每个服务涉及的原始行号，用于结果定位。
    rows: dict[str, list[int]] = {}
    evidences: dict[str, list[str]] = {}
    sections: dict[str, list[str]] = {}

    for item in items:
        # 第一步：判断资源名称是否属于本规则关注的安全服务。
        # 只用资源名称做服务归一，不把规格/功能描述中的“防病毒”等能力词直接当作服务名称。
        service_name = _match_rule_15_service(item)
        if not service_name:
            continue

        # 第二步：判断该资源来自安全服务需求表、PaaS服务清单还是密码服务资源内容清单。
        # 来源归类只看来源/表名，不看规格描述，避免“说明文字中出现密码/安全”导致误归类。
        bucket = _classify_rule_15_source_bucket(item)
        if not bucket:
            bucket = "未识别来源"

        # 第三步：同一服务、同一来源可能有多行，数量需要累加。
        grouped.setdefault(service_name, {})
        rows.setdefault(service_name, [])
        evidences.setdefault(service_name, [])
        sections.setdefault(service_name, [])
        grouped[service_name][bucket] = grouped[service_name].get(bucket, 0.0) + item.quantity
        rows[service_name].append(item.row_index)
        evidences[service_name].append(_item_evidence(item))
        sections[service_name].append(_item_section(item))

    recognized_buckets = {
        bucket
        for quantities in grouped.values()
        for bucket in quantities
        if bucket in SOURCE_BUCKET_ALIASES
    }
    if not grouped or len(recognized_buckets) < len(RULE_15_EXPECTED_BUCKETS):
        # 没有完整三类清单时，本条不能进行交叉数量一致性判断。
        # 返回一条总括性结果，避免对单个服务反复报“资料不足”。
        missing_buckets = [bucket for bucket in RULE_15_EXPECTED_BUCKETS if bucket not in recognized_buckets]
        all_quantities = _merge_rule_15_quantities(grouped)
        return [
            ResourceRuleFinding(
                rule_code=RULE_15_CODE,
                rule_name=RULE_15_NAME,
                resource_name="三类清单完整性",
                severity="需人工确认",
                result_label="无法判断",
                reason=(
                    "未识别到完整的安全服务需求表、PaaS服务清单、密码服务资源内容清单，"
                    f"缺少：{', '.join(missing_buckets) if missing_buckets else '相关服务数量'}；"
                    "无法执行第15条三类清单数量一致性校验。"
                ),
                suggestion="请补充或上传包含三类清单及服务数量字段的结构化资源清单；若本项目明确不涉及三类清单，可在报告中说明本规则不适用。",
                source_quantities=all_quantities,
                row_indexes=_flatten_rule_15_rows(rows),
                evidence_examples=_unique_limited(
                    evidence for evidence_list in evidences.values() for evidence in evidence_list
                ),
                source_section=_join_sections(section for section_list in sections.values() for section in section_list)
                if sections
                else "安全服务需求表 / PaaS服务清单 / 密码服务资源内容清单",
            )
        ]

    findings: list[ResourceRuleFinding] = []
    expected_buckets = set(SOURCE_BUCKET_ALIASES)
    for service_name, quantities in grouped.items():
        # 只有标准三类来源才参与一致性比较；未识别来源保留在返回明细里。
        comparable = {key: value for key, value in quantities.items() if key in expected_buckets}
        if len(comparable) < 2:
            findings.append(
                ResourceRuleFinding(
                    rule_code=RULE_15_CODE,
                    rule_name=RULE_15_NAME,
                    resource_name=service_name,
                    severity="需人工确认",
                    result_label="资料不足",
                    reason=f"{service_name} 仅识别到 {', '.join(quantities.keys())} 的数量，无法完成三类清单交叉一致性校验。",
                    suggestion="请补充安全服务需求表、PaaS服务清单、密码服务资源内容清单中的对应数量。",
                    source_quantities=quantities,
                    row_indexes=rows[service_name],
                    evidence_examples=_unique_limited(evidences.get(service_name, [])),
                    source_section=_join_sections(sections.get(service_name, [])),
                )
            )
            continue

        # 只要参与比较的数量集合只有一个值，就认为一致。
        unique_values = {round(value, 4) for value in comparable.values()}
        if len(unique_values) == 1:
            findings.append(
                ResourceRuleFinding(
                    rule_code=RULE_15_CODE,
                    rule_name=RULE_15_NAME,
                    resource_name=service_name,
                    severity="通过",
                    result_label="通过",
                    reason=f"{service_name} 在已识别清单中的数量一致。",
                    suggestion=None,
                    source_quantities=quantities,
                    row_indexes=rows[service_name],
                    evidence_examples=_unique_limited(evidences.get(service_name, [])),
                    source_section=_join_sections(sections.get(service_name, [])),
                )
            )
        else:
            findings.append(
                ResourceRuleFinding(
                    rule_code=RULE_15_CODE,
                    rule_name=RULE_15_NAME,
                    resource_name=service_name,
                    severity="高",
                    result_label="不一致",
                    reason=f"{service_name} 在不同清单中的数量不一致：{_format_quantities(comparable)}。",
                    suggestion="请核对 6.6 安全建设内容、6.7 PaaS服务工具箱及密码服务资源内容清单，保持相关服务数量一致。",
                    source_quantities=quantities,
                    row_indexes=rows[service_name],
                    evidence_examples=_unique_limited(evidences.get(service_name, [])),
                    source_section=_join_sections(sections.get(service_name, [])),
                )
            )
    return findings


def check_major_component_quantities(items: list[ParsedResourceItem]) -> list[ResourceRuleFinding]:
    """执行第 16 行规则。

    第 16 行实际上包含两个数量关系：
    - 每新申请一台服务器，需要配置一套操作系统。
    - 每申请一台数据库服务器，需要配置一个数据库。
    """

    server_matches = _rule_16_server_matches(items)
    os_matches = _rule_16_os_matches(items, server_matches)
    db_server_matches = _rule_16_db_server_matches(items)
    database_matches = _rule_16_database_matches(items)

    server_total, server_rows = _match_total_rows(server_matches)
    os_total, os_rows = _match_total_rows(os_matches)
    db_server_total, db_server_rows = _match_total_rows(db_server_matches)
    database_total, database_rows = _match_total_rows(database_matches)

    findings = [
        _compare_quantity(
            rule_code=RULE_16_OS_CODE,
            resource_name="服务器与操作系统",
            expected_name="服务器数量",
            actual_name="操作系统数量",
            expected=server_total,
            actual=os_total,
            row_indexes=server_rows + os_rows,
            evidence_examples=_match_evidence(server_matches + os_matches),
            source_section=_match_sections(server_matches + os_matches),
            missing_reason="未同时识别到服务器数量和操作系统数量，无法判断每台服务器是否配置一套操作系统。",
            mismatch_suggestion="请核对 PaaS 清单和计算资源清单，确保每新申请一台服务器配置一套操作系统。",
        ),
        _compare_quantity(
            rule_code=RULE_16_DB_CODE,
            resource_name="数据库服务器与数据库",
            expected_name="数据库服务器数量",
            actual_name="数据库数量",
            expected=db_server_total,
            actual=database_total,
            row_indexes=db_server_rows + database_rows,
            evidence_examples=_match_evidence(db_server_matches + database_matches),
            source_section=_match_sections(db_server_matches + database_matches),
            missing_reason="未同时识别到数据库服务器数量和数据库数量，无法判断每台数据库服务器是否配置一个数据库。",
            mismatch_suggestion="请核对 PaaS 清单和计算资源清单，确保每申请一台数据库服务器配置一个数据库。",
        ),
    ]
    return findings


def _compare_quantity(
    rule_code: str,
    resource_name: str,
    expected_name: str,
    actual_name: str,
    expected: float,
    actual: float,
    row_indexes: list[int],
    evidence_examples: list[str],
    source_section: str,
    missing_reason: str,
    mismatch_suggestion: str,
) -> ResourceRuleFinding:
    """通用数量比较函数。

    第 16 行的两组关系都属于“左侧数量应该等于右侧数量”，所以抽成同一个函数。
    """

    quantities = {expected_name: expected, actual_name: actual}
    if expected > MAX_REASONABLE_COMPARE_QUANTITY or actual > MAX_REASONABLE_COMPARE_QUANTITY:
        return ResourceRuleFinding(
            rule_code=rule_code,
            rule_name=RULE_16_NAME,
            resource_name=resource_name,
            severity="需人工确认",
            result_label="数量异常",
            reason=(
                f"{expected_name}为 {_format_number(expected)}，{actual_name}为 {_format_number(actual)}，"
                "数量明显超出常规资源申请范围，可能混入预算金额、规格参数或非资源表格。"
            ),
            suggestion="请核对上传材料是否为资源申请清单；如上传完整报告，请确认资源相关章节或清单标题清晰。",
            source_quantities=quantities,
            row_indexes=row_indexes,
            evidence_examples=evidence_examples,
            source_section=source_section,
        )
    if expected <= 0 and actual <= 0:
        return ResourceRuleFinding(
            rule_code=rule_code,
            rule_name=RULE_16_NAME,
            resource_name=resource_name,
            severity="通过",
            result_label="不适用",
            reason=f"未识别到{resource_name}相关的可信三大件资源申请清单，本条关系不适用。",
            suggestion="如项目确需申请服务器、操作系统、数据库服务器或数据库软件，请在资源清单中列明资源名称和数量。",
            source_quantities=quantities,
            row_indexes=row_indexes,
            evidence_examples=evidence_examples,
            source_section=source_section,
        )
    if expected <= 0 or actual <= 0:
        return ResourceRuleFinding(
            rule_code=rule_code,
            rule_name=RULE_16_NAME,
            resource_name=resource_name,
            severity="需人工确认",
            result_label="无法判断",
            reason=missing_reason,
            suggestion="请在资源清单中提供资源名称和数量字段，例如服务器、操作系统、数据库服务器、数据库。",
            source_quantities=quantities,
            row_indexes=row_indexes,
            evidence_examples=evidence_examples,
            source_section=source_section,
        )

    if round(expected, 4) == round(actual, 4):
        return ResourceRuleFinding(
            rule_code=rule_code,
            rule_name=RULE_16_NAME,
            resource_name=resource_name,
            severity="通过",
            result_label="通过",
            reason=f"{expected_name}与{actual_name}一致，数量均为 {expected:g}。",
            suggestion=None,
            source_quantities=quantities,
            row_indexes=row_indexes,
            evidence_examples=evidence_examples,
            source_section=source_section,
        )

    return ResourceRuleFinding(
        rule_code=rule_code,
        rule_name=RULE_16_NAME,
        resource_name=resource_name,
        severity="高",
        result_label="不一致",
        reason=f"{expected_name}为 {expected:g}，{actual_name}为 {actual:g}，数量不一致。",
        suggestion=mismatch_suggestion,
        source_quantities=quantities,
        row_indexes=row_indexes,
        evidence_examples=evidence_examples,
        source_section=source_section,
    )


def _rule_16_server_matches(items: Iterable[ParsedResourceItem]) -> list[tuple[ParsedResourceItem, float]]:
    """识别第 16 条左侧服务器数量。

    Word 报告中服务器通常会同时出现在资源需求表、测算说明和预算表中。优先使用带操作系统
    配置列的资源需求表，避免把预算表或正文说明重复累加。
    """

    structured_with_os: list[tuple[ParsedResourceItem, float]] = []
    fallback: list[tuple[ParsedResourceItem, float]] = []
    for item in items:
        if _is_rule_16_os_product_item(item) or _is_rule_16_database_product_item(item):
            continue
        if not _is_rule_16_server_resource_item(item):
            continue
        if _is_rule_16_spec_only_item(item):
            continue
        match = (item, item.quantity)
        if _is_rule_16_budget_or_quote_row(item):
            fallback.append(match)
        elif _rule_16_server_row_has_os_config(item):
            structured_with_os.append(match)
        else:
            fallback.append(match)
    return _dedupe_rule_16_quantity_matches(structured_with_os or fallback)


def _rule_16_os_matches(
    items: Iterable[ParsedResourceItem],
    server_matches: list[tuple[ParsedResourceItem, float]],
) -> list[tuple[ParsedResourceItem, float]]:
    """识别第 16 条操作系统数量。

    如果服务器资源行自身带有操作系统字段（如 kylin-v10-sp3），按这些服务器行数量作为
    操作系统覆盖数量；否则再寻找单独的操作系统授权/产品行。
    """

    covered_server_rows = [
        (item, quantity)
        for item, quantity in server_matches
        if _rule_16_server_row_has_os_config(item)
    ]
    if covered_server_rows:
        return covered_server_rows

    matches: list[tuple[ParsedResourceItem, float]] = []
    for item in items:
        if not _is_rule_16_os_product_item(item):
            continue
        if _is_rule_16_spec_only_item(item):
            continue
        matches.append((item, _rule_16_effective_quantity(item, True)))
    return _dedupe_rule_16_quantity_matches(matches)


def _rule_16_db_server_matches(items: Iterable[ParsedResourceItem]) -> list[tuple[ParsedResourceItem, float]]:
    structured: list[tuple[ParsedResourceItem, float]] = []
    fallback: list[tuple[ParsedResourceItem, float]] = []
    for item in items:
        if not _is_rule_16_database_server_resource_item(item):
            continue
        if _is_rule_16_spec_only_item(item):
            continue
        if _is_rule_16_budget_or_quote_row(item):
            fallback.append((item, item.quantity))
        else:
            structured.append((item, item.quantity))
    return _dedupe_rule_16_quantity_matches(structured or fallback)


def _rule_16_database_matches(items: Iterable[ParsedResourceItem]) -> list[tuple[ParsedResourceItem, float]]:
    purchase_matches: list[tuple[ParsedResourceItem, float]] = []
    fallback_matches: list[tuple[ParsedResourceItem, float]] = []
    for item in items:
        if _is_rule_16_database_server_resource_item(item):
            continue
        if not _is_rule_16_database_product_item(item):
            continue
        if _is_rule_16_spec_only_item(item):
            continue
        quantity = _rule_16_effective_quantity(item, True)
        if _is_rule_16_database_purchase_row(item):
            purchase_matches.append((item, quantity))
        else:
            fallback_matches.append((item, quantity))

    matches = _dedupe_rule_16_quantity_matches(purchase_matches or fallback_matches)
    if purchase_matches and matches:
        # 同一数据库产品可能同时出现在 PaaS/产品清单、预算清单和报价清单中。
        # 对“数据库服务器 -> 数据库”关系，应取有效授权/采购数量，而不是跨清单累加。
        max_quantity = max(quantity for _, quantity in matches)
        for item, quantity in matches:
            if round(quantity, 4) == round(max_quantity, 4):
                return [(item, quantity)]
    return matches


def _match_total_rows(matches: list[tuple[ParsedResourceItem, float]]) -> tuple[float, list[int]]:
    return sum(quantity for _, quantity in matches), [item.row_index for item, _ in matches]


def _match_evidence(matches: list[tuple[ParsedResourceItem, float]]) -> list[str]:
    return _unique_limited([_item_evidence(item, quantity) for item, quantity in matches])


def _match_sections(matches: list[tuple[ParsedResourceItem, float]]) -> str:
    return _join_sections([_item_section(item) for item, _ in matches])


def _is_rule_16_server_resource_item(item: ParsedResourceItem) -> bool:
    """Return True only for server resources, not devices mentioning servers."""

    name = _rule_16_name_text(item)
    full = _rule_16_full_text(item)
    if _has_any_lower(full, RULE_16_SERVER_EXCLUDE_KEYWORDS):
        return False
    if _is_rule_16_database_product_item(item) or _is_rule_16_os_product_item(item):
        return False
    if _has_any_lower(name, RULE_16_STRONG_SERVER_ALIASES):
        return True
    if _has_any_lower(name, RULE_16_WEAK_SERVER_ALIASES):
        return _has_rule_16_resource_context(item) or _looks_like_rule_16_structured_server_row(item)
    return False


def _is_rule_16_database_server_resource_item(item: ParsedResourceItem) -> bool:
    """Recognize database servers, including compute resources dedicated to DB workloads."""

    name = _rule_16_name_text(item)
    full = _rule_16_full_text(item)
    if _is_rule_16_database_product_item(item):
        return False
    if _has_any_lower(name, DATABASE_SERVER_ALIASES) or _has_any_lower(full, DATABASE_SERVER_ALIASES):
        return True
    return _is_rule_16_server_resource_item(item) and _has_any_lower(full, RULE_16_DATABASE_WORKLOAD_KEYWORDS)


def _looks_like_rule_16_structured_server_row(item: ParsedResourceItem) -> bool:
    """Allow generic '*服务器' rows from structured resource tables without document-specific titles."""

    name = _rule_16_name_text(item)
    if "服务器" not in name:
        return False
    if _is_rule_16_device_server_item(item) and not _has_rule_16_resource_context(item):
        return False
    if len(name.strip()) <= len("服务器"):
        return False
    if item.quantity <= 0 or item.quantity > MAX_REASONABLE_COMPARE_QUANTITY:
        return False
    if str(item.unit or "").lower() in {"核", "g", "gb", "t", "tb", "年", "月", "人", "块", "元", "万元"}:
        return False
    return True


def _is_rule_16_device_server_item(item: ParsedResourceItem) -> bool:
    """Identify appliance/device rows that should not be treated as 三大件 servers by default."""

    text = _rule_16_full_text(item)
    return _has_any_lower(text, RULE_16_DEVICE_SERVER_KEYWORDS)


def _is_rule_16_os_product_item(item: ParsedResourceItem) -> bool:
    """Recognize OS products/licenses, not device compatibility or appliance descriptions."""

    name = _rule_16_name_text(item)
    full = _rule_16_full_text(item)
    if _has_any_lower(full, RULE_16_OS_EXCLUDE_KEYWORDS):
        return False
    if not _has_any_lower(name, RULE_16_OS_PRODUCT_ALIASES):
        return False
    if _has_any_lower(name, ("服务器操作系统", "国产操作系统")):
        return True
    if _has_any_lower(name, ("麒麟", "kylin", "统信", "uos", "欧拉", "openeuler", "openEuler")):
        return _has_rule_16_product_or_resource_context(item)
    if "操作系统" in name:
        return _has_rule_16_product_or_resource_context(item)
    return False


def _rule_16_server_row_has_os_config(item: ParsedResourceItem) -> bool:
    """Recognize an OS configured on a server row without counting compatibility text."""

    full = _rule_16_full_text(item)
    if _has_any_lower(full, RULE_16_OS_EXCLUDE_KEYWORDS):
        return False
    if re.search(r"(?:操作系统|系统)\s*[:：]\s*(?:麒麟|统信|uos|kylin|openeuler|openEuler|windows|linux)", full, flags=re.IGNORECASE):
        return True
    if re.search(r"(?:预装|配置|配套).{0,8}(?:操作系统|麒麟|统信|uos|kylin|openeuler|openEuler)", full, flags=re.IGNORECASE):
        return True
    if re.search(r"\b(?:kylin|uos|openeuler|openEuler)(?:[-_a-z0-9.]+)?\b", full, flags=re.IGNORECASE):
        return True
    if re.search(r"(?:麒麟|统信|欧拉)(?:[-_a-z0-9.版]*)?", full, flags=re.IGNORECASE):
        return True
    return False


def _is_rule_16_database_product_item(item: ParsedResourceItem) -> bool:
    """Recognize database software/products, not database-security or DB workload servers."""

    name = _rule_16_name_text(item)
    full = _rule_16_full_text(item)
    if _has_any_lower(full, RULE_16_DATABASE_EXCLUDE_KEYWORDS):
        return False
    if _is_rule_16_database_server_text(name):
        return False
    if _has_any_lower(name, RULE_16_DATABASE_PRODUCT_ALIASES):
        return True
    if _normalize_rule_16_identity(name) == "数据库":
        return _has_any_lower(full, RULE_16_PLAIN_DATABASE_PRODUCT_CONTEXT_KEYWORDS) or _is_rule_16_database_purchase_row(item)
    return False


def _is_rule_16_database_server_text(value: str) -> bool:
    return _has_any_lower(value, DATABASE_SERVER_ALIASES)


def _has_rule_16_resource_context(item: ParsedResourceItem) -> bool:
    text = _rule_16_full_text(item)
    return _has_any_lower(
        text,
        (
            "资源申请",
            "资源清单",
            "云资源",
            "计算资源",
            "计算资源服务",
            "paas",
            "iaas",
            "三大件",
            "云托管",
            "硬件租赁",
            "服务器操作系统",
            "数据库软件",
            "数据库服务器",
        ),
    )


def _has_rule_16_product_or_resource_context(item: ParsedResourceItem) -> bool:
    text = _rule_16_full_text(item)
    return _has_rule_16_resource_context(item) or _has_any_lower(
        text,
        ("授权", "许可", "license", "产品", "软件", "采购", "申请数量", "配置数量", "资源数量"),
    )


def _rule_16_text(item: ParsedResourceItem) -> str:
    return f"{item.name} {item.spec}".lower()


def _rule_16_name_text(item: ParsedResourceItem) -> str:
    return str(item.name or "").lower()


def _rule_16_full_text(item: ParsedResourceItem) -> str:
    return f"{item.name} {item.spec} {item.raw_text}".lower()


def _has_any_lower(text: str, keywords: Iterable[str]) -> bool:
    lowered = str(text or "").lower()
    return any(str(keyword or "").lower() in lowered for keyword in keywords if str(keyword or ""))


def _matches_any(text: str, aliases: tuple[str, ...]) -> bool:
    lowered = text.lower()
    if any(alias.lower() in lowered for alias in aliases):
        return True
    return _best_bigram_alias_score(text, aliases) >= 0.62


def _is_rule_16_budget_or_quote_row(item: ParsedResourceItem) -> bool:
    text = _rule_16_full_text(item)
    return any(keyword in text for keyword in ("硬件产品", "产品软件", "单价", "总金额", "总价", "报价", "预算", "申报金额", "硬件租赁", "软件租赁"))


def _is_rule_16_database_purchase_row(item: ParsedResourceItem) -> bool:
    text = _rule_16_full_text(item)
    negative = ("知识库", "数据处理", "数据治理", "海关内网数据存储", "外贸业务库", "数据采集", "数据库安全", "数据库审计", "数据库脱敏")
    if any(keyword in text for keyword in negative):
        return False
    positive = ("国产数据库", "数据库授权", "数据库产品", "产品软件", "gbase", "kingbase", "达梦", "人大金仓", "gaussdb")
    return any(keyword.lower() in text for keyword in positive)


def _is_rule_16_spec_only_item(item: ParsedResourceItem) -> bool:
    text = _rule_16_full_text(item)
    spec_markers = ("ssd", "硬盘", "raid", "核", "线程", "内存", "qps", "tb", "gb", "ghz")
    name_text = item.name.lower()
    return (
        item.unit.lower() in {"核", "g", "gb", "t", "tb", "年", "月", "人", "块"}
        or any(marker in text for marker in spec_markers) and not _matches_any(name_text, SERVER_ALIASES + DATABASE_ALIASES + OS_ALIASES)
    )


def _sum_by_alias(
    items: Iterable[ParsedResourceItem],
    aliases: tuple[str, ...],
    exclude: tuple[str, ...] = (),
    prefer_coverage_quantity: bool = False,
) -> tuple[float, list[int]]:
    """按关键词别名汇总数量，并返回参与汇总的原始行号。"""

    matches: list[tuple[ParsedResourceItem, float]] = []
    for item in items:
        text = f"{item.name} {item.spec}".lower()
        # 排除词优先，防止“服务器操作系统”被当成普通服务器。
        if any(alias.lower() in text for alias in exclude):
            continue
        if any(alias.lower() in text for alias in aliases):
            quantity = _rule_16_effective_quantity(item, prefer_coverage_quantity)
            if 0 < quantity <= MAX_REASONABLE_COMPARE_QUANTITY:
                matches.append((item, quantity))

    deduped_matches = _dedupe_rule_16_quantity_matches(matches)
    total = sum(quantity for _, quantity in deduped_matches)
    rows = [item.row_index for item, _ in deduped_matches]
    return total, rows


def _dedupe_rule_16_quantity_matches(
    matches: list[tuple[ParsedResourceItem, float]],
) -> list[tuple[ParsedResourceItem, float]]:
    """去除第 16 条中同一资源批次的重复提及。

    Word/PDF 里同一批服务器可能同时出现在明细表、正文说明和“本项目共申请”汇总句中。
    这里先按名称、规格和有效数量去掉精确重复；再用名称和数量识别汇总句与明细行的重复。
    """

    deduped: list[tuple[ParsedResourceItem, float]] = []
    seen_strict_keys: set[tuple[str, float]] = set()
    detail_loose_keys: set[tuple[str, float]] = set()
    summary_loose_keys: set[tuple[str, float]] = set()

    for item, quantity in matches:
        strict_key = (_normalize_rule_16_identity(f"{item.name} {item.spec}"), round(quantity, 4))
        loose_key = (_normalize_rule_16_identity(item.name), round(quantity, 4))
        is_summary = _is_rule_16_summary_mention(item)

        if strict_key in seen_strict_keys:
            continue
        if is_summary and loose_key in detail_loose_keys:
            continue
        if not is_summary and loose_key in summary_loose_keys:
            deduped = [
                (kept_item, kept_quantity)
                for kept_item, kept_quantity in deduped
                if not (
                    _is_rule_16_summary_mention(kept_item)
                    and (_normalize_rule_16_identity(kept_item.name), round(kept_quantity, 4)) == loose_key
                )
            ]
            summary_loose_keys.discard(loose_key)

        deduped.append((item, quantity))
        seen_strict_keys.add(strict_key)
        if is_summary:
            summary_loose_keys.add(loose_key)
        else:
            detail_loose_keys.add(loose_key)

    return deduped


def _rule_16_effective_quantity(item: ParsedResourceItem, prefer_coverage_quantity: bool) -> float:
    """返回第 16 条用于比较的有效数量。

    操作系统/数据库软件可能以“1种/1套产品 + 授权100套/覆盖100台”的方式填报。
    这类右侧资源应优先使用授权或覆盖数量，避免把产品种类数误当成配置数量。
    """

    if not prefer_coverage_quantity:
        return item.quantity
    coverage_quantity = _extract_rule_16_coverage_quantity(f"{item.spec} {item.raw_text}")
    return coverage_quantity if coverage_quantity is not None else item.quantity


def _extract_rule_16_coverage_quantity(value: str) -> float | None:
    """从规格/原始行文本中提取授权、覆盖或适配数量。"""

    if not value or not _contains_rule_16_coverage_keyword(value):
        return None

    candidates: list[float] = []
    for match in RULE_16_NUMBER_PATTERN.finditer(value):
        quantity = float(match.group(1))
        if quantity <= 0 or quantity > MAX_REASONABLE_COMPARE_QUANTITY:
            continue

        unit = (match.group(2) or "").strip()
        unit_lower = unit.lower()
        if unit and any(unit_lower.startswith(bad_unit) for bad_unit in RULE_16_NON_COVERAGE_UNITS):
            continue

        left = value[max(0, match.start() - 12) : match.start()]
        right = value[match.end() : min(len(value), match.end() + 18)]
        context = f"{left}{right}"
        has_coverage_context = _contains_rule_16_coverage_keyword(context)
        has_coverage_unit = any(unit_lower.startswith(unit_item.lower()) for unit_item in RULE_16_COVERAGE_UNITS)
        if has_coverage_context and (has_coverage_unit or not unit):
            candidates.append(quantity)

    return max(candidates) if candidates else None


def _contains_rule_16_coverage_keyword(value: str) -> bool:
    normalized = value.lower()
    return any(keyword.lower() in normalized for keyword in RULE_16_COVERAGE_KEYWORDS)


def _is_rule_16_summary_mention(item: ParsedResourceItem) -> bool:
    value = f"{item.source} {item.sheet_name} {item.spec} {item.raw_text}"
    return any(keyword in value for keyword in RULE_16_SUMMARY_KEYWORDS)


def _normalize_rule_16_identity(value: str) -> str:
    return re.sub(r"[\s,，.。;；:：|/\\()（）\[\]【】]+", "", value).lower()


def _item_section(item: ParsedResourceItem) -> str:
    parts = [item.source, item.sheet_name]
    return " / ".join(part for part in parts if part)


def _item_evidence(item: ParsedResourceItem, quantity: float | None = None) -> str:
    prefix = _item_section(item)
    quantity_text = f"；识别数量={quantity:g}" if quantity is not None else f"；识别数量={item.quantity:g}"
    raw = item.raw_text or f"{item.name} {item.spec}".strip()
    if prefix:
        return f"{prefix}：{raw[:220]}{quantity_text}"
    return f"{raw[:220]}{quantity_text}"


def _unique_limited(values: Iterable[str], limit: int = 6) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
        if len(output) >= limit:
            break
    return output


def _join_sections(values: Iterable[str], limit: int = 4) -> str:
    sections = _unique_limited(values, limit=limit)
    return "；".join(sections) if sections else "资源申请清单"



def _match_rule_15_service(item: ParsedResourceItem) -> str | None:
    """Recognize services covered by rule 15 from the resource name only."""

    service_name = _match_alias(item.name, SECURITY_SERVICE_ALIASES)
    if service_name == "安全防病毒服务" and _rule_15_antivirus_is_firewall_feature(item):
        return None
    return service_name


def _rule_15_antivirus_is_firewall_feature(item: ParsedResourceItem) -> bool:
    """Firewall products that merely list anti-virus capability are not anti-virus services."""

    full_text = f"{item.name} {item.spec} {item.raw_text}"
    if "防病毒" not in full_text and "病毒防护" not in full_text:
        return False
    name_text = item.name or ""
    if "安全防病毒服务" in name_text or "病毒防护" in name_text:
        return False
    return any(keyword.lower() in full_text.lower() for keyword in RULE_15_FIREWALL_FEATURE_KEYWORDS)


def _classify_rule_15_source_bucket(item: ParsedResourceItem) -> str | None:
    """Classify rule 15 source using source/table name only, not row descriptions."""

    return _classify_source_bucket(f"{item.source} {item.sheet_name}")


def _merge_rule_15_quantities(grouped: dict[str, dict[str, float]]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for quantities in grouped.values():
        for bucket, value in quantities.items():
            merged[bucket] = merged.get(bucket, 0.0) + value
    return merged


def _flatten_rule_15_rows(rows: dict[str, list[int]]) -> list[int]:
    flattened: list[int] = []
    for row_list in rows.values():
        flattened.extend(row_list)
    return sorted(set(flattened))


def _match_alias(value: str, alias_groups: dict[str, tuple[str, ...]]) -> str | None:
    """把原始资源名称映射到规则内部的标准名称。"""

    normalized = value.lower()
    for canonical, aliases in alias_groups.items():
        if any(alias.lower() in normalized for alias in aliases):
            return canonical

    best_name: str | None = None
    best_score = 0.0
    for canonical, aliases in alias_groups.items():
        score = _best_bigram_alias_score(value, (*aliases, canonical))
        if score > best_score:
            best_name = canonical
            best_score = score

    return best_name if best_score >= 0.58 else None


def _best_bigram_alias_score(value: str, aliases: tuple[str, ...]) -> float:
    return max((_bigram_similarity(value, alias) for alias in aliases), default=0.0)


def _bigram_similarity(left: str, right: str) -> float:
    left_grams = _char_bigrams(left)
    right_grams = _char_bigrams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _char_bigrams(value: str) -> set[str]:
    compact = re.sub(r"[\s,，.。;；:：|/\\()（）\[\]【】《》<>_\-－—、]+", "", str(value or "")).lower()
    if len(compact) < 2:
        return set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def _classify_source_bucket(value: str) -> str | None:
    """把原始来源文本归类为第 15 行规则要求的三类清单之一。"""

    normalized = value.lower()
    for bucket, aliases in SOURCE_BUCKET_ALIASES.items():
        if any(alias.lower() in normalized for alias in aliases):
            return bucket

    best_bucket: str | None = None
    best_score = 0.0
    for bucket, aliases in SOURCE_BUCKET_ALIASES.items():
        score = _best_bigram_alias_score(value, (*aliases, bucket))
        if score > best_score:
            best_bucket = bucket
            best_score = score

    return best_bucket if best_score >= 0.62 else None


def _format_quantities(quantities: dict[str, float]) -> str:
    """把数量明细格式化为人可读文本。"""

    return "，".join(f"{source}={quantity:g}" for source, quantity in quantities.items())
