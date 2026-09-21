import json
import base64
import html
import io
import mimetypes
import os
import re
import tempfile
import threading
import traceback
import uuid
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.models import CheckResult, CheckRun, Project, SourceArtifact
from app.db.session import SessionLocal, get_db
from app.modules.content_consistency.service import run_content_consistency_check_from_document
from app.modules.data_rules.service import (
    run_data_reporting_check_from_document,
    run_data_rules_check_from_document,
)
from app.modules.duplicate.service import (
    run_duplicate_compare_check,
    run_duplicate_check_from_document,
    run_internal_duplicate_check,
)
from app.modules.shanghai_review.base import BaseValidator
from app.modules.resource.service import run_resource_check_from_file
from app.modules.price.service import (
    import_price_benchmarks,
    import_price_benchmarks_from_file,
    list_price_benchmarks,
    run_price_check_from_file,
)
from app.modules.price.parser import ParsedPriceDocument, parse_price_document
from app.modules.shanghai_review.security_review import run_security_check_from_document
from app.modules.shanghai_review.service import run_basis_check_from_document
from app.schemas import DuplicateInternalResponse, ResourceCheckResponse
from app.services.result_refiner import refine_findings
from app.services.check_service import (
    list_check_rules,
    run_function_correspondence_check,
    run_sensitive_word_check,
)
from app.services.llm_client import get_llm_status, ping_llm


router = APIRouter()


FUNCTION_CORRESPONDENCE_EXTENSIONS = {".docx", ".txt"}
SENSITIVE_WORD_EXTENSIONS = {".docx", ".txt", ".xlsx", ".xlsm"}
DUPLICATE_EXTENSIONS = {".xlsx", ".csv", ".docx"}
DOCUMENT_RULE_EXTENSIONS = {".docx", ".txt"}
BASIS_EXTENSIONS = {".docx", ".txt"}
RESOURCE_EXTENSIONS = {".csv", ".xlsx", ".docx"}
TASK_FILE_EXTENSIONS = (
    FUNCTION_CORRESPONDENCE_EXTENSIONS
    | SENSITIVE_WORD_EXTENSIONS
    | DUPLICATE_EXTENSIONS
    | BASIS_EXTENSIONS
    | DOCUMENT_RULE_EXTENSIONS
    | RESOURCE_EXTENSIONS
)
RUNNABLE_MODULES = {
    "basis",
    "duplicate",
    "function_correspondence",
    "data_reasonableness",
    "security",
    "resource",
    "sensitive_word",
    "price",
    "price_reference",
}
CONTENT_CONSISTENCY_RULE_ID = "FUNC_CORR_006"
CONTENT_CONSISTENCY_RULE_IDS = {"FUNC_CORR_004", "FUNC_CORR_006"}
CONTENT_CONSISTENCY_RULE_NAMES = {"建设内容一致性校验规则"}
CONTENT_CONSISTENCY_RULE_NAME = "建设内容一致性专项校验规则"
TASKS: dict[str, dict[str, Any]] = {}
TASKS_LOCK = threading.Lock()


MODULE_DISPLAY_NAMES = {
    "basis": "建设依据审查",
    "duplicate": "重复建设检查",
    "function_correspondence": "建设功能的对应关系检查",
    "content_consistency": "建设内容一致性检查",
    "data_reasonableness": "数据填报合理性检查",
    "security": "安全内容的合理性",
    "resource": "资源申请合理性检查",
    "sensitive_word": "敏感词检查",
    "price": "价格合理性",
    "price_reference": "软硬件价格参考",
}


