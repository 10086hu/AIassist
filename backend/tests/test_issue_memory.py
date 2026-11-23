import copy
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.models import Base, CheckResult, CheckRun, Project, ReviewMemoryRevision, ReviewRunLink
from app.services import issue_memory as memory, result_refiner


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Project(id="p", name="项目"))
        session.commit()
        yield session
    engine.dispose()


def finding(entity="服务器", evidence="服务器未配置操作系统", rule="r1", **extra):
    return {"rule_id": rule, "issue_type": "资源数量不符", "item": entity,
            "evidence": evidence, "risk_level": "中", **extra}


def save(db, index, findings, *, module="resource", filename="报告.docx", baseline=None,
         status="completed", passed=False, scope=None, content=None, run_id=None):
    run = CheckRun(id=run_id or f"run-{index}", project_id="p", report_name=filename,
                   source_filename=filename, created_at=datetime(2026, 1, 1) + timedelta(days=index))
    db.add(run)
    db.flush()
    memory.link_run(db, run, baseline)
    result = {"findings": copy.deepcopy(findings), "status": status,
              "memory_scope": scope if scope is not None else {"selected": ["r1"], "definition": "v1"}}
    if passed:
        result["rule_results"] = [{"rule_id": "r1", "passed": True, "findings": []}]
    row = CheckResult(id=f"result-{index}", project_id="p", check_run_id=run.id,
                      module=module, check_subtype="aggregate", result_label=status,
                      reason="test", severity="中", created_at=run.created_at,
                      reference_data=json.dumps({"report_name": filename, "result": result}, ensure_ascii=False))
    db.add(row)
    db.flush()
    snapshot = memory.record_memory(db, row, content or f"readable test file {index}".encode(), filename)
    db.commit()
    return row, snapshot


def test_issue_lifecycle_over_five_revisions(db):
    _, v1 = save(db, 1, [finding()])
    _, v2 = save(db, 2, [finding()])
    _, v3 = save(db, 3, [finding(evidence="服务器仍未配置操作系统")])
    _, v4 = save(db, 4, [], passed=True, filename="报告.txt", baseline="run-3")
    _, v5 = save(db, 5, [finding()], filename="报告.txt")
    assert v1["summary"]["new_count"] == 1
    assert v2["summary"]["persistent_count"] == 1
    assert v3["summary"]["changed_count"] == 1
    assert v4["summary"]["resolved_count"] == 1
    assert v5["summary"]["reopened_count"] == 1
    assert len({v["issues"][0]["issue_id"] for v in (v1, v2, v3, v4, v5)}) == 1


@pytest.mark.parametrize("options", [{}, {"status": "failed", "passed": True},
                                     {"scope": {"selected": ["r2"]}, "passed": True}])
def test_absence_or_failure_or_changed_scope_is_not_resolved(db, options):
    save(db, 1, [finding()])
    _, snapshot = save(db, 2, [], **options)
    assert snapshot["summary"]["resolved_count"] == 0
    assert snapshot["summary"]["unconfirmed_count"] == 1


def test_pending_history_survives_failed_revision(db):
    _, first = save(db, 1, [finding()])
    save(db, 2, [], status="failed")
    _, third = save(db, 3, [finding()])
    assert third["issues"][0]["issue_id"] == first["issues"][0]["issue_id"]
    assert third["summary"]["persistent_count"] == 1


def test_explicit_renamed_revision_and_new_stream(db):
    _, first = save(db, 1, [finding()])
    _, renamed = save(db, 2, [finding()], filename="报告修订版.docx", baseline="run-1")
    _, third = save(db, 3, [finding()], filename="报告修订版.docx")
    _, fresh = save(db, 4, [finding()], filename="报告修订版.docx", baseline="new")
    assert renamed["summary"]["persistent_count"] == 1
    assert third["issues"][0]["issue_id"] == first["issues"][0]["issue_id"]
    assert fresh["summary"]["has_baseline"] is False
    assert fresh["summary"]["new_count"] == 1


