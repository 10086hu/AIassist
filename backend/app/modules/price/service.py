from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy.orm import Session

from app.db.models import PriceBenchmark, PriceItem, Project
from app.modules.price.parser import (
    ParsedPriceDocument,
    ParsedPriceItem,
    parse_benchmark_records,
    parse_price_document,
)
from app.modules.price.standards import FeeEvaluation, evaluate_class2_fees, load_municipal_class2_standard


PRICE_REASON_001 = {
    "rule_id": "PRICE_REASON_001",
    "rule_name": "二类费用计量计价规则",
    "rule_category": "计量计价规则",
    "rule_detail": "依据《上海市市级数字化项目配置标准（二类费用）》检查咨询、监理、软件测试、系统集成、安全/等保和密码测评费用，并校验12%合计上限。",
}
PRICE_REF_001 = {
    "rule_id": "PRICE_REF_001",
    "rule_name": "应用软件开发投资估算明细规则",
    "rule_category": "计量计价规则",
    "rule_detail": "功能人月单价为2.5万元；数字化新技术应用可调整为3万元；单个功能点工作量为1至5人月。",
}
PRICE_REF_002 = {
    "rule_id": "PRICE_REF_002",
    "rule_name": "软硬件产品计量计价规则",
    "rule_category": "计量计价规则",
    "rule_detail": "产品价格按名称、品牌、型号和规格匹配价格参考库，比较申报单价与参考价格差异。",
}
PRICE_RULES = (PRICE_REASON_001, PRICE_REF_001, PRICE_REF_002)
PRICE_EXTENSIONS = {".xlsx", ".xlsm", ".csv", ".docx", ".pdf"}
SOFTWARE_UNIT_PRICE = 25000.0
NEW_TECH_UNIT_PRICE = 30000.0
NEW_TECH_KEYWORDS = (
    "人工智能", "大模型", "区块链", "数字孪生", "物联网", "知识图谱", "隐私计算",
    "机器学习", "深度学习", "自然语言处理", "智能识别", "智能分析", "智能问答",
    "知识库", "模型训练", "算法模型", "OCR", "NLP", "语音识别", "图像识别",
)
SOFTWARE_KEYWORDS = ("软件开发", "应用软件", "功能点", "软件系统", "开发明细", "人月")
FEE_KEYWORDS = ("二类费用", "咨询费", "设计费", "监理费", "系统集成费", "测试费", "培训费", "实施费", "运维费", "服务费")
PRODUCT_KEYWORDS = ("服务器", "存储", "交换机", "防火墙", "数据库", "中间件", "终端", "设备", "软件")


def public_price_rules() -> list[dict[str, str]]:
    standard = load_municipal_class2_standard()
    rules = []
    for rule in PRICE_RULES:
        public = dict(rule, source="local_builtin")
        if rule["rule_id"] == PRICE_REASON_001["rule_id"]:
            public.update(
                standard_id=standard["standard_id"],
                standard_title=standard["title"],
                standard_source_sha256=standard["source_sha256"],
            )
        rules.append(public)
    return rules


