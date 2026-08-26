from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.modules.price.parser import ParsedPriceItem, PriceProjectContext


FEE_ALIASES: dict[str, tuple[str, ...]] = {
    "consulting": ("咨询费", "方案设计费", "咨询（或方案设计）费", "咨询或方案设计费"),
    "supervision": ("工程监理费", "监理费"),
    "software_testing": ("软件测试费", "软件测评费", "第三方软件测试费", "软测费"),
    "integration": ("系统集成费",),
    "security_assessment": ("安全测评费", "安全评估费"),
    "grade_assessment": ("等级保护测评费", "等保测评费", "等级测评费"),
    "crypto_assessment": ("密码应用测评费", "密码测评费", "密测费"),
    "crypto_security_evaluation": ("密码应用安全性评估费", "密码应用安全评估费"),
}


@dataclass(frozen=True)
class FeeEvaluation:
    fee_key: str
    fee_name: str
    declared_amount: float | None
    standard_amount: float | None
    status: str
    reason: str
    suggestion: str
    base_amount: float | None = None
    source_item: ParsedPriceItem | None = None


@lru_cache(maxsize=1)
def load_municipal_class2_standard() -> dict[str, Any]:
    path = Path(__file__).with_name("data") / "shanghai_municipal_class2_fees.json"
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_class2_fees(
    items: list[ParsedPriceItem],
    context: PriceProjectContext,
    project_level: str,
) -> tuple[list[FeeEvaluation], dict[str, Any]]:
    standard = load_municipal_class2_standard()
    declared = _declared_fees(items)
    evaluations: list[FeeEvaluation] = []

    if not declared:
        evaluations.append(
            FeeEvaluation(
                fee_key="class2_fee_scope",
                fee_name="二类费用清单识别",
                declared_amount=None,
                standard_amount=None,
                status="manual",
                reason="未在价格相关表格中识别到咨询、监理、软件测试、系统集成、安全/等保或密码测评费用。",
                suggestion="请确认报告包含投资估算总表及二类费用明细，并保留清晰的费用名称和金额列。",
            )
        )
        return evaluations, _standard_meta(standard, context)

    if "市级" not in project_level:
        for fee_key, item in declared.items():
            evaluations.append(
                FeeEvaluation(
                    fee_key=fee_key,
                    fee_name=_fee_name(fee_key, standard),
                    declared_amount=_declared_amount(item),
                    standard_amount=None,
                    status="manual",
                    reason="当前附件仅适用于上海市市级项目，无法用于区级或未明确层级的项目。",
                    suggestion="请补充对应项目层级的二类费用配置标准。",
                    source_item=item,
                )
            )
        return evaluations, _standard_meta(standard, context)

    for fee_key, item in declared.items():
        declared_amount = _declared_amount(item)
        rule_key = fee_key
        if fee_key == "grade_assessment":
            if context.security_level == 2:
                rule_key = "grade_assessment_level2"
            elif context.security_level == 3:
                rule_key = "grade_assessment_level3"
            else:
                evaluations.append(
                    FeeEvaluation(
                        fee_key=fee_key,
                        fee_name="等级保护测评费",
                        declared_amount=declared_amount,
                        standard_amount=None,
                        status="manual",
                        reason="报告中未识别到等保二级或三级，无法选择对应测评标准。",
                        suggestion="请明确系统等级保护级别并核对测评费用。",
                        source_item=item,
                    )
                )
                continue

        evaluation = _evaluate_one(rule_key, fee_key, item, declared_amount, context, standard)
        evaluations.append(evaluation)

    if "security_assessment" in declared and "grade_assessment" in declared and context.project_type != "operation":
        duplicate_amount = sum(filter(None, (_declared_amount(declared["security_assessment"]), _declared_amount(declared["grade_assessment"]))))
        evaluations.append(
            FeeEvaluation(
                fee_key="security_grade_mutual_exclusion",
                fee_name="安全测评费与等保测评费互斥校验",
                declared_amount=duplicate_amount,
                standard_amount=None,
                status="fail",
                reason="同一建设项目同时申报了安全测评费和等级保护测评费，标准规定两者不能重复申报。",
                suggestion="保留一种符合项目实际情况的测评费用并删除重复申报项。",
            )
        )

    combined = _combined_limit_evaluation(declared, context, standard)
    if combined is not None:
        evaluations.append(combined)
    return evaluations, _standard_meta(standard, context)


