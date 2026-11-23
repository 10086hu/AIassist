"""Upgrade a pre-role database without promoting users or discarding history."""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db import session as database


def test_existing_users_migrate_to_user_role_and_history_is_preserved(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.execute(text("""CREATE TABLE users (
            id VARCHAR(36) PRIMARY KEY, username VARCHAR(32) NOT NULL UNIQUE,
            display_name VARCHAR(64) NOT NULL, password_hash VARCHAR(255) NOT NULL,
            is_active BOOLEAN NOT NULL, created_at DATETIME, last_login_at DATETIME
        )"""))
        connection.execute(text("""INSERT INTO users
            (id, username, display_name, password_hash, is_active)
            VALUES ('old-user', 'existing', '已有账号', 'unchanged-hash', 1)"""))
        connection.execute(text("""CREATE TABLE preserved_history (value TEXT)"""))
        connection.execute(text("INSERT INTO preserved_history VALUES ('existing record')"))
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    database.init_db()
    database.init_db()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT role, password_hash FROM users")).one() == ("user", "unchanged-hash")
        assert connection.execute(text("SELECT value FROM preserved_history")).scalar_one() == "existing record"
    assert {"history_uploads", "history_deliveries", "history_imports"} <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_mysql_transfer_tables_compile_with_large_text():
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateTable
    from app.db.models import HistoryUpload, HistoryDelivery, HistoryImport

    for model in (HistoryUpload, HistoryDelivery, HistoryImport):
        ddl = str(CreateTable(model.__table__).compile(dialect=mysql.dialect()))
        assert "UNIQUE" in ddl
        if model is not HistoryImport:
            assert "LONGTEXT" in ddl


def test_existing_issue_memory_sources_and_run_recovery_survive_auth_migration(tmp_path, monkeypatch):
    from app.db.models import (
        Base, CheckResult, CheckRun, Project, ReviewMemoryRevision, ReviewRunLink, SourceArtifact,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'existing-reviews.db'}")
    factory = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    snapshot = '{"evidence":"' + "原有证据" * 20000 + '"}'
    original = b"original-report" * 10000
    with factory.begin() as db:
        db.add(Project(id="existing-project", name="已有项目"))
        db.flush()
        db.add(CheckRun(id="existing-run", project_id="existing-project", report_name="报告.docx", status="running"))
        db.flush()
        db.add(CheckResult(id="existing-result", project_id="existing-project", check_run_id="existing-run",
                           module="basis", check_subtype="aggregate", result_label="需复核", reason="已有意见"))
        db.flush()
        db.add(ReviewRunLink(check_run_id="existing-run", mode="auto", track_key="original-track"))
        db.add(ReviewMemoryRevision(result_id="existing-result", project_id="existing-project", module="basis",
                                   track_key="original-track", source_hash="a" * 64, snapshot_json=snapshot))
        db.add(SourceArtifact(id="existing-source", check_result_id="existing-result", filename="报告.docx", content=original))
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    database.init_db()
    database.init_db()
    with factory() as db:
        assert db.get(Project, "existing-project").name == "已有项目"
        assert db.get(CheckRun, "existing-run").status == "interrupted"
        assert db.get(CheckResult, "existing-result").reason == "已有意见"
        assert db.get(ReviewRunLink, "existing-run").track_key == "original-track"
        assert db.get(ReviewMemoryRevision, "existing-result").snapshot_json == snapshot
        assert db.get(SourceArtifact, "existing-source").content == original
    engine.dispose()


def test_mysql_existing_review_models_support_full_results_memory_and_sources():
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateTable
    from app.db.models import CheckResult, ReviewMemoryRevision, SourceArtifact

    for model, column, sql_type in (
        (CheckResult, "reference_data", "LONGTEXT"),
        (ReviewMemoryRevision, "snapshot_json", "LONGTEXT"),
        (SourceArtifact, "content", "LONGBLOB"),
    ):
        ddl = str(CreateTable(model.__table__).compile(dialect=mysql.dialect()))
        assert f"{column} {sql_type}" in ddl


def test_transfer_body_limit_applies_without_content_length():
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from app.core.history_limits import HistoryBodyLimitMiddleware

    app = FastAPI()
    app.add_middleware(HistoryBodyLimitMiddleware, max_bytes=8)

    @app.post("/api/history/uploads")
    async def echo(request: Request):
        return {"size": len(await request.body())}

    with TestClient(app) as client:
        assert client.post("/api/history/uploads", content=b"12345678").json() == {"size": 8}
        assert client.post("/api/history/uploads", content=iter([b"1234", b"56789"])).status_code == 413