def test_unrelated_reports_and_modules_do_not_share_memory(db):
    save(db, 1, [finding()])
    _, unrelated = save(db, 2, [finding()], filename="另一个报告.docx")
    _, module = save(db, 3, [finding()], module="basis")
    assert unrelated["summary"]["has_baseline"] is False
    assert module["summary"]["has_baseline"] is False


def test_matching_is_one_to_one_and_keeps_entities_separate():
    old = [{"finding": finding(), "issue_id": "a"}]
    matches = memory.match_findings([finding(), finding(), finding(entity="数据库")], old)
    assert len(matches) == 1
    assert 2 not in matches


def test_new_table_does_not_change_existing_result_and_rule_verdict(db):
    row, _ = save(db, 1, [finding()])
    result = json.loads(row.reference_data)["result"]
    assert result["status"] == "completed"
    assert result["findings"][0]["risk_level"] == "中"
    assert result["findings"][0]["issue_memory"]["status"] == "new"
    assert db.query(ReviewMemoryRevision).count() == 1
    assert db.query(ReviewRunLink).count() == 1


def test_same_file_is_explicitly_identified(db):
    save(db, 1, [finding()], content=b"unchanged")
    _, snapshot = save(db, 2, [finding()], content=b"unchanged")
    assert snapshot["summary"]["same_file"] is True
    assert snapshot["summary"]["persistent_count"] == 1


def test_same_file_with_a_different_verdict_is_not_called_remediated(db):
    save(db, 1, [finding()], filename="报告.txt", content=b"same exact document")
    _, second = save(db, 2, [], filename="报告.txt", content=b"same exact document", passed=True)
    assert second["summary"]["resolved_count"] == 0
    assert second["summary"]["unconfirmed_count"] == 1


def test_retry_after_a_failed_changed_upload_can_confirm_the_actual_fix(db):
    save(db, 1, [finding()], filename="报告.txt", content=b"original document")
    save(db, 2, [], filename="报告.txt", content=b"updated document", status="failed")
    _, third = save(db, 3, [], filename="报告.txt", content=b"updated document", passed=True)
    assert third["summary"]["same_file"] is True
    assert third["summary"]["resolved_count"] == 1


def test_subrule_and_top_level_duplicates_are_tracked_once():
    item = finding()
    result = {"findings": [item], "rule_results": [{"findings": [copy.deepcopy(item)]}]}
    assert len(memory.unique_findings(result)) == 1


def test_history_is_prioritized_before_high_risk_new_issues(db, monkeypatch):
    save(db, 1, [finding(risk_level="低")], filename="report.txt")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setattr(result_refiner, "LLM_GROUP_LIMIT", 1)
    calls = []

    def review(**kwargs):
        calls.append(kwargs)
        return {"llm_available": True, "user_reason": "复核完成", "user_suggestion": "补充配置"}

    monkeypatch.setattr(result_refiner, "review_finding_with_llm", review)
    with memory.review_history(db, "p", "resource", "report.txt"):
        values, meta = result_refiner.refine_findings("resource", [
            finding(entity="新数据库", rule="r2", risk_level="高"), finding(risk_level="低")], [], True)
    assert calls[0]["finding"]["item"] == "服务器"
    assert values[1]["llm_review_status"] == "completed"
    assert values[0]["llm_review_status"] == "skipped"
    assert meta["llm_status"] == "partial"


def test_deleted_result_cascades_only_its_own_memory(db):
    row, _ = save(db, 1, [finding()])
    db.delete(row)
    db.commit()
    assert db.query(ReviewMemoryRevision).count() == 0


def test_changed_scope_cannot_become_a_valid_baseline_after_another_revision(db):
    save(db, 1, [finding()], filename="报告.txt")
    changed = {"selected": ["r2"], "definition": "v2"}
    save(db, 2, [], filename="报告.txt", scope=changed)
    _, third = save(db, 3, [], filename="报告.txt", passed=True, scope=changed)
    assert third["summary"]["resolved_count"] == 0
    assert third["summary"]["unconfirmed_count"] == 1