def run_price_check_from_file(
    db: Session,
    content: bytes,
    filename: str,
    project_id: str | None,
    project_name: str,
    department: str | None,
    selected_rule_ids: Sequence[str] | None = None,
    project_level: str = "市级项目",
    parsed_document: ParsedPriceDocument | None = None,
) -> dict[str, Any]:
    if Path(filename).suffix.lower() not in PRICE_EXTENSIONS:
        raise ValueError(f"价格检查不支持该文件格式：{filename}")
    parsed = parsed_document or parse_price_document(content, filename)
    items = parsed.items
    project = _get_or_create_project(db, project_id, project_name, department)
    _replace_price_items(db, project.id, items)
    selected = {str(value).strip() for value in selected_rule_ids or [] if str(value).strip()}
    rules = [rule for rule in PRICE_RULES if not selected or rule["rule_id"] in selected] or list(PRICE_RULES)
    results = []
    for rule in rules:
        if rule["rule_id"] == PRICE_REASON_001["rule_id"]:
            results.append(_evaluate_reason(items, parsed, project_level))
        elif rule["rule_id"] == PRICE_REF_001["rule_id"]:
            results.append(_evaluate_software(items))
        else:
            results.append(_evaluate_products(db, items))
    findings = [issue for result in results for issue in result["issues"]]
    notices = [notice for result in results for notice in result.get("notices", [])]
    reason_result = next((result for result in results if result["rule_id"] == PRICE_REASON_001["rule_id"]), {})
    product_result = next((result for result in results if result["rule_id"] == PRICE_REF_002["rule_id"]), {})
    return {
        "module_code": "price",
        "module_name": "价格合理性",
        "status": "发现问题" if findings else "部分完成" if notices else "通过",
        "summary": {
            "total_items": len(items),
            "total_findings": len(findings),
            "high_risk": sum(i["risk_level"] == "高" for i in findings),
            "warnings": sum(i["risk_level"] == "中" for i in findings),
            "manual_review": sum(i["risk_level"] == "需人工确认" for i in findings),
            "project_level": project_level,
            "parse_elapsed_seconds": parsed.elapsed_seconds,
            "tables_scanned": parsed.table_count,
            "price_tables_found": parsed.relevant_table_count,
            "parse_warnings": parsed.warnings,
            "project_context": parsed.context.public_dict(),
            "standard_id": reason_result.get("standard_id"),
            "standard_title": reason_result.get("standard_title"),
            "evaluated_fee_count": reason_result.get("evaluated_fee_count", 0),
            "passed_fee_count": reason_result.get("passed_fee_count", 0),
            "benchmark_count": product_result.get("benchmark_count"),
            "matched_product_count": product_result.get("matched_count"),
            "notice_count": len(notices),
            "price_library_status": product_result.get("library_status"),
        },
        "findings": findings,
        "notices": notices,
        "rules_used": [dict(rule, source="local_builtin") for rule in rules],
        "items": [_public_item(item) for item in items],
        "rule_results": results,
        "project_id": project.id,
    }


def import_price_benchmarks(db: Session, records: Iterable[dict[str, Any]]) -> dict[str, int]:
    imported = 0
    updated = 0
    skipped = 0
    for record in records:
        name = _text(record.get("item_name_std") or record.get("item_name") or record.get("name"))
        price = _number(record.get("unit_price"))
        if not name or price is None:
            skipped += 1
            continue
        raw = record.get("raw_data") if isinstance(record.get("raw_data"), dict) else {}
        raw = dict(raw)
        for key in ("spec", "unit", "project_level", "aliases"):
            if record.get(key) is not None:
                raw[key] = record[key]
        source = _text(record.get("source")) or "人工导入"
        brand = _text(record.get("brand"))
        model = _text(record.get("model"))
        existing = (
            db.query(PriceBenchmark)
            .filter(
                PriceBenchmark.source == source,
                PriceBenchmark.item_name_std == name,
                PriceBenchmark.brand == brand,
                PriceBenchmark.model == model,
            )
            .first()
        )
        if existing is not None:
            existing.category = _text(record.get("category")) or None
            existing.unit_price = price
            existing.source_url = _text(record.get("source_url")) or None
            existing.source_date = _text(record.get("source_date")) or None
            existing.is_active = 1 if record.get("is_active", True) else 0
            existing.raw_data = json.dumps(raw, ensure_ascii=False)
            updated += 1
        else:
            db.add(PriceBenchmark(
                source=source,
                category=_text(record.get("category")),
                item_name_std=name,
                brand=brand,
                model=model,
                unit_price=price,
                source_url=_text(record.get("source_url")),
                source_date=_text(record.get("source_date")),
                is_active=1 if record.get("is_active", True) else 0,
                raw_data=json.dumps(raw, ensure_ascii=False),
            ))
            imported += 1
    db.commit()
    return {"imported": imported, "updated": updated, "skipped": skipped}


