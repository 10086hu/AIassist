import json
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.models import CheckResult, Project
from app.db.session import get_db
from app.modules.duplicate.service import (
    run_internal_duplicate_check,
    run_duplicate_check_from_document,
)
from app.schemas import DuplicateInternalResponse
from app.services.check_service import (
    list_check_rules,
    run_function_correspondence_check,
    run_sensitive_word_check,
)


router = APIRouter()


FUNCTION_CORRESPONDENCE_EXTENSIONS = {".docx", ".pdf", ".txt"}
SENSITIVE_WORD_EXTENSIONS = {".docx", ".pdf", ".txt", ".xlsx", ".xlsm"}


@router.get("/rules")
def evaluate_rules(module: str, rule_source: str = "api") -> dict[str, Any]:
    try:
        return list_check_rules(module=module, rule_source=rule_source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/results")
def evaluate_results(limit: int = 50, db: Session = Depends(get_db)) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    rows = (
        db.query(CheckResult)
        .order_by(CheckResult.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"items": [_check_result_to_list_item(row) for row in rows]}


@router.get("/results/{result_id}")
def evaluate_result_detail(result_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(CheckResult, result_id)
    if row is None:
        raise HTTPException(status_code=404, detail="审查记录不存在")
    return _check_result_to_detail(row)


@router.post("/duplicate/internal", response_model=DuplicateInternalResponse)
async def evaluate_duplicate_internal(
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    project_name: str = Form(default="未命名可研项目"),
    department: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> DuplicateInternalResponse:
    """
    支持 Excel/Word/PDF 多格式输入的内部去重检查

    自动检测文件格式：
    - .xlsx / .csv → 直接解析为功能点
    - .docx / .pdf → 先用 LLM 提取功能点，再去重判定
    """
    filename = file.filename or ""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件为空")

    try:
        # 根据文件扩展名选择处理方式
        if filename.lower().endswith((".xlsx", ".csv")):
            return run_internal_duplicate_check(
                db=db,
                content=content,
                filename=filename,
                project_id=project_id,
                project_name=project_name,
                department=department,
            )
        elif filename.lower().endswith((".docx", ".pdf")):
            return run_duplicate_check_from_document(
                db=db,
                content=content,
                filename=filename,
                project_id=project_id,
                project_name=project_name,
                department=department,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="不支持的文件格式。支持：.xlsx, .csv, .docx, .pdf",
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/function-correspondence")
async def evaluate_function_correspondence(
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    project_name: str = Form(default="未命名可研项目"),
    department: str | None = Form(default=None),
    rule_source: str = Form(default="api"),
    project_level: str = Form(default="市级项目"),
    use_llm: bool = Form(default=False),
    selected_rule_ids: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    temp_path = await _save_upload_to_temp(file, FUNCTION_CORRESPONDENCE_EXTENSIONS)
    try:
        result = run_function_correspondence_check(
            report_file_path=temp_path,
            rule_source=rule_source,
            project_level=project_level,
            use_llm=use_llm,
            selected_rule_ids=_parse_selected_rule_ids(selected_rule_ids),
        )
        project = _get_or_create_project(db, project_id, project_name, department)
        _store_check_result(
            db=db,
            project=project,
            module="function_correspondence",
            result=result,
            severity=_highest_risk((result.get("summary") or {}).get("risk_count") or {}),
            suggestion=_first_finding_value(result.get("findings") or [], "suggestion"),
        )
        db.commit()
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"建设功能对应关系检查失败：{exc}") from exc
    finally:
        _remove_temp_file(temp_path)


@router.post("/sensitive-word")
async def evaluate_sensitive_word(
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    project_name: str = Form(default="未命名可研项目"),
    department: str | None = Form(default=None),
    rule_source: str = Form(default="api"),
    project_level: str = Form(default="通用"),
    use_llm: bool = Form(default=False),
    selected_rule_ids: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    temp_path = await _save_upload_to_temp(file, SENSITIVE_WORD_EXTENSIONS)
    try:
        result = run_sensitive_word_check(
            report_file_path=temp_path,
            rule_source=rule_source,
            project_level=project_level,
            use_llm=use_llm,
            selected_rule_ids=_parse_selected_rule_ids(selected_rule_ids),
        )
        project = _get_or_create_project(db, project_id, project_name, department)
        risk_summary = result.get("risk_summary") or {}
        _store_check_result(
            db=db,
            project=project,
            module="sensitive_word",
            result=result,
            severity=_highest_risk(risk_summary.get("risk_distribution") or {}),
            suggestion=(result.get("suggestions") or [None])[0],
        )
        db.commit()
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"敏感词检查失败：{exc}") from exc
    finally:
        _remove_temp_file(temp_path)


@router.post("/{project_id}/resource")
def evaluate_resource(project_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    # Week 1 skeleton endpoint. Module 5 rules/RAG/LLM evaluation starts in week 4.
    return {
        "project_id": project_id,
        "module": "resource",
        "status": "not_implemented",
        "message": "资源申请合理性检查接口已预留，当前冲刺先交付模块2内部去重。",
    }


@router.post("/{project_id}/price")
def evaluate_price(project_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    # Week 1 skeleton endpoint. Module 8 benchmark import/matching starts after module 5.
    return {
        "project_id": project_id,
        "module": "price",
        "status": "not_implemented",
        "message": "软硬件产品价格参考接口已预留，当前冲刺先交付模块2内部去重。",
    }


async def _save_upload_to_temp(file: UploadFile, allowed_extensions: set[str]) -> str:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_extensions:
        supported = ", ".join(sorted(allowed_extensions))
        raise HTTPException(status_code=400, detail=f"不支持的文件格式。支持：{supported}")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件为空")

    fd, path = tempfile.mkstemp(prefix="aiassist_report_", suffix=suffix)
    with os.fdopen(fd, "wb") as target:
        target.write(content)
    return path


def _remove_temp_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _parse_selected_rule_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _get_or_create_project(
    db: Session,
    project_id: str | None,
    project_name: str,
    department: str | None,
) -> Project:
    if project_id:
        project = db.get(Project, project_id)
        if project is not None:
            project.status = "evaluated"
            return project

    project = Project(name=project_name, department=department, status="evaluated")
    db.add(project)
    db.flush()
    return project


def _store_check_result(
    db: Session,
    project: Project,
    module: str,
    result: dict[str, Any],
    severity: str,
    suggestion: str | None,
) -> None:
    result_json = {
        "project_name": project.name,
        "checked_module": module,
        "result": result,
    }
    db.add(
        CheckResult(
            project_id=project.id,
            module=module,
            check_subtype="aggregate",
            severity=severity,
            result_label=str(result.get("status") or "完成"),
            reason=json.dumps(
                result.get("summary") or result.get("risk_summary") or {},
                ensure_ascii=False,
            ),
            suggestion=suggestion,
            reference_data=json.dumps(result_json, ensure_ascii=False),
            model_name="local-python-checker",
        )
    )


def _check_result_to_list_item(row: CheckResult) -> dict[str, Any]:
    payload = _load_reference_payload(row)
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    summary = _result_summary(result)
    project_name = payload.get("project_name") or (row.project.name if row.project else "")
    return {
        "id": row.id,
        "project_name": project_name,
        "report_name": project_name,
        "module": row.module,
        "module_name": result.get("module_name") or _module_display_name(row.module),
        "status": row.result_label,
        "risk_level": row.severity,
        "summary": summary,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "findings_count": _display_findings_count(row.module, result),
    }


def _check_result_to_detail(row: CheckResult) -> dict[str, Any]:
    payload = _load_reference_payload(row)
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    findings = result.get("findings") or []
    if row.module == "function_correspondence" and isinstance(findings, list) and len(findings) > 50:
        findings = findings[:50]
    return {
        **_check_result_to_list_item(row),
        "result": result,
        "findings": findings,
    }


def _load_reference_payload(row: CheckResult) -> dict[str, Any]:
    try:
        payload = json.loads(row.reference_data or "{}")
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") or result.get("risk_summary") or {}
    return summary if isinstance(summary, dict) else {}


def _findings_count(result: dict[str, Any]) -> int:
    findings = result.get("findings")
    if isinstance(findings, list):
        return len(findings)

    summary = _result_summary(result)
    for key in ("displayed_findings_count", "merged_findings_count", "total_findings"):
        try:
            return int(summary.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return 0


def _display_findings_count(module: str, result: dict[str, Any]) -> int:
    count = _findings_count(result)
    if module == "function_correspondence":
        return min(count, 50)
    if module == "sensitive_word":
        return min(count, 100)
    return count


def _module_display_name(module: str) -> str:
    return {
        "function_correspondence": "建设功能的对应关系检查",
        "sensitive_word": "敏感词检查",
    }.get(module, module)


def _highest_risk(risk_counts: dict[str, Any]) -> str:
    for risk in ("高", "中", "需人工确认", "需确认", "低"):
        if int(risk_counts.get(risk) or 0) > 0:
            return risk
    return "通过"


def _first_finding_value(findings: list[dict[str, Any]], key: str) -> str | None:
    for item in findings:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return None
