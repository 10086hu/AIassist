# -*- coding: utf-8 -*-
"""建设依据审查 (Construction Basis Validator)

合并了以下 4 项一致性校验规则：
  1. 投资总额一致性校验 (InvestmentConsistencyValidator)
  2. 指标一致性校验 (IndicatorConsistencyValidator)
  3. 安全内容一致性校验 (SecurityConsistencyValidator)
  4. 功能模块与预算对应校验 (ModuleBudgetConsistencyValidator)

各子规则对应关系：
  - 规则1 → _validate_investment (ICC-*)
  - 规则2 → _validate_indicator (IDC-*)
  - 规则3 → _validate_security (SCC-*)
  - 规则4 → _validate_module_budget (MBC-*)

所有子规则共享基类 BaseValidator，通过统一的 validate(document) 方法
返回聚合后的 ValidationResult。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Set

from app.modules.shanghai_review.base import (
    BaseValidator,
    ErrorCode,
    ValidationError,
    ValidationResult,
)
# 二类费用阶梯表（内嵌，不依赖外部文件）
_SECOND_CLASS_TOTAL_CAP_RATE = 0.12
_INTEGRATION_RATE = 0.06

_CONSULTING_TIERS = [
    (10, 300, 0.03, 0), (300, 1000, 0.025, 9), (1000, 3000, 0.02, 26.5),
    (3000, 10000, 0.015, 66.5), (10000, 20000, 0.01, 171.5),
    (20000, 30000, 0.005, 271.5), (30000, float("inf"), 0.002, 321.5),
]
_SUPERVISION_POINTS = [
    (300, 9), (500, 11.7), (1000, 13.4), (3000, 23.6), (5000, 40.9),
    (10000, 75.1), (20000, 161.7), (30000, 280.8), (40000, 430.0), (50000, 563.3),
]
_TESTING_TIERS = [
    (10, 100, 0.03, 0), (100, 500, 0.028, 3), (500, 1000, 0.025, 14.3),
    (1000, float("inf"), 0.02, 26.7),
]
_SECURITY_TIERS = [
    (10, 100, 0, 2), (100, 1000, 0.02, 2), (1000, 3000, 0.018, 20),
    (3000, float("inf"), 0.015, 56),
]
_CRYPTO_TIERS = [
    (10, 100, 0, 2), (100, 1000, 0.02, 2), (1000, 3000, 0.018, 20),
    (3000, float("inf"), 0.015, 56),
]


def _calc_tiered(base, tiers, cap=None, floor=None):
    fee = 0.0
    for lo, hi, rate, intercept in tiers:
        if base <= lo:
            break
        if lo < base <= hi or hi == float("inf"):
            fee = intercept + (base - lo) * rate
            break
    if cap is not None:
        fee = min(fee, cap)
    if floor is not None:
        fee = max(fee, floor)
    return round(fee, 4)


def _calculate_second_class_fees(direct_cost, hw_sw_cost, project_type="new"):
    """内嵌的二类费用计算器。返回 dict 包含 items / service_fees_total / integration_fee 等。"""
    consulting = _calc_tiered(direct_cost, _CONSULTING_TIERS)
    # supervision
    base = direct_cost
    if base < 300:
        supervision = round(base * 0.03, 4)
    else:
        pts = _SUPERVISION_POINTS
        if base >= pts[-1][0]:
            last_x, last_y = pts[-1]; prev_x, prev_y = pts[-2]
            marginal = (last_y - prev_y) / (last_x - prev_x)
            supervision = round(last_y + (base - last_x) * marginal, 4)
        else:
            supervision = 0.0
            for i in range(len(pts) - 1):
                x1, y1 = pts[i]; x2, y2 = pts[i + 1]
                if x1 <= base <= x2:
                    supervision = round(y1 + (y2 - y1) / (x2 - x1) * (base - x1), 4)
                    break
    testing = _calc_tiered(direct_cost, _TESTING_TIERS, cap=100.0, floor=1.0)
    integration = round(hw_sw_cost * _INTEGRATION_RATE, 4)
    security = _calc_tiered(direct_cost, _SECURITY_TIERS, cap=150.0)
    crypto = _calc_tiered(direct_cost, _CRYPTO_TIERS, cap=150.0)
    service_total = consulting + supervision + testing + security + crypto
    cap_limit = round(direct_cost * _SECOND_CLASS_TOTAL_CAP_RATE, 4)
    is_within_cap = service_total <= cap_limit
    # fake a result-like object
    items = [
        type('_FeeItem', (), {'name': '咨询费/方案设计费', 'calculated_fee': consulting})(),
        type('_FeeItem', (), {'name': '工程监理费', 'calculated_fee': supervision})(),
        type('_FeeItem', (), {'name': '软件测试费', 'calculated_fee': testing})(),
        type('_FeeItem', (), {'name': '系统集成费', 'calculated_fee': integration})(),
        type('_FeeItem', (), {'name': '安全测评费', 'calculated_fee': security})(),
        type('_FeeItem', (), {'name': '密码测评费', 'calculated_fee': crypto})(),
    ]
    return type('_FeeResult', (), {
        'items': items, 'direct_cost': direct_cost,
        'service_fees_total': service_total, 'integration_fee': integration,
        'grand_total': service_total + integration,
        'cap_limit': cap_limit, 'is_within_cap': is_within_cap,
    })()


# =============================================================================
# 共享提取工具
# =============================================================================

_AMOUNT_RE = re.compile(r"([\d,]+\.?\d*)\s*万?\s*元?", re.UNICODE)

_LEVEL_PATTERNS = [
    (re.compile(r"(?:等级保护|等保|安全等级|安全保护等级)\s*(?:要求|为|：|:|达到|定为|按)?\s*([一二三四五])级"),
     {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}),
    (re.compile(r"(?:网络安全等级|信息系统安全等级|安全保护等级)\s*[第等]?\s*([2-5])\s*级"), None),
    (re.compile(r"(?:二级|三级|四级|五级)\s*(?:等保|等级保护|安全)"), {"二级": 2, "三级": 3, "四级": 4, "五级": 5}),
]

_HEADING_RE = re.compile(r"^(\d+(?:\.\d+){1,3})\s+(.*)$", re.UNICODE | re.MULTILINE)

_SECURITY_RISK_MAP: Dict[str, FrozenSet[str]] = {
    "身份认证": frozenset({"身份认证", "统一身份", "单点登录", "SSO", "认证服务", "多因素"}),
    "访问控制": frozenset({"访问控制", "权限管理", "RBAC", "ABAC", "授权", "细粒度"}),
    "数据加密": frozenset({"加密", "传输加密", "存储加密", "国密", "SM2", "SM3", "SM4", "TLS"}),
    "安全审计": frozenset({"审计", "日志", "操作记录", "行为审计", "数据库审计"}),
    "数据分类分级": frozenset({"分类分级", "数据分类", "数据分级", "敏感数据", "脱敏"}),
    "网络安全": frozenset({"防火墙", "WAF", "IDS", "IPS", "网络隔离", "DMZ", "VPC", "安全组"}),
    "主机安全": frozenset({"主机安全", "HIDS", "漏洞扫描", "基线核查", "补丁管理"}),
    "备份恢复": frozenset({"备份", "恢复", "容灾", "灾备", "RPO", "RTO", "异地"}),
    "安全测评": frozenset({"安全测评", "等保测评", "渗透测试", "漏洞扫描", "代码审计"}),
    "终端安全": frozenset({"终端安全", "EDR", "防病毒", "终端管控", "准入控制"}),
    "数据安全": frozenset({"数据安全", "数据防泄漏", "DLP", "水印", "数据脱敏"}),
    "密码应用": frozenset({"密码", "国密", "商用密码", "密码测评", "密评"}),
}


def _parse_first_amount(text: str) -> Optional[float]:
    m = _AMOUNT_RE.search(text)
    return float(m.group(1).replace(",", "")) if m else None


def _extract_security_level(text: str) -> Optional[int]:
    for pattern, mapping in _LEVEL_PATTERNS:
        m = pattern.search(text)
        if m:
            raw = m.group(1)
            if mapping:
                return mapping.get(raw)
            try:
                return int(raw)
            except (ValueError, TypeError):
                continue
    return None


def _extract_risk_categories(text: str) -> Set[str]:
    found: Set[str] = set()
    text_lower = text.lower()
    for category, keywords in _SECURITY_RISK_MAP.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                found.add(category)
                break
    return found


def _to_text(sec: Any) -> str:
    """Convert a section value to a single text string."""
    if isinstance(sec, str):
        return sec
    if isinstance(sec, list):
        return "\n".join(
            str(item.get("content", item)) if isinstance(item, dict) else str(item)
            for item in sec
        )
    if isinstance(sec, dict):
        parts: List[str] = []
        for key in ("content", "text", "description", "measures", "risks"):
            if key in sec:
                val = sec[key]
                if isinstance(val, list):
                    parts.extend(str(v) for v in val)
                else:
                    parts.append(str(val))
        return "\n".join(parts) if parts else str(sec)
    return str(sec)


def _approx_equal(a: float, b: float, tolerance: float = 0.01) -> bool:
    if a == 0 and b == 0:
        return True
    if a == 0 or b == 0:
        return abs(a - b) < tolerance
    return abs(a - b) / max(abs(a), abs(b)) < 0.001 or abs(a - b) < tolerance


# =============================================================================
# 子规则 1: 投资总额一致性校验
# =============================================================================

def _validate_investment(document):
    # type: (dict) -> list
    """校验 2.7 投资概况 vs 8.2 预算编制说明 的总金额一致性。

    Checks:
        ICC-001: 2.7 总金额 ≠ 8.2 分项汇总
        ICC-002: 缺少 2.7 或 8.2 章节
        ICC-003: 二类费用分项计算异常
        ICC-004: 5项服务费超 12% 上限
        ICC-005: 系统集成费 ≠ 6% 软硬件购置费
    """
    errors: List[ValidationError] = []

    # --- 提取 2.7 ---
    sec_2_7 = document.get("2.7") or document.get("投资概况")
    if sec_2_7 is None:
        errors.append(ValidationError(
            code=ErrorCode.ICC_SECTION_MISSING,
            message="未找到第2.7节「投资概况」内容。",
            section="2.7", severity="risk",
            suggestion="请确认文档包含投资概况章节。",
        ))
        total_2_7 = None
    else:
        total_2_7 = _parse_first_amount(_to_text(sec_2_7))

    if total_2_7 is None and sec_2_7 is not None:
        errors.append(ValidationError(
            code=ErrorCode.ICC_SECTION_MISSING,
            message="无法从第2.7节中解析投资总额。",
            section="2.7", severity="risk",
            suggestion="请确保2.7节中明确标注投资总金额（万元）。",
        ))

    # --- 提取 8.2 ---
    sec_8_2 = document.get("8.2") or document.get("预算编制说明")
    if sec_8_2 is None:
        errors.append(ValidationError(
            code=ErrorCode.ICC_SECTION_MISSING,
            message="未找到第8.2节「预算编制说明」内容。",
            section="8.2", severity="risk",
            suggestion="请确认文档包含预算编制说明章节。",
        ))
        return errors

    # --- 从 8.2 提取费用明细 ---
    text_8_2 = _to_text(sec_8_2)
    fee_items: Dict[str, float] = {}
    for kw in ["咨询费", "监理费", "测试费", "集成费", "安全测评", "密码测评"]:
        m = re.search(rf"{kw}.*?([\d,]+\.?\d*)", text_8_2)
        if m:
            fee_items[kw] = float(m.group(1).replace(",", ""))

    direct_cost = _parse_first_amount(text_8_2) or 0.0
    hw_sw_cost = direct_cost * 0.4  # 估算，实际应从明细中提取
    reported_total = direct_cost + sum(fee_items.values())

    # --- 核对总金额 ---
    if total_2_7 is not None and reported_total > 0:
        if not _approx_equal(total_2_7, reported_total):
            diff = abs(total_2_7 - reported_total)
            errors.append(ValidationError(
                code=ErrorCode.ICC_TOTAL_MISMATCH,
                message=f"投资总额不一致：2.7节 {total_2_7:.2f}万元 vs 8.2节 {reported_total:.2f}万元，差异 {diff:.2f}万元。",
                section="2.7, 8.2", severity="risk",
                suggestion="请核实两处金额来源，统一修正后重新提交。",
            ))

    # --- 二类费用反算 ---
    if direct_cost > 0:
        fee_result = _calculate_second_class_fees(direct_cost=direct_cost, hw_sw_cost=hw_sw_cost)

        # 逐项核对
        for item_key, reported_val in fee_items.items():
            for item in fee_result.items:
                if item_key in item.name or item.name in item_key:
                    if not _approx_equal(reported_val, item.calculated_fee, tolerance=0.05):
                        errors.append(ValidationError(
                            code=ErrorCode.ICC_FEE_CALC_ERROR,
                            message=f"「{item_key}」计费异常：申报值 {reported_val:.2f}万元，按标准应计 {item.calculated_fee:.2f}万元。",
                            section="8.2", severity="warning",
                            suggestion="请按《二类费用标准》阶梯公式重新核算。",
                        ))
                    break

        # 12% 上限
        if not fee_result.is_within_cap:
            errors.append(ValidationError(
                code=ErrorCode.ICC_SECOND_CLASS_CAP_EXCEEDED,
                message=f"二类费用超标：5项服务费合计 {fee_result.service_fees_total:.2f}万元，超过直接建设费12%上限（{direct_cost * 0.12:.2f}万元）。",
                section="8.2", severity="risk",
                suggestion="请调整服务费用或重新确认直接建设费基数。",
            ))

        # 集成费 6% 检查
        expected_integration = round(hw_sw_cost * 0.06, 4)
        reported_integration = fee_items.get("集成费") or fee_items.get("系统集成费", 0)
        if reported_integration and not _approx_equal(reported_integration, expected_integration, tolerance=0.05):
            errors.append(ValidationError(
                code=ErrorCode.ICC_INTEGRATION_RATE_WRONG,
                message=f"系统集成费应为软硬件购置费的6%：{hw_sw_cost:.2f}万元 × 6% = {expected_integration:.2f}万元，申报 {reported_integration:.2f}万元。",
                section="8.2", severity="warning",
                suggestion="系统集成费基数不应包含软件开发费用。",
            ))

    return errors


# =============================================================================
# 子规则 2: 指标一致性校验
# =============================================================================

def _normalize_name(name: str) -> str:
    name = re.sub(r"\s+", "", name)
    name = name.replace("（", "(").replace("）", ")")
    name = name.replace("：", ":").replace("，", ",")
    return name.lower()


def _validate_indicator(document: Dict[str, Any]) -> List[ValidationError]:
    """校验 2.6 规划指标 vs 4.2 验收标准 的指标名称和目标值一致性。

    Checks:
        IDC-003: 2.6 指标未在 4.2 找到
        IDC-004: 4.2 指标未在 2.6 找到
    """
    errors: List[ValidationError] = []

    sec_2_6 = document.get("2.6") or document.get("规划指标")
    sec_4_2 = document.get("4.2") or document.get("建设目标需求分析")

    text_2_6 = _to_text(sec_2_6) if sec_2_6 else ""
    text_4_2 = _to_text(sec_4_2) if sec_4_2 else ""

    if not text_2_6.strip():
        errors.append(ValidationError(
            code=ErrorCode.IDC_MISSING_IN_2_6,
            message="未找到第2.6节「规划指标」内容。",
            section="2.6", severity="risk",
            suggestion="请确认文档包含规划指标章节。",
        ))

    if not text_4_2.strip():
        errors.append(ValidationError(
            code=ErrorCode.IDC_MISSING_IN_4_2,
            message="未找到第4.2节「建设目标需求分析」内容。",
            section="4.2", severity="risk",
            suggestion="请确认文档包含建设目标需求分析章节。",
        ))

    # 简单的关键词交集检查
    if text_2_6 and text_4_2:
        words_2_6 = set(re.findall(r"[一-鿿]{2,4}", text_2_6))
        words_4_2 = set(re.findall(r"[一-鿿]{2,4}", text_4_2))
        overlap = words_2_6 & words_4_2
        if len(words_2_6) > 0 and len(overlap) / min(len(words_2_6), 50) < 0.3:
            errors.append(ValidationError(
                code=ErrorCode.IDC_MISSING_IN_4_2,
                message="2.6节规划指标与4.2节验收标准中的指标关键词重叠度较低，可能存在指标不一致。",
                section="2.6, 4.2", severity="warning",
                suggestion="请核实4.2节的验收指标是否与2.6节的规划指标保持对应。",
            ))

    return errors


# =============================================================================
# 子规则 3: 安全内容一致性校验
# =============================================================================

def _validate_security(document: Dict[str, Any]) -> List[ValidationError]:
    """校验 4.7 安全需求分析 vs 6.6 安全建设内容 的一致性。

    Checks:
        SCC-001: 安全等级不一致
        SCC-002: 4.7 风险点在 6.6 中未覆盖
        SCC-003: 缺少章节
    """
    errors: List[ValidationError] = []

    sec_4_7 = document.get("4.7") or document.get("安全需求分析")
    sec_6_6 = document.get("6.6") or document.get("安全建设内容")

    if sec_4_7 is None:
        errors.append(ValidationError(
            code=ErrorCode.SCC_SECTION_MISSING,
            message="未找到第4.7节「安全需求分析」内容。",
            section="4.7", severity="risk",
            suggestion="请确认文档包含安全需求分析章节。",
        ))
    if sec_6_6 is None:
        errors.append(ValidationError(
            code=ErrorCode.SCC_SECTION_MISSING,
            message="未找到第6.6节「安全建设内容」。",
            section="6.6", severity="risk",
            suggestion="请确认文档包含安全建设内容章节。",
        ))

    if sec_4_7 is None or sec_6_6 is None:
        return errors

    text_4_7 = _to_text(sec_4_7)
    text_6_6 = _to_text(sec_6_6)

    # --- 安全等级一致性 ---
    level_4_7 = _extract_security_level(text_4_7)
    level_6_6 = _extract_security_level(text_6_6)

    if level_4_7 is not None and level_6_6 is not None:
        if level_4_7 != level_6_6:
            errors.append(ValidationError(
                code=ErrorCode.SCC_LEVEL_MISMATCH,
                message=f"安全等级不一致：4.7节描述为等保{level_4_7}级，6.6节描述为等保{level_6_6}级。",
                section="4.7, 6.6", severity="risk",
                suggestion="请确保两处的安全保护等级描述完全一致。",
            ))
    elif level_4_7 is not None and level_6_6 is None:
        errors.append(ValidationError(
            code=ErrorCode.SCC_LEVEL_MISMATCH,
            message=f"4.7节明确了安全等级（等保{level_4_7}级），但6.6节未明确提及安全等级。",
            section="4.7, 6.6", severity="warning",
            suggestion="请在6.6节中明确对应的安全保护等级。",
        ))
    elif level_6_6 is not None and level_4_7 is None:
        errors.append(ValidationError(
            code=ErrorCode.SCC_LEVEL_MISMATCH,
            message=f"6.6节提及安全等级（等保{level_6_6}级），但4.7节未明确。",
            section="4.7, 6.6", severity="warning",
            suggestion="请在4.7节安全需求分析中明确安全保护等级依据。",
        ))

    # --- 风险覆盖 ---
    risks_4_7 = _extract_risk_categories(text_4_7)
    risks_6_6 = _extract_risk_categories(text_6_6)

    if risks_4_7:
        uncovered = risks_4_7 - risks_6_6
        coverage = len(risks_4_7 - uncovered) / len(risks_4_7) if risks_4_7 else 0

        if coverage < 0.6:
            uncovered_list = "、".join(sorted(uncovered)) if uncovered else ""
            errors.append(ValidationError(
                code=ErrorCode.SCC_RISK_NOT_COVERED,
                message=f"安全风险覆盖不足（覆盖率 {coverage:.0%}）：4.7节提出的风险类别「{uncovered_list}」在6.6节中未找到对应的安全措施。",
                section="4.7, 6.6", severity="risk",
                suggestion="请在6.6节补充对应的安全保障措施和建设方案。",
            ))

    return errors


# =============================================================================
# 子规则 4: 功能模块与预算对应校验
# =============================================================================

@dataclass(frozen=True)
class ModuleEntry:
    number: str
    title: str
    parent_number: str
    source_section: str


def _extract_modules_from_text(text: str, section: str) -> List[ModuleEntry]:
    entries: List[ModuleEntry] = []
    for m in _HEADING_RE.finditer(text):
        number = m.group(1)
        title = m.group(2).strip()[:100]
        parts = number.rsplit(".", 1)
        parent = parts[0] if len(parts) > 1 else ""
        entries.append(ModuleEntry(number=number, title=title,
                                    parent_number=parent, source_section=section))
    return entries


def _extract_modules_from_list(items: List[Any], section: str) -> List[ModuleEntry]:
    entries: List[ModuleEntry] = []
    for item in items:
        number = ""
        title = ""
        if isinstance(item, dict):
            number = str(item.get("number", item.get("no", item.get("num", ""))))
            title = str(item.get("title", item.get("name", "")))
        elif isinstance(item, (list, tuple)):
            number = str(item[0]) if len(item) > 0 else ""
            title = str(item[1]) if len(item) > 1 else ""
        else:
            number = ""
            title = str(item)
        if number and title:
            parts = number.rsplit(".", 1)
            parent = parts[0] if len(parts) > 1 else ""
            entries.append(ModuleEntry(number=number, title=title,
                                        parent_number=parent, source_section=section))
    return entries


def _find_partial_match(number: str, index: Dict[str, str], max_depth: int = 2) -> Optional[str]:
    parts = number.split(".")
    for i in range(len(parts), max(0, len(parts) - max_depth), -1):
        prefix = ".".join(parts[:i])
        for key in index:
            if key.startswith(prefix) or prefix.startswith(key):
                return key
    return None


def _parse_modules(sec: Any, section_label: str) -> List[ModuleEntry]:
    if isinstance(sec, list):
        return _extract_modules_from_list(sec, section_label)
    if isinstance(sec, str):
        return _extract_modules_from_text(sec, section_label)
    if isinstance(sec, dict):
        for key in ("modules", "items", "功能点", "function_points", "list"):
            if key in sec:
                return _parse_modules(sec[key], section_label)
        return _extract_modules_from_text(str(sec), section_label)
    return []


def _validate_module_budget(document: Dict[str, Any]) -> List[ValidationError]:
    """校验 6.1 建设内容编号 vs 8.2 功能清单编号 的一一对应。

    Checks:
        MBC-001: 6.1 模块未在 8.2 找到
        MBC-002: 8.2 条目无对应 6.1 模块
        MBC-003: 编号不一致
    """
    errors: List[ValidationError] = []

    sec_6_1 = document.get("6.1") or document.get("建设内容")
    sec_8_2 = document.get("8.2") or document.get("预算编制说明")

    modules_6_1 = _parse_modules(sec_6_1, "6.1") if sec_6_1 else []
    budget_items = _parse_modules(sec_8_2, "8.2") if sec_8_2 else []

    if not modules_6_1:
        errors.append(ValidationError(
            code=ErrorCode.MBC_MODULE_MISSING_IN_BUDGET,
            message="未能从第6.1节解析出功能模块（需包含编号如3.1.1）。",
            section="6.1", severity="warning",
            suggestion="请确保6.1节使用三级编号（如3.1.1）标识各建设模块。",
        ))

    if not budget_items:
        errors.append(ValidationError(
            code=ErrorCode.MBC_MODULE_MISSING_IN_BUDGET,
            message="未能从第8.2节解析出软件开发功能清单。",
            section="8.2", severity="warning",
            suggestion="请确保8.2节包含软件开发功能清单。",
        ))

    if not modules_6_1 or not budget_items:
        return errors

    idx_6_1 = {m.number: m.title for m in modules_6_1}
    idx_8_2 = {m.number: m.title for m in budget_items}

    # 6.1 → 8.2
    for num, title in idx_6_1.items():
        if num not in idx_8_2:
            pm = _find_partial_match(num, idx_8_2)
            if pm is None:
                errors.append(ValidationError(
                    code=ErrorCode.MBC_MODULE_MISSING_IN_BUDGET,
                    message=f"6.1节模块「{num} {title}」在8.2节软件开发功能清单中未找到对应功能点。",
                    section="6.1, 8.2", severity="risk",
                    suggestion="请确认该模块是否有对应的预算/开发条目。",
                ))
            else:
                errors.append(ValidationError(
                    code=ErrorCode.MBC_NUMBER_MISALIGN,
                    message=f"6.1「{num}」与8.2「{pm}」编号不完全一致。",
                    section="6.1, 8.2", severity="warning",
                    suggestion="请统一两处使用的功能点编号。",
                ))

    # 8.2 → 6.1
    for num, title in idx_8_2.items():
        if num not in idx_6_1:
            pm = _find_partial_match(num, idx_6_1)
            if pm is None:
                errors.append(ValidationError(
                    code=ErrorCode.MBC_BUDGET_ITEM_NO_MODULE,
                    message=f"8.2节功能点「{num} {title}」在6.1节建设内容中未找到对应模块。",
                    section="6.1, 8.2", severity="warning",
                    suggestion="请确认该功能点是否属于本项目范围。",
                ))

    return errors


# =============================================================================
# 聚合校验器: 建设依据审查
# =============================================================================

class ConstructionBasisValidator(BaseValidator):
    """建设依据审查 — 聚合了 4 项一致性校验子规则。

    子规则:
        1. 投资总额一致性校验 (ICC-*)
        2. 指标一致性校验 (IDC-*)
        3. 安全内容一致性校验 (SCC-*)
        4. 功能模块与预算对应校验 (MBC-*)

    用法:
        >>> validator = ConstructionBasisValidator()
        >>> result = validator.validate(document)
        >>> print(result.is_valid)
    """

    validator_id: str = "construction_basis"
    validator_name: str = "建设依据审查"

    def __init__(self) -> None:
        pass  # 二类费用计算已内嵌，无需外部依赖

    def validate(self, document: Dict[str, Any]) -> ValidationResult:
        all_errors: List[ValidationError] = []

        # 规则1: 投资总额一致性
        all_errors.extend(_validate_investment(document))

        # 规则2: 指标一致性
        all_errors.extend(_validate_indicator(document))

        # 规则3: 安全内容一致性
        all_errors.extend(_validate_security(document))

        # 规则4: 功能模块与预算对应
        all_errors.extend(_validate_module_budget(document))

        return ValidationResult.from_errors(all_errors)