def import_price_benchmarks_from_file(db: Session, content: bytes, filename: str) -> dict[str, int]:
    records = parse_benchmark_records(content, filename)
    result = import_price_benchmarks(db, records)
    result["parsed"] = len(records)
    return result


def list_price_benchmarks(db: Session, limit: int = 200) -> list[dict[str, Any]]:
    rows = db.query(PriceBenchmark).filter(PriceBenchmark.is_active == 1).order_by(PriceBenchmark.fetched_at.desc()).limit(max(1, min(limit, 1000))).all()
    return [_benchmark_public(row) for row in rows]


def _evaluate_reason(items: list[ParsedPriceItem], parsed: ParsedPriceDocument, project_level: str) -> dict[str, Any]:
    evaluations, metadata = evaluate_class2_fees(items, parsed.context, project_level)
    issues = [_fee_evaluation_issue(evaluation) for evaluation in evaluations if evaluation.status != "pass"]
    passed_count = sum(evaluation.status == "pass" for evaluation in evaluations)
    return {
        **_rule_result(PRICE_REASON_001, issues),
        "evaluated_fee_count": len(evaluations),
        "passed_fee_count": passed_count,
        "fee_evaluations": [_fee_evaluation_public(evaluation) for evaluation in evaluations],
        **metadata,
    }


def _fee_evaluation_issue(evaluation: FeeEvaluation) -> dict[str, Any]:
    item = evaluation.source_item or ParsedPriceItem(item_name=evaluation.fee_name)
    risk = "高" if evaluation.status == "fail" else "需人工确认"
    return _issue(
        item,
        PRICE_REASON_001,
        risk,
        evaluation.reason,
        evaluation.suggestion,
        fee_key=evaluation.fee_key,
        declared_amount=evaluation.declared_amount,
        standard_amount=evaluation.standard_amount,
        calculation_base=evaluation.base_amount,
        result_label="超过标准" if evaluation.status == "fail" else "需人工确认",
    )


def _fee_evaluation_public(evaluation: FeeEvaluation) -> dict[str, Any]:
    return {
        "fee_key": evaluation.fee_key,
        "fee_name": evaluation.fee_name,
        "declared_amount": evaluation.declared_amount,
        "standard_amount": evaluation.standard_amount,
        "base_amount": evaluation.base_amount,
        "status": evaluation.status,
        "reason": evaluation.reason,
        "suggestion": evaluation.suggestion,
    }


