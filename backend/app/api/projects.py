import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CheckResult, CheckRun, Project
from app.db.session import get_db
from app.schemas import CheckRunCreate, ProjectCreate, ProjectOut


router = APIRouter()


@router.post("", response_model=ProjectOut)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    project = Project(
        name=payload.name,
        department=payload.department,
        description=payload.description,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)) -> list[Project]:
    return list(db.scalars(select(Project).order_by(Project.created_at.desc())).all())


@router.post("/{project_id}/check-runs")
def create_check_run(project_id: str, payload: CheckRunCreate, db: Session = Depends(get_db)) -> dict[str, object]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    run = CheckRun(
        project_id=project.id,
        report_name=payload.report_name,
        source_filename=payload.source_filename,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return _check_run_to_dict(run)


@router.get("/tree")
def project_tree(db: Session = Depends(get_db)) -> dict[str, list[dict[str, object]]]:
    projects = list(db.scalars(select(Project).order_by(Project.updated_at.desc(), Project.created_at.desc())).all())
    return {"items": [_project_tree_to_dict(project) for project in projects]}


@router.delete("/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    db.delete(project)
    db.commit()
    return {"success": True, "deleted_id": project_id}


@router.delete("/{project_id}/check-runs/{check_run_id}")
def delete_check_run(project_id: str, check_run_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    """Delete a real check batch or one legacy result group."""
    if check_run_id.startswith("legacy:"):
        legacy_payload = check_run_id[len("legacy:"):]
        if ":" not in legacy_payload:
            raise HTTPException(status_code=404, detail="历史检查记录编号无效")
        report_name, created_day = legacy_payload.rsplit(":", 1)
        try:
            target_day = date.fromisoformat(created_day)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="历史检查记录日期无效") from exc

        candidates = [
            result
            for result in db.query(CheckResult)
            .filter(CheckResult.project_id == project_id, CheckResult.check_run_id.is_(None))
            .all()
            if result.created_at
            and result.created_at.date() == target_day
            and _legacy_report_name(result) == report_name
        ]
        if not candidates:
            raise HTTPException(status_code=404, detail="历史检查记录不存在或不属于当前项目")
        for result in candidates:
            db.delete(result)
        db.commit()
        return {"success": True, "deleted_id": check_run_id, "deleted_count": len(candidates), "legacy": True}

    run = db.get(CheckRun, check_run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="报告检查记录不存在或不属于当前项目")

    db.delete(run)
    db.commit()
    return {"success": True, "deleted_id": check_run_id, "project_id": project_id}


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_db)) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _project_tree_to_dict(project: Project) -> dict[str, object]:
    runs = {run.id: _check_run_to_dict(run) for run in project.check_runs}
    for result in project.check_results:
        if result.check_run_id or result.check_subtype != "aggregate":
            continue
        report_name = _legacy_report_name(result)
        created_day = result.created_at.date().isoformat() if result.created_at else "unknown"
        legacy_id = f"legacy:{report_name}:{created_day}"
        run = runs.setdefault(
            legacy_id,
            {
                "id": legacy_id,
                "report_name": report_name,
                "source_filename": None,
                "status": "completed",
                "created_at": result.created_at.isoformat() if result.created_at else "",
                "updated_at": result.created_at.isoformat() if result.created_at else "",
                "results": [],
            },
        )
        run["results"].append(_result_to_dict(result))

    ordered_runs = sorted(runs.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "id": project.id,
        "name": project.name,
        "department": project.department,
        "description": project.description,
        "status": project.status,
        "created_at": project.created_at.isoformat() if project.created_at else "",
        "updated_at": project.updated_at.isoformat() if project.updated_at else "",
        "check_runs": ordered_runs,
    }


def _check_run_to_dict(run: CheckRun) -> dict[str, object]:
    return {
        "id": run.id,
        "report_name": run.report_name,
        "source_filename": run.source_filename,
        "status": run.status,
        "created_at": run.created_at.isoformat() if run.created_at else "",
        "updated_at": run.updated_at.isoformat() if run.updated_at else "",
        "results": [_result_to_dict(result) for result in sorted(run.check_results, key=lambda item: item.created_at or 0) if result.check_subtype == "aggregate"],
    }


def _result_to_dict(result: CheckResult) -> dict[str, object]:
    payload = _load_result_payload(result)
    module_result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    summary = module_result.get("summary") if isinstance(module_result.get("summary"), dict) else {}
    if not summary and isinstance(module_result.get("risk_summary"), dict):
        summary = module_result["risk_summary"]
    return {
        "id": result.id,
        "module": result.module,
        "module_name": module_result.get("module_name") or result.module,
        "status": result.result_label,
        "risk_level": result.severity,
        "findings_count": int(summary.get("total_findings") or 0) if isinstance(summary, dict) else 0,
        "created_at": result.created_at.isoformat() if result.created_at else "",
    }


def _load_result_payload(result: CheckResult) -> dict[str, object]:
    try:
        payload = json.loads(result.reference_data or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _legacy_report_name(result: CheckResult) -> str:
    payload = _load_result_payload(result)
    return str(payload.get("report_name") or payload.get("project_name") or (result.project.name if result.project else "未命名报告"))
