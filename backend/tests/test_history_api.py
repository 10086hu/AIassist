"""Real-auth integration coverage for transferring history between two databases."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import importlib
import json
import secrets
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.core.auth import token_digest
from app.db.models import (
    AuthSession, Base, CheckResult, CheckRun, HistoryDelivery, HistoryImport,
    HistoryUpload, Project, SourceArtifact, User,
)
from app.db.session import get_db


REPORT_BYTES = "历史报告中的原文：建设内容应保持一致。".encode("utf-8")


class HistoryEnvironment:
    def __init__(self, tmp_path, monkeypatch):
        self.main = importlib.import_module("app.main")
        self.history = importlib.import_module("app.api.history")
        self.access = importlib.import_module("app.core.auth")
        self.codec = importlib.import_module("app.services.record_packages")
        self.monkeypatch = monkeypatch
        self.mode = "local"
        self.engines = {}
        self.factories = {}
        self.identities = {}
        for mode in ("local", "server"):
            engine = create_engine(
                f"sqlite:///{tmp_path / (mode + '.db')}",
                connect_args={"check_same_thread": False, "timeout": 30},
            )

            @event.listens_for(engine, "connect")
            def enable_foreign_keys(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")

            Base.metadata.create_all(engine)
            self.engines[mode] = engine
            factory = self.factories[mode] = sessionmaker(bind=engine)
            self.identities[mode] = {}
            with factory.begin() as db:
                for name in ("admin", "alice", "bob"):
                    token = secrets.token_urlsafe(32)
                    user = User(
                        username=name, display_name=name,
                        password_hash="unused-direct-session-fixture",
                        role="admin" if name == "admin" else "user",
                    )
                    db.add(user)
                    db.flush()
                    db.add(AuthSession(
                        user_id=user.id, token_hash=token_digest(token),
                        expires_at=datetime.utcnow() + timedelta(hours=1),
                    ))
                    self.identities[mode][name] = {
                        "id": user.id, "headers": {"Authorization": "Bearer " + token},
                    }
        self.main.app.dependency_overrides[get_db] = self.database
        monkeypatch.setattr(self.main, "init_db", lambda: None)
        self.switch("local")
        with self.factories["local"].begin() as db:
            project = Project(name="本地审查项目", department="测试部门", status="completed")
            run = CheckRun(project=project, report_name="报告.txt", source_filename="报告.txt", status="completed")
            result = CheckResult(
                project=project, check_run=run, module="sensitive_word", check_subtype="aggregate",
                result_label="发现问题", reason="保留原始审查结论", severity="warning",
                reference_data=json.dumps({"report_name": "报告.txt", "result": {
                    "summary": {"total_findings": 1},
                    "findings": [{"reason": "原始意见", "source_location": {"line": 1}}],
                }}, ensure_ascii=False),
            )
            artifact = SourceArtifact(
                check_result=result, filename="报告.txt", role="current",
                media_type="text/plain", content=REPORT_BYTES,
            )
            db.add_all([project, run, result, artifact])
            db.flush()
            self.source_id, self.run_id = project.id, run.id
            self.source_result_id, self.source_artifact_id = result.id, artifact.id
        self.package = self.export_source()

    def database(self):
        with self.factories[self.mode]() as db:
            yield db

    def switch(self, mode):
        self.mode = mode
        for module in (self.history, self.access):
            self.monkeypatch.setattr(module, "settings", replace(module.settings, backend_mode=mode))
        return self

    def identity(self, name="alice", mode=None):
        return self.identities[mode or self.mode][name]

    def request(self, method, path, user="alice", **kwargs):
        if user is not None:
            kwargs["headers"] = self.identity(user)["headers"]
        return self.client.request(method, path, **kwargs)

    def export_source(self):
        with self.factories["local"]() as db:
            return self.codec.export_record_package(db, self.source_id).model_dump(mode="json")

    def upload(self, user="alice", client_record_id=None, package=None):
        return self.request("POST", "/api/history/uploads", user=user, json={
            "client_record_id": client_record_id or self.source_id,
            "package": package or self.package,
        })

    def deliver(self, project_id, recipient="bob"):
        return self.request("POST", "/api/admin/history/deliveries", user="admin", json={
            "recipient_user_id": self.identity(recipient, "server")["id"],
            "project_id": project_id,
        })

    def count(self, model, mode=None):
        with self.factories[mode or self.mode]() as db:
            return db.scalar(select(func.count()).select_from(model))


@pytest.fixture
def history_env(tmp_path, monkeypatch):
    env = HistoryEnvironment(tmp_path, monkeypatch)
    try:
        with TestClient(env.main.app, raise_server_exceptions=False) as client:
            env.client = client
            yield env
    finally:
        env.main.app.dependency_overrides.clear()
        for engine in env.engines.values():
            engine.dispose()


def success(response):
    assert response.status_code in {200, 201}, response.text
    return response.json()


def test_deleted_project_does_not_return_a_successful_upload_retry(history_env):
    env = history_env.switch("server")
    uploaded = success(env.upload())
    assert env.request("DELETE", f"/api/projects/{uploaded['project_id']}", user="admin").status_code == 200
    assert env.upload().status_code == 409
    assert env.count(Project) == 0


def test_uploaded_active_content_cannot_be_used_as_inline_preview(history_env):
    import base64
    import hashlib

    env = history_env.switch("server")
    package = json.loads(json.dumps(env.package))
    source = package["sources"][0]
    raw = b"<script>window.top.document.body.innerHTML='injected'</script>"
    source["content_base64"] = base64.b64encode(raw).decode()
    source["sha256"] = hashlib.sha256(raw).hexdigest()
    source["media_type"] = "text/html"
    result = package["results"][0]
    path = f"/api/evaluate/results/{result['id']}/sources/{source['id']}"
    result["reference_data"]["result"]["source_documents"] = [{"id": source["id"], "preview_url": path}]
    rejected = env.upload(package=package)
    assert rejected.status_code == 422, rejected.text
    assert source["content_base64"] not in rejected.text
    result["reference_data"]["result"]["source_documents"][0]["preview_url"] = path + "/preview"
    uploaded = success(env.upload(package=package))
    with env.factories["server"]() as db:
        stored = db.scalar(select(CheckResult).where(CheckResult.project_id == uploaded["project_id"]))
        attachment = db.scalar(select(SourceArtifact).where(SourceArtifact.check_result_id == stored.id))
        download_path = f"/api/evaluate/results/{stored.id}/sources/{attachment.id}"
    download = env.request("GET", download_path, user="admin")
    assert download.status_code == 200
    assert download.headers["Content-Disposition"].startswith("attachment;")
    assert download.headers["Content-Type"].startswith("text/plain")
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in download.headers["Content-Security-Policy"]
    preview = env.request("GET", download_path + "/preview", user="admin")
    assert preview.status_code == 200
    assert "<script>window.top" not in preview.text
    assert "&lt;script&gt;" in preview.text


def test_upload_retry_conflict_and_user_scoped_listing(history_env):
    env = history_env.switch("server")
    first = success(env.upload())
    retry = success(env.upload())
    assert first["duplicate"] is False
    assert retry["duplicate"] is True and retry["id"] == first["id"]
    assert env.count(HistoryUpload) == env.count(Project) == 1
    assert env.count(CheckRun) == env.count(CheckResult) == env.count(SourceArtifact) == 1

    # A reused transfer identifier must never overwrite an accepted revision.
    with env.factories["local"].begin() as db:
        db.get(Project, env.source_id).name = "不同版本的项目"
    changed = env.export_source()
    assert env.upload(package=changed).status_code == 409
    assert env.count(HistoryUpload) == env.count(Project) == 1

    # Identical client UUIDs belong to each sender's namespace.
    other = success(env.upload(user="bob"))
    assert other["id"] != first["id"] and other["project_id"] != first["project_id"]
    for user, upload in (("alice", first), ("bob", other)):
        items = success(env.request("GET", "/api/history/uploads", user=user))["items"]
        assert [item["id"] for item in items] == [upload["id"]]
        assert all("package_json" not in item for item in items)
    all_items = success(env.request("GET", "/api/admin/history/uploads", user="admin"))["items"]
    assert {item["id"] for item in all_items} == {first["id"], other["id"]}
    exported = env.request("GET", f"/api/admin/history/uploads/{first['id']}/package", user="admin")
    assert success(exported) == env.package
    assert exported.headers["X-Package-SHA256"] == first["package_sha256"]


def test_anonymous_and_regular_users_cannot_bypass_server_authorization(history_env):
    env = history_env.switch("server")
    upload = success(env.upload())
    delivery = success(env.deliver(upload["project_id"]))
    delivery_id = delivery["id"]
    anonymous_routes = [
        ("GET", "/api/history/uploads", None),
        ("POST", "/api/history/uploads", {"client_record_id": env.source_id, "package": env.package}),
        ("GET", "/api/history/inbox", None),
        ("GET", f"/api/history/inbox/{delivery_id}/package", None),
        ("POST", f"/api/history/inbox/{delivery_id}/ack", {"package_sha256": delivery["package_sha256"]}),
        ("GET", "/api/admin/history/uploads", None),
        ("GET", f"/api/history/exports/{upload['project_id']}", None),
    ]
    for method, path, payload in anonymous_routes:
        response = env.request(method, path, user=None, **({"json": payload} if payload else {}))
        assert response.status_code == 401, (path, response.text)
    forbidden_routes = [
        ("GET", "/api/admin/history/uploads", None),
        ("GET", f"/api/admin/history/uploads/{upload['id']}/package", None),
        ("DELETE", f"/api/admin/history/uploads/{upload['id']}", None),
        ("GET", "/api/admin/history/deliveries", None),
        ("POST", "/api/admin/history/deliveries", {
            "recipient_user_id": env.identity("alice")["id"], "project_id": upload["project_id"]}),
        ("POST", f"/api/admin/history/deliveries/{delivery_id}/revoke", None),
        ("GET", f"/api/history/exports/{upload['project_id']}", None),
        ("GET", "/api/projects", None),
        ("GET", "/api/projects/tree", None),
        ("GET", "/api/evaluate/results", None),
        ("GET", f"/api/completeness/reports?project_id={upload['project_id']}", None),
    ]
    for method, path, payload in forbidden_routes:
        response = env.request(method, path, **({"json": payload} if payload else {}))
        assert response.status_code == 403, (path, response.text)
    assert env.count(HistoryUpload) == env.count(HistoryDelivery) == 1


def test_delivery_recipient_isolation_retry_and_acknowledgement(history_env):
    env = history_env.switch("server")
    upload = success(env.upload())
    delivery = success(env.deliver(upload["project_id"]))
    retry = success(env.deliver(upload["project_id"]))
    assert delivery["status"] == "pending" and delivery["duplicate"] is False
    assert retry["id"] == delivery["id"] and retry["duplicate"] is True
    delivery_id = delivery["id"]
    inbox = success(env.request("GET", "/api/history/inbox", user="bob"))["items"]
    assert [item["id"] for item in inbox] == [delivery_id]
    assert success(env.request("GET", "/api/history/inbox", user="alice"))["items"] == []
    for suffix in ("package", "ack"):
        method = "GET" if suffix == "package" else "POST"
        kwargs = {} if method == "GET" else {"json": {"package_sha256": delivery["package_sha256"]}}
        response = env.request(method, f"/api/history/inbox/{delivery_id}/{suffix}", user="alice", **kwargs)
        assert response.status_code == 404
    response = env.request("GET", f"/api/history/inbox/{delivery_id}/package", user="bob")
    success(response)
    assert response.headers["X-Package-SHA256"] == delivery["package_sha256"]
    assert response.headers["Cache-Control"] == "no-store"
    mismatch = env.request("POST", f"/api/history/inbox/{delivery_id}/ack", user="bob", json={"package_sha256": "0" * 64})
    assert mismatch.status_code == 409
    for _ in range(2):
        ack = success(env.request("POST", f"/api/history/inbox/{delivery_id}/ack", user="bob", json={
            "package_sha256": delivery["package_sha256"],
        }))
        assert ack["status"] == "received"
    filtered = success(env.request("GET", "/api/admin/history/deliveries", user="admin", params={
        "recipient_user_id": env.identity("alice")["id"],
    }))["items"]
    assert filtered == []
    with env.factories["server"]() as db:
        row = db.get(HistoryDelivery, delivery_id)
        assert row.received_at is not None


def test_revoked_delivery_cannot_be_downloaded_or_acknowledged(history_env):
    env = history_env.switch("server")
    upload = success(env.upload())
    delivery = success(env.deliver(upload["project_id"]))
    delivery_id = delivery["id"]
    for _ in range(2):
        revoked = success(env.request("POST", f"/api/admin/history/deliveries/{delivery_id}/revoke", user="admin"))
        assert revoked["status"] == "revoked"
    assert env.request("GET", f"/api/history/inbox/{delivery_id}/package", user="bob").status_code == 410
    assert env.request("POST", f"/api/history/inbox/{delivery_id}/ack", user="bob", json={
        "package_sha256": delivery["package_sha256"],
    }).status_code == 410
    assert env.request("GET", f"/api/history/inbox/{delivery_id}/package", user="alice").status_code == 404


def test_two_database_round_trip_preserves_results_and_attachment_preview(history_env):
    env = history_env
    source = env.request("GET", f"/api/history/exports/{env.source_id}")
    package = success(source)
    assert package == env.package
    env.switch("server")
    upload = success(env.upload(package=package))
    delivery = success(env.deliver(upload["project_id"]))
    delivered = env.request("GET", f"/api/history/inbox/{delivery['id']}/package", user="bob")
    transferred_package = success(delivered)
    payload = {
        "delivery_id": delivery["id"], "package_sha256": delivered.headers["X-Package-SHA256"],
        "package": transferred_package,
    }
    # A server's session token is not a valid local session.
    server_headers = env.identity("bob")["headers"]
    env.switch("local")
    assert env.client.post("/api/history/imports", json=payload, headers=server_headers).status_code == 401
    imported = success(env.request("POST", "/api/history/imports", json=payload))
    retried = success(env.request("POST", "/api/history/imports", json=payload))
    assert imported["duplicate"] is False
    assert retried["duplicate"] is True and retried["project_id"] == imported["project_id"]
    assert env.count(HistoryImport) == 1
    assert env.count(Project) == env.count(CheckRun) == env.count(CheckResult) == env.count(SourceArtifact) == 2
    with env.factories["local"]() as db:
        result = db.scalar(select(CheckResult).where(CheckResult.project_id == imported["project_id"]))
        artifact = result.source_artifacts[0]
        assert result.reason == "保留原始审查结论"
        assert artifact.content == REPORT_BYTES and artifact.filename == "报告.txt"
        result_id, artifact_id = result.id, artifact.id
    detail = success(env.request("GET", f"/api/evaluate/results/{result_id}"))
    assert detail["findings"][0]["reason"] == "原始意见"
    assert detail["source_documents"][0]["id"] == artifact_id
    attachment = env.request("GET", f"/api/evaluate/results/{result_id}/sources/{artifact_id}")
    assert attachment.status_code == 200 and attachment.content == REPORT_BYTES
    preview = env.request("GET", f"/api/evaluate/results/{result_id}/sources/{artifact_id}/preview")
    assert preview.status_code == 200 and "建设内容应保持一致" in preview.text
    corrupt = {**payload, "package_sha256": "0" * 64}
    assert env.request("POST", "/api/history/imports", json=corrupt).status_code == 422
    different = {**payload, "package": env.package, "package_sha256": source.headers["X-Package-SHA256"]}
    assert env.request("POST", "/api/history/imports", json=different).status_code == 409
    assert env.count(HistoryImport) == 1


def test_deleted_upload_does_not_destroy_an_already_addressed_snapshot(history_env):
    env = history_env.switch("server")
    upload = success(env.upload())
    delivery = success(env.deliver(upload["project_id"]))
    original = success(env.request("GET", f"/api/history/inbox/{delivery['id']}/package", user="bob"))
    deleted = env.request("DELETE", f"/api/admin/history/uploads/{upload['id']}", user="admin")
    assert deleted.status_code in {200, 204}, deleted.text
    assert env.count(HistoryUpload) == env.count(Project) == env.count(CheckResult) == env.count(SourceArtifact) == 0
    assert env.count(HistoryDelivery) == 1
    assert success(env.request("GET", f"/api/history/inbox/{delivery['id']}/package", user="bob")) == original
    assert env.request("GET", f"/api/admin/history/uploads/{upload['id']}/package", user="admin").status_code == 404


def test_duplicate_uploads_racing_create_one_complete_import(history_env):
    env = history_env.switch("server")
    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(lambda _: env.upload(), range(2)))
    results = [success(response) for response in responses]
    assert results[0]["id"] == results[1]["id"]
    assert sorted(result["duplicate"] for result in results) == [False, True]
    assert env.count(HistoryUpload) == env.count(Project) == env.count(CheckRun) == env.count(CheckResult) == env.count(SourceArtifact) == 1


def test_duplicate_first_deliveries_racing_create_one_inbox_entry(history_env):
    env = history_env.switch("server")
    upload = success(env.upload())
    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(lambda _: env.deliver(upload["project_id"]), range(2)))
    results = [success(response) for response in responses]
    assert results[0]["id"] == results[1]["id"]
    assert sorted(result["duplicate"] for result in results) == [False, True]
    assert env.count(HistoryDelivery) == 1


def test_redispatch_reopens_same_revoked_delivery_with_current_state(history_env):
    env = history_env.switch("server")
    upload = success(env.upload())
    delivery = success(env.deliver(upload["project_id"]))
    ack = success(env.request("POST", f"/api/history/inbox/{delivery['id']}/ack", user="bob", json={
        "package_sha256": delivery["package_sha256"],
    }))
    assert ack["received_at"] is not None
    revoked = success(env.request("POST", f"/api/admin/history/deliveries/{delivery['id']}/revoke", user="admin"))
    assert revoked["revoked_at"] is not None
    renewed = success(env.deliver(upload["project_id"]))
    assert renewed["id"] == delivery["id"] and renewed["duplicate"] is True
    assert renewed["status"] == "pending"
    assert renewed["received_at"] is None and renewed["revoked_at"] is None
    assert env.request("GET", f"/api/history/inbox/{delivery['id']}/package", user="bob").status_code == 200


def test_mode_guards_keep_uploads_remote_and_imports_local(history_env):
    env = history_env
    assert env.upload().status_code == 403
    assert env.request("GET", "/api/history/inbox").status_code == 403
    source = env.request("GET", f"/api/history/exports/{env.source_id}")
    package = success(source)
    payload = {"delivery_id": str(uuid4()), "package_sha256": source.headers["X-Package-SHA256"], "package": package}
    env.switch("server")
    assert env.request("POST", "/api/history/imports", json=payload).status_code == 403