def _evaluate_software(items: list[ParsedPriceItem]) -> dict[str, Any]:
    candidates = [item for item in items if _is_detail_item(item) and _is_software(item)]
    new_technology_sections = _new_technology_sections(candidates)
    issues: list[dict[str, Any]] = []
    missing_unit_price: dict[str, list[ParsedPriceItem]] = {}
    unsupported_adjustment: dict[str, list[ParsedPriceItem]] = {}
    for item in candidates:
        if item.effort_person_month is None:
            issues.append(_issue(item, PRICE_REF_001, "需人工确认", "软件功能点缺少人月工作量。", "补充1至5人月的功能点工作量及估算依据。"))
            continue
        if item.effort_person_month < 1 or item.effort_person_month > 5:
            issues.append(_issue(item, PRICE_REF_001, "高", f"功能点工作量为 {item.effort_person_month:g} 人月，超出1至5人月范围。", "拆分功能点或补充超出范围的工作量依据。"))
        if item.unit_price is None:
            missing_unit_price.setdefault(item.source_section or "未标明表格", []).append(item)
        elif item.unit_price > NEW_TECH_UNIT_PRICE * 1.01:
            issues.append(_issue(
                item,
                PRICE_REF_001,
                "中",
                f"申报人月单价为{item.unit_price:g}元，超过数字化新技术可调整单价{NEW_TECH_UNIT_PRICE:g}元。",
                "将人月单价调整至规则范围内，或补充主管部门认可的特殊计价依据。",
            ))
        elif (
            item.unit_price > SOFTWARE_UNIT_PRICE * 1.01
            and not _is_new_technology(item)
            and item.source_section not in new_technology_sections
        ):
            unsupported_adjustment.setdefault(item.source_section or "未标明表格", []).append(item)
        amount_issue = _amount_issue(item, PRICE_REF_001, "软件开发")
        if amount_issue:
            issues.append(amount_issue)

    for source, grouped_items in missing_unit_price.items():
        issues.append(_grouped_software_issue(
            grouped_items,
            source,
            "软件人月单价缺失",
            f"{source}中有{len(grouped_items)}项软件工作量未提供人月单价，无法逐项核验2.5万元基础单价及3万元可调整上限。",
            "请在该表补充统一或逐项人月单价及计价依据后重新检查。",
            result_label="表格缺少单价",
        ))
    for source, grouped_items in unsupported_adjustment.items():
        issues.append(_grouped_software_issue(
            grouped_items,
            source,
            "3万元人月单价依据待确认",
            f"{source}中有{len(grouped_items)}项采用高于2.5万元的人月单价，但文本中未识别到明确的数字化新技术应用依据。",
            "请补充人工智能、大模型、区块链等新技术应用说明，或按2.5万元基础单价核对。",
            result_label="调整依据待确认",
        ))
    return _rule_result(PRICE_REF_001, issues)


def _evaluate_products(db: Session, items: list[ParsedPriceItem]) -> dict[str, Any]:
    candidates = [item for item in items if _is_detail_item(item) and _is_product(item) and not _is_software(item)]
    benchmarks = db.query(PriceBenchmark).filter(PriceBenchmark.is_active == 1).all()
    issues = []
    if candidates and not benchmarks:
        placeholder = candidates[0]
        notice = _notice(
            "PRICE_LIBRARY_EMPTY",
            "价格参考库为空",
            f"已识别 {len(candidates)} 个软硬件产品，但当前价格参考库为空，本次未执行产品市场价比对。",
            "请通过桌面端设置页导入政府采购、产品库或正式询价数据后重新检查。",
            source_section=placeholder.source_section,
            identified_product_count=len(candidates),
        )
        return {
            **_rule_result(PRICE_REF_002, issues),
            "benchmark_count": 0,
            "matched_count": 0,
            "library_status": "empty",
            "notices": [notice],
        }
    matched_count = 0
    for item in candidates:
        match, score = _match(item, benchmarks)
        if match is None:
            issues.append(_issue(item, PRICE_REF_002, "需人工确认", "价格参考库中未找到可靠匹配项。", "补充品牌、型号、规格或提供政府采购/询价依据。", match_score=score))
            continue
        if item.unit_price is None:
            issues.append(_issue(item, PRICE_REF_002, "需人工确认", "产品缺少申报单价。", "补充产品申报单价。", match_score=score))
            continue
        reference = float(match.unit_price)
        matched_count += 1
        ratio = (item.unit_price - reference) / reference if reference else 0
        if ratio > .20:
            risk, label = "高", "明显偏高"
        elif ratio > .10:
            risk, label = "中", "价格偏高"
        elif ratio < -.10:
            risk, label = "需人工确认", "价格偏低，需确认配置"
        else:
            risk, label = "低", "价格基本合理"
        if risk != "低":
            issues.append(_issue(item, PRICE_REF_002, risk, f"申报单价{item.unit_price:g}元，参考单价{reference:g}元，差异{ratio:.1%}。", "补充配置差异、品牌型号和询价依据。", match_score=score, difference_amount=item.unit_price - reference, difference_ratio=ratio, result_label=label, benchmark=_benchmark_public(match)))
    return {
        **_rule_result(PRICE_REF_002, issues),
        "benchmark_count": len(benchmarks),
        "matched_count": matched_count,
        "library_status": "ready",
        "notices": [],
    }