def test_unreadable_source_never_closes_an_old_issue(db):
    save(db, 1, [finding()])
    _, second = save(db, 2, [], passed=True, content=b"not a docx archive")
    assert second["summary"]["resolved_count"] == 0
    assert second["summary"]["unconfirmed_count"] == 1


def test_removed_sensitive_term_closes_even_when_other_terms_remain(db):
    first = finding(entity="甲品牌", hit_text="甲品牌")
    second = finding(entity="乙品牌", hit_text="乙品牌", evidence="乙品牌需要核查")
    save(db, 1, [first, second], filename="报告.txt", module="sensitive_word")
    _, result = save(db, 2, [second], filename="报告.txt", module="sensitive_word",
                     content=("项目建设说明" * 30 + "乙品牌").encode())
    assert result["summary"]["resolved_count"] == 1
    assert result["summary"]["persistent_count"] == 1


def test_parallel_revision_snapshots_do_not_overwrite_each_other(db):
    _, first = save(db, 1, [finding()])
    _, a = save(db, 2, [], filename="报告.txt", baseline="run-1", passed=True)
    _, b = save(db, 3, [finding()], baseline="run-1")
    assert a["summary"]["resolved_count"] == 1
    assert b["summary"]["persistent_count"] == 1
    original = json.loads(db.get(ReviewMemoryRevision, "result-1").snapshot_json)
    assert original == first


def test_cross_project_baseline_is_rejected(db):
    save(db, 1, [finding()])
    db.add(Project(id="q", name="另一个项目"))
    db.flush()
    run = CheckRun(id="q-run", project_id="q", report_name="报告.docx",
                   created_at=datetime(2026, 2, 1))
    db.add(run)
    db.flush()
    with pytest.raises(ValueError):
        memory.link_run(db, run, "run-1")


def test_ai_budget_rotates_through_unreviewed_historical_issues(db, monkeypatch):
    original = [finding(entity=f"资源{index}", evidence=f"资源{index}未配置操作系统") for index in range(7)]
    original, _ = result_refiner.refine_findings("resource", original, [], False)
    for item in original[:2]:
        item["llm_refined"] = True
    save(db, 1, original, filename="报告.txt")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setattr(result_refiner, "LLM_GROUP_LIMIT", 2)
    calls = []

    def review(**kwargs):
        calls.append(kwargs["finding"]["item"])
        return {"llm_available": True, "user_reason": "复核完成", "user_suggestion": "补充配置"}

    monkeypatch.setattr(result_refiner, "review_finding_with_llm", review)
    with memory.review_history(db, "p", "resource", "报告.txt"):
        revised, _ = result_refiner.refine_findings("resource", original, [], True)
    save(db, 2, revised, filename="报告.txt")
    with memory.review_history(db, "p", "resource", "报告.txt"):
        result_refiner.refine_findings("resource", original, [], True)
    assert calls == ["资源2", "资源3", "资源4", "资源5"]


def test_additive_tables_preserve_an_existing_database():
    from sqlalchemy import inspect
    engine = create_engine("sqlite://")
    existing = [table for table in Base.metadata.sorted_tables
                if table.name not in {"review_run_links", "review_memory_revisions"}]
    Base.metadata.create_all(engine, tables=existing)
    def columns():
        return {table.name: [{**column, "type": str(column["type"])}
                             for column in inspect(engine).get_columns(table.name)] for table in existing}
    before = columns()
    with Session(engine) as session:
        session.add(Project(id="existing-project", name="原项目"))
        session.commit()
    Base.metadata.create_all(engine)
    after = columns()
    assert before == after
    with Session(engine) as session:
        assert session.get(Project, "existing-project").name == "原项目"
    engine.dispose()
