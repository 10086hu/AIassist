"""Exercise the deployed application's real auth dependencies and DB transactions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from app.core.auth import token_digest
from app.core.passwords import hash_password, verify_password
from app.db.models import AuthRateLimit, AuthSession, Base, User
from app.db.session import get_db


PASSWORD = "Auth-test-passphrase-2026!"
NEW_PASSWORD = "New-auth-passphrase-2026!"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    main = importlib.import_module("app.main")
    engine = create_engine(f"sqlite:///{tmp_path / 'auth.db'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)

    def database():
        with factory() as db:
            yield db

    main.app.dependency_overrides[get_db] = database
    monkeypatch.setattr(main, "init_db", lambda: Base.metadata.create_all(engine))
    with TestClient(main.app) as client:
        yield client, factory, main.app
    main.app.dependency_overrides.clear()
    engine.dispose()


def register(client, username="auth_tester", password=PASSWORD, **extra):
    return client.post("/api/auth/register", json={"username": username, "password": password, **extra})


def login(client, username="auth_tester", password=PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def headers(token):
    return {"Authorization": "Bearer " + token}


def test_full_lifecycle_and_real_business_access(setup):
    client, factory, _ = setup
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/projects").status_code == 401
    response = register(client, username=" Auth_Tester ", display_name=" 测试用户 ")
    assert response.status_code == 201, response.text
    user = response.json()
    assert user["username"] == "auth_tester" and user["display_name"] == "测试用户"
    assert user["created_at"].endswith("Z")
    assert "password" not in response.text and "hash" not in response.text
    response = login(client, username="AUTH_TESTER")
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    token = body["access_token"]
    assert len(token) == 43 and body["expires_in"] == 86400
    assert body["token_type"] == "bearer" and body["expires_at"].endswith("Z")
    assert body["user"]["last_login_at"] is not None
    auth = headers(token)
    assert client.get("/api/auth/me", headers=auth).json()["id"] == user["id"]
    project = client.post("/api/projects", headers=auth, json={"name": "登录后的业务测试"})
    assert project.status_code == 200
    project_id = project.json()["id"]
    assert any(p["id"] == project_id for p in client.get("/api/projects", headers=auth).json())
    assert client.delete(f"/api/projects/{project_id}", headers=auth).status_code == 200
    with factory() as db:
        stored_user = db.scalar(select(User))
        stored_session = db.scalar(select(AuthSession))
        assert stored_user.password_hash != PASSWORD
        assert verify_password(PASSWORD, stored_user.password_hash)
        assert stored_session.token_hash == token_digest(token) and stored_session.token_hash != token
    assert client.post("/api/auth/logout", headers=auth).status_code == 204
    assert client.get("/api/auth/me", headers=auth).status_code == 401
    assert client.get("/api/projects", headers=auth).status_code == 401
    with factory() as db:
        assert db.scalar(select(AuthSession)) is None


def test_duplicates_and_uniform_invalid_credentials(setup):
    client, _, _ = setup
    assert register(client).status_code == 201
    assert register(client, username="AUTH_TESTER").status_code == 409
    wrong = login(client, password="incorrect-password")
    unknown = login(client, username="unknown_user")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("payload", [
    {"username": "a!b", "password": PASSWORD},
    {"username": "ab", "password": PASSWORD},
    {"username": "a" * 33, "password": PASSWORD},
    {"username": "valid", "password": "short-secret"[:8]},
    {"username": "valid", "password": "a" * 129},
    {"username": "valid", "password": " " * 12},
    {"username": "valid", "password": PASSWORD, "display_name": "  "},
    {"username": "valid", "password": PASSWORD, "is_active": True},
])
def test_validation_redacts_inputs(setup, payload):
    client, factory, _ = setup
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 422, response.text
    for error in response.json()["detail"]:
        assert set(error) == {"loc", "msg", "type"}
    assert payload["password"] not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    with factory() as db:
        assert db.scalar(select(User)) is None


def test_unicode_and_password_whitespace_preserved(setup):
    client, _, _ = setup
    secret = "  密码口令含有中文还有空格2026  "
    assert register(client, password=secret).status_code == 201
    assert login(client, password=secret).status_code == 200
    assert login(client, password=secret.strip()).status_code == 401


@pytest.mark.parametrize("authorization", [None, "Basic abc", "Bearer abc", "Bearer " + "x" * 43])
def test_missing_malformed_and_forged_tokens(setup, authorization):
    client, _, _ = setup
    auth = {"Authorization": authorization} if authorization else {}
    assert client.get("/api/auth/me", headers=auth).status_code == 401
    assert client.get("/api/evaluate/results", headers=auth).status_code == 401
    assert client.get("/api/completeness/reports?project_id=missing", headers=auth).status_code == 401
    assert client.post("/api/evaluate/tasks", headers=auth).status_code == 401


def test_every_business_route_declares_auth_in_openapi(setup):
    client, _, _ = setup
    schema = client.get("/openapi.json").json()
    for path, methods in schema["paths"].items():
        if path.startswith(("/api/projects", "/api/evaluate", "/api/completeness")):
            for operation in methods.values():
                assert {"BearerAuth": []} in operation["security"], path


def test_expiry_and_account_deactivation(setup):
    client, factory, _ = setup
    assert register(client).status_code == 201
    token = login(client).json()["access_token"]
    with factory.begin() as db:
        db.get(AuthSession, token_digest(token)).expires_at = datetime.utcnow() - timedelta(seconds=1)
    assert client.get("/api/auth/me", headers=headers(token)).status_code == 401
    token = login(client).json()["access_token"]
    with factory.begin() as db:
        db.scalar(select(User)).is_active = False
    assert client.get("/api/auth/me", headers=headers(token)).status_code == 401
    assert login(client).status_code == 401


def test_password_change_revokes_all_sessions_and_logout_all(setup):
    client, _, _ = setup
    register(client)
    first = login(client).json()["access_token"]
    second = login(client).json()["access_token"]
    auth = headers(first)
    bad = client.put("/api/auth/password", headers=auth, json={"current_password": "wrong", "new_password": NEW_PASSWORD})
    assert bad.status_code == 400
    same = client.put("/api/auth/password", headers=auth, json={"current_password": PASSWORD, "new_password": PASSWORD})
    assert same.status_code == 400
    changed = client.put("/api/auth/password", headers=auth, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert changed.status_code == 204, changed.text
    assert client.get("/api/auth/me", headers=auth).status_code == 401
    assert client.get("/api/auth/me", headers=headers(second)).status_code == 401
    assert login(client).status_code == 401
    third = login(client, password=NEW_PASSWORD).json()["access_token"]
    fourth = login(client, password=NEW_PASSWORD).json()["access_token"]
    assert client.post("/api/auth/logout-all", headers=headers(third)).status_code == 204
    for token in (third, fourth):
        assert client.get("/api/auth/me", headers=headers(token)).status_code == 401


def test_single_logout_does_not_revoke_another_device(setup):
    client, _, _ = setup
    register(client)
    first = login(client).json()["access_token"]
    second = login(client).json()["access_token"]
    assert client.post("/api/auth/logout", headers=headers(first)).status_code == 204
    assert client.get("/api/auth/me", headers=headers(second)).status_code == 200


def test_account_and_ip_rate_limits(setup):
    client, factory, _ = setup
    for _ in range(10):
        assert login(client, username="unknown_user").status_code == 401
    limited = login(client, username="unknown_user")
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
    for i in range(19):
        assert login(client, username=f"unknown_{i}").status_code == 401
    assert login(client, username="another_user").status_code == 429
    for _ in range(10):
        assert register(client).status_code in {201, 409}
    assert register(client).status_code == 429
    # Expired time buckets are pruned, and normal requests resume.
    with factory.begin() as db:
        db.execute(delete(AuthRateLimit))
    assert login(client).status_code == 200


def test_concurrent_duplicate_registration_has_one_winner(setup):
    client, factory, _ = setup
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: register(client).status_code, range(2)))
    assert sorted(statuses) == [201, 409]
    with factory() as db:
        assert len(db.scalars(select(User)).all()) == 1


def test_session_survives_a_new_application_client(setup):
    client, _, app = setup
    register(client)
    token = login(client).json()["access_token"]
    with TestClient(app) as another:
        assert another.get("/api/auth/me", headers=headers(token)).status_code == 200


def test_scrypt_uses_random_salts_and_rejects_corruption():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second
    assert verify_password(PASSWORD, first) and verify_password(PASSWORD, second)
    assert not verify_password("wrong", first)
    assert not verify_password(PASSWORD, "broken")
    assert not verify_password(PASSWORD, first.replace("32768", "999999999"))