def _evaluate_one(
    rule_key: str,
    fee_key: str,
    item: ParsedPriceItem,
    declared_amount: float | None,
    context: PriceProjectContext,
    standard: dict[str, Any],
) -> FeeEvaluation:
    rule = standard["rules"][rule_key]
    fee_name = str(rule["name"])
    allowed_types = rule.get("project_types") or []
    if allowed_types and context.project_type not in allowed_types:
        project_label = "运维" if context.project_type == "operation" else "建设" if context.project_type == "construction" else "未识别"
        return FeeEvaluation(
            fee_key=fee_key,
            fee_name=fee_name,
            declared_amount=declared_amount,
            standard_amount=None,
            status="fail" if context.project_type != "unknown" else "manual",
            reason=f"{fee_name}不适用于当前识别的{project_label}项目类型。",
            suggestion="核对项目类型和费用类别；如项目类型识别不准确，请在报告中明确建设/运维属性。",
            source_item=item,
        )

    base_key = str(rule.get("base") or "")
    base_amount = getattr(context, base_key, None)
    if base_amount is None or base_amount <= 0:
        return FeeEvaluation(
            fee_key=fee_key,
            fee_name=fee_name,
            declared_amount=declared_amount,
            standard_amount=None,
            status="manual",
            reason=f"未识别到{_base_label(base_key)}，无法计算{fee_name}标准上限。",
            suggestion=f"请在投资估算表中明确{_base_label(base_key)}。",
            source_item=item,
        )

    base_wan = base_amount / 10_000
    if base_wan < 10:
        return FeeEvaluation(
            fee_key=fee_key,
            fee_name=fee_name,
            declared_amount=declared_amount,
            standard_amount=None,
            status="manual",
            reason=f"{_base_label(base_key)}为{base_wan:.2f}万元，低于附件计费表最低10万元的覆盖范围。",
            suggestion="请按主管部门确认的小额项目计费口径人工复核。",
            base_amount=base_amount,
            source_item=item,
        )

    if rule_key == "supervision":
        standard_wan = _supervision_amount(base_wan, rule)
        if standard_wan is None:
            external = rule.get("external_standard") or "外部标准"
            return FeeEvaluation(
                fee_key=fee_key,
                fee_name=fee_name,
                declared_amount=declared_amount,
                standard_amount=None,
                status="manual",
                reason=f"计费基数为{base_wan:.2f}万元，超出附件控制数表可自动计算范围。6亿元以上还需参照{external}。",
                suggestion="补充适用的监理费控制数或外部标准计算依据。",
                base_amount=base_amount,
                source_item=item,
            )
    elif "maximum_rate" in rule:
        standard_wan = base_wan * float(rule["maximum_rate"])
    else:
        standard_wan = _tier_amount(base_wan, rule.get("tiers") or [])
        if standard_wan is None:
            return FeeEvaluation(
                fee_key=fee_key,
                fee_name=fee_name,
                declared_amount=declared_amount,
                standard_amount=None,
                status="manual",
                reason=f"计费基数{base_wan:.2f}万元不在附件可计算区间。",
                suggestion="请人工核对该费用的计价依据。",
                base_amount=base_amount,
                source_item=item,
            )
        if rule_key == "consulting" and context.application_development_ratio is not None and context.application_development_ratio <= float(rule["infrastructure_application_ratio_max"]):
            standard_wan *= float(rule["infrastructure_adjustment"])

    if rule.get("minimum") is not None:
        standard_wan = max(standard_wan, float(rule["minimum"]))
    if rule.get("maximum") is not None:
        standard_wan = min(standard_wan, float(rule["maximum"]))
    standard_amount = round(standard_wan * 10_000, 2)

    if declared_amount is None:
        return FeeEvaluation(
            fee_key=fee_key,
            fee_name=fee_name,
            declared_amount=None,
            standard_amount=standard_amount,
            status="manual",
            reason=f"已计算标准上限为{standard_wan:.2f}万元，但申报项缺少金额。",
            suggestion="请补充申报金额后重新核验。",
            base_amount=base_amount,
            source_item=item,
        )
    tolerance = max(100, standard_amount * 0.005)
    if declared_amount > standard_amount + tolerance:
        return FeeEvaluation(
            fee_key=fee_key,
            fee_name=fee_name,
            declared_amount=declared_amount,
            standard_amount=standard_amount,
            status="fail",
            reason=f"申报金额{declared_amount / 10_000:.2f}万元，高于标准测算上限{standard_wan:.2f}万元。",
            suggestion="按标准上限调整申报金额，或补充经主管部门确认的特殊计价依据。",
            base_amount=base_amount,
            source_item=item,
        )
    return FeeEvaluation(
        fee_key=fee_key,
        fee_name=fee_name,
        declared_amount=declared_amount,
        standard_amount=standard_amount,
        status="pass",
        reason=f"申报金额{declared_amount / 10_000:.2f}万元，未超过标准测算上限{standard_wan:.2f}万元。",
        suggestion="",
        base_amount=base_amount,
        source_item=item,
    )


