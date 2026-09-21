from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import CheckResult, Project, ResourceItem
from app.modules.maintenance.service import evaluate_maintenance_rules, is_maintenance_project_document
from app.modules.resource.parser import ParsedResourceItem, parse_resource_items
from app.modules.resource.rules import ResourceRuleFinding, evaluate_resource_rules
from app.schemas import ProjectOut, ResourceCheckFindingOut, ResourceCheckResponse


# CheckResult.module 的固定值，用于和其他审查模块区分。
RESOURCE_MODULE = "resource"
# CheckResult.check_subtype 的固定值，用于只替换第 15/16 行资源规则的历史结果。
RESOURCE_CHECK_SUBTYPE = "resource_rule_15_16"


def run_resource_check_from_file(
    db: Session,
    content: bytes,
    filename: str,
    project_id: Optional[str],
    project_name: str,
    department: Optional[str],
    selected_rule_ids: Optional[list[str]] = None,
) -> ResourceCheckResponse:
    """资源申请合理性检查主流程。

    这是 API 层调用的唯一入口，完整执行链路如下：
    1. 解析上传的 Excel/CSV，得到标准化资源项。
    2. 创建或复用项目。
    3. 把解析后的资源项写入 resource_items 表，便于追溯。
    4. 执行第 15/16 行资源规则。
    5. 把检查结果写入 check_results 表。
    6. 返回前端/Swagger 可直接展示的响应结构。
    """

    # project_id 存在时复用旧项目；不存在或查不到时新建项目。
    project = _get_or_create_project(db, project_id, project_name, department)

    selected = {str(item).strip() for item in selected_rule_ids or [] if str(item).strip()}
    # Only rows 15/16 use the revised parser; maintenance-only requests retain the original path.
    from app.modules.resource.quantity_parser import parse_resource_items as parse_revised_resources
    from app.modules.resource.document_check import check_resource_document
    parsed_items = (parse_revised_resources if _standard_resource_rules_selected(selected) else parse_resource_items)(content, filename)

    findings: list[ResourceRuleFinding] = []
    explicit_maintenance_rules_selected = _maintenance_rules_selected(selected)
    run_maintenance_rules = explicit_maintenance_rules_selected and is_maintenance_project_document(content, filename)
    if _standard_resource_rules_selected(selected):
        findings.extend(
            finding
            for finding in check_resource_document(content, filename, parsed_items)
            if _resource_finding_selected(finding, selected)
        )

    if run_maintenance_rules:
        findings.extend(
            evaluate_maintenance_rules(
                content,
                filename,
                selected_rule_ids=selected if explicit_maintenance_rules_selected else None,
                db=db,
            )
        )

    if not parsed_items and _standard_resource_rules_selected(selected) and not run_maintenance_rules:
        findings = [
            *findings,
            ResourceRuleFinding(
                rule_code="RESOURCE_PARSE_SCOPE",
                rule_name="资源申请清单解析",
                resource_name="资源申请清单",
                severity="需人工确认",
                result_label="未找到资源清单",
                reason=(
                    "未在上传材料中识别到安全服务需求表、PaaS服务清单、密码服务资源内容清单、"
                    "云资源申请表、计算资源清单或三大件清单等资源申请相关内容。"
                ),
                suggestion="建议上传结构化 .xlsx/.csv 资源清单，或在 Word 中保留清晰的资源相关章节标题和数量列。",
                source_quantities={},
                row_indexes=[],
            )
        ]

    # 当前实现采用“本次上传结果覆盖该项目旧资源清单”的策略。
    _replace_resource_items(db, project.id, parsed_items)

    # 删除旧检查结果并写入新结果，保证同一项目重复上传时不会产生重复记录。
    _replace_check_results(db, project.id, findings, parsed_items)

    # 标记项目已完成一次审查。
    project.status = "evaluated"
    db.commit()
    db.refresh(project)

    # Pydantic 响应模型负责控制 API 返回字段，避免直接暴露 SQLAlchemy 对象。
    return _build_response(project, imported_count=len(parsed_items), findings=findings, parsed_items=parsed_items, filename=filename)