@router.get("/rules")
def evaluate_rules(module: str, rule_source: str = "api") -> dict[str, Any]:
    try:
        return list_check_rules(module=module, rule_source=rule_source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/llm/status")
def evaluate_llm_status() -> dict[str, Any]:
    return get_llm_status()


@router.post("/llm/ping")
def evaluate_llm_ping(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    text = str(payload.get("text") or "请用 JSON 返回一次连通性测试结果。")
    return ping_llm(text)


@router.post("/tasks")
async def create_evaluate_task(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    check_run_id: str | None = Form(default=None),
    project_name: str = Form(default="未命名可研项目"),
    department: str | None = Form(default=None),
    modules: str = Form(default="function_correspondence,sensitive_word"),
    rule_source: str = Form(default="api"),
    use_llm: bool = Form(default=False),
    selected_rule_ids: str | None = Form(default=None),
) -> dict[str, Any]:
    module_list = [item.strip() for item in modules.split(",") if item.strip()]
    module_list = [item for item in module_list if item in RUNNABLE_MODULES]
    if not module_list:
        raise HTTPException(status_code=400, detail="请至少选择一个已接入的检测模块")

    original_filename = file.filename or "未命名报告"
    temp_path = await _save_upload_to_temp(file, TASK_FILE_EXTENSIONS)
    task_id = str(uuid.uuid4())
    _set_task(
        task_id,
        status="queued",
        progress=0,
        stage="queued",
        message="检测任务已创建",
        result_ids=[],
        error=None,
    )
    background_tasks.add_task(
        _run_evaluate_task,
        task_id,
        temp_path,
        original_filename,
        project_id,
        check_run_id,
        project_name,
        department,
        module_list,
        rule_source,
        use_llm,
        selected_rule_ids,
    )
    return {"task_id": task_id, "status": "queued", "message": "检测任务已创建"}


@router.get("/tasks/{task_id}")
def get_evaluate_task(task_id: str) -> dict[str, Any]:
    with TASKS_LOCK:
        task = dict(TASKS.get(task_id) or {})
    if not task:
        raise HTTPException(status_code=404, detail="检测任务不存在")
    return {"task_id": task_id, **task}


@router.get("/results")
def evaluate_results(limit: int = 50, db: Session = Depends(get_db)) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    rows = (
        db.query(CheckResult)
        .filter(CheckResult.check_subtype == "aggregate")
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


@router.get("/results/{result_id}/sources/{source_id}")
def evaluate_result_source(result_id: str, source_id: str, db: Session = Depends(get_db)) -> Response:
    """读取审查记录保存的原始报告快照，供历史报告定位后重新打开。"""
    artifact = (
        db.query(SourceArtifact)
        .filter(
            SourceArtifact.id == source_id,
            SourceArtifact.check_result_id == result_id,
        )
        .first()
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="原始报告附件不存在")
    media_type = artifact.media_type or "application/octet-stream"
    filename = artifact.filename.replace('"', "")
    return Response(
        content=artifact.content,
        media_type=media_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/results/{result_id}/sources/{source_id}/preview")
def evaluate_result_source_preview(result_id: str, source_id: str, db: Session = Depends(get_db)) -> Response:
    """Render a source inside the desktop review pane while retaining the original download endpoint."""
    artifact = (
        db.query(SourceArtifact)
        .filter(
            SourceArtifact.id == source_id,
            SourceArtifact.check_result_id == result_id,
        )
        .first()
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="原始报告附件不存在")

    suffix = Path(artifact.filename).suffix.lower()
    if suffix == ".docx":
        try:
            return HTMLResponse(
                content=_render_docx_preview(artifact.content, artifact.filename),
                headers={"Cache-Control": "no-store"},
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Word 原文预览生成失败：{exc}") from exc
    return HTMLResponse(
        content=_render_plain_source_preview(artifact.content, artifact.filename),
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/results/{result_id}")
def delete_evaluate_result(result_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(CheckResult, result_id)
    if row is None:
        raise HTTPException(status_code=404, detail="审查记录不存在")

    db.delete(row)
    db.commit()
    return {"success": True, "deleted_id": result_id}


@router.post("/duplicate/internal", response_model=DuplicateInternalResponse)
async def evaluate_duplicate_internal(
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    project_name: str = Form(default="未命名可研项目"),
    department: str | None = Form(default=None),
    use_llm: bool = Form(default=True),
    db: Session = Depends(get_db),
) -> DuplicateInternalResponse:
    filename = file.filename or ""
    content = await _read_upload(file, DUPLICATE_EXTENSIONS)

    try:
        return _run_duplicate_module(db, content, filename, project_id, project_name, department, use_llm=use_llm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/duplicate/compare")
async def evaluate_duplicate_compare(
    current_file: UploadFile = File(...),
    history_files: list[UploadFile] = File(default=[]),
    project_id: str | None = Form(default=None),
    check_run_id: str | None = Form(default=None),
    project_name: str = Form(default="未命名可研项目"),
    department: str | None = Form(default=None),
    current_stage: str = Form(default="本期"),
    history_stages: str | None = Form(default=None),
    use_llm: bool = Form(default=True),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    current_filename = current_file.filename or ""
    current_content = await _read_upload(current_file, DUPLICATE_EXTENSIONS)
    history_payloads: list[tuple[bytes, str]] = []
    for file in history_files:
        filename = file.filename or ""
        history_payloads.append((await _read_upload(file, DUPLICATE_EXTENSIONS), filename))

    try:
        raw = run_duplicate_compare_check(
            db=db,
            current_content=current_content,
            current_filename=current_filename,
            history_files=history_payloads,
            project_id=project_id,
            project_name=project_name,
            department=department,
            current_stage=current_stage,
            history_stages=_parse_history_stages(history_stages),
            use_llm=use_llm,
        )
        normalized = _attach_finding_locations(_normalize_duplicate_result(raw), current_content, current_filename)
        project = db.get(Project, raw.project.id)
        if project is not None:
            check_run = db.get(CheckRun, check_run_id) if check_run_id else None
            if check_run_id and (check_run is None or check_run.project_id != project.id):
                raise ValueError("报告检查记录不存在或不属于当前项目")
            result_id = _store_check_result(
                db=db,
                project=project,
                module="duplicate",
                result=normalized,
                severity=_highest_result_severity(normalized),
                suggestion=_first_finding_value(normalized.get("findings") or [], "revision_advice"),
                check_run_id=check_run.id if check_run else None,
                report_name=current_filename,
            )
            source_documents = _store_duplicate_source_artifacts(
                db=db,
                result_id=result_id,
                current=(current_content, current_filename),
                history=history_payloads,
                current_stage=current_stage or "本期",
                history_stages=_parse_history_stages(history_stages),
            )
            normalized["source_documents"] = source_documents
            result_row = db.get(CheckResult, result_id)
            if result_row is not None:
                result_row.reference_data = _json_dumps(
                    {
                        "project_name": project.name,
                        "report_name": current_filename,
                        "checked_module": "duplicate",
                        "result": normalized,
                    }
                )
            if check_run is not None:
                check_run.status = "completed"
            db.commit()
        return normalized
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"重复建设跨报告检查失败：{exc}") from exc


@router.post("/content-consistency/document")
async def evaluate_content_consistency_document(
    file: UploadFile = File(...),
    project_name: str = Form(default="未命名可研项目"),
    use_llm: bool = Form(default=False),
    project_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    filename = file.filename or ""
    content = await _read_upload(file, DOCUMENT_RULE_EXTENSIONS)
    try:
        raw = run_content_consistency_check_from_document(
            content=content,
            filename=filename,
            project_name=project_name,
        )
        normalized = _attach_finding_locations(_normalize_document_rule_result(raw, "content_consistency", use_llm=use_llm), content, filename)
        project = _get_or_create_project(db, project_id, project_name, None)
        result_id = _store_check_result(db, project, "content_consistency", normalized, _highest_result_severity(normalized), _first_finding_value(normalized.get("findings") or [], "revision_advice"), report_name=filename)
        _store_result_source_artifact(db, result_id, content, filename)
        db.commit()
        normalized["source_documents"] = _source_artifacts_to_list(db.get(CheckResult, result_id))
        return normalized
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.post("/basis/document")
async def evaluate_basis_document(
    file: UploadFile = File(...),
    project_name: str = Form(default="未命名可研项目"),
    use_llm: bool = Form(default=False),
    selected_rule_ids: str | None = Form(default=None),
    project_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    filename = file.filename or ""
    content = await _read_upload(file, BASIS_EXTENSIONS)
    try:
        raw = run_basis_check_from_document(
            content=content,
            filename=filename,
            project_name=project_name,
            selected_rule_ids=_parse_selected_rule_ids(selected_rule_ids),
        )
        normalized = _attach_finding_locations(_normalize_document_rule_result(raw, "basis", use_llm=use_llm), content, filename)
        project = _get_or_create_project(db, project_id, project_name, None)
        result_id = _store_check_result(db, project, "basis", normalized, _highest_result_severity(normalized), _first_finding_value(normalized.get("findings") or [], "revision_advice"), report_name=filename)
        _store_result_source_artifact(db, result_id, content, filename)
        db.commit()
        normalized["source_documents"] = _source_artifacts_to_list(db.get(CheckResult, result_id))
        return normalized
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/security/document")
async def evaluate_security_document(
    file: UploadFile = File(...),
    project_name: str = Form(default="未命名可研项目"),
    use_llm: bool = Form(default=True),
    selected_rule_ids: str | None = Form(default=None),
    project_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    filename = file.filename or ""
    content = await _read_upload(file, DOCUMENT_RULE_EXTENSIONS)
    try:
        raw = run_security_check_from_document(
            content=content,
            filename=filename,
            project_name=project_name,
            selected_rule_ids=_parse_selected_rule_ids(selected_rule_ids),
            use_llm=use_llm,
        )
        normalized = _attach_finding_locations(_normalize_security_result(raw), content, filename)
        project = _get_or_create_project(db, project_id, project_name, None)
        result_id = _store_check_result(db, project, "security", normalized, _highest_result_severity(normalized), _first_finding_value(normalized.get("findings") or [], "revision_advice"), report_name=filename)
        _store_result_source_artifact(db, result_id, content, filename)
        db.commit()
        normalized["source_documents"] = _source_artifacts_to_list(db.get(CheckResult, result_id))
        return normalized
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/data-rules/document")
async def evaluate_data_rules_document(
    file: UploadFile = File(...),
    project_name: str = Form(default="未命名可研项目"),
    use_llm: bool = Form(default=False),
    selected_rule_ids: str | None = Form(default=None),
    project_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    filename = file.filename or ""
    content = await _read_upload(file, DOCUMENT_RULE_EXTENSIONS)
    try:
        raw = run_data_rules_check_from_document(
            content=content,
            filename=filename,
            project_name=project_name,
            selected_rule_ids=_parse_selected_rule_ids(selected_rule_ids),
        )
        normalized = _attach_finding_locations(_normalize_document_rule_result(raw, "data_reasonableness", use_llm=use_llm), content, filename)
        project = _get_or_create_project(db, project_id, project_name, None)
        result_id = _store_check_result(db, project, "data_reasonableness", normalized, _highest_result_severity(normalized), _first_finding_value(normalized.get("findings") or [], "revision_advice"), report_name=filename)
        _store_result_source_artifact(db, result_id, content, filename)
        db.commit()
        normalized["source_documents"] = _source_artifacts_to_list(db.get(CheckResult, result_id))
        return normalized
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/data-reporting/document")
async def evaluate_data_reporting_document(
    file: UploadFile = File(...),
    project_name: str = Form(default="未命名可研项目"),
    use_llm: bool = Form(default=False),
    selected_rule_ids: str | None = Form(default=None),
    project_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    filename = file.filename or ""
    content = await _read_upload(file, DOCUMENT_RULE_EXTENSIONS)
    try:
        raw = run_data_reporting_check_from_document(
            content=content,
            filename=filename,
            project_name=project_name,
            selected_rule_ids=_parse_selected_rule_ids(selected_rule_ids),
        )
        normalized = _attach_finding_locations(_normalize_document_rule_result(raw, "data_reasonableness", use_llm=use_llm), content, filename)
        project = _get_or_create_project(db, project_id, project_name, None)
        result_id = _store_check_result(db, project, "data_reasonableness", normalized, _highest_result_severity(normalized), _first_finding_value(normalized.get("findings") or [], "revision_advice"), report_name=filename)
        _store_result_source_artifact(db, result_id, content, filename)
        db.commit()
        normalized["source_documents"] = _source_artifacts_to_list(db.get(CheckResult, result_id))
        return normalized
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/resource", response_model=ResourceCheckResponse)
async def evaluate_resource_upload(
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    project_name: str = Form(default="未命名资源申请项目"),
    department: str | None = Form(default=None),
    use_llm: bool = Form(default=False),
    db: Session = Depends(get_db),
) -> ResourceCheckResponse:
    filename = file.filename or ""
    content = await _read_upload(file, RESOURCE_EXTENSIONS)
    try:
        return run_resource_check_from_file(
            db=db,
            content=content,
            filename=filename,
            project_id=project_id,
            project_name=project_name,
            department=department,
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
    original_filename = file.filename or "未命名报告"
    temp_path = await _save_upload_to_temp(file, FUNCTION_CORRESPONDENCE_EXTENSIONS)
    try:
        selected_ids = _parse_selected_rule_ids(selected_rule_ids)
        result = run_function_correspondence_check(
            report_file_path=temp_path,
            rule_source=rule_source,
            project_level=project_level,
            use_llm=use_llm,
            selected_rule_ids=selected_ids,
        )
        normalized = _normalize_function_or_sensitive_result(result, "function_correspondence")
        normalized = _merge_content_consistency_result(
            normalized=normalized,
            content=Path(temp_path).read_bytes(),
            filename=original_filename,
            project_name=project_name,
            selected_rule_ids=selected_ids,
            use_llm=use_llm,
        )
        _attach_finding_locations(normalized, Path(temp_path).read_bytes(), original_filename)
        project = _get_or_create_project(db, project_id, project_name, department)
        result_id = _store_check_result(
            db=db,
            project=project,
            module="function_correspondence",
            result=normalized,
            severity=_highest_risk((normalized.get("summary") or {}).get("risk_count") or {}),
            suggestion=_first_finding_value(normalized.get("findings") or [], "revision_advice"),
            report_name=original_filename,
        )
        _store_result_source_artifact(db, result_id, Path(temp_path).read_bytes(), original_filename)
        db.commit()
        normalized["source_documents"] = _source_artifacts_to_list(db.get(CheckResult, result_id))
        return normalized
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
    original_filename = file.filename or "未命名报告"
    temp_path = await _save_upload_to_temp(file, SENSITIVE_WORD_EXTENSIONS)
    try:
        result = run_sensitive_word_check(
            report_file_path=temp_path,
            rule_source=rule_source,
            project_level=project_level,
            use_llm=use_llm,
            selected_rule_ids=_parse_selected_rule_ids(selected_rule_ids),
        )
        normalized = _attach_finding_locations(
            _normalize_function_or_sensitive_result(result, "sensitive_word"),
            Path(temp_path).read_bytes(),
            original_filename,
        )
        project = _get_or_create_project(db, project_id, project_name, department)
        summary = normalized.get("risk_summary") or normalized.get("summary") or {}
        result_id = _store_check_result(
            db=db,
            project=project,
            module="sensitive_word",
            result=normalized,
            severity=_highest_risk(summary.get("risk_distribution") or summary.get("risk_count") or {}),
            suggestion=_first_finding_value(normalized.get("findings") or [], "revision_advice"),
            report_name=original_filename,
        )
        _store_result_source_artifact(db, result_id, Path(temp_path).read_bytes(), original_filename)
        db.commit()
        normalized["source_documents"] = _source_artifacts_to_list(db.get(CheckResult, result_id))
        return normalized
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"敏感词检查失败：{exc}") from exc
    finally:
        _remove_temp_file(temp_path)


@router.post("/{project_id}/resource", response_model=ResourceCheckResponse)
async def evaluate_resource(
    project_id: str,
    file: UploadFile = File(...),
    project_name: str = Form(default="未命名资源申请项目"),
    department: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> ResourceCheckResponse:
    filename = file.filename or ""
    content = await _read_upload(file, RESOURCE_EXTENSIONS)
    try:
        return run_resource_check_from_file(
            db=db,
            content=content,
            filename=filename,
            project_id=project_id,
            project_name=project_name,
            department=department,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/price/document")
async def evaluate_price_document(
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    project_name: str = Form(default="未命名价格项目"),
    department: str | None = Form(default=None),
    project_level: str = Form(default="市级项目"),
    selected_rule_ids: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return await _evaluate_price_document_for_module(
        module="price",
        file=file,
        project_id=project_id,
        project_name=project_name,
        department=department,
        project_level=project_level,
        selected_rule_ids=selected_rule_ids,
        db=db,
    )


@router.post("/price-reference/document")
async def evaluate_price_reference_document(
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    project_name: str = Form(default="未命名价格参考项目"),
    department: str | None = Form(default=None),
    project_level: str = Form(default="市级项目"),
    selected_rule_ids: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return await _evaluate_price_document_for_module(
        module="price_reference",
        file=file,
        project_id=project_id,
        project_name=project_name,
        department=department,
        project_level=project_level,
        selected_rule_ids=selected_rule_ids,
        db=db,
    )


async def _evaluate_price_document_for_module(
    module: str,
    file: UploadFile,
    project_id: str | None,
    project_name: str,
    department: str | None,
    project_level: str,
    selected_rule_ids: str | None,
    db: Session,
) -> dict[str, Any]:
    content = await _read_upload(file, {".xlsx", ".xlsm", ".csv", ".docx"})
    rule_ids = _parse_selected_rule_ids(selected_rule_ids)
    if not rule_ids:
        rule_ids = ["PRICE_REASON_001"] if module == "price" else ["PRICE_REF_001", "PRICE_REF_002"]
    try:
        result = run_price_check_from_file(
            db=db,
            content=content,
            filename=file.filename or "price.xlsx",
            project_id=project_id,
            project_name=project_name,
            department=department,
            project_level=project_level,
            selected_rule_ids=rule_ids,
        )
        normalized = _attach_finding_locations(_normalize_price_result(result, module), content, file.filename or "price.xlsx")
        project = db.get(Project, result["project_id"])
        if project is not None:
            result_id = _store_check_result(db, project, module, normalized, _highest_result_severity(normalized), _first_finding_value(normalized.get("findings") or [], "suggestion"), report_name=file.filename or "price.xlsx")
            _store_result_source_artifact(db, result_id, content, file.filename or "price.xlsx")
            normalized["source_documents"] = _source_artifacts_to_list(db.get(CheckResult, result_id))
        db.commit()
        return normalized
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"{_module_display_name(module)}检查失败：{exc}") from exc


@router.post("/price-benchmarks/import")
def evaluate_price_benchmark_import(payload: list[dict[str, Any]] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return import_price_benchmarks(db, payload)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"价格参考数据导入失败：{exc}") from exc


@router.post("/price-benchmarks/import-file")
async def evaluate_price_benchmark_file_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    content = await _read_upload(file, {".xlsx", ".xlsm", ".csv"})
    try:
        return import_price_benchmarks_from_file(db, content, file.filename or "price-benchmarks.xlsx")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"价格参考文件导入失败：{exc}") from exc


@router.get("/price-benchmarks")
def evaluate_price_benchmark_list(limit: int = 200, db: Session = Depends(get_db)) -> dict[str, Any]:
    items = list_price_benchmarks(db, limit)
    return {"items": items, "count": len(items)}


def _set_task(task_id: str, **updates: Any) -> None:
    with TASKS_LOCK:
        current = dict(TASKS.get(task_id) or {})
        current.update(updates)
        TASKS[task_id] = current


def _run_evaluate_task(
    task_id: str,
    temp_path: str,
    original_filename: str,
    project_id: str | None,
    check_run_id: str | None,
    project_name: str,
    department: str | None,
    modules: list[str],
    rule_source: str,
    use_llm: bool,
    selected_rule_ids: str | None,
) -> None:
    db = SessionLocal()
    result_ids: list[str] = []
    task_errors: list[str] = []
    task_context: dict[str, Any] = {}
    try:
        _set_task(task_id, status="running", progress=5, stage="running", message="正在执行规则审查")
        selected_rules = _parse_selected_rule_ids_by_module(selected_rule_ids)
        project = _get_or_create_project(db, project_id, project_name, department)
        check_run = db.get(CheckRun, check_run_id) if check_run_id else None
        if check_run is None or check_run.project_id != project.id:
            check_run = CheckRun(
                project_id=project.id,
                report_name=original_filename,
                source_filename=original_filename,
                status="running",
            )
            db.add(check_run)
            db.flush()
        else:
            check_run.status = "running"
        db.commit()

        for index, module in enumerate(modules, start=1):
            progress_base = int((index - 1) / max(1, len(modules)) * 80) + 5
            _set_task(
                task_id,
                status="running",
                progress=progress_base,
                stage=module,
                message=f"正在检测：{_module_display_name(module)}",
            )
            try:
                result = _run_module_check(
                    db=db,
                    module=module,
                    temp_path=temp_path,
                    source_filename=original_filename,
                    project=project,
                    project_name=project_name,
                    department=department,
                    rule_source=rule_source,
                    use_llm=use_llm,
                    selected_rule_ids=selected_rules.get(module) or [],
                    task_context=task_context,
                )
                _attach_finding_locations(result, Path(temp_path).read_bytes(), original_filename)
                summary = _result_summary(result)
                severity = _highest_result_severity(result)
                suggestion = _first_finding_value(result.get("findings") or [], "revision_advice")
                result_id = _store_check_result(
                    db=db,
                    project=project,
                    module=module,
                    result=result,
                    severity=severity,
                    suggestion=suggestion,
                    check_run_id=check_run.id,
                    report_name=original_filename,
                )
                _store_current_source_artifact(
                    db=db,
                    result_id=result_id,
                    content=Path(temp_path).read_bytes(),
                    filename=original_filename,
                )
                db.commit()
                result_ids.append(result_id)
                llm_status = str(summary.get("llm_status") or "")
                if llm_status in {"partial", "timeout"}:
                    task_errors.append(f"{_module_display_name(module)}: {llm_status}")
            except Exception as exc:
                db.rollback()
                task_errors.append(f"{_module_display_name(module)}: {exc}")

        if not result_ids:
            check_run.status = "failed"
            db.commit()
            _set_task(
                task_id,
                status="failed",
                progress=100,
                stage="failed",
                message="检测失败",
                result_ids=[],
                error="; ".join(task_errors),
            )
            return

        final_status = "partial" if task_errors else "completed"
        check_run.status = final_status
        db.commit()
        _set_task(
            task_id,
            status=final_status,
            progress=100,
            stage=final_status,
            message="检测完成" if final_status == "completed" else "检测完成，部分模块失败或超时",
            result_ids=result_ids,
            error="; ".join(task_errors) if task_errors else None,
        )
    except Exception as exc:
        db.rollback()
        _set_task(
            task_id,
            status="failed",
            progress=100,
            stage="failed",
            message="检测失败",
            result_ids=result_ids,
            error=str(exc),
        )
    finally:
        db.close()
        _remove_temp_file(temp_path)


def _run_module_check(
    db: Session,
    module: str,
    temp_path: str,
    source_filename: str,
    project: Project,
    project_name: str,
    department: str | None,
    rule_source: str,
    use_llm: bool,
    selected_rule_ids: list[str],
    task_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    suffix = Path(temp_path).suffix.lower()
    content = Path(temp_path).read_bytes()

    if module == "function_correspondence":
        _ensure_extension(suffix, FUNCTION_CORRESPONDENCE_EXTENSIONS, module)
        raw = run_function_correspondence_check(
            report_file_path=temp_path,
            rule_source=rule_source,
            project_level="市级项目",
            use_llm=use_llm,
            selected_rule_ids=selected_rule_ids,
        )
        normalized = _normalize_function_or_sensitive_result(raw, module)
        return _merge_content_consistency_result(
            normalized=normalized,
            content=content,
            filename=source_filename,
            project_name=project_name,
            selected_rule_ids=selected_rule_ids,
            use_llm=use_llm,
        )

    if module == "basis":
        _ensure_extension(suffix, BASIS_EXTENSIONS, module)
        raw = run_basis_check_from_document(
            content=content,
            filename=source_filename,
            project_name=project_name,
            selected_rule_ids=selected_rule_ids,
        )
        return _normalize_document_rule_result(raw, module, use_llm=use_llm)

    if module == "security":
        _ensure_extension(suffix, DOCUMENT_RULE_EXTENSIONS, module)
        raw = run_security_check_from_document(
            content=content,
            filename=source_filename,
            project_name=project_name,
            selected_rule_ids=selected_rule_ids,
            use_llm=use_llm,
        )
        return _normalize_security_result(raw)

    if module == "sensitive_word":
        _ensure_extension(suffix, SENSITIVE_WORD_EXTENSIONS, module)
        raw = run_sensitive_word_check(
            report_file_path=temp_path,
            rule_source=rule_source,
            project_level="通用",
            use_llm=use_llm,
            selected_rule_ids=selected_rule_ids,
        )
        return _normalize_function_or_sensitive_result(raw, module)

    if module == "duplicate":
        _ensure_extension(suffix, DUPLICATE_EXTENSIONS, module)
        raw = _run_duplicate_module(
            db=db,
            content=content,
            filename=source_filename,
            project_id=project.id,
            project_name=project_name,
            department=department,
            use_llm=use_llm,
        )
        return _normalize_duplicate_result(raw)

    if module == "data_reasonableness":
        _ensure_extension(suffix, DOCUMENT_RULE_EXTENSIONS, module)
        raw = run_data_rules_check_from_document(
            content=content,
            filename=source_filename,
            project_name=project_name,
            selected_rule_ids=selected_rule_ids,
        )
        return _normalize_document_rule_result(raw, module, use_llm=use_llm)

    if module == "resource":
        _ensure_extension(suffix, RESOURCE_EXTENSIONS, module)
        try:
            raw = run_resource_check_from_file(
                db=db,
                content=content,
                filename=source_filename,
                project_id=project.id,
                project_name=project_name,
                department=department,
            )
        except Exception as exc:
            fallback = _resource_result_from_existing_checks(db, project.id, exc)
            if fallback.get("findings"):
                return fallback
            raise
        try:
            return _normalize_resource_result(raw, use_llm=use_llm)
        except Exception as exc:
            return _fallback_resource_result(raw, exc)

    if module in {"price", "price_reference"}:
        _ensure_extension(suffix, {".xlsx", ".xlsm", ".csv", ".docx"}, module)
        rule_ids = selected_rule_ids
        if not rule_ids:
            rule_ids = ["PRICE_REASON_001"] if module == "price" else ["PRICE_REF_001", "PRICE_REF_002"]
        parsed_document: ParsedPriceDocument | None = None
        if task_context is not None:
            parsed_document = task_context.get("price_document")
            if parsed_document is None:
                parsed_document = parse_price_document(content, source_filename)
                task_context["price_document"] = parsed_document
        raw = run_price_check_from_file(
            db=db,
            content=content,
            filename=source_filename,
            project_id=project.id,
            project_name=project_name,
            department=department,
            selected_rule_ids=rule_ids,
            parsed_document=parsed_document,
        )
        return _normalize_price_result(raw, module)

    raise ValueError(f"暂未接入检测模块：{module}")


def _run_duplicate_module(
    db: Session,
    content: bytes,
    filename: str,
    project_id: str | None,
    project_name: str,
    department: str | None,
    use_llm: bool = True,
) -> DuplicateInternalResponse:
    lower = filename.lower()
    if lower.endswith((".xlsx", ".csv")):
        return run_internal_duplicate_check(
            db=db,
            content=content,
            filename=filename,
            project_id=project_id,
            project_name=project_name,
            department=department,
            use_llm=use_llm,
        )
    if lower.endswith(".docx"):
        return run_duplicate_check_from_document(
            db=db,
            content=content,
            filename=filename,
            project_id=project_id,
            project_name=project_name,
            department=department,
            use_llm=use_llm,
        )
    raise ValueError("不支持的文件格式。重复建设检查支持：.xlsx, .csv, .docx")


def _merge_content_consistency_result(
    normalized: dict[str, Any],
    content: bytes,
    filename: str,
    project_name: str,
    selected_rule_ids: list[str],
    use_llm: bool,
) -> dict[str, Any]:
    """Attach five sub-rule result groups without mutating the original four-rule findings."""
    original_findings = list(normalized.get("findings") or [])
    rule_results = _build_function_rule_results(normalized, original_findings)
    rule_results.append(
        _build_content_consistency_rule_result(
            content=content,
            filename=filename,
            project_name=project_name,
            use_llm=use_llm,
        )
    )

    normalized["findings"] = original_findings
    normalized["rule_results"] = rule_results
    normalized["rule_results_summary"] = _summary_from_rule_results(rule_results)
    summary = normalized.setdefault("summary", {})
    if isinstance(summary, dict):
        summary["rule_results_count"] = len(rule_results)
        summary["rule_results_total_findings"] = normalized["rule_results_summary"]["total_findings"]
        summary["content_consistency_status"] = rule_results[-1].get("status")
    return normalized


def _build_function_rule_results(
    normalized: dict[str, Any],
    findings: list[dict[str, Any]],
    default_source: str = "function_correspondence",
) -> list[dict[str, Any]]:
    rules = [rule for rule in normalized.get("rules_used") or [] if isinstance(rule, dict)]
    if not rules:
        rules = _rules_from_findings(findings)

    rule_results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in rules:
        rule_id = str(rule.get("rule_id") or rule.get("id") or "").strip()
        rule_name = str(rule.get("rule_name") or rule.get("name") or "").strip()
        key = rule_id or rule_name
        if not key or key in seen or rule_id == CONTENT_CONSISTENCY_RULE_ID:
            continue
        seen.add(key)
        matched_findings = [
            dict(finding)
            for finding in findings
            if _finding_matches_rule(finding, rule_id, rule_name)
        ]
        rule_results.append(
            _make_rule_result(
                rule_id=rule_id,
                rule_name=rule_name or key,
                rule_category=str(rule.get("rule_category") or ""),
                rule_detail=str(rule.get("rule_detail") or rule.get("judgement_condition") or ""),
                source=str(rule.get("source") or default_source),
                findings=matched_findings,
            )
        )
    return rule_results


def _attach_function_rule_results(normalized: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible wrapper for callers using the previous helper name."""
    findings = [dict(item) for item in normalized.get("findings") or [] if isinstance(item, dict)]
    rule_results = _build_function_rule_results(normalized, findings)
    normalized["findings"] = findings
    normalized["rule_results"] = rule_results
    normalized["rule_results_summary"] = _summary_from_rule_results(rule_results)
    return normalized


def _rules_from_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for finding in findings:
        rule_id = str(finding.get("rule_id") or "").strip()
        rule_name = str(finding.get("rule_name") or finding.get("rule_basis") or "").strip()
        key = rule_id or rule_name
        if not key or key in seen:
            continue
        seen.add(key)
        rules.append({"rule_id": rule_id, "rule_name": rule_name})
    return rules


def _finding_matches_rule(finding: dict[str, Any], rule_id: str, rule_name: str) -> bool:
    finding_rule_id = str(finding.get("rule_id") or "").strip()
    finding_rule_name = str(finding.get("rule_name") or finding.get("rule_basis") or "").strip()
    if rule_id and finding_rule_id == rule_id:
        return True
    return bool(rule_name and finding_rule_name == rule_name)


def _build_content_consistency_rule_result(
    content: bytes,
    filename: str,
    project_name: str,
    use_llm: bool,
) -> dict[str, Any]:
    rule_id = CONTENT_CONSISTENCY_RULE_ID
    rule_name = CONTENT_CONSISTENCY_RULE_NAME
    suffix = Path(filename).suffix.lower()
    if suffix not in DOCUMENT_RULE_EXTENSIONS:
        return _make_rule_result(
            rule_id=rule_id,
            rule_name=rule_name,
            rule_category="一致性校验规则",
            rule_detail="检查需求描述、建设内容、数据产出和项目预算之间的功能点对应关系。",
            source="content_consistency",
            findings=[],
            status="跳过",
            message="第五条建设内容一致性专项校验仅支持 Word .docx 文件。",
        )

    try:
        consistency_raw = run_content_consistency_check_from_document(
            content=content,
            filename=filename,
            project_name=project_name,
        )
        consistency_result = _normalize_document_rule_result(
            consistency_raw,
            "content_consistency",
            use_llm=use_llm,
        )
        consistency_findings = [dict(item) for item in consistency_result.get("findings") or []]
        for finding in consistency_findings:
            finding["rule_id"] = rule_id
            finding["rule_name"] = rule_name
            finding["display_title"] = finding.get("display_title") or finding.get("issue_type") or rule_name
            finding["rule_basis"] = rule_name
            finding["source"] = "content_consistency"
            finding["risk_level"] = _normalize_issue_degree(finding.get("risk_level") or "需人工确认")

        raw_results = consistency_raw.get("results") or []
        raw_summary = _first_text([item.get("summary") for item in raw_results if isinstance(item, dict)])
        return _make_rule_result(
            rule_id=rule_id,
            rule_name=rule_name,
            rule_category="一致性校验规则",
            rule_detail="检查需求描述、建设内容、数据产出和项目预算之间的功能点对应关系。",
            source="content_consistency",
            findings=consistency_findings,
            status="发现问题" if consistency_findings else "通过",
            message=raw_summary or "建设内容一致性专项校验完成。",
            raw_result=consistency_raw,
        )
    except Exception as exc:
        finding = {
            "display_title": rule_name,
            "risk_level": "需人工确认",
            "review_opinion": f"第五条建设内容一致性专项校验执行失败：{exc}",
            "evidence_summary": "",
            "revision_advice": "请确认文件可解析且不是扫描件或加密文件；原四条规则结果不受影响。",
            "rule_basis": rule_name,
            "rule_id": rule_id,
            "rule_name": rule_name,
            "source_section": "建设内容一致性",
            "evidence_examples": [],
            "merged_count": 1,
            "source": "content_consistency",
        }
        return _make_rule_result(
            rule_id=rule_id,
            rule_name=rule_name,
            rule_category="一致性校验规则",
            rule_detail="检查需求描述、建设内容、数据产出和项目预算之间的功能点对应关系。",
            source="content_consistency",
            findings=[finding],
            status="执行失败",
            message=str(exc),
        )


def _make_rule_result(
    rule_id: str,
    rule_name: str,
    rule_category: str,
    rule_detail: str,
    source: str,
    findings: list[dict[str, Any]],
    status: str | None = None,
    message: str = "",
    raw_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = _summary_from_findings(findings)
    result = {
        "rule_id": rule_id,
        "rule_name": rule_name,
        "rule_category": rule_category,
        "rule_detail": rule_detail,
        "source": source,
        "status": status or _business_status_from_findings(findings),
        "passed": not findings,
        "finding_count": len(findings),
        "risk_level": _highest_risk(summary.get("risk_count") or {}),
        "summary": summary,
        "message": message,
        "findings": findings,
    }
    if raw_result is not None:
        result["raw_result"] = raw_result
    return result


def _summary_from_rule_results(rule_results: list[dict[str, Any]]) -> dict[str, Any]:
    all_findings: list[dict[str, Any]] = []
    for rule_result in rule_results:
        findings = rule_result.get("findings") or []
        if isinstance(findings, list):
            all_findings.extend(item for item in findings if isinstance(item, dict))
    summary = _summary_from_findings(all_findings)
    summary["rule_count"] = len(rule_results)
    return summary


def _selected_content_consistency_rule(
    normalized: dict[str, Any],
    selected_rule_ids: list[str],
) -> dict[str, Any] | None:
    selected_ids = {str(rule_id).strip() for rule_id in selected_rule_ids or [] if str(rule_id).strip()}
    rules_used = [rule for rule in normalized.get("rules_used") or [] if isinstance(rule, dict)]

    for rule in rules_used:
        rule_id = str(rule.get("rule_id") or "").strip()
        if rule_id in selected_ids and _is_content_consistency_rule(rule):
            return rule

    if selected_ids & CONTENT_CONSISTENCY_RULE_IDS:
        return {
            "rule_id": next(iter(selected_ids & CONTENT_CONSISTENCY_RULE_IDS)),
            "rule_name": "建设内容一致性校验规则",
        }

    if not selected_ids:
        for rule in rules_used:
            if _is_content_consistency_rule(rule):
                return rule
        return {
            "rule_id": CONTENT_CONSISTENCY_RULE_ID,
            "rule_name": "建设内容一致性校验规则",
        }

    return None


def _is_content_consistency_rule(rule: dict[str, Any]) -> bool:
    rule_id = str(rule.get("rule_id") or "").strip()
    rule_name = str(rule.get("rule_name") or "").strip()
    rule_text = " ".join(
        str(rule.get(key) or "")
        for key in ("rule_name", "rule_category", "rule_detail", "judgement_condition")
    )
    return (
        rule_id in CONTENT_CONSISTENCY_RULE_IDS
        or rule_name in CONTENT_CONSISTENCY_RULE_NAMES
        or ("建设内容一致性" in rule_text and "功能点" in rule_text)
    )


async def _save_upload_to_temp(file: UploadFile, allowed_extensions: set[str]) -> str:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    _ensure_extension(suffix, allowed_extensions, "uploaded_file")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件为空")

    fd, path = tempfile.mkstemp(prefix="aiassist_report_", suffix=suffix)
    with os.fdopen(fd, "wb") as target:
        target.write(content)
    return path


async def _read_upload(file: UploadFile, allowed_extensions: set[str]) -> bytes:
    suffix = Path(file.filename or "").suffix.lower()
    _ensure_extension(suffix, allowed_extensions, "uploaded_file")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件为空")
    return content


def _ensure_extension(suffix: str, allowed_extensions: set[str], module: str) -> None:
    if suffix not in allowed_extensions:
        supported = ", ".join(sorted(allowed_extensions))
        module_name = _module_display_name(module)
        raise HTTPException(status_code=400, detail=f"{module_name}不支持该文件格式。支持：{supported}")


def _remove_temp_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _parse_selected_rule_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_history_stages(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def _parse_selected_rule_ids_by_module(value: str | None) -> dict[str, list[str]]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        ids = _parse_selected_rule_ids(value)
        return {"function_correspondence": ids, "sensitive_word": ids}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, list[str]] = {}
    for module, ids in parsed.items():
        if isinstance(ids, str):
            result[str(module)] = _parse_selected_rule_ids(ids)
        elif isinstance(ids, list):
            result[str(module)] = [str(item).strip() for item in ids if str(item).strip()]
    return result


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
    check_run_id: str | None = None,
    report_name: str | None = None,
) -> str:
    serializable_result = jsonable_encoder(result)
    result_json = {
        "project_name": project.name,
        "report_name": report_name or result.get("report_name") or project.name,
        "checked_module": module,
        "result": serializable_result,
    }
    row = CheckResult(
        project_id=project.id,
        check_run_id=check_run_id,
        module=module,
        check_subtype="aggregate",
        severity=severity or "通过",
        result_label=str(serializable_result.get("status") or "completed"),
        reason=_json_dumps(_result_summary(serializable_result)),
        suggestion=suggestion,
        reference_data=_json_dumps(result_json),
        model_name=str(serializable_result.get("model_name") or "local-python-checker"),
    )
    db.add(row)
    db.flush()
    return row.id


def _store_duplicate_source_artifacts(
    db: Session,
    result_id: str,
    current: tuple[bytes, str],
    history: list[tuple[bytes, str]],
    current_stage: str,
    history_stages: list[str],
) -> list[dict[str, Any]]:
    """保存跨报告比较涉及的文件快照，并返回可供详情 JSON 使用的入口。"""
    documents: list[tuple[str, str, tuple[bytes, str]]] = [("current", current_stage, current)]
    for index, payload in enumerate(history):
        stage = (
            history_stages[index]
            if index < len(history_stages) and history_stages[index]
            else f"往期{index + 1}"
        )
        documents.append(("history", stage, payload))

    result: list[dict[str, Any]] = []
    for role, stage, (content, filename) in documents:
        safe_filename = filename or "未命名报告"
        media_type = mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
        artifact = SourceArtifact(
            check_result_id=result_id,
            role=role,
            stage=stage,
            filename=safe_filename,
            media_type=media_type,
            content=content,
        )
        db.add(artifact)
        db.flush()
        result.append(
            {
                "id": artifact.id,
                "role": role,
                "stage": stage,
                "filename": safe_filename,
                "media_type": media_type,
                "download_url": f"/api/evaluate/results/{result_id}/sources/{artifact.id}",
                "preview_url": f"/api/evaluate/results/{result_id}/sources/{artifact.id}/preview",
            }
        )
    return result


def _store_current_source_artifact(
    db: Session,
    result_id: str,
    content: bytes,
    filename: str,
) -> SourceArtifact:
    safe_filename = filename or "未命名报告"
    artifact = SourceArtifact(
        check_result_id=result_id,
        role="current",
        stage="本期",
        filename=safe_filename,
        media_type=mimetypes.guess_type(safe_filename)[0] or "application/octet-stream",
        content=content,
    )
    db.add(artifact)
    db.flush()
    return artifact


def _store_result_source_artifact(
    db: Session,
    result_id: str,
    content: bytes,
    filename: str,
) -> None:
    """Attach the original Word/text/spreadsheet snapshot to every standalone result."""
    _store_current_source_artifact(db, result_id, content, filename)


def _render_docx_preview(content: bytes, filename: str) -> str:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(io.BytesIO(content))
    blocks: list[str] = []
    paragraph_index = 0
    line_index = 0

    def render_run(run: Any) -> str:
        value = html.escape(run.text or "").replace("\n", "<br>")
        if value and run.bold:
            value = f"<strong>{value}</strong>"
        if value and run.italic:
            value = f"<em>{value}</em>"
        images: list[str] = []
        for blip in run._element.xpath(".//a:blip"):
            relationship_id = blip.get(qn("r:embed"))
            if not relationship_id:
                continue
            part = document.part.related_parts.get(relationship_id)
            if part is None:
                continue
            encoded = base64.b64encode(part.blob).decode("ascii")
            images.append(f'<img src="data:{part.content_type};base64,{encoded}" alt="文档图片">')
        return value + "".join(images)

    def render_paragraph(paragraph: Any) -> str:
        nonlocal paragraph_index, line_index
        paragraph_index += 1
        raw_text = paragraph.text.strip()
        if raw_text:
            line_index += 1
        body = "".join(render_run(run) for run in paragraph.runs)
        if not body and not raw_text:
            body = "&nbsp;"
        style_name = str(getattr(paragraph.style, "name", "") or "").lower()
        tag = "p"
        if "heading 1" in style_name or "标题 1" in style_name:
            tag = "h1"
        elif "heading 2" in style_name or "标题 2" in style_name:
            tag = "h2"
        elif "heading" in style_name or "标题" in style_name:
            tag = "h3"
        return (
            f'<{tag} class="source-block" data-paragraph="{paragraph_index}" '
            f'data-line="{line_index if raw_text else 0}" data-text="{html.escape(raw_text, quote=True)}">'
            f"{body}</{tag}>"
        )

    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            blocks.append(render_paragraph(Paragraph(child, document)))
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            rows: list[str] = []
            for row in table.rows:
                cells: list[str] = []
                for cell in row.cells:
                    cell_html = "".join(render_paragraph(paragraph) for paragraph in cell.paragraphs)
                    cells.append(f'<td class="source-block table-cell" data-text="{html.escape(cell.text.strip(), quote=True)}">{cell_html}</td>')
                rows.append(f"<tr>{''.join(cells)}</tr>")
            blocks.append(f'<table class="document-table"><tbody>{"".join(rows)}</tbody></table>')

    return _source_preview_page(filename, "".join(blocks))


def _render_plain_source_preview(content: bytes, filename: str) -> str:
    text = content.decode("utf-8-sig", errors="replace")
    blocks = []
    for index, line in enumerate(text.splitlines(), start=1):
        blocks.append(
            f'<p class="source-block" data-line="{index}" data-paragraph="{index}" '
            f'data-text="{html.escape(line, quote=True)}">{html.escape(line) or "&nbsp;"}</p>'
        )
    return _source_preview_page(filename, "".join(blocks))


def _source_preview_page(filename: str, body: str) -> str:
    safe_title = html.escape(filename)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title}</title>
<style>
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; background: #eef2f7; }}
  body {{ margin: 0; color: #172033; font-family: "Microsoft YaHei", "Segoe UI", sans-serif; }}
  .paper {{ width: min(920px, calc(100% - 32px)); min-height: calc(100vh - 32px); margin: 16px auto; padding: 54px 60px 72px; background: white; box-shadow: 0 2px 14px rgba(15,23,42,.12); }}
  p {{ margin: 0 0 12px; font-size: 15px; line-height: 1.8; white-space: pre-wrap; }}
  h1, h2, h3 {{ margin: 22px 0 12px; line-height: 1.45; letter-spacing: 0; }}
  h1 {{ font-size: 24px; }} h2 {{ font-size: 20px; }} h3 {{ font-size: 17px; }}
  img {{ display: block; max-width: 100%; height: auto; margin: 12px auto; }}
  .document-table {{ width: 100%; margin: 16px 0; border-collapse: collapse; table-layout: fixed; }}
  .document-table td {{ padding: 7px 8px; border: 1px solid #aeb8c6; vertical-align: top; overflow-wrap: anywhere; }}
  .document-table p {{ margin: 0; font-size: 13px; line-height: 1.55; }}
  .source-block.source-target {{ background: #fff0a6; outline: 3px solid #f59e0b; outline-offset: 3px; border-radius: 2px; transition: background .2s ease; }}
  .location-note {{ position: fixed; right: 18px; bottom: 18px; z-index: 5; max-width: min(440px, calc(100% - 36px)); padding: 9px 12px; color: #713f12; background: #fffbeb; border: 1px solid #f59e0b; border-radius: 6px; box-shadow: 0 4px 18px rgba(15,23,42,.16); font-size: 13px; opacity: 0; pointer-events: none; transition: opacity .18s ease; }}
  .location-note.show {{ opacity: 1; }}
  @media (max-width: 720px) {{ .paper {{ width: 100%; margin: 0; padding: 32px 24px 56px; box-shadow: none; }} }}
</style>
</head>
<body>
<main class="paper">{body}</main>
<div id="location-note" class="location-note">已定位到审查依据原文</div>
<script>
  const normalize = value => (value || '').replace(/\\s+/g, '').replace(/[，。；：、“”‘’（）()]/g, '').toLowerCase();
  window.locateSource = function(quote, paragraph, line, section) {{
    const blocks = Array.from(document.querySelectorAll('.source-block'));
    blocks.forEach(block => block.classList.remove('source-target'));
    const candidates = [quote, section].map(normalize).filter(value => value.length >= 2);
    let target = null;
    for (const candidate of candidates) {{
      target = blocks.find(block => {{
        const value = normalize(block.dataset.text || block.innerText);
        return value.includes(candidate) || (value.length >= 8 && candidate.includes(value));
      }});
      if (target) break;
    }}
    if (!target && paragraph) target = document.querySelector(`[data-paragraph="${{paragraph}}"]`);
    if (!target && line) target = document.querySelector(`[data-line="${{line}}"]`);
    if (!target) return false;
    target.classList.add('source-target');
    target.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    const note = document.getElementById('location-note');
    note.classList.add('show');
    window.clearTimeout(window.__locationTimer);
    window.__locationTimer = window.setTimeout(() => note.classList.remove('show'), 1800);
    return true;
  }};
</script>
</body>
</html>"""


def _json_dumps(value: Any) -> str:
    return json.dumps(jsonable_encoder(value), ensure_ascii=False, default=str)


def _normalize_function_or_sensitive_result(result: dict[str, Any], module: str) -> dict[str, Any]:
    normalized = dict(result)
    normalized.setdefault("module_code", module)
    normalized.setdefault("module_name", _module_display_name(module))
    normalized.setdefault("status", "completed")
    normalized.setdefault("findings", [])
    if "summary" not in normalized and "risk_summary" in normalized:
        normalized["summary"] = normalized["risk_summary"]
    normalized["summary"] = _ensure_summary(normalized.get("summary"), len(normalized.get("findings") or []))
    return normalized


def _normalize_duplicate_result(result: DuplicateInternalResponse) -> dict[str, Any]:
    data = result.model_dump(mode="json")
    pairs = data.get("pairs") or []
    findings = []
    for pair in pairs:
        comparison_type = str(pair.get("comparison_type") or "internal")
        is_cross = comparison_type == "cross_report"
        prefix = "与往期报告重复" if is_cross else "本报告内部重复"
        left_report = str(pair.get("item_report_name") or "当前报告")
        right_report = str(pair.get("related_report_name") or ("往期报告" if is_cross else "当前报告"))
        evidence_parts = [f"相似度：{float(pair.get('similarity') or 0):.2%}"]
        if is_cross:
            evidence_parts.append(f"当前：{left_report}")
            evidence_parts.append(f"往期：{right_report}")
        findings.append(
            {
                "display_title": f"【{prefix}】{pair.get('item_name', '')} ↔ {pair.get('related_item_name', '')}".strip(" ↔"),
                "risk_level": _normalize_issue_degree(pair.get("severity") or pair.get("result_label") or "需人工确认"),
                "review_opinion": pair.get("reason") or pair.get("result_label") or "疑似重复建设，请复核。",
                "evidence_summary": "；".join(evidence_parts),
                "revision_advice": pair.get("suggestion") or "请核对两个功能点是否存在建设内容、服务对象或实现范围重复。",
                "rule_basis": "功能点相似度预筛与大模型语义复核",
                "source_section": prefix,
                "comparison_type": comparison_type,
                "item_report_name": left_report,
                "related_report_name": right_report,
                "item_stage": pair.get("item_stage") or "",
                "related_stage": pair.get("related_stage") or "",
                "item_row_index": pair.get("item_row_index"),
                "related_row_index": pair.get("related_row_index"),
                "item_source_location": pair.get("item_source_location") or {},
                "related_source_location": pair.get("related_source_location") or {},
                "item_source_excerpt": pair.get("item_source_excerpt") or "",
                "related_source_excerpt": pair.get("related_source_excerpt") or "",
                "location_hint": pair.get("location_hint"),
                "llm_refined": pair.get("model_name") == "deepseek",
                "model_name": pair.get("model_name") or "",
                "merged_count": 1,
            }
        )
    internal_count = len(data.get("internal_pairs") or [pair for pair in pairs if (pair.get("comparison_type") or "internal") == "internal"])
    cross_count = len(data.get("cross_pairs") or [pair for pair in pairs if pair.get("comparison_type") == "cross_report"])
    summary = _summary_from_findings(
        findings,
        imported_count=data.get("imported_count", 0),
        history_imported_count=data.get("history_imported_count", 0),
        internal_duplicate_count=internal_count,
        cross_report_duplicate_count=cross_count,
    )
    summary["llm_model"] = _first_text([pair.get("model_name") for pair in pairs if pair.get("model_name")]) or "deepseek"
    return {
        "module_code": "duplicate",
        "module_name": _module_display_name("duplicate"),
        "status": "completed",
        "imported_count": data.get("imported_count", 0),
        "history_imported_count": data.get("history_imported_count", 0),
        "threshold": data.get("threshold", 0),
        "summary": summary,
        "findings": findings,
        "internal_pairs": data.get("internal_pairs") or [],
        "cross_pairs": data.get("cross_pairs") or [],
        "raw_result": data,
    }


def _normalize_price_result(result: dict[str, Any], module: str) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for finding in result.get("findings") or []:
        item = dict(finding)
        item.setdefault("display_title", item.get("rule_name") or _module_display_name(module))
        item.setdefault("review_opinion", item.get("reason") or item.get("message") or "价格规则需要复核。")
        item.setdefault("revision_advice", item.get("suggestion") or "请补充价格依据。")
        item.setdefault("rule_basis", item.get("rule_name") or "价格规则")
        findings.append(item)
    summary = dict(result.get("summary") or {})
    notices = result.get("notices") or []
    summary["total_findings"] = len(findings)
    summary["notice_count"] = len(notices)
    summary["risk_summary"] = dict(Counter(item.get("risk_level") for item in findings if item.get("risk_level")))
    return {
        "module_code": module,
        "module_name": _module_display_name(module),
        "status": "发现问题" if findings else "部分完成" if notices else "通过",
        "summary": summary,
        "findings": findings,
        "notices": notices,
        "rules_used": result.get("rules_used") or [],
        "items": result.get("items") or [],
        "rule_results": result.get("rule_results") or [],
        "project_id": result.get("project_id"),
    }


def _normalize_document_rule_result(result: dict[str, Any], module: str, use_llm: bool = False) -> dict[str, Any]:
    raw_results = result.get("results") or []
    findings: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    for rule_result in raw_results:
        rule_name = str(rule_result.get("rule_name") or _module_display_name(module))
        rule_id = str(rule_result.get("rule_id") or rule_result.get("rule_excel_row") or rule_name)
        rules.append(
            {
                "rule_id": rule_id,
                "rule_name": rule_name,
                "rule_category": rule_result.get("rule_category") or "",
                "rule_detail": rule_result.get("rule_description") or rule_result.get("judgement_condition") or "",
                "source": module,
            }
        )
        issues = rule_result.get("issues") or []
        suggestions = rule_result.get("suggestions") or []
        if not issues and not rule_result.get("passed", False):
            issues = [{"message": rule_result.get("summary") or "该规则需要人工复核"}]
        for issue in issues:
            issue_type = issue.get("issue_type") or rule_result.get("issue_type")
            item = issue.get("item")
            evidence = issue.get("evidence")
            findings.append(
                {
                    "display_title": issue_type or rule_name,
                    "issue_type": issue_type,
                    "item": item,
                    "raw_item": issue.get("raw_item"),
                    "feature": item or issue.get("feature"),
                    "risk_level": _normalize_issue_degree(issue.get("severity") or rule_result.get("severity") or "需人工确认"),
                    "review_opinion": issue.get("message") or rule_result.get("summary") or "规则检查发现需复核事项。",
                    # Keep the parser's original evidence separate from the
                    # generated display summary. The former is the only safe
                    # value to use for exact Word source matching.
                    "evidence": evidence,
                    "evidence_summary": (
                        f"系统在“{issue.get('section') or module}”中识别到该功能点对应关系不足；原始条目已保留在证据明细。"
                        if item and evidence
                        else evidence or issue.get("section") or ""
                    ),
                    "revision_advice": _first_text(suggestions) or "请根据规则要求补充说明、核对数据或调整相关章节表述。",
                    "rule_basis": rule_name,
                    "rule_id": rule_id,
                    "rule_name": rule_name,
                    "source_section": issue.get("section") or module,
                    "evidence_examples": [item for item in [issue.get("evidence"), rule_result.get("summary")] if item],
                    "merged_count": 1,
                }
            )
    findings, llm_meta = refine_findings(
        module_code=module,
        findings=findings,
        rules=rules,
        use_llm=use_llm,
    )
    summary = _summary_from_findings(findings, checked_rule_count=len(raw_results))
    summary.update(llm_meta)
    status = _business_status_from_findings(findings) if module == "data_reasonableness" else result.get("status") or "completed"
    normalized = {
        "module_code": module,
        "module_name": _module_display_name(module),
        "status": status,
        "summary": summary,
        "findings": findings,
        "rules_used": rules,
        "raw_result": result,
    }
    if module in {"basis", "data_reasonableness"}:
        rule_results = _build_function_rule_results(normalized, findings, default_source=module)
        normalized["rule_results"] = rule_results
        normalized["rule_results_summary"] = _summary_from_rule_results(rule_results)
        summary["rule_results_count"] = len(rule_results)
        summary["rule_results_total_findings"] = normalized["rule_results_summary"]["total_findings"]
    return normalized


def _normalize_security_result(result: dict[str, Any]) -> dict[str, Any]:
    payload = dict(result)
    findings = []
    for item in payload.get("findings") or []:
        findings.append(
            {
                "display_title": item.get("display_title") or "安全需求分析合规性审查规则",
                "risk_level": _normalize_issue_degree(item.get("risk_level") or "需人工确认"),
                "review_opinion": item.get("review_opinion") or "",
                "evidence_summary": item.get("evidence_summary") or "",
                "revision_advice": item.get("revision_advice") or "请补充4.7安全需求分析中的安全风险分析、安全等级定位、数据分类分级、安全防护措施和密码应用措施。",
                "rule_basis": item.get("rule_basis") or "安全需求分析合规性审查规则",
                "rule_id": item.get("rule_id") or "SECURITY_REASON_001",
                "rule_name": item.get("rule_name") or "安全需求分析合规性审查规则",
                "rule_category": item.get("rule_category") or "内容合规性审查规则",
                "source_section": item.get("source_section") or "4.7安全需求分析",
                "evidence_examples": item.get("evidence_examples") or [],
                "merged_count": int(item.get("merged_count") or 1),
                "llm_refined": bool(item.get("llm_refined")),
                "llm_review": item.get("llm_review") or {},
                "raw_issue_type": item.get("raw_issue_type") or item.get("rule_name") or "安全内容合理性",
            }
        )

    summary = dict(payload.get("summary") or {})
    summary["total_findings"] = len(findings)
    summary["risk_count"] = _summary_from_findings(findings).get("risk_count", {})
    summary.setdefault("llm_display_mode", "security_review")
    summary.setdefault("rule_source", "deepseek" if summary.get("llm_enabled") else "local_fallback")
    payload.update(
        {
            "module_code": "security",
            "module_name": _module_display_name("security"),
            "status": "通过" if not findings else "发现问题",
            "summary": summary,
            "findings": findings,
        }
    )
    return payload


def _normalize_resource_result(result: ResourceCheckResponse, use_llm: bool = False) -> dict[str, Any]:
    data = result.model_dump(mode="json")
    findings = []
    rules: list[dict[str, Any]] = []
    for item in data.get("findings") or []:
        rules.append(
            {
                "rule_id": item.get("rule_code") or item.get("rule_name") or "",
                "rule_name": item.get("rule_name") or "",
                "rule_category": "资源申请合理性检查",
                "rule_detail": item.get("result_label") or item.get("reason") or "",
            }
        )
        risk_level = _normalize_issue_degree(item.get("severity") or "需人工确认")
        if risk_level == "通过":
            continue
        quantities = item.get("source_quantities") or {}
        evidence = "；".join(f"{key}: {value}" for key, value in quantities.items())
        findings.append(
            {
                "display_title": item.get("resource_name") or item.get("rule_name") or "资源申请检查项",
                "risk_level": risk_level,
                "review_opinion": item.get("reason") or item.get("result_label") or "资源申请存在需复核事项。",
                "evidence_summary": evidence,
                "revision_advice": item.get("suggestion") or "请核对资源申请数量、用途说明和相关章节/清单是否一致。",
                "rule_basis": item.get("rule_name") or item.get("rule_code") or "资源申请合理性规则",
                "rule_id": item.get("rule_code") or item.get("rule_name") or "",
                "rule_name": item.get("rule_name") or "",
                "source_section": "资源申请清单",
                "row_indexes": item.get("row_indexes") or [],
                "source_locations": item.get("source_locations") or [],
                "evidence": item.get("evidence"),
                "location_hint": item.get("location_hint"),
                "evidence_examples": [value for value in [evidence, item.get("reason")] if value],
                "merged_count": 1,
            }
        )
    findings, llm_meta = refine_findings(
        module_code="resource",
        findings=findings,
        rules=rules,
        use_llm=use_llm,
    )
    summary = _summary_from_findings(
        findings,
        imported_count=data.get("imported_count", 0),
        checked_rule_count=data.get("checked_rule_count", 0),
    )
    summary.update(llm_meta)
    return {
        "module_code": "resource",
        "module_name": _module_display_name("resource"),
        "status": _business_status_from_findings(findings),
        "imported_count": data.get("imported_count", 0),
        "checked_rule_count": data.get("checked_rule_count", 0),
        "summary": summary,
        "findings": findings,
        "raw_result": data,
    }


def _fallback_resource_result(result: ResourceCheckResponse, exc: Exception) -> dict[str, Any]:
    data = result.model_dump(mode="json")
    findings = []
    for item in data.get("findings") or []:
        risk_level = _normalize_issue_degree(item.get("severity") or "需人工确认")
        if risk_level == "通过":
            continue
        quantities = item.get("source_quantities") or {}
        evidence = "；".join(f"{key}: {value}" for key, value in quantities.items())
        findings.append(
            {
                "display_title": item.get("resource_name") or item.get("rule_name") or "资源申请检查项",
                "risk_level": risk_level,
                "review_opinion": item.get("reason") or item.get("result_label") or "资源申请存在需复核事项。",
                "evidence_summary": evidence,
                "revision_advice": item.get("suggestion") or "请核对资源申请数量、用途说明和相关章节/清单是否一致。",
                "rule_basis": item.get("rule_name") or item.get("rule_code") or "资源申请合理性规则",
                "row_indexes": item.get("row_indexes") or [],
                "source_locations": item.get("source_locations") or [],
                "evidence": item.get("evidence"),
                "location_hint": item.get("location_hint"),
                "merged_count": 1,
                "llm_refined": False,
            }
        )
    summary = _summary_from_findings(
        findings,
        imported_count=data.get("imported_count", 0),
        checked_rule_count=data.get("checked_rule_count", 0),
    )
    summary.update(
        {
            "llm_enabled": False,
            "llm_status": "fallback",
            "llm_error": str(exc),
            "llm_reviewed_count": 0,
            "llm_error_count": 1,
            "llm_skipped_count": len(findings),
        }
    )
    return {
        "module_code": "resource",
        "module_name": _module_display_name("resource"),
        "status": _business_status_from_findings(findings),
        "summary": summary,
        "findings": findings,
        "raw_result": data,
    }


def _resource_result_from_existing_checks(db: Session, project_id: str, exc: Exception) -> dict[str, Any]:
    rows = (
        db.query(CheckResult)
        .filter(
            CheckResult.project_id == project_id,
            CheckResult.module == "resource",
            CheckResult.check_subtype == "resource_rule_15_16",
        )
        .order_by(CheckResult.created_at.desc())
        .all()
    )
    findings: list[dict[str, Any]] = []
    for row in rows:
        risk_level = _normalize_issue_degree(row.severity or "需人工确认")
        if risk_level == "通过":
            continue
        findings.append(
            {
                "display_title": row.result_label or "资源申请检查项",
                "risk_level": risk_level,
                "review_opinion": row.reason,
                "evidence_summary": row.reference_data or "",
                "revision_advice": row.suggestion or "请核对资源申请数量、用途说明和相关章节/清单是否一致。",
                "rule_basis": "资源申请合理性规则",
                "row_indexes": _reference_row_indexes(row.reference_data),
                "source_locations": _reference_source_locations(row.reference_data),
                "merged_count": 1,
                "llm_refined": False,
            }
        )
    summary = _summary_from_findings(findings)
    summary.update(
        {
            "llm_enabled": False,
            "llm_status": "fallback",
            "resource_fallback_error": str(exc),
            "llm_reviewed_count": 0,
            "llm_error_count": 1,
            "llm_skipped_count": len(findings),
        }
    )
    return {
        "module_code": "resource",
        "module_name": _module_display_name("resource"),
        "status": _business_status_from_findings(findings),
        "summary": summary,
        "findings": findings,
    }


def _reference_row_indexes(reference_data: str | None) -> list[int]:
    try:
        payload = json.loads(reference_data or "{}")
    except (TypeError, ValueError):
        return []
    values = payload.get("row_indexes") if isinstance(payload, dict) else []
    return [int(value) for value in values or [] if str(value).strip().isdigit()]


def _reference_source_locations(reference_data: str | None) -> list[dict[str, Any]]:
    try:
        payload = json.loads(reference_data or "{}")
    except (TypeError, ValueError):
        return []
    values = payload.get("source_locations") if isinstance(payload, dict) else []
    return values if isinstance(values, list) else []


def _business_status_from_findings(findings: list[dict[str, Any]]) -> str:
    return "发现问题" if findings else "通过"


def _attach_finding_locations(
    result: dict[str, Any],
    content: bytes,
    filename: str,
) -> dict[str, Any]:
    """Add stable, machine-readable source locations without changing finding text."""
    lines = _extract_location_lines(content, filename)

    def enrich(finding: dict[str, Any]) -> None:
        if not isinstance(finding, dict):
            return
        existing = finding.get("source_location")
        location = dict(existing) if isinstance(existing, dict) else {}
        location.setdefault("file_name", filename)
        quote_values = [
            finding.get("evidence"),
            finding.get("context"),
            finding.get("hit_text"),
            finding.get("item_name"),
            finding.get("item"),
            finding.get("raw_item"),
            finding.get("item_source_excerpt"),
            finding.get("related_source_excerpt"),
        ]
        for key in ("evidence_examples", "context_examples"):
            values = finding.get(key)
            if isinstance(values, list):
                quote_values.extend(values)
        quote = next((str(value).strip() for value in quote_values if str(value or "").strip()), "")

        if finding.get("page_no") is not None:
            location.setdefault("page", finding.get("page_no"))
        if finding.get("paragraph_index") is not None:
            location.setdefault("paragraph", finding.get("paragraph_index"))
        if isinstance(finding.get("start"), int) and finding.get("start", -1) >= 0:
            location.setdefault("char_start", finding.get("start"))
        if isinstance(finding.get("end"), int) and finding.get("end", -1) >= 0:
            location.setdefault("char_end", finding.get("end"))
        if finding.get("source_row") is not None:
            location.setdefault("row", finding.get("source_row"))
        if finding.get("affected_row_start") is not None:
            location.setdefault("row_start", finding.get("affected_row_start"))
        if finding.get("affected_row_end") is not None:
            location.setdefault("row_end", finding.get("affected_row_end"))
        if finding.get("item_row_index") is not None:
            location.setdefault("item_row", finding.get("item_row_index"))
        if finding.get("related_row_index") is not None:
            location.setdefault("related_row", finding.get("related_row_index"))
        if finding.get("row_indexes"):
            location.setdefault("rows", finding.get("row_indexes"))
        if finding.get("source_locations"):
            location.setdefault("entries", finding.get("source_locations"))
        if finding.get("item_source_location"):
            location.setdefault("current", finding.get("item_source_location"))
        if finding.get("related_source_location"):
            location.setdefault("related", finding.get("related_source_location"))

        section = str(finding.get("source_section") or finding.get("section") or "").strip()
        if section:
            location.setdefault("section", section)

        match = None
        row_hint = finding.get("source_row") or finding.get("item_row_index")
        if row_hint is None and finding.get("row_indexes"):
            row_hint = (finding.get("row_indexes") or [None])[0]
        if row_hint is not None:
            try:
                row_number = int(row_hint)
            except (TypeError, ValueError):
                row_number = 0
            if row_number > 0:
                match = next((entry for entry in lines if entry.get("table_row") == row_number), None)
        # Prefer evidence that really occurs in the document. A generated
        # summary or section label may also match, but only after concrete
        # hit text, item names, and excerpts have had a chance.
        concrete_candidates = [
            str(value or "").strip()
            for value in quote_values
            if str(value or "").strip()
        ]
        # Evidence summaries may contain several source lines joined by a
        # delimiter. Try each bounded fragment so a real line can be matched
        # without treating the whole generated summary as document text.
        evidence_summary = str(finding.get("evidence_summary") or "").strip()
        if evidence_summary:
            for fragment in re.split(r"\s*(?:\||；|;|\n)\s*", evidence_summary):
                fragment = fragment.strip()
                if fragment and fragment not in concrete_candidates:
                    concrete_candidates.append(fragment)
        for candidate in concrete_candidates:
            if match:
                break
            if candidate:
                match = _find_location_line(lines, candidate)
            if match:
                break
        # Rule engines often emit a generated summary as evidence. It is not
        # expected to occur verbatim in the report, so fall back to the real
        # section heading before exposing a text-only location.
        if match is None and section:
            match = _find_section_location_line(lines, section)
        if match:
            location.setdefault("line_start", match["line"])
            location.setdefault("line_end", match["line"])
            if match.get("page") is not None:
                location.setdefault("page", match["page"])
            if match.get("paragraph") is not None:
                location.setdefault("paragraph", match["paragraph"])
            location.setdefault("quote", match["text"])
            location.setdefault("precision", "line")
        elif quote and quote not in {section, "全文"}:
            # Keep a bounded excerpt even when the parser cannot resolve a
            # concrete line. Clients can render this as a text highlight.
            location.setdefault("quote", quote[:500])
            location.setdefault("precision", "text")
        elif section and len("".join(section.split())) >= 2:
            section_match = _find_section_location_line(lines, section)
            if section_match:
                location.setdefault("line_start", section_match["line"])
                location.setdefault("line_end", section_match["line"])
                if section_match.get("paragraph") is not None:
                    location.setdefault("paragraph", section_match["paragraph"])
                location.setdefault("quote", section_match["text"])
                location.setdefault("precision", "line")
            else:
                location.setdefault("precision", "section")
        else:
            location.setdefault("precision", "section" if section else "unknown")

        if location:
            for location_key, excerpt_key in (
                ("current", "item_source_excerpt"),
                ("related", "related_source_excerpt"),
            ):
                nested = location.get(location_key)
                if not isinstance(nested, dict):
                    continue
                nested_location = dict(nested)
                nested_quote = str(finding.get(excerpt_key) or nested_location.get("quote") or "").strip()
                nested_match = _find_location_line(lines, nested_quote, allow_short=True) if nested_quote else None
                if nested_match:
                    nested_location.setdefault("line_start", nested_match["line"])
                    nested_location.setdefault("line_end", nested_match["line"])
                    if nested_match.get("page") is not None:
                        nested_location.setdefault("page", nested_match["page"])
                    if nested_match.get("paragraph") is not None:
                        nested_location.setdefault("paragraph", nested_match["paragraph"])
                    nested_location.setdefault("quote", nested_match["text"])
                    nested_location.setdefault("precision", "line")
                location[location_key] = nested_location
            finding["source_location"] = location
            finding["location_hint"] = _format_location_hint(location)
            highlights = _build_source_highlights(finding, location)
            finding["source_highlights"] = highlights
            finding["revision_target"] = {
                "location_hint": finding["location_hint"],
                "source_location": location,
                "highlights": highlights,
                "target_type": "source_text" if highlights else "section_or_document",
                "advice": str(
                    finding.get("revision_advice")
                    or finding.get("suggestion")
                    or ""
                ),
            }

    def enrich_findings(items: Any) -> None:
        if isinstance(items, list):
            for item in items:
                enrich(item)

    enrich_findings(result.get("findings"))
    for rule_result in result.get("rule_results") or []:
        if isinstance(rule_result, dict):
            enrich_findings(rule_result.get("findings"))
    return result


def _build_source_highlights(
    finding: dict[str, Any],
    location: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build explicit source excerpts that a client can render as highlights."""
    candidates: list[tuple[str, dict[str, Any], str]] = []
    current = location.get("current")
    related = location.get("related")
    entries = location.get("entries")
    if isinstance(current, dict):
        candidates.append(("current", current, str(finding.get("item_source_excerpt") or "")))
    if isinstance(related, dict):
        candidates.append(("related", related, str(finding.get("related_source_excerpt") or "")))
    if isinstance(entries, list):
        for index, entry in enumerate(entries, start=1):
            if isinstance(entry, dict):
                candidates.append((f"evidence_{index}", entry, str(entry.get("quote") or "")))
    if not candidates:
        candidates.append(
            (
                "current",
                location,
                str(
                    location.get("quote")
                    or finding.get("hit_text")
                    or finding.get("evidence")
                    or finding.get("evidence_summary")
                    or finding.get("context")
                    or next(
                        (
                            str(value)
                            for key in ("evidence_examples", "context_examples")
                            for value in (finding.get(key) or [])
                            if str(value or "").strip()
                        ),
                        "",
                    )
                    or ""
                ),
            )
        )

    highlights: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for role, source, fallback_quote in candidates:
        # Some modules keep the excerpt on the finding while the location
        # object only carries row/page metadata. Use that fallback so the
        # JSON contract still contains a renderable highlight.
        source_quote = str(source.get("quote") or fallback_quote or "").strip()
        quote_text = source_quote[:500]
        if not quote_text:
            continue
        item = {
            "role": role,
            "file_name": source.get("file_name") or location.get("file_name"),
            "file_type": source.get("file_type"),
            "sheet_name": source.get("sheet_name"),
            "page": source.get("page"),
            "paragraph": source.get("paragraph"),
            "line_start": source.get("line_start"),
            "line_end": source.get("line_end"),
            "row_index": source.get("row_index") or source.get("row"),
            "section": source.get("section"),
            "char_start": source.get("char_start"),
            "char_end": source.get("char_end"),
            "quote": quote_text,
            "precision": source.get("precision") or location.get("precision") or "unknown",
            "highlight": True,
        }
        key = (
            item["role"], item["file_name"], item["page"], item["line_start"],
            item["row_index"], item["section"], item["quote"],
        )
        if key in seen:
            continue
        seen.add(key)
        highlights.append(item)
    return highlights


def _extract_location_lines(content: bytes, filename: str) -> list[dict[str, Any]]:
    suffix = Path(filename).suffix.lower()
    try:
        if suffix in {".txt", ".md", ".csv"}:
            return [
                {"text": line, "line": index, "page": None, "paragraph": index}
                for index, line in enumerate(content.decode("utf-8-sig", errors="replace").splitlines(), start=1)
                if line.strip()
            ]
        if suffix == ".docx":
            from docx import Document

            document = Document(io.BytesIO(content))
            entries: list[dict[str, Any]] = []
            line_index = 0
            for paragraph_index, paragraph in enumerate(document.paragraphs, start=1):
                text = paragraph.text.strip()
                if not text:
                    continue
                line_index += 1
                entries.append({"text": text, "line": line_index, "page": None, "paragraph": paragraph_index})
            for table in document.tables:
                for row_index, row in enumerate(table.rows, start=1):
                    text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                    if text:
                        line_index += 1
                        entries.append({
                            "text": text,
                            "line": line_index,
                            "page": None,
                            "paragraph": None,
                            "table_row": row_index,
                        })
            return entries
    except Exception:
        return []
    return []


def _find_location_line(
    lines: list[dict[str, Any]],
    quote: str,
    allow_short: bool = False,
) -> dict[str, Any] | None:
    compact_quote = "".join(str(quote or "").split())
    if len(compact_quote) < (2 if allow_short else 4):
        return None
    for entry in lines:
        line = str(entry.get("text") or "")
        if quote in line:
            return entry
        compact_line = "".join(line.split())
        if compact_quote in compact_line:
            return entry
        if len(compact_line) >= 8 and compact_line in compact_quote:
            return entry
    return None


def _find_section_location_line(
    lines: list[dict[str, Any]],
    section: str,
) -> dict[str, Any] | None:
    """Resolve a section label, including validators' comma-joined labels."""
    text = str(section or "").strip()
    if not text:
        return None
    candidates = [text]
    candidates.extend(
        fragment.strip()
        for fragment in re.split(r"\s*(?:,|，|、|;|；|/|\\)\s*", text)
        if fragment.strip()
    )
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        match = _find_location_line(lines, candidate, allow_short=True)
        if match:
            return match
    return None


def _format_location_hint(location: dict[str, Any]) -> str:
    if isinstance(location.get("current"), dict) or isinstance(location.get("related"), dict):
        current = location.get("current") if isinstance(location.get("current"), dict) else {}
        related = location.get("related") if isinstance(location.get("related"), dict) else {}
        current_hint = _format_single_source_hint(current, "当前报告")
        related_hint = _format_single_source_hint(related, "关联报告")
        return f"当前：{current_hint}；关联：{related_hint}"
    filename = str(location.get("file_name") or "").strip()
    prefix = f"{filename}，" if filename else ""
    if location.get("page") is not None and location.get("line_start") is not None:
        return f"{prefix}第{location['page']}页，第{location['line_start']}行"
    if location.get("row") is not None:
        section = str(location.get("section") or "资源清单")
        return f"{prefix}{section}第{location['row']}行"
    if location.get("rows"):
        section = str(location.get("section") or "资源清单")
        rows = "、".join(str(item) for item in location["rows"][:8])
        return f"{prefix}{section}原始行：{rows}"
    if location.get("line_start") is not None:
        section = str(location.get("section") or "原文")
        return f"{prefix}{section}第{location['line_start']}行"
    if location.get("paragraph") is not None:
        return f"{prefix}原文第{location['paragraph']}段"
    if location.get("section"):
        return f"{prefix}章节：{location['section']}（未匹配到具体行）"
    return f"{prefix}未识别到具体原文位置"


def _format_single_source_hint(location: dict[str, Any], fallback_name: str) -> str:
    filename = str(location.get("file_name") or fallback_name)
    if location.get("page") is not None and location.get("line_start") is not None:
        return f"{filename}第{location['page']}页第{location['line_start']}行"
    if location.get("line_start") is not None:
        return f"{filename}第{location['line_start']}行"
    if location.get("row_index") is not None:
        sheet = str(location.get("sheet_name") or "")
        sheet_text = f"（{sheet}）" if sheet else ""
        return f"{filename}{sheet_text}第{location['row_index']}行"
    if location.get("row") is not None:
        return f"{filename}第{location['row']}行"
    if location.get("paragraph") is not None:
        return f"{filename}第{location['paragraph']}段"
    section = str(location.get("section") or "").strip()
    return f"{filename}章节：{section}" if section else f"{filename}位置未识别"


def _summary_from_findings(findings: list[dict[str, Any]], **extras: Any) -> dict[str, Any]:
    risk_count: dict[str, int] = {}
    for finding in findings:
        risk = _normalize_issue_degree(finding.get("risk_level") or "需人工确认")
        risk_count[risk] = risk_count.get(risk, 0) + 1
    return {
        "total_findings": len(findings),
        "risk_count": risk_count,
        **{key: value for key, value in extras.items() if value is not None},
    }


def _ensure_summary(summary: Any, findings_count: int) -> dict[str, Any]:
    if isinstance(summary, dict):
        summary.setdefault("total_findings", findings_count)
        return summary
    return {"total_findings": findings_count, "risk_count": {}}


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
    _upgrade_stored_finding_locations(result)
    findings = result.get("findings") or []
    if row.module == "function_correspondence" and isinstance(findings, list) and len(findings) > 50:
        findings = findings[:50]
    if row.module == "sensitive_word" and isinstance(findings, list) and len(findings) > 100:
        findings = findings[:100]
    source_documents = result.get("source_documents") or _source_artifacts_to_list(row)
    return {
        **_check_result_to_list_item(row),
        "result": result,
        "findings": findings,
        "source_documents": source_documents,
    }


def _upgrade_stored_finding_locations(result: dict[str, Any]) -> None:
    """Upgrade older stored results to the current revision-location contract."""
    def upgrade(finding: Any) -> None:
        if not isinstance(finding, dict):
            return
        existing_target = finding.get("revision_target")
        existing_highlights = finding.get("source_highlights")
        if (
            isinstance(existing_target, dict)
            and isinstance(existing_highlights, list)
            and existing_highlights
        ):
            return
        existing = finding.get("source_location")
        location = dict(existing) if isinstance(existing, dict) else {}
        if finding.get("item_source_location"):
            location.setdefault("current", finding.get("item_source_location"))
        if finding.get("related_source_location"):
            location.setdefault("related", finding.get("related_source_location"))
        if finding.get("source_locations"):
            location.setdefault("entries", finding.get("source_locations"))
        section = str(finding.get("source_section") or "").strip()
        if section:
            location.setdefault("section", section)
        if not location:
            return
        finding["source_location"] = location
        finding.setdefault("location_hint", _format_location_hint(location))
        highlights = _build_source_highlights(finding, location)
        finding["source_highlights"] = highlights
        finding["revision_target"] = {
            "location_hint": finding.get("location_hint"),
            "source_location": location,
            "highlights": highlights,
            "target_type": "source_text" if highlights else "section_or_document",
            "advice": str(finding.get("revision_advice") or finding.get("suggestion") or ""),
        }

    for finding in result.get("findings") or []:
        upgrade(finding)
    for rule_result in result.get("rule_results") or []:
        if isinstance(rule_result, dict):
            for finding in rule_result.get("findings") or []:
                upgrade(finding)


def _source_artifacts_to_list(row: CheckResult) -> list[dict[str, Any]]:
    return [
        {
            "id": artifact.id,
            "role": artifact.role,
            "stage": artifact.stage,
            "filename": artifact.filename,
            "media_type": artifact.media_type,
            "download_url": f"/api/evaluate/results/{row.id}/sources/{artifact.id}",
            "preview_url": f"/api/evaluate/results/{row.id}/sources/{artifact.id}/preview",
        }
        for artifact in (row.source_artifacts or [])
    ]


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
    rule_results_summary = result.get("rule_results_summary")
    if isinstance(rule_results_summary, dict):
        try:
            return int(rule_results_summary.get("total_findings") or 0)
        except (TypeError, ValueError):
            pass

    summary = _result_summary(result)
    for key in ("rule_results_total_findings", "displayed_findings_count", "merged_findings_count", "total_findings"):
        try:
            count = int(summary.get(key) or 0)
            if count:
                return count
        except (TypeError, ValueError):
            continue

    findings = result.get("findings")
    if isinstance(findings, list):
        return len(findings)

    return 0


def _display_findings_count(module: str, result: dict[str, Any]) -> int:
    count = _findings_count(result)
    if module == "sensitive_word":
        return min(count, 100)
    return count


def _module_display_name(module: str) -> str:
    return MODULE_DISPLAY_NAMES.get(module, module)


def _highest_result_severity(result: dict[str, Any]) -> str:
    findings = result.get("findings") or []
    for level in ("高", "中", "需人工确认", "需确认", "低"):
        for finding in findings:
            if _normalize_issue_degree(finding.get("risk_level") or "") == level:
                return level
    summary = _result_summary(result)
    return _highest_risk(summary.get("risk_count") or summary.get("risk_distribution") or {})


def _highest_risk(risk_counts: dict[str, Any]) -> str:
    normalized_counts: dict[str, int] = {}
    for risk, count in risk_counts.items():
        normalized = _normalize_issue_degree(risk)
        try:
            normalized_counts[normalized] = normalized_counts.get(normalized, 0) + int(count or 0)
        except (TypeError, ValueError):
            continue
    for risk in ("高", "中", "需人工确认", "需确认", "低"):
        try:
            if int(normalized_counts.get(risk) or 0) > 0:
                return risk
        except (TypeError, ValueError):
            continue
    return "通过"


def _normalize_issue_degree(value: Any) -> str:
    text = str(value or "").strip()
    mapping = {
        "pass": "通过",
        "passed": "通过",
        "通过": "通过",
        "warning": "需人工确认",
        "需确认": "需人工确认",
        "需复核": "需人工确认",
        "需人工复核": "需人工确认",
        "risk": "高",
        "failed": "高",
        "中风险": "中",
        "medium": "中",
        "high": "高",
        "高风险": "高",
        "low": "低",
        "低风险": "低",
    }
    return mapping.get(text, text or "需人工确认")


def _first_finding_value(findings: list[dict[str, Any]], key: str) -> str | None:
    for item in findings:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return None


def _first_text(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""