def _rule_result(rule: dict[str, str], issues: list[dict[str, Any]]) -> dict[str, Any]:
    return {"rule_id": rule["rule_id"], "rule_name": rule["rule_name"], "rule_category": rule["rule_category"], "rule_detail": rule["rule_detail"], "passed": not issues, "issues": issues}


def _issue(item: ParsedPriceItem, rule: dict[str, str], risk: str, reason: str, suggestion: str, **extra: Any) -> dict[str, Any]:
    return {"rule_id": rule["rule_id"], "rule_name": rule["rule_name"], "item_name": item.item_name, "risk_level": risk, "severity": risk, "message": reason, "reason": reason, "suggestion": suggestion, "source_section": item.source_section, "source_row": item.source_row, "evidence": item.evidence, **extra}


def _grouped_software_issue(
    items: list[ParsedPriceItem],
    source: str,
    title: str,
    reason: str,
    suggestion: str,
    **extra: Any,
) -> dict[str, Any]:
    first = items[0]
    rows = [item.source_row for item in items if item.source_row is not None]
    return _issue(
        first,
        PRICE_REF_001,
        "需人工确认",
        reason,
        suggestion,
        item_name=title,
        source_section=source,
        source_row=min(rows) if rows else None,
        affected_item_count=len(items),
        affected_row_start=min(rows) if rows else None,
        affected_row_end=max(rows) if rows else None,
        evidence=f"{source}，涉及{len(items)}项；示例：{first.item_name}",
        **extra,
    )


def _notice(code: str, title: str, message: str, action: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "title": title, "message": message, "action": action, **extra}


def _amount_issue(item: ParsedPriceItem, rule: dict[str, str], label: str) -> dict[str, Any] | None:
    multiplier = item.quantity if item.quantity is not None else item.effort_person_month
    if item.total_price is None or item.unit_price is None or multiplier is None:
        return None
    expected = item.unit_price * multiplier
    if expected and abs(item.total_price - expected) / expected > .01:
        return _issue(item, rule, "中", f"{label}合价{item.total_price:g}元与数量×单价{expected:g}元不一致。", "核对数量、单价、合价及金额单位。")
    return None


def _is_fee(item: ParsedPriceItem) -> bool:
    text = f"{item.category} {item.item_name} {item.evidence}"
    return any(k in text for k in FEE_KEYWORDS) or (not _is_software(item) and not _is_product(item))


def _is_detail_item(item: ParsedPriceItem) -> bool:
    if item.category == "投资估算汇总":
        return False
    name = _norm(item.item_name)
    if name in {"合计", "小计", "总计", "累计"}:
        return False
    return re.match(r"^\s*(?:合计|小计|总计|累计)(?:\s*\||\s|$)", item.evidence) is None


def _is_software(item: ParsedPriceItem) -> bool:
    text = f"{item.category} {item.item_name} {item.spec} {item.evidence}"
    return item.effort_person_month is not None or any(k in text for k in SOFTWARE_KEYWORDS)


def _is_new_technology(item: ParsedPriceItem) -> bool:
    text = f"{item.item_name} {item.spec} {item.evidence}".upper()
    return any(keyword.upper() in text for keyword in NEW_TECH_KEYWORDS)


def _new_technology_sections(items: list[ParsedPriceItem]) -> set[str]:
    adjusted_by_section: dict[str, list[ParsedPriceItem]] = {}
    for item in items:
        if item.source_section and item.unit_price is not None and item.unit_price > SOFTWARE_UNIT_PRICE * 1.01:
            adjusted_by_section.setdefault(item.source_section, []).append(item)

    sections: set[str] = set()
    for source, adjusted_items in adjusted_by_section.items():
        evidence_count = sum(_is_new_technology(item) for item in adjusted_items)
        required = 1 if len(adjusted_items) == 1 else max(2, int(len(adjusted_items) * 0.2 + 0.999))
        if evidence_count >= required:
            sections.add(source)
    return sections


