"""Real bearer authorization for administrator and ordinary desktop clients."""
from dataclasses import replace
from datetime import datetime, timedelta
import importlib
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.cli.manage_admin import set_role
from app.core.auth import token_digest
from app.db.models import AuditLog, AuthSession, Base, User
from app.db.session import get_db


@pytest.fixture
def setup(tmp_path, monkeypatch):
    main = importlib.import_module("app.main")
    auth_dependencies = importlib.import_module("app.core.auth")
    monkeypatch.setattr(auth_dependencies, "settings", replace(auth_dependencies.settings, backend_mode="server"))
    engine = create_engine(f"sqlite:///{tmp_path / 'roles.db'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    identities = {}
    with factory.begin() as db:
        for index, (username, role) in enumerate((("admin_tester", "admin"), ("user_one", "user"), ("user_two", "user"))):
            user = User(username=username, display_name=username, role=role, password_hash="unused-test-hash")
            db.add(user)
            db.flush()
            token = str(index) * 43
            db.add(AuthSession(token_hash=token_digest(token), user_id=user.id, expires_at=datetime.utcnow() + timedelta(hours=1)))
            identities[username] = {"id": user.id, "headers": {"Authorization": "Bearer " + token}}

    def database():
        with factory() as db:
            yield db

    main.app.dependency_overrides[get_db] = database
    monkeypatch.setattr(main, "init_db", lambda: None)
    with TestClient(main.app) as client:
        yield client, factory, identities, auth_dependencies
    main.app.dependency_overrides.clear()
    engine.dispose()


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/projects"),
    ("POST", "/api/projects"),
    ("GET", "/api/projects/tree"),
    ("GET", "/api/projects/private-project"),
    ("DELETE", "/api/projects/private-project"),
    ("POST", "/api/projects/private-project/check-runs"),
    ("GET", "/api/evaluate/results"),
    ("GET", "/api/evaluate/results/private-result"),
    ("GET", "/api/evaluate/results/private-result/sources/private-source"),
    ("GET", "/api/evaluate/results/private-result/sources/private-source/preview"),
    ("DELETE", "/api/evaluate/results/private-result"),
    ("POST", "/api/evaluate/tasks"),
    ("GET", "/api/evaluate/tasks/private-task"),
    ("POST", "/api/evaluate/price-benchmarks/import"),
    ("GET", "/api/completeness/reports"),
    ("POST", "/api/completeness/reports"),
    ("GET", "/api/completeness/reports/private-report/export"),
    ("GET", "/api/completeness/reports/private-report/sources/0"),
])
def test_server_user_cannot_bypass_history_permissions_via_legacy_routes(setup, method, path):
    client, _, identities, _ = setup
    response = client.request(method, path, headers=identities["user_one"]["headers"])
    assert response.status_code == 403, response.text


def test_roles_returned_and_admin_can_manage_shared_workspace(setup):
    client, _, identities, _ = setup
    for username, role in (("admin_tester", "admin"), ("user_one", "user")):
        response = client.get("/api/auth/me", headers=identities[username]["headers"])
        assert response.status_code == 200
        assert response.json()["role"] == role
    auth = identities["admin_tester"]["headers"]
    created = client.post("/api/projects", headers=auth, json={"name": "统一管理项目"})
    assert created.status_code == 200, created.text
    assert len(client.get("/api/projects", headers=auth).json()) == 1
    assert client.delete("/api/projects/" + created.json()["id"], headers=auth).status_code == 200


def test_local_user_can_review_without_administrator_role(setup, monkeypatch):
    client, _, identities, auth_dependencies = setup
    monkeypatch.setattr(auth_dependencies, "settings", replace(auth_dependencies.settings, backend_mode="local"))
    auth = identities["user_one"]["headers"]
    assert client.post("/api/projects", headers=auth, json={"name": "本地项目"}).status_code == 200
    assert client.get("/api/projects/tree", headers=auth).status_code == 200
    assert client.get("/api/admin/users", headers=auth).status_code == 403


