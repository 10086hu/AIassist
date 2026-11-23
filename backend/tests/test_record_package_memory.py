"""Transport real issue ledgers without losing baselines or altering rule evidence."""
import copy
from datetime import datetime, timedelta
import hashlib
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.db.models import Base, CheckResult, CheckRun, Project, ReviewMemoryRevision, ReviewRunLink, SourceArtifact
from app.services import issue_memory
from app.services.record_packages import RecordPackage, canonical_package_bytes, export_record_package, import_record_package


SCOPE = {"checker_signature": "same-checker-v1", "selected_rule_ids": ["r1"]}


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'memory-transfer.db'}")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def finding():
    return {"rule_id": "r1", "issue_type": "资源数量不符", "item": "服务器",
            "evidence": "服务器未配置操作系统", "review_opinion": "请补充数量依据", "risk_level": "中"}


def revision(db, project, day, *, findings=None, baseline=None, filename="报告.txt", scope=None):
    date = datetime(2026, 1, 1) + timedelta(days=day)
    run = CheckRun(project_id=project.id, report_name=filename, source_filename=filename,
                   status="completed", created_at=date, updated_at=date)
    db.add(run)
    db.flush()
    issue_memory.link_run(db, run, baseline)
    result = {"status": "completed", "findings": copy.deepcopy(findings or []),
              "memory_scope": copy.deepcopy(scope or SCOPE)}
    if not findings:
        result["rule_results"] = [{"rule_id": "r1", "passed": True, "findings": []}]
    row = CheckResult(project_id=project.id, check_run_id=run.id, module="resource",
                      check_subtype="aggregate", result_label="completed", reason="规则原始意见",
                      created_at=date, reference_data=json.dumps({"report_name": filename, "result": result}))
    db.add(row)
    db.flush()
    content = (f"readable document version {day}\n" + "evidence " * 20).encode()
    artifact = SourceArtifact(check_result_id=row.id, filename=filename, content=content)
    db.add(artifact)
    snapshot = issue_memory.record_memory(db, row, content, filename)
    db.commit()
    return run, row, snapshot


def project(db):
    row = Project(name="资源项目")
    db.add(row)
    db.commit()
    return row


def test_full_memory_roundtrip_preserves_status_sources_scope_and_internal_references(db):
    original = project(db)
    run1, row1, first = revision(db, original, 1, findings=[finding()])
    run2, row2, _ = revision(db, original, 2, findings=[finding()])
    run3, row3, final = revision(db, original, 3)
    assert final["summary"]["resolved_count"] == 1
    package = export_record_package(db, original.id)
    assert package.schema_version == 2
    assert len(package.memory.run_links) == len(package.memory.revisions) == 3
    imported = import_record_package(db, package, "memory-roundtrip")
    db.commit()
    ids = imported["id_map"]
    assert db.get(ReviewRunLink, ids[run2.id]).baseline_run_id == ids[run1.id]
    snapshot = json.loads(db.get(ReviewMemoryRevision, ids[row3.id]).snapshot_json)
    assert snapshot["issues"][0]["status"] == "resolved"
    assert snapshot["issues"][0]["first_result_id"] == ids[row1.id]
    assert snapshot["issues"][0]["last_seen_result_id"] == ids[row2.id]
    assert snapshot["summary"]["baseline_result_id"] == ids[row2.id]
    assert snapshot["scope"] == SCOPE
    assert snapshot["issues"][0]["issue_id"] != first["issues"][0]["issue_id"]
    assert snapshot["track_key"] != final["track_key"]
    assert snapshot["track_key"] == db.get(ReviewRunLink, ids[run3.id]).track_key
    copied = db.get(CheckResult, ids[row3.id])
    copied_data = json.loads(copied.reference_data)["result"]["issue_memory"]
    assert copied_data["resolved_count"] == 1
    assert copied_data["historical_items"][0]["previous_result_id"] == ids[row2.id]
    original_sources = db.scalars(select(SourceArtifact).where(SourceArtifact.check_result_id == row3.id)).all()
    copied_sources = db.scalars(select(SourceArtifact).where(SourceArtifact.check_result_id == copied.id)).all()
    assert copied_sources[0].content == original_sources[0].content
    assert export_record_package(db, imported["project_id"]).schema_version == 2