def _is_product(item: ParsedPriceItem) -> bool:
    text = f"{item.category} {item.item_name} {item.spec} {item.evidence}"
    return any(k in text for k in PRODUCT_KEYWORDS) or bool(item.brand or item.model or item.spec)


def _match(item: ParsedPriceItem, benchmarks: list[PriceBenchmark]) -> tuple[PriceBenchmark | None, int]:
    best, best_score = None, 0
    for benchmark in benchmarks:
        raw = _raw(benchmark.raw_data)
        score = 0
        name, target = _norm(item.item_name), _norm(benchmark.item_name_std)
        aliases = raw.get("aliases") or []
        if name == target or name in [_norm(value) for value in aliases]:
            score += 50
        elif target and (target in name or name in target):
            score += 35
        if item.brand and benchmark.brand and _norm(item.brand) == _norm(benchmark.brand):
            score += 20
        if item.model and benchmark.model and _norm(item.model) == _norm(benchmark.model):
            score += 20
        spec = _text(raw.get("spec"))
        if item.spec and spec and (_norm(item.spec) in _norm(spec) or _norm(spec) in _norm(item.spec)):
            score += 10
        if score > best_score:
            best, best_score = benchmark, score
    return (best, best_score) if best_score >= 50 else (None, best_score)


def _replace_price_items(db: Session, project_id: str, items: list[ParsedPriceItem]) -> None:
    db.query(PriceItem).filter(PriceItem.project_id == project_id).delete(synchronize_session=False)
    for item in items:
        db.add(PriceItem(
            project_id=project_id,
            item_name=item.item_name[:200],
            brand=item.brand[:100] or None,
            model=item.model[:100] or None,
            quantity=int(item.quantity or 0),
            unit_price=item.unit_price or 0,
            total_price=item.total_price or 0,
            category=item.category[:64] or None,
            remark=json.dumps({
                "spec": item.spec,
                "unit": item.unit,
                "effort_person_month": item.effort_person_month,
                "source_section": item.source_section,
                "source_row": item.source_row,
                "evidence": item.evidence,
            }, ensure_ascii=False),
        ))


def _get_or_create_project(db: Session, project_id: str | None, name: str, department: str | None) -> Project:
    if project_id:
        project = db.get(Project, project_id)
        if project:
            project.status = "evaluated"
            return project
    project = Project(name=name, department=department, status="evaluated")
    db.add(project)
    db.flush()
    return project


def _public_item(item: ParsedPriceItem) -> dict[str, Any]:
    return {
        "item_name": item.item_name,
        "brand": item.brand,
        "model": item.model,
        "spec": item.spec,
        "category": item.category,
        "unit": item.unit,
        "quantity": item.quantity,
        "unit_price": item.unit_price,
        "total_price": item.total_price,
        "effort_person_month": item.effort_person_month,
        "source_section": item.source_section,
        "source_row": item.source_row,
        "evidence": item.evidence,
    }


def _benchmark_public(row: PriceBenchmark) -> dict[str, Any]:
    raw = _raw(row.raw_data)
    return {
        "id": row.id,
        "item_name_std": row.item_name_std,
        "brand": row.brand,
        "model": row.model,
        "spec": raw.get("spec", ""),
        "unit_price": row.unit_price,
        "source": row.source,
        "source_date": row.source_date,
        "source_url": row.source_url,
    }


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _norm(value: Any) -> str:
    return re.sub(r"[\s\-_/（）()，,。．.]+", "", _text(value)).lower()


def _number(value: Any) -> float | None:
    normalized = re.sub(r"\s+", "", _text(value)).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", normalized)
    return float(match.group(0)) if match else None


def _raw(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}
