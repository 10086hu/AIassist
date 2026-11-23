"""Authenticated project memory, annotations, and explicit review batches."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.models import CheckResult, Project, User
from app.db.session import get_db
from app.services import memory_workspace as workspace


router = APIRouter(prefix="/api/memory", tags=["memory"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextInput(Input):
    facts: list[dict] = Field(default_factory=list, max_length=50)
    aliases: list[dict] = Field(default_factory=list, max_length=50)


class FeedbackInput(Input):
    issue_id: UUID
    action: str = Field(max_length=32)
    reason: str = Field(min_length=1, max_length=5000)
    evidence: str = Field(default="", max_length=5000)


class JobInput(Input):
    use_llm: bool = False


class ContinueInput(Input):
    batch_size: int = Field(default=3, ge=1, le=5)
    retry_failed: bool = False
    skip_failed: bool = False


def _result(db, result_id):
    row = db.get(CheckResult, str(result_id))
    if row is None:
        raise HTTPException(404, "审查结果不存在")
    return row


def _actor(user: User):
    # A display attribution only; never transmit a token, role, or password.
    return (user.display_name or user.username or "当前用户")[:256]


def _call(callback, *args, **kwargs):
    try:
        return callback(*args, **kwargs)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/projects/{project_id}/context")
def context(project_id: UUID, db: Session = Depends(get_db)):
    if db.get(Project, str(project_id)) is None:
        raise HTTPException(404, "项目不存在")
    return workspace.project_context(db, str(project_id))


@router.put("/projects/{project_id}/context")
def save_context(project_id: UUID, payload: ContextInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _call(workspace.put_project_context, db, str(project_id), payload.model_dump(), _actor(user))


@router.get("/results/{result_id}")
def result_memory(result_id: UUID, db: Session = Depends(get_db)):
    return workspace.get_result_memory(db, _result(db, result_id))


@router.get("/results/{result_id}/feedback")
def feedback(result_id: UUID, db: Session = Depends(get_db)):
    return workspace.result_feedback(db, _result(db, result_id))


@router.post("/results/{result_id}/feedback")
def save_feedback(result_id: UUID, payload: FeedbackInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _call(workspace.add_feedback, db, _result(db, result_id), str(payload.issue_id), payload.action,
                 payload.reason, payload.evidence, _actor(user))


@router.post("/results/{result_id}/review-jobs")
def create_job(result_id: UUID, payload: JobInput, db: Session = Depends(get_db)):
    return workspace.create_review_job(db, _result(db, result_id), payload.use_llm)


@router.get("/review-jobs/{job_id}")
def review_job(job_id: UUID, db: Session = Depends(get_db)):
    return workspace.get_review_job(db, str(job_id))


@router.post("/review-jobs/{job_id}/continue")
def continue_job(job_id: UUID, payload: ContinueInput, db: Session = Depends(get_db)):
    return _call(workspace.continue_review_job, db, str(job_id), **payload.model_dump())
