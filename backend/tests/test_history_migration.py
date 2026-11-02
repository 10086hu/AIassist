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