def _combined_limit_evaluation(
    declared: dict[str, ParsedPriceItem],
    context: PriceProjectContext,
    standard: dict[str, Any],
) -> FeeEvaluation | None:
    relevant_keys = ("consulting", "supervision", "software_testing", "crypto_assessment", "crypto_security_evaluation")
    amounts = [_declared_amount(declared[key]) for key in relevant_keys if key in declared]
    security_amount = _declared_amount(declared["security_assessment"]) if "security_assessment" in declared else None
    grade_amount = _declared_amount(declared["grade_assessment"]) if "grade_assessment" in declared else None
    if security_amount is not None or grade_amount is not None:
        amounts.append(max(security_amount or 0, grade_amount or 0))
    amounts = [amount for amount in amounts if amount is not None]
    if not amounts:
        return None
    if not context.total_investment:
        return FeeEvaluation(
            fee_key="combined_12_percent_limit",
            fee_name="五项二类费用合计占比校验",
            declared_amount=sum(amounts),
            standard_amount=None,
            status="manual",
            reason="未识别到项目总投资，无法校验五项费用合计不超过总投资12%的规则。",
            suggestion="请在报告中明确项目总投资。",
        )
    maximum = context.total_investment * float(standard["combined_limit"]["maximum_rate_of_total_investment"])
    total = sum(amounts)
    if total > maximum + max(100, maximum * 0.005):
        return FeeEvaluation(
            fee_key="combined_12_percent_limit",
            fee_name="五项二类费用合计占比校验",
            declared_amount=total,
            standard_amount=maximum,
            status="fail",
            reason=f"五项费用合计{total / 10_000:.2f}万元，超过项目总投资12%的上限{maximum / 10_000:.2f}万元。",
            suggestion="压减相关二类费用或补充主管部门认可的特殊依据。",
        )
    return FeeEvaluation(
        fee_key="combined_12_percent_limit",
        fee_name="五项二类费用合计占比校验",
        declared_amount=total,
        standard_amount=maximum,
        status="pass",
        reason=f"五项费用合计{total / 10_000:.2f}万元，未超过项目总投资12%的上限{maximum / 10_000:.2f}万元。",
        suggestion="",
    )


def _declared_fees(items: list[ParsedPriceItem]) -> dict[str, ParsedPriceItem]:
    output: dict[str, ParsedPriceItem] = {}
    for item in items:
        text = f"{item.category} {item.item_name}"
        for key, aliases in FEE_ALIASES.items():
            if any(alias in text for alias in aliases):
                current = output.get(key)
                if current is None or (_declared_amount(item) or 0) > (_declared_amount(current) or 0):
                    output[key] = item
                break
    return output


def _declared_amount(item: ParsedPriceItem) -> float | None:
    if item.total_price is not None:
        return item.total_price
    if item.unit_price is not None:
        return item.unit_price * (item.quantity or 1)
    return None


def _tier_amount(base_wan: float, tiers: list[list[Any]]) -> float | None:
    for lower, upper, accumulated, rate in tiers:
        lower = float(lower)
        if base_wan < lower:
            continue
        if upper is not None and base_wan > float(upper):
            continue
        return float(accumulated) + (base_wan - lower) * float(rate)
    return None


def _supervision_amount(base_wan: float, rule: dict[str, Any]) -> float | None:
    if base_wan < 300:
        return base_wan * float(rule["below_300_rate"])
    points = [(float(x), float(y)) for x, y in rule["control_points"]]
    if base_wan > points[-1][0]:
        return None
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        if x1 <= base_wan <= x2:
            return y1 + (y2 - y1) * (base_wan - x1) / (x2 - x1)
    return points[0][1] if base_wan == points[0][0] else None


def _fee_name(fee_key: str, standard: dict[str, Any]) -> str:
    rule_key = fee_key if fee_key in standard["rules"] else "grade_assessment_level2" if fee_key == "grade_assessment" else fee_key
    return str(standard["rules"].get(rule_key, {}).get("name") or fee_key)


def _base_label(base_key: str) -> str:
    return {
        "direct_construction_cost": "项目直接建设费",
        "software_development_cost": "软件开发费用",
        "hardware_software_purchase_cost": "软硬件购置费（不含应用软件开发费）",
    }.get(base_key, base_key)


def _standard_meta(standard: dict[str, Any], context: PriceProjectContext) -> dict[str, Any]:
    return {
        "standard_id": standard["standard_id"],
        "standard_title": standard["title"],
        "standard_scope": standard["scope"],
        "standard_source_file": standard["source_file"],
        "standard_source_sha256": standard["source_sha256"],
        "project_context": context.public_dict(),
    }
