"""Verify a deployed auth API. Reports contain no passwords or bearer tokens.

--keep --state-file /tmp/private.json retains a mode-0600 session for a restart
test. After restarting services use --verify-state /tmp/private.json. A synthetic
auth_smoke_* account remains for administrator cleanup; its ID is in the report.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://10.60.150.179:8000")
    parser.add_argument("--record", type=Path)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--verify-state", type=Path)
    args = parser.parse_args()
    if args.keep and not args.state_file:
        parser.error("--keep requires --state-file outside the repository")
    if args.state_file and args.state_file.exists():
        parser.error("state file already exists; refusing to overwrite")
    base = args.base_url.rstrip("/")
    client = requests.Session()
    client.trust_env = False
    checks = {}

    def request(method, path, expected=200, token=None, **kwargs):
        auth = {"Authorization": "Bearer " + token} if token else {}
        response = client.request(method, base + path, headers=auth, timeout=30, **kwargs)
        if response.status_code != expected:
            raise AssertionError(f"{method} {path}: expected {expected}, got {response.status_code}")
        return response

    def login(username, password, expected=200):
        return request("POST", "/api/auth/login", expected=expected,
                       json={"username": username, "password": password})

    if args.verify_state:
        state = json.loads(args.verify_state.read_text())
        assert state["base_url"] == base
        username, password, token = state["username"], state["password"], state["token"]
        assert request("GET", "/api/auth/me", token=token).json()["id"] == state["user_id"]
        checks["session_persistence_after_api_and_mysql_restart"] = "passed"
        fresh = login(username, password).json()["access_token"]
        checks["password_persistence_after_restart"] = "passed"
        request("POST", "/api/auth/logout-all", expected=204, token=fresh)
        request("GET", "/api/auth/me", expected=401, token=token)
        checks["logout_all_after_restart"] = "passed"
        user_id = state["user_id"]
        args.verify_state.unlink()
    else:
        username = "auth_smoke_" + uuid4().hex[:12]
        password = secrets.token_urlsafe(24)
        new_password = secrets.token_urlsafe(24)
        request("GET", "/api/health")
        for path in ("/api/projects", "/api/evaluate/results", "/api/completeness/reports?project_id=none"):
            request("GET", path, expected=401)
        checks["unauthenticated_business_routes"] = "passed"
        bad = request("POST", "/api/auth/register", expected=422,
                      json={"username": "invalid!", "password": "short-secret"})
        assert "short-secret" not in bad.text
        checks["validation_and_password_redaction"] = "passed"
        payload = {"username": username, "password": password, "display_name": "认证部署验证"}
        created = request("POST", "/api/auth/register", expected=201, json=payload)
        assert created.headers["Cache-Control"] == "no-store"
        user_id = created.json()["id"]
        assert "password" not in created.text
        checks["registration"] = "passed"
        request("POST", "/api/auth/register", expected=409, json={**payload, "username": username.upper()})
        checks["case_insensitive_duplicate_registration"] = "passed"
        login(username, "wrong-password", expected=401)
        checks["invalid_password"] = "passed"
        first = login(username.upper(), password).json()
        assert first["expires_in"] == 86400 and first["token_type"] == "bearer"
        token = first["access_token"]
        second = login(username, password).json()["access_token"]
        assert request("GET", "/api/auth/me", token=token).json()["id"] == user_id
        checks["login_and_current_user"] = "passed"
        request("GET", "/api/auth/me", expected=401, token="x" * 43)
        checks["forged_token"] = "passed"
        project = request("POST", "/api/projects", token=token, json={"name": "认证测试临时项目"}).json()
        try:
            assert any(p["id"] == project["id"] for p in request("GET", "/api/projects", token=token).json())
            checks["authenticated_project_write_read"] = "passed"
        finally:
            request("DELETE", f"/api/projects/{project['id']}", token=token)
        request("POST", "/api/auth/logout", expected=204, token=token)
        request("GET", "/api/auth/me", expected=401, token=token)
        request("GET", "/api/auth/me", token=second)
        checks["single_session_logout"] = "passed"
        request("PUT", "/api/auth/password", expected=400, token=second,
                json={"current_password": "wrong-password", "new_password": new_password})
        request("PUT", "/api/auth/password", expected=204, token=second,
                json={"current_password": password, "new_password": new_password})
        request("GET", "/api/auth/me", expected=401, token=second)
        login(username, password, expected=401)
        fresh = login(username, new_password).json()["access_token"]
        checks["password_change_and_revocation"] = "passed"
        request("POST", "/api/auth/logout-all", expected=204, token=fresh)
        request("GET", "/api/auth/me", expected=401, token=fresh)
        checks["logout_all"] = "passed"
        token = login(username, new_password).json()["access_token"]
        unknown = "auth_limit_" + uuid4().hex[:12]
        for _ in range(10):
            login(unknown, password, expected=401)
        limited = login(unknown, password, expected=429)
        assert int(limited.headers["Retry-After"]) > 0
        checks["persistent_account_rate_limit"] = "passed"
        if args.keep:
            descriptor = os.open(args.state_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"base_url": base, "username": username, "user_id": user_id,
                           "password": new_password, "token": token}, stream)
        else:
            request("POST", "/api/auth/logout", expected=204, token=token)

    record = {"base_url": base, "verified_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
              "test_username": username, "test_user_id": user_id, "checks": checks}
    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
