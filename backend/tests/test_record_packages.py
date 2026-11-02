"""Review packages preserve evidence and cannot overwrite another history."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.db.models import Base, CheckResult, CheckRun, Project, SourceArtifact
from app.services.record_packages import (
    RecordPackage, canonical_package_bytes, export_record_package,
    import_record_package, package_sha256,
)


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'packages.db'}")
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def seed(db):
    project = Project(name="可研项目", department="审查部门")
    run = CheckRun(project=project, report_name="报告.docx", status="completed")
    result = CheckResult(project=project, check_run=run, module="resource", check_subtype="aggregate",
                         result_label="completed", reason="需复核", reference_data="{}")
    source = SourceArtifact(check_result=result, filename="报告.docx", content=b"original report\x00\xff")
    db.add(project)
    db.flush()
    result.reference_data = json.dumps({"result": {
        "id": result.id, "project_id": project.id,
        "source_documents": [{"id": source.id, "filename": source.filename,
                              "download_url": f"/api/evaluate/results/{result.id}/sources/{source.id}",
                              "preview_url": f"/api/evaluate/results/{result.id}/sources/{source.id}/preview?quote={source.id}"}],
        "findings": [{"reason": f"原文包含编号 {source.id}", "quote": source.id}],
    }}, ensure_ascii=False)
    db.commit()
    return project, run, result, source


def test_round_trip_preserves_rows_files_and_rewrites_only_structured_references(database):
    project, run, result, source = seed(database)
    package = export_record_package(database, project.id)
    assert package.sources[0].sha256 == hashlib.sha256(source.content).hexdigest()
    assert base64.b64decode(package.sources[0].content_base64) == source.content
    imported = import_record_package(database, package, "upload:user-a:record-a")
    database.commit()
    copied_project = database.get(Project, imported["project_id"])
    copied_run = database.get(CheckRun, imported["check_run_ids"][0])
    copied_result = database.get(CheckResult, imported["result_ids"][0])
    copied_source = database.get(SourceArtifact, imported["source_ids"][0])
    assert copied_project.name == project.name
    assert copied_run.project_id == copied_project.id
    assert copied_result.check_run_id == copied_run.id
    assert copied_source.check_result_id == copied_result.id
    assert copied_source.content == source.content
    assert copied_result.created_at == result.created_at
    data = json.loads(copied_result.reference_data)["result"]
    assert data["id"] == copied_result.id
    assert data["project_id"] == copied_project.id
    document = data["source_documents"][0]
    assert document["id"] == copied_source.id
    assert document["download_url"] == f"/api/evaluate/results/{copied_result.id}/sources/{copied_source.id}"
    assert document["preview_url"].endswith(f"/preview?quote={source.id}")
    assert data["findings"][0]["reason"] == f"原文包含编号 {source.id}"
    assert data["findings"][0]["quote"] == source.id
    # The rewritten copy is a valid package itself, including preserved links.
    assert export_record_package(database, copied_project.id).sources[0].sha256 == package.sources[0].sha256


def test_namespace_isolates_users_and_repeat_never_overwrites(database):
    project, _, _, _ = seed(database)
    package = export_record_package(database, project.id)
    first = import_record_package(database, package, "user-a:record-1")
    database.commit()
    second = import_record_package(database, package, "user-b:record-1")
    database.commit()
    assert first["project_id"] != second["project_id"]
    package.project.name = "试图覆盖"
    with pytest.raises(HTTPException) as error:
        import_record_package(database, package, "user-a:record-1")
    assert error.value.status_code == 409
    assert database.get(Project, first["project_id"]).name == "可研项目"


def test_caller_rollback_removes_entire_import(database):
    project, _, _, _ = seed(database)
    imported = import_record_package(database, export_record_package(database, project.id), "rollback")
    database.rollback()
    assert database.get(Project, imported["project_id"]) is None
    assert database.get(CheckRun, imported["check_run_ids"][0]) is None
    assert database.get(CheckResult, imported["result_ids"][0]) is None
    assert database.get(SourceArtifact, imported["source_ids"][0]) is None
    assert len(database.scalars(select(Project)).all()) == 1


@pytest.mark.parametrize("field", ["description", "reason", "suggestion"])
@pytest.mark.parametrize("text", ["a" * 65535, "中" * 21845], ids=["ascii", "chinese"])
def test_mysql_text_columns_preserve_utf8_byte_boundary(database, field, text):
    project, _, _, _ = seed(database)
    data = export_record_package(database, project.id).model_dump(mode="json")
    row = data["project"] if field == "description" else data["results"][0]
    row[field] = text
    package = RecordPackage.model_validate(data)
    assert len(text.encode("utf-8")) == 65535
    assert getattr(package.project if field == "description" else package.results[0], field) == text
    row[field] = text + "a"
    with pytest.raises(ValidationError, match="65,535 UTF-8"):
        RecordPackage.model_validate(data)


@pytest.mark.parametrize("mutation", ["hash", "base64", "foreign_run", "foreign_result", "foreign_source", "foreign_url", "absolute_url", "duplicate_id", "extra_field", "active_run"])
def test_untrusted_package_graph_and_files_are_validated(database, mutation):
    project, _, _, _ = seed(database)
    data = export_record_package(database, project.id).model_dump(mode="json")
    source, result = data["sources"][0], data["results"][0]
    if mutation == "hash":
        source["sha256"] = "0" * 64
    elif mutation == "base64":
        source["content_base64"] = "not valid base64!"
    elif mutation == "foreign_run":
        result["check_run_id"] = str(uuid4())
    elif mutation == "foreign_result":
        source["check_result_id"] = str(uuid4())
    elif mutation == "foreign_source":
        result["reference_data"]["result"]["source_documents"][0]["id"] = str(uuid4())
    elif mutation == "foreign_url":
        result["reference_data"]["result"]["source_documents"][0]["preview_url"] = f"/api/evaluate/results/{uuid4()}/sources/{source['id']}/preview"
    elif mutation == "absolute_url":
        result["reference_data"]["result"]["source_documents"][0]["preview_url"] = "https://example.com/payload"
    elif mutation == "duplicate_id":
        source["id"] = result["id"]
    elif mutation == "extra_field":
        data["role"] = "admin"
    elif mutation == "active_run":
        data["check_runs"][0]["status"] = "running"
    with pytest.raises(ValidationError):
        RecordPackage.model_validate(data)


def test_export_is_scoped_to_project_and_batch_and_rejects_running(database):
    project, run, _, _ = seed(database)
    other = Project(name="other")
    other_run = CheckRun(project=other, report_name="other.docx", status="completed")
    database.add(other)
    database.commit()
    with pytest.raises(HTTPException) as error:
        export_record_package(database, project.id, other_run.id)
    assert error.value.status_code == 404
    run.status = "running"
    database.commit()
    with pytest.raises(HTTPException) as error:
        export_record_package(database, project.id, run.id)
    assert error.value.status_code == 409


def test_legacy_without_run_or_sources_is_supported(database):
    project = Project(name="旧历史")
    result = CheckResult(project=project, module="completeness", check_subtype="completeness_report",
                         result_label="通过", reason="旧记录", reference_data='{"inventory": []}')
    database.add(project)
    database.commit()
    package = export_record_package(database, project.id)
    assert not package.check_runs and not package.sources
    copied = import_record_package(database, package, "legacy")
    database.commit()
    assert database.get(CheckResult, copied["result_ids"][0]).check_run_id is None


def test_hash_normalizes_timezones_and_record_order(database):
    project, _, _, _ = seed(database)
    package = export_record_package(database, project.id)
    data = package.model_dump(mode="json")
    data["project"]["created_at"] = package.project.created_at.astimezone(timezone(timedelta(hours=8))).isoformat()
    assert package_sha256(RecordPackage.model_validate(data)) == package_sha256(package)
    assert json.loads(canonical_package_bytes(package))["schema_version"] == 1


def test_completeness_inventory_and_query_ids_round_trip(database):
    project, _, result, source = seed(database)
    result.module = "completeness"
    result.check_subtype = "completeness_report"
    result.check_run_id = None
    result.reference_data = json.dumps({
        "inventory": [{"filename": source.filename, "sha256": hashlib.sha256(source.content).hexdigest(),
                       "size": len(source.content)}],
        "download_url": f"/api/completeness/reports/{result.id}/sources/0?project_id={project.id}&quote=a%20b%26c&result_id={result.id}#anchor",
    })
    database.commit()
    package = export_record_package(database, project.id)
    copied = import_record_package(database, package, "completeness")
    database.commit()
    copied_result = database.get(CheckResult, copied["result_ids"][0])
    data = json.loads(copied_result.reference_data)
    assert data["inventory"][0]["filename"] == source.filename
    assert data["download_url"] == (
        f"/api/completeness/reports/{copied_result.id}/sources/0?project_id={copied['project_id']}"
        f"&quote=a%20b%26c&result_id={copied_result.id}#anchor")
    assert export_record_package(database, copied["project_id"]).sources[0].sha256 == package.sources[0].sha256
    invalid = package.model_dump(mode="json")
    invalid["results"][0]["reference_data"]["download_url"] = invalid["results"][0]["reference_data"]["download_url"].replace("/sources/0?", "/sources/1?")
    with pytest.raises(ValidationError):
        RecordPackage.model_validate(invalid)
    invalid = package.model_dump(mode="json")
    invalid["results"][0]["reference_data"]["download_url"] = invalid["results"][0]["reference_data"]["download_url"].replace(f"project_id={project.id}", f"project_id={uuid4()}")
    with pytest.raises(ValidationError):
        RecordPackage.model_validate(invalid)


def test_package_limits_checked_before_any_insert(database, monkeypatch):
    import app.services.record_packages as codec
    project, _, _, _ = seed(database)
    data = export_record_package(database, project.id).model_dump(mode="json")
    monkeypatch.setattr(codec, "MAX_SOURCE_BYTES", 2)
    with pytest.raises(ValidationError):
        RecordPackage.model_validate(data)
    with pytest.raises(HTTPException) as error:
        export_record_package(database, project.id)
    assert error.value.status_code == 413
    assert len(database.scalars(select(Project)).all()) == 1