def test_batch_export_brings_its_baseline_chain_but_not_unrelated_report(db):
    original = project(db)
    run1, row1, _ = revision(db, original, 1, findings=[finding()])
    _, unrelated, _ = revision(db, original, 2, findings=[finding()], filename="其他报告.txt")
    run3, row3, _ = revision(db, original, 3, findings=[finding()], baseline=run1.id, filename="改名报告.txt")
    package = export_record_package(db, original.id, run3.id)
    assert {str(item.id) for item in package.results} == {row1.id, row3.id}
    assert unrelated.id not in {str(item.id) for item in package.results}
    assert not package.memory.missing_references
    imported = import_record_package(db, package, "renamed-batch")
    db.commit()
    imported_project = db.get(Project, imported["project_id"])
    _, _, next_snapshot = revision(db, imported_project, 4, findings=[finding()], filename="改名报告.txt")
    assert next_snapshot["summary"]["persistent_count"] == 1
    previous_snapshot = json.loads(db.get(ReviewMemoryRevision, imported["id_map"][row3.id]).snapshot_json)
    assert next_snapshot["issues"][0]["issue_id"] == previous_snapshot["issues"][0]["issue_id"]


def test_imported_resolved_issue_reopens_and_scope_change_remains_unconfirmed(db):
    original = project(db)
    revision(db, original, 1, findings=[finding()])
    revision(db, original, 2)
    copied = import_record_package(db, export_record_package(db, original.id), "reopen")
    db.commit()
    target = db.get(Project, copied["project_id"])
    _, _, third = revision(db, target, 3, findings=[finding()])
    assert third["summary"]["reopened_count"] == 1
    _, _, fourth = revision(db, target, 4, scope={"checker_signature": "changed"})
    assert fourth["summary"]["resolved_count"] == 0
    assert fourth["summary"]["unconfirmed_count"] == 1


def test_independent_new_stream_mode_is_preserved(db):
    original = project(db)
    revision(db, original, 1, findings=[finding()])
    run, row, _ = revision(db, original, 2, findings=[finding()], baseline="new")
    imported = import_record_package(db, export_record_package(db, original.id, run.id), "new-stream")
    db.commit()
    assert len(imported["check_run_ids"]) == 1
    link = db.get(ReviewRunLink, imported["id_map"][run.id])
    assert link.mode == "new" and link.baseline_run_id is None
    assert db.get(ReviewMemoryRevision, imported["id_map"][row.id]).baseline_result_id is None


def test_missing_deleted_history_is_explicit_and_never_points_at_original_database(db):
    original = project(db)
    run1, row1, _ = revision(db, original, 1, findings=[finding()])
    run2, row2, _ = revision(db, original, 2, findings=[finding()])
    deleted_run_id, deleted_result_id = run1.id, row1.id
    db.delete(run1)
    db.commit()
    package = export_record_package(db, original.id, run2.id)
    assert {(gap.kind, str(gap.id)) for gap in package.memory.missing_references} == {
        ("run", deleted_run_id), ("result", deleted_result_id)}
    copied = import_record_package(db, package, "missing-history")
    db.commit()
    copied_result = db.get(CheckResult, copied["id_map"][row2.id])
    data = json.loads(copied_result.reference_data)["result"]
    assert data["issue_memory"]["transfer_status"] == "partial_history"
    assert db.get(ReviewRunLink, copied["id_map"][run2.id]).baseline_run_id != deleted_run_id
    assert db.get(ReviewMemoryRevision, copied_result.id).baseline_result_id != deleted_result_id
    # The current imported snapshot remains usable for the next revision.
    target = db.get(Project, copied["project_id"])
    _, _, next_snapshot = revision(db, target, 3, findings=[finding()])
    assert next_snapshot["summary"]["persistent_count"] == 1


def test_v1_wire_shape_and_digests_remain_compatible(db):
    original = project(db)
    row = CheckResult(project=original, module="resource", check_subtype="aggregate",
                      result_label="completed", reason="old", reference_data="{}")
    db.add(row)
    db.commit()
    package = export_record_package(db, original.id)
    data = package.model_dump(mode="json")
    assert package.schema_version == 1 and "memory" not in data
    expected = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert canonical_package_bytes(package) == expected
    assert import_record_package(db, RecordPackage.model_validate(data), "legacy")["project_id"] != original.id


