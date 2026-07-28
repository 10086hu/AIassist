import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.models import CheckResult, Project
from app.db.session import SessionLocal, get_db
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
from app.services.llm_client import get_llm_status, ping_llm


router = APIRouter()


FUNCTION_CORRESPONDENCE_EXTENSIONS = {".docx", ".pdf", ".txt"}
SENSITIVE_WORD_EXTENSIONS = {".docx", ".pdf", ".txt", ".xlsx", ".xlsm"}
TASK_FILE_EXTENSIONS = FUNCTION_CORRESPONDENCE_EXTENSIONS | SENSITIVE_WORD_EXTENSIONS
TASKS: dict[str, dict[str, Any]] = {}
TASKS_LOCK = threading.Lock()


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
    project_name: str = Form(default="未命名可研项目"),
    department: str | None = Form(default=None),
    modules: str = Form(default="function_correspondence,sensitive_word"),
    rule_source: str = Form(default="api"),
    use_llm: bool = Form(default=False),
    selected_rule_ids: str | None = Form(default=None),
) -> dict[str, Any]:
    module_list = [item.strip() for item in modules.split(",") if item.strip()]
    module_list = [item for item in module_list if item in {"function_correspondence", "sensitive_word"}]
    if not module_list:
        raise HTTPException(status_code=400, detail="请至少选择一个已接入的检测模块")

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


def _set_task(task_id: str, **updates: Any) -> None:
    with TASKS_LOCK:
        current = dict(TASKS.get(task_id) or {})
        current.update(updates)
        TASKS[task_id] = current


def _run_evaluate_task(
    task_id: str,
    temp_path: str,
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
    try:
        _set_task(task_id, status="running", progress=5, stage="running", message="正在执行规则审查")
        selected_rules = _parse_selected_rule_ids_by_module(selected_rule_ids)
        project = _get_or_create_project(db, None, project_name, department)

        for index, module in enumerate(modules, start=1):
            progress_base = int((index - 1) / max(1, len(modules)) * 80) + 5
            _set_task(
                task_id,
                status="running",
                progress=progress_base,
                stage=module,
                message=f"正在检测 {module}",
            )
            try:
                if module == "function_correspondence":
                    result = run_function_correspondence_check(
                        report_file_path=temp_path,
                        rule_source=rule_source,
                        project_level="市级项目",
                        use_llm=use_llm,
                        selected_rule_ids=selected_rules.get(module) or [],
                    )
                    summary = result.get("summary") or {}
                    severity = _highest_risk(summary.get("risk_count") or summary.get("risk_summary") or {})
                    suggestion = _first_finding_value(result.get("findings") or [], "revision_advice")
                elif module == "sensitive_word":
                    result = run_sensitive_word_check(
                        report_file_path=temp_path,
                        rule_source=rule_source,
                        project_level="通用",
                        use_llm=use_llm,
                        selected_rule_ids=selected_rules.get(module) or [],
                    )
                    summary = result.get("risk_summary") or {}
                    severity = _highest_risk(summary.get("risk_distribution") or summary.get("risk_summary") or {})
                    suggestion = _first_finding_value(result.get("findings") or [], "revision_advice")
                else:
                    continue

                result_id = _store_check_result(
                    db=db,
                    project=project,
                    module=module,
                    result=result,
                    severity=severity,
                    suggestion=suggestion,
                )
                db.commit()
                result_ids.append(result_id)
                llm_status = str((result.get("summary") or result.get("risk_summary") or {}).get("llm_status") or "")
                if llm_status in {"partial", "timeout"}:
                    task_errors.append(f"{module}: {llm_status}")
            except Exception as exc:
                db.rollback()
                task_errors.append(f"{module}: {exc}")

        if not result_ids:
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
        _set_task(
            task_id,
            status=final_status,
            progress=100,
            stage=final_status,
            message="检测完成" if final_status == "completed" else "检测完成，部分大模型整理超时，已保留规则审查结果",
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


def _store_check_result(
    db: Session,
    project: Project,
    module: str,
    result: dict[str, Any],
    severity: str,
    suggestion: str | None,
) -> str:
    result_json = {
        "project_name": project.name,
        "checked_module": module,
        "result": result,
    }
    row = CheckResult(
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
    db.add(row)
    db.flush()
    return row.id


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