def test_user_directory_is_admin_only_paginated_and_redacted(setup):
    client, _, identities, _ = setup
    assert client.get("/api/admin/users").status_code == 401
    assert client.get("/api/admin/users", headers=identities["user_one"]["headers"]).status_code == 403
    auth = identities["admin_tester"]["headers"]
    response = client.get("/api/admin/users", params={"q": " USER_ ", "role": "user", "limit": 1, "offset": 1}, headers=auth)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 2 and len(page["items"]) == 1
    assert page["items"][0]["username"] == "user_two"
    assert not any(word in response.text for word in ("password", "hash", "token"))
    assert client.get("/api/admin/users", params={"q": "%"}, headers=auth).json()["total"] == 0
    assert client.get("/api/admin/users?limit=101", headers=auth).status_code == 422


def test_deactivation_revokes_sessions_and_cannot_disable_self(setup):
    client, factory, identities, _ = setup
    admin = identities["admin_tester"]
    target = identities["user_one"]
    url = "/api/admin/users/" + target["id"]
    assert client.patch(url, json={"is_active": False}, headers=target["headers"]).status_code == 403
    assert client.patch("/api/admin/users/" + admin["id"], json={"is_active": False}, headers=admin["headers"]).status_code == 409
    assert client.patch(url, json={"is_active": False, "role": "admin"}, headers=admin["headers"]).status_code == 422
    assert client.patch(url, json={"is_active": "false"}, headers=admin["headers"]).status_code == 422
    response = client.patch(url, json={"is_active": False}, headers=admin["headers"])
    assert response.status_code == 200 and response.json()["is_active"] is False
    assert client.get("/api/auth/me", headers=target["headers"]).status_code == 401
    with factory() as db:
        assert db.scalar(select(AuthSession).where(AuthSession.user_id == target["id"])) is None
        assert db.scalar(select(AuditLog).where(AuditLog.action == "set_user_status")) is not None
    assert client.patch(url, json={"is_active": True}, headers=admin["headers"]).status_code == 200
    # Re-enabling an account does not revive a revoked token.
    assert client.get("/api/auth/me", headers=target["headers"]).status_code == 401
    assert client.patch("/api/admin/users/missing", json={"is_active": False}, headers=admin["headers"]).status_code == 404


def test_role_cli_requires_explicit_account_and_protects_last_admin(setup):
    client, factory, identities, _ = setup
    with factory.begin() as db:
        with pytest.raises(ValueError, match="最后一个"):
            set_role(db, "admin_tester", "user")
        with pytest.raises(ValueError, match="用户不存在"):
            set_role(db, "missing", "admin")
        set_role(db, " USER_ONE ", "admin")
    assert client.get("/api/auth/me", headers=identities["user_one"]["headers"]).status_code == 401
    with factory.begin() as db:
        assert db.get(User, identities["user_one"]["id"]).role == "admin"
        set_role(db, "admin_tester", "user")
    assert client.get("/api/auth/me", headers=identities["admin_tester"]["headers"]).status_code == 401
    with factory() as db:
        assert len(db.scalars(select(AuditLog).where(AuditLog.action == "set_user_role")).all()) == 2


def test_concurrent_admins_cannot_disable_one_another(setup):
    client, factory, identities, _ = setup
    first, second = identities["admin_tester"], identities["user_two"]
    with factory.begin() as db:
        db.get(User, second["id"]).role = "admin"

    def disable(pair):
        caller, target = pair
        return client.patch("/api/admin/users/" + target["id"],
                            json={"is_active": False}, headers=caller["headers"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(disable, ((first, second), (second, first))))
    assert [response.status_code for response in responses] == [409, 409]
    with factory() as db:
        active_admins = db.scalars(select(User).where(User.role == "admin", User.is_active.is_(True))).all()
        assert len(active_admins) == 2