def test_legacy_memory_prose_is_labeled_and_missing_history_link_is_not_openable(db):
    original = project(db)
    absent = str(uuid4())
    row = CheckResult(project=original, module="resource", check_subtype="aggregate",
                      result_label="completed", reason="legacy", reference_data=json.dumps({"result": {
                          "issue_memory": {"resolved_count": 0, "historical_items": [
                              {"previous_result_id": absent, "status": "unconfirmed"}]},
                      }}))
    db.add(row)
    db.commit()
    # Mimic an older v1 sender, which did not carry the ledger tables.
    wire = {"schema_version": 1, "project": {
        "id": original.id, "name": original.name, "department": None, "description": None,
        "status": original.status, "created_at": original.created_at.isoformat(),
        "updated_at": original.updated_at.isoformat()}, "results": [{
            "id": row.id, "module": row.module, "check_subtype": row.check_subtype,
            "severity": row.severity, "score": None, "result_label": row.result_label,
            "reason": row.reason, "suggestion": None, "reference_data": json.loads(row.reference_data),
            "model_name": row.model_name, "created_at": row.created_at.isoformat()}]}
    imported = import_record_package(db, RecordPackage.model_validate(wire), "legacy-memory")
    db.commit()
    copied = db.get(CheckResult, imported["result_ids"][0])
    memory = json.loads(copied.reference_data)["result"]["issue_memory"]
    assert memory["transfer_status"] == "legacy_snapshot"
    assert memory["historical_items"][0]["previous_result_id"] is None
    assert db.get(ReviewMemoryRevision, copied.id) is None


@pytest.mark.parametrize("mutation", ["foreign_link", "future_baseline", "foreign_revision", "wrong_module", "missing_reference", "duplicate", "v1_memory"])
def test_malformed_memory_graph_is_rejected_before_import(db, mutation):
    original = project(db)
    run1, row1, _ = revision(db, original, 1, findings=[finding()])
    run2, row2, _ = revision(db, original, 2, findings=[finding()])
    data = export_record_package(db, original.id).model_dump(mode="json")
    links = data["memory"]["run_links"]
    revisions = data["memory"]["revisions"]
    second_link = next(item for item in links if item["check_run_id"] == run2.id)
    second_revision = next(item for item in revisions if item["result_id"] == row2.id)
    if mutation == "foreign_link":
        second_link["baseline_run_id"] = str(uuid4())
    elif mutation == "future_baseline":
        links[0]["baseline_run_id"] = links[0]["check_run_id"]
    elif mutation == "foreign_revision":
        second_revision["result_id"] = str(uuid4())
    elif mutation == "wrong_module":
        second_revision["module"] = "sensitive_word"
    elif mutation == "missing_reference":
        second_revision["snapshot"]["issues"][0]["first_result_id"] = str(uuid4())
    elif mutation == "duplicate":
        links.append(copy.deepcopy(links[0]))
    else:
        data["schema_version"] = 1
    with pytest.raises(ValidationError):
        RecordPackage.model_validate(data)


def test_memory_ancestor_budget_and_cross_project_references_fail_closed(db, monkeypatch):
    original = project(db)
    run1, _, _ = revision(db, original, 1, findings=[finding()])
    run2, _, _ = revision(db, original, 2, findings=[finding()])
    import app.services.record_packages as codec
    monkeypatch.setattr(codec, "MAX_RUNS", 1)
    with pytest.raises(HTTPException) as caught:
        export_record_package(db, original.id, run2.id)
    assert caught.value.status_code == 413
    monkeypatch.setattr(codec, "MAX_RUNS", 200)
    other = project(db)
    other_run, _, _ = revision(db, other, 1, findings=[finding()])
    db.get(ReviewRunLink, run2.id).baseline_run_id = other_run.id
    db.commit()
    with pytest.raises(HTTPException) as caught:
        export_record_package(db, original.id, run2.id)
    assert caught.value.status_code == 409


def test_deleted_last_seen_history_is_not_presented_as_openable_after_transfer(db):
    original = project(db)
    run1, row1, _ = revision(db, original, 1, findings=[finding()])
    run2, row2, _ = revision(db, original, 2, scope={"checker_signature": "changed"})
    # The previous issue is pending because the current scope changed.
    assert json.loads(row2.reference_data)["result"]["issue_memory"]["historical_items"]
    db.delete(run1)
    db.commit()
    package = export_record_package(db, original.id, run2.id)
    imported = import_record_package(db, package, "deleted-last-seen")
    db.commit()
    copied = db.get(CheckResult, imported["id_map"][row2.id])
    item = json.loads(copied.reference_data)["result"]["issue_memory"]["historical_items"][0]
    # WPF chooses last_seen_result_id before previous_result_id.
    assert not (item.get("last_seen_result_id") or item.get("previous_result_id"))
    assert item.get("first_result_id") is None and item["history_unavailable"]
    # Navigation cleanup must not destroy the durable ledger's provenance.
    ledger_item = json.loads(copied.memory_revision.snapshot_json)["issues"][0]
    assert ledger_item["last_seen_result_id"] == imported["id_map"][row1.id]
    assert ledger_item["first_result_id"] == imported["id_map"][row1.id]