def _build_response(
    project: Project,
    imported_count: int,
    findings: list[ResourceRuleFinding],
    parsed_items: list[ParsedResourceItem] | None = None,
    filename: str = "",
) -> ResourceCheckResponse:
    parsed_items = parsed_items or []
    items_by_row: dict[int, list[ParsedResourceItem]] = {}
    for item in parsed_items:
        items_by_row.setdefault(item.row_index, []).append(item)

    def to_output(finding: ResourceRuleFinding) -> ResourceCheckFindingOut:
        locations = getattr(finding, "source_locations", None) or [
            {
                "file_name": filename,
                "sheet_name": item.sheet_name,
                "row_index": row,
                "source": item.source,
                "quote": item.raw_text,
                "precision": "row",
            }
            for row in finding.row_indexes
            for item in items_by_row.get(row, [])
        ]
        highlights = [
            {
                "role": "current",
                "file_name": location.get("file_name") or filename,
                "sheet_name": location.get("sheet_name"),
                "row_index": location.get("row_index"),
                "section": location.get("source"),
                "quote": str(location.get("quote") or "")[:500],
                "precision": location.get("precision") or "row",
                "highlight": True,
            }
            for location in locations
            if str(location.get("quote") or "").strip()
        ]
        location_hint = "；".join(dict.fromkeys(
            f"{item.sheet_name}第{row}行"
            for row in finding.row_indexes[:8]
            for item in items_by_row.get(row, [])
        )) or None
        if filename and location_hint:
            location_hint = f"{filename}，{location_hint}"
        evidence = "；".join(
            item.raw_text
            for row in finding.row_indexes
            for item in items_by_row.get(row, [])
            if item.raw_text
        ) or None
        if finding.rule_code in {"R15_SECURITY_PAAS_CRYPTO_QUANTITY", "R16_SERVER_OS_QUANTITY", "R16_DB_SERVER_DATABASE_QUANTITY"} and getattr(finding, "source_locations", None):
            evidence = "；".join(dict.fromkeys(str(loc.get("quote") or "") for loc in locations)) or None
            location_hint = "；".join(dict.fromkeys(
                f"{loc.get('sheet_name', '')}第{loc['row_index']}行" if loc.get('row_index') is not None
                else str(loc.get('sheet_name') or '原文') for loc in locations))
            if filename:
                location_hint = f"{filename}，{location_hint}"
        output = ResourceCheckFindingOut(
            rule_code=finding.rule_code,
            rule_name=finding.rule_name,
            resource_name=finding.resource_name,
            severity=finding.severity,
            result_label=finding.result_label,
            reason=finding.reason,
            suggestion=finding.suggestion,
            source_quantities=finding.source_quantities,
            row_indexes=finding.row_indexes,
            source_locations=locations,
            evidence=evidence,
            location_hint=location_hint,
            source_highlights=highlights,
            revision_target={
                "location_hint": location_hint or f"{filename}，资源申请清单（未匹配到具体行）",
                "source_location": {"entries": locations, "file_name": filename},
                "highlights": highlights,
                "target_type": "source_text" if highlights else "section_or_document",
                "advice": finding.suggestion or "",
            },
            evidence_examples=finding.evidence_examples,
            source_section=finding.source_section,
        )

        location = getattr(finding, 'evidence_location', None)
        if location and finding.rule_code in {"R15_SECURITY_PAAS_CRYPTO_QUANTITY", "R16_SERVER_OS_QUANTITY", "R16_DB_SERVER_DATABASE_QUANTITY"}:
            output.source_locations = location['source_locations']
            output.source_highlights = location['source_highlights']
            output.location_hint = location['location_hint']
            output.revision_target = location['revision_target']
        return output

    return ResourceCheckResponse(
        project=ProjectOut.model_validate(project),
        imported_count=imported_count,
        checked_rule_count=len({finding.rule_code for finding in findings}),
        findings=[
            to_output(finding)
            for finding in findings
        ],
    )


def _get_or_create_project(
    db: Session,
    project_id: str | None,
    project_name: str,
    department: str | None,
) -> Project:
    """按 project_id 查找项目；查不到则创建新项目。"""

    if project_id:
        project = db.get(Project, project_id)
        if project is not None:
            return project

    # db.flush() 会把新项目 id 写回对象，但暂不提交事务。
    project = Project(name=project_name, department=department, status="draft")
    db.add(project)
    db.flush()
    return project


def _replace_resource_items(
    db: Session,
    project_id: str,
    parsed_items: list[ParsedResourceItem],
) -> None:
    """用本次上传内容替换项目的 resource_items。

    注意：ResourceItem 是项目现有通用表，字段较少，所以这里把 source/sheet/spec
    合并写入 spec，把原始行文本写入 justification，用于后续人工排查。
    """

    db.execute(delete(ResourceItem).where(ResourceItem.project_id == project_id))
    for item in parsed_items:
        db.add(
            ResourceItem(
                project_id=project_id,
                # resource_type 字段最长 64，这里截断避免数据库字段过长。
                resource_type=item.name[:64],
                spec=f"{item.source} | {item.sheet_name} | {item.spec}".strip(" |"),
                # 现有模型 quantity 是 int；如果解析到小数，这里四舍五入保存。
                quantity=int(item.quantity) if float(item.quantity).is_integer() else round(item.quantity),
                unit=item.unit,
                justification=item.raw_text,
                is_major_item=1 if _is_major_item(item.name) else 0,
            )
        )
    db.flush()


