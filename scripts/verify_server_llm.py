"""Run inside the deployed backend container; emit a report without credentials.

From the repository root:
  ssh worker8 'docker exec -i aiassist-backend python -' < scripts/verify_server_llm.py

Creates a synthetic account and project, verifies real provider calls and an
asynchronous resource review, and deletes only the data it created.
"""
from datetime import datetime
import json
import os
import secrets
import sys
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import delete, func, select

from app.core.llm_client import LLMClient
from app.db.models import AuthSession, CheckResult, CheckRun, Project, User
from app.db.session import SessionLocal


def main():
    client = requests.Session()
    client.trust_env = False
    base = "http://127.0.0.1:8000"
    username = "llm_smoke_" + uuid4().hex[:12]
    password = secrets.token_urlsafe(24)
    user_id = project_id = token = task_id = None
    report = {
        "verified_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "checks": {},
    }
    checks = report["checks"]

    def request(method, path, expected=200, **kwargs):
        response = client.request(method, base + path, timeout=45, **kwargs)
        if response.status_code != expected:
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
        return response

    try:
        assert request("GET", "/api/health").json() == {"status": "ok"}
        request("GET", "/api/evaluate/llm/status", expected=401)
        checks["health_and_auth_protection"] = "passed"
        user = request("POST", "/api/auth/register", expected=201, json={
            "username": username, "password": password, "display_name": "模型验证临时账号",
        }).json()
        user_id = user["id"]
        token = request("POST", "/api/auth/login", json={
            "username": username, "password": password,
        }).json()["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        assert request("GET", "/api/auth/me").json()["id"] == user_id
        checks["registration_and_login"] = "passed"

        status = request("GET", "/api/evaluate/llm/status").json()
        report["configuration"] = status
        assert status["configured"] and status["api_key_present"]
        checks["runtime_configuration"] = "passed"

        ping = request("POST", "/api/evaluate/llm/ping", json={
            "text": "服务器验证：请确认理解了这句话，并返回简短 JSON。",
        }).json()
        report["ping"] = {k: ping[k] for k in (
            "success", "model", "transport", "content_source", "error_type",
        ) if k in ping}
        assert ping.get("success") is True, "HTTP LLM ping did not succeed"
        assert ping.get("content"), "HTTP LLM ping returned no content"
        checks["authenticated_http_model_ping"] = "passed"

        llm = LLMClient(timeout=45, max_retries=0)
        assert llm.config.base_url == status["base_url"]
        assert llm.config.model == status["model"]
        response = llm.chat_json([
            {"role": "system", "content": "只输出合法 JSON。"},
            {"role": "user", "content": '连通性验证：请准确输出 {"ok": true}。'},
        ], max_tokens=128)
        assert response.get("ok") is True
        checks["common_client_real_json_call"] = "passed"

        project_id = request("POST", "/api/projects", json={
            "name": "模型真实审查验证-" + uuid4().hex[:8], "department": "自动化验证",
        }).json()["id"]
        content = (
            "来源,资源名称,数量,单位\n"
            "计算资源清单,云服务器,2,台\n"
            "PaaS服务清单,服务器操作系统,1,套\n"
        ).encode("utf-8-sig")
        task_id = request("POST", "/api/evaluate/tasks", data={
            "project_id": project_id, "modules": "resource",
            "rule_source": "local", "use_llm": "true",
        }, files={"file": ("模型审查验证.csv", content, "text/csv")}).json()["task_id"]
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            state = request("GET", "/api/evaluate/tasks/" + task_id).json()
            if state["status"] in {"completed", "partial", "failed"}:
                break
            time.sleep(0.5)
        assert state["status"] == "completed", "Model review task did not complete successfully"
        assert state["result_ids"]
        detail = request("GET", "/api/evaluate/results/" + state["result_ids"][0]).json()
        result = detail.get("result") or detail
        summary = result.get("summary") or {}
        report["review"] = {
            "task_status": state["status"],
            "module": "resource",
            "llm_enabled": summary.get("llm_enabled"),
            "llm_status": summary.get("llm_status"),
            "llm_reviewed_count": summary.get("llm_reviewed_count"),
            "llm_error_count": summary.get("llm_error_count"),
            "result_count": len(state["result_ids"]),
        }
        assert summary.get("llm_enabled") is True
        assert summary.get("llm_status") == "completed"
        assert summary.get("llm_reviewed_count", 0) > 0
        assert summary.get("llm_error_count") == 0
        checks["real_async_review_with_llm"] = "passed"
        checks["review_result_persistence"] = "passed"
    except Exception as exc:
        # Provider exceptions can contain request or response details. Keep the
        # report limited to a redacted type and our own assertion messages.
        report["error_type"] = type(exc).__name__
        if isinstance(exc, AssertionError):
            message = str(exc)
            for value in (os.getenv("DEEPSEEK_API_KEY"), token, password):
                if value:
                    message = message.replace(value, "[REDACTED]")
            report["error"] = message
    finally:
        if project_id:
            try:
                request("DELETE", "/api/projects/" + project_id)
                checks["test_project_cleanup"] = "passed"
            except Exception:
                checks["test_project_cleanup"] = "failed"
        if token:
            try:
                request("POST", "/api/auth/logout", expected=204)
                request("GET", "/api/auth/me", expected=401)
                checks["session_revocation"] = "passed"
            except Exception:
                checks["session_revocation"] = "failed"
        if user_id:
            with SessionLocal.begin() as db:
                db.execute(delete(User).where(User.id == user_id, User.username == username))
            with SessionLocal() as db:
                users = db.scalar(select(func.count()).select_from(User).where(User.id == user_id))
                sessions = db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id == user_id))
                checks["test_account_cleanup"] = "passed" if users == sessions == 0 else "failed"
                if project_id:
                    remaining = sum(db.scalar(select(func.count()).select_from(model).where(
                        (model.id if model is Project else model.project_id) == project_id,
                    )) for model in (Project, CheckRun, CheckResult))
                    checks["persisted_test_data_cleanup"] = "passed" if remaining == 0 else "failed"
        client.close()

    report["success"] = "error_type" not in report and all(value == "passed" for value in checks.values())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
