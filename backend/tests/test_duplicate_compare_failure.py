import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api import evaluate
from app.db.models import Base, CheckResult, CheckRun, Project, SourceArtifact


def test_failed_cross_report_review_preserves_both_inputs_and_marks_run_failed(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(evaluate, "run_duplicate_compare_check", lambda **kwargs: (
        _ for _ in ()
    ).throw(ValueError("LLM response truncated, finish_reason=length")))
    with Session(engine) as db:
        db.add(Project(id="p", name="项目"))
        db.add(CheckRun(id="run", project_id="p", report_name="二期", status="running"))
        db.commit()
        with pytest.raises(HTTPException) as error:
            evaluate._evaluate_duplicate_compare_sync(
                db, b"current", "二期.docx", [(b"history", "一期.docx")],
                "p", "run", "项目", None, "二期", '["一期"]',
            )
        assert error.value.status_code == 400
        stored = db.scalar(select(CheckResult))
        result = json.loads(stored.reference_data)["result"]
        assert result["status"] == "failed"
        assert result["summary"]["comparison_completed"] is False
        assert result["summary"]["history_file_count"] == 1
        assert db.get(CheckRun, "run").status == "failed"
        sources = list(db.scalars(select(SourceArtifact).order_by(SourceArtifact.role)).all())
        assert {(s.role, s.stage, s.content) for s in sources} == {
            ("current", "二期", b"current"), ("history", "一期", b"history"),
        }


def test_wrong_report_owner_is_rejected_before_model_or_failure_record(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(evaluate, "run_duplicate_compare_check", lambda **kwargs: pytest.fail("不应调用模型"))
    with Session(engine) as db:
        db.add(Project(id="p", name="项目"))
        db.add(CheckRun(id="run", project_id="p", report_name="二期", status="running"))
        db.commit()
        with pytest.raises(HTTPException):
            evaluate._evaluate_duplicate_compare_sync(
                db, b"current", "二期.docx", [(b"history", "一期.docx")],
                "other", "run", "其他", None, "二期", None,
            )
        assert not db.scalar(select(CheckResult))