def _replace_check_results(
    db: Session,
    project_id: str,
    findings,
    parsed_items: list[ParsedResourceItem] | None = None,
) -> None:
    """用本次规则发现替换项目旧的第 15/16 行资源检查结果。"""

    db.execute(
        delete(CheckResult).where(
            CheckResult.project_id == project_id,
            CheckResult.module == RESOURCE_MODULE,
            CheckResult.check_subtype == RESOURCE_CHECK_SUBTYPE,
        )
    )
    parsed_items = parsed_items or []
    items_by_row: dict[int, list[ParsedResourceItem]] = {}
    for item in parsed_items:
        items_by_row.setdefault(item.row_index, []).append(item)
    for finding in findings:
        source_locations = getattr(finding, "source_locations", None) or [
            {
                "sheet_name": item.sheet_name,
                "row_index": row,
                "source": item.source,
                "quote": item.raw_text,
            }
            for row in finding.row_indexes
            for item in items_by_row.get(row, [])
        ]
        # reference_data 保存结构化明细，便于前端后续做展开展示或导出报告。
        db.add(
            CheckResult(
                project_id=project_id,
                module=RESOURCE_MODULE,
                check_subtype=RESOURCE_CHECK_SUBTYPE,
                severity=finding.severity,
                score=1.0 if finding.severity in {"通过", "pass"} else 0.0,
                result_label=finding.result_label,
                reason=finding.reason,
                suggestion=finding.suggestion,
                reference_data=json.dumps(
                    {
                        "rule_code": finding.rule_code,
                        "rule_name": finding.rule_name,
                        "resource_name": finding.resource_name,
                        "source_quantities": finding.source_quantities,
                        "row_indexes": finding.row_indexes,
                        "evidence_examples": finding.evidence_examples,
                        "source_section": finding.source_section,
                        "source_locations": source_locations,
                    },
                    ensure_ascii=False,
                ),
                model_name="local-resource-rule-engine",
            )
        )


def _is_major_item(name: str) -> bool:
    """标记是否属于三大件或关键资源，用于 ResourceItem.is_major_item。"""

    keywords = ("服务器", "操作系统", "数据库", "PaaS", "密码服务", "安全服务")
    return int(any(keyword.lower() in name.lower() for keyword in keywords))


def _standard_resource_rules_selected(selected: set[str]) -> bool:
    if not selected:
        return True
    aliases = {
        # 兼容前端/Excel 直接传“规则分工”行号的场景。
        "15",
        "16",
        "RESOURCE_REASON_001",
        "RESOURCE_REASON_002",
        "R15_SECURITY_PAAS_CRYPTO_QUANTITY",
        "R16_SERVER_OS_QUANTITY",
        "R16_DB_SERVER_DATABASE_QUANTITY",
        "安全服务需求表、PaaS服务清单、密码服务资源内容清单的关联内容一致性校验规则",
        "三大件数量一致性校验规则",
    }
    return bool(selected & aliases)


def _maintenance_rules_selected(selected: set[str]) -> bool:
    # 运维专项规则必须使用 MAINT_OPS_* 或 MAINT_OPS_ALL。
    # 不能把纯数字行号当作运维规则，否则“16”会和规则分工第16行冲突，
    # 导致非运维项目被运维门禁拦截，标准三大件规则不运行。
    return any(item == "MAINT_OPS_ALL" or item.startswith("MAINT_OPS_") for item in selected)


def _resource_finding_selected(finding: ResourceRuleFinding, selected: set[str]) -> bool:
    if not selected:
        return True
    if finding.rule_code in selected or finding.rule_name in selected:
        return True
    if finding.rule_code == "R15_SECURITY_PAAS_CRYPTO_QUANTITY":
        return "RESOURCE_REASON_001" in selected or "15" in selected
    if finding.rule_code in {"R16_SERVER_OS_QUANTITY", "R16_DB_SERVER_DATABASE_QUANTITY"}:
        return "RESOURCE_REASON_002" in selected or "16" in selected or "三大件数量一致性校验规则" in selected
    return False
