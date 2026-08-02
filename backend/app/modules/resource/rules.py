from __future__ import annotations

from dataclasses import dataclass
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
    "安全认证网关服务": ("安全认证网关服务", "认证网关", "安全网关"),
    "时间戳服务": ("时间戳服务", "时间戳"),
    "签名验签服务": ("签名验签服务", "签名验签", "电子签名", "验签"),
    "可信密码服务（数据加解密服务）": ("可信密码服务", "数据加解密服务", "加解密", "加密服务", "解密服务"),
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

# 第 16 行规则关注“服务器 -> 操作系统”“数据库服务器 -> 数据库”的一一配置关系。
# 这些别名用于从资源名称和规格中识别相关资源。
SERVER_ALIASES = ("服务器", "云服务器", "虚拟机", "计算资源", "应用服务器", "主机")
DATABASE_SERVER_ALIASES = ("数据库服务器", "数据库主机", "db服务器", "DB服务器")
OS_ALIASES = ("操作系统", "服务器操作系统", "os", "OS", "麒麟", "统信", "欧拉")
DATABASE_ALIASES = ("数据库软件", "数据库", "mysql", "postgresql", "oracle", "达梦", "人大金仓", "gaussdb")
MAX_REASONABLE_COMPARE_QUANTITY = 10000.0


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

    for item in items:
        # 第一步：判断资源名称是否属于本规则关注的安全服务。
        service_name = _match_alias(item.name, SECURITY_SERVICE_ALIASES)
        if not service_name:
            continue

        # 第二步：判断该资源来自安全服务需求表、PaaS服务清单还是密码服务资源内容清单。
        bucket = _classify_source_bucket(f"{item.source} {item.sheet_name} {item.spec}")
        if not bucket:
            bucket = "未识别来源"

        # 第三步：同一服务、同一来源可能有多行，数量需要累加。
        grouped.setdefault(service_name, {})
        rows.setdefault(service_name, [])
        grouped[service_name][bucket] = grouped[service_name].get(bucket, 0.0) + item.quantity
        rows[service_name].append(item.row_index)

    if not grouped:
        # 完全没识别到相关安全服务时，不判定为失败，而是提示资料不足/无法判断。
        return [
            ResourceRuleFinding(
                rule_code=RULE_15_CODE,
                rule_name=RULE_15_NAME,
                resource_name="安全服务关联数量",
                severity="需人工确认",
                result_label="无法判断",
                reason="未识别到安全防病毒、认证网关、时间戳、签名验签、可信密码、身份认证或数字证书等服务数量。",
                suggestion="请在清单中提供服务名称、来源清单和数量字段，或补充安全服务需求表、PaaS服务清单、密码服务资源内容清单。",
                source_quantities={},
                row_indexes=[],
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
                )
            )
    return findings


def check_major_component_quantities(items: list[ParsedResourceItem]) -> list[ResourceRuleFinding]:
    """执行第 16 行规则。

    第 16 行实际上包含两个数量关系：
    - 每新申请一台服务器，需要配置一套操作系统。
    - 每申请一台数据库服务器，需要配置一个数据库。
    """

    # 普通服务器数量：排除“操作系统”“数据库”等会误命中的条目。
    server_total, server_rows = _sum_by_alias(items, SERVER_ALIASES, exclude=OS_ALIASES + DATABASE_ALIASES)
    # 数据库服务器数量：只匹配数据库服务器相关别名。
    db_server_total, db_server_rows = _sum_by_alias(items, DATABASE_SERVER_ALIASES)
    # 操作系统数量：用于和服务器数量比较。
    os_total, os_rows = _sum_by_alias(items, OS_ALIASES)
    # 数据库软件数量：排除“数据库服务器”，避免把服务器误算成数据库软件。
    database_total, database_rows = _sum_by_alias(items, DATABASE_ALIASES, exclude=DATABASE_SERVER_ALIASES)

    findings = [
        _compare_quantity(
            rule_code=RULE_16_OS_CODE,
            resource_name="服务器与操作系统",
            expected_name="服务器数量",
            actual_name="操作系统数量",
            expected=server_total,
            actual=os_total,
            row_indexes=server_rows + os_rows,
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
    )


def _sum_by_alias(
    items: Iterable[ParsedResourceItem],
    aliases: tuple[str, ...],
    exclude: tuple[str, ...] = (),
) -> tuple[float, list[int]]:
    """按关键词别名汇总数量，并返回参与汇总的原始行号。"""

    total = 0.0
    rows: list[int] = []
    for item in items:
        text = f"{item.name} {item.spec}".lower()
        # 排除词优先，防止“服务器操作系统”被当成普通服务器。
        if any(alias.lower() in text for alias in exclude):
            continue
        if any(alias.lower() in text for alias in aliases):
            if 0 < item.quantity <= MAX_REASONABLE_COMPARE_QUANTITY:
                total += item.quantity
                rows.append(item.row_index)
    return total, rows


def _match_alias(value: str, alias_groups: dict[str, tuple[str, ...]]) -> str | None:
    """把原始资源名称映射到规则内部的标准名称。"""

    normalized = value.lower()
    for canonical, aliases in alias_groups.items():
        if any(alias.lower() in normalized for alias in aliases):
            return canonical
    return None


def _classify_source_bucket(value: str) -> str | None:
    """把原始来源文本归类为第 15 行规则要求的三类清单之一。"""

    normalized = value.lower()
    for bucket, aliases in SOURCE_BUCKET_ALIASES.items():
        if any(alias.lower() in normalized for alias in aliases):
            return bucket
    return None


def _format_quantities(quantities: dict[str, float]) -> str:
    """把数量明细格式化为人可读文本。"""

    return "，".join(f"{source}={quantity:g}" for source, quantity in quantities.items())
