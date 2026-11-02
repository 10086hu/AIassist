"""Verify a deployed history service with three existing account sessions.

Tokens are read only from files containing a plain bearer token. The script never
creates accounts, changes permissions, prints tokens, or writes tokens to state.
Without --package-file it creates a small synthetic review with a text attachment.

Use --record state.json --keep-state before restarting the service, then run with
--verify-state state.json and the same token files to check persistence and clean
up. The first pass leaves the delivery received; the second revokes it and removes
the uploaded project. Delivery receipts have no DELETE API and are reported for
the operator to remove, if desired, by their exact IDs.
"""
from __future__ import annotations

import argparse
import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import requests


class SmokeFailure(Exception):
    """A diagnostic made only from fixed labels, HTTP paths and status codes."""


def require(condition: bool, label: str) -> None:
    if not condition:
        raise SmokeFailure(label)


def read_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        raise SmokeFailure("token_file_unreadable") from None
    require(re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is not None, "invalid_token_file_format")
    return token


def uid(value: Any) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise SmokeFailure("invalid_identifier") from None


def sha256_value(value: Any) -> str:
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "invalid_sha256_value")
    return value


def normalized_base(value: str) -> str:
    parsed = urlsplit(value)
    require(parsed.scheme in {"http", "https"} and bool(parsed.hostname), "invalid_base_url")
    require(not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
            "base_url_must_not_contain_credentials_query_or_fragment")
    return value.rstrip("/")


def synthetic_package(marker: str) -> dict:
    project_id, run_id, result_id, source_id = (str(uuid4()) for _ in range(4))
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    content = ("历史记录服务器验证样本。建设内容应保持一致。\n" + marker).encode("utf-8")
    filename = "history-smoke.txt"
    return {
        "schema_version": 1,
        "project": {"id": project_id, "name": marker, "department": "automated-smoke",
                    "description": "Synthetic history transfer verification", "status": "completed",
                    "created_at": now, "updated_at": now},
        "check_runs": [{"id": run_id, "report_name": filename, "source_filename": filename,
                        "status": "completed", "created_at": now, "updated_at": now}],
        "results": [{"id": result_id, "check_run_id": run_id, "module": "sensitive_word",
                     "check_subtype": "aggregate", "item_id": None, "related_item_id": None,
                     "severity": "info", "score": None, "result_label": "通过", "reason": "合成测试记录",
                     "suggestion": None, "model_name": "smoke-local-rule", "created_at": now,
                     "reference_data": {"report_name": filename, "result": {
                         "summary": {"total_findings": 0}, "findings": [],
                         "source_documents": [{"id": source_id, "filename": filename, "role": "current",
                             "download_url": f"/api/evaluate/results/{result_id}/sources/{source_id}",
                             "preview_url": f"/api/evaluate/results/{result_id}/sources/{source_id}/preview"}],
                     }}}],
        "sources": [{"id": source_id, "check_result_id": result_id, "role": "current", "stage": None,
                     "filename": filename, "media_type": "text/plain", "created_at": now,
                     "content_base64": base64.b64encode(content).decode("ascii"),
                     "sha256": hashlib.sha256(content).hexdigest()}],
    }


def attachment_fingerprints(package: dict) -> list[dict]:
    fingerprints = []
    for source in package.get("sources", []):
        try:
            content = base64.b64decode(source["content_base64"], validate=True)
        except (ValueError, TypeError, KeyError):
            raise SmokeFailure("invalid_attachment_encoding") from None
        digest = hashlib.sha256(content).hexdigest()
        require(source.get("sha256") == digest, "attachment_hash_mismatch")
        fingerprints.append({"sha256": digest, "bytes": len(content)})
    return sorted(fingerprints, key=lambda item: (item["sha256"], item["bytes"]))


class HistorySmoke:
    def __init__(self, args):
        self.args = args
        self.base = normalized_base(args.base_url)
        self.sessions = {}
        for role, path in (("admin", args.admin_token_file), ("user", args.user_token_file),
                           ("other", args.other_token_file)):
            session = requests.Session()
            session.trust_env = False
            session.headers["Authorization"] = "Bearer " + read_token(path)
            self.sessions[role] = session
        anonymous = requests.Session()
        anonymous.trust_env = False
        self.sessions["anonymous"] = anonymous
        self.identities = {}
        self.state = {"schema_version": 1, "base_url": self.base, "checks": {}, "cleanup": {}}
        # Authentication must not overwrite the state that resume still needs.
        self.record_path = None if args.verify_state else args.record
        self.cleanup_authorized = not bool(args.verify_state)

    def save(self):
        if self.record_path:
            self.record_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.record_path.with_name(self.record_path.name + ".tmp")
            temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.record_path)

    def mark(self, label):
        self.state["checks"][label] = "passed"
        self.save()

    def request(self, role, method, endpoint, expected=(200,), **kwargs):
        require(endpoint.startswith("/api/") and "?" not in endpoint, "invalid_api_path")
        try:
            response = self.sessions[role].request(
                method, self.base + endpoint, timeout=(10, 60), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise SmokeFailure(f"transport_failed:{method}:{endpoint}") from None
        require(response.status_code in expected, f"http_failed:{method}:{endpoint}:{response.status_code}")
        return response

    def body(self, response):
        try:
            value = response.json()
        except ValueError:
            raise SmokeFailure("invalid_json_response") from None
        require(isinstance(value, dict), "unexpected_json_response_type")
        return value

    def package(self, response, expected_digest=None):
        digest = hashlib.sha256(response.content).hexdigest()
        require(response.headers.get("X-Package-SHA256") == digest, "package_response_hash_mismatch")
        if expected_digest:
            require(digest == expected_digest, "package_persistence_hash_mismatch")
        return self.body(response), digest

    def find(self, role, endpoint, predicate, **params):
        offset = 0
        while True:
            page = self.body(self.request(role, "GET", endpoint, params={**params, "limit": 100, "offset": offset}))
            items = page.get("items", [])
            require(isinstance(items, list), "invalid_list_response")
            for item in items:
                if predicate(item):
                    return item
            if len(items) < 100:
                return None
            offset += len(items)

    def authenticate(self):
        self.request("anonymous", "GET", "/api/health")
        for role in ("admin", "user", "other"):
            self.identities[role] = self.body(self.request(role, "GET", "/api/auth/me"))
        require(self.identities["admin"].get("role") == "admin", "administrator_role_required")
        require(all(self.identities[role].get("role") == "user" for role in ("user", "other")), "two_regular_users_required")
        ids = {uid(identity.get("id")) for identity in self.identities.values()}
        require(len(ids) == 3, "three_distinct_accounts_required")
        recipient = self.identities["other"]
        found = self.find("admin", "/api/admin/users", lambda item: item.get("id") == recipient["id"], q=recipient["username"])
        require(found is not None, "recipient_missing_from_admin_directory")
        self.request("user", "GET", "/api/admin/users", expected=(403,))
        self.request("user", "GET", "/api/projects", expected=(403,))
        self.request("user", "GET", "/api/evaluate/results", expected=(403,))
        self.request("anonymous", "GET", "/api/history/inbox", expected=(401,))
        self.mark("authentication_and_roles")

    def create(self):
        run_id = str(uuid4())
        marker = "history-smoke-" + run_id
        if self.args.package_file:
            try:
                package = json.loads(self.args.package_file.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                raise SmokeFailure("package_file_unreadable_or_invalid") from None
            require(isinstance(package, dict) and isinstance(package.get("project"), dict), "invalid_package_file")
            package["project"]["name"] = marker
        else:
            package = synthetic_package(marker)
        self.state.update({
            "run_id": run_id, "marker": marker, "client_record_id": str(uuid4()),
            "sender_user_id": uid(self.identities["user"]["id"]),
            "recipient_user_id": uid(self.identities["other"]["id"]),
            "admin_user_id": uid(self.identities["admin"]["id"]),
            "source_attachments": attachment_fingerprints(package), "phase": "uploading",
        })
        self.save()
        payload = {"client_record_id": self.state["client_record_id"], "package": package}
        upload = self.body(self.request("user", "POST", "/api/history/uploads", expected=(200, 201), json=payload))
        self.state.update({"upload_id": uid(upload["id"]), "project_id": uid(upload["project_id"]),
                           "upload_sha256": sha256_value(upload["package_sha256"])})
        self.save()
        require(upload.get("duplicate") is False, "unexpected_existing_upload")
        repeated = self.body(self.request("user", "POST", "/api/history/uploads", json=payload))
        require(repeated.get("duplicate") is True and repeated.get("id") == upload["id"], "upload_retry_not_idempotent")
        changed = copy.deepcopy(payload)
        changed["package"]["project"]["name"] = marker + "-changed"
        self.request("user", "POST", "/api/history/uploads", expected=(409,), json=changed)
        self.mark("upload_idempotency_and_conflict")

        delivery_payload = {"recipient_user_id": self.state["recipient_user_id"], "project_id": self.state["project_id"]}
        delivery = self.body(self.request("admin", "POST", "/api/admin/history/deliveries", expected=(200, 201), json=delivery_payload))
        self.state.update({"delivery_id": uid(delivery["id"]), "delivery_sha256": sha256_value(delivery["package_sha256"]), "phase": "dispatched"})
        self.save()
        require(delivery.get("status") == "pending", "delivery_not_pending")
        repeated = self.body(self.request("admin", "POST", "/api/admin/history/deliveries", json=delivery_payload))
        require(repeated.get("duplicate") is True and repeated.get("id") == delivery["id"], "delivery_retry_not_idempotent")
        self.mark("addressed_delivery")
        self.verify_records()
        delivery_id = self.state["delivery_id"]
        self.request("other", "POST", f"/api/history/inbox/{delivery_id}/ack", expected=(409,), json={"package_sha256": "0" * 64})
        for _ in range(2):
            acknowledged = self.body(self.request("other", "POST", f"/api/history/inbox/{delivery_id}/ack", json={
                "package_sha256": self.state["delivery_sha256"],
            }))
            require(acknowledged.get("status") == "received", "acknowledgement_not_received")
        self.state["phase"] = "received"
        self.mark("acknowledgement_hash_and_retry")

    def verify_records(self):
        upload_id, project_id, delivery_id = (uid(self.state[key]) for key in ("upload_id", "project_id", "delivery_id"))
        uploaded = self.body(self.request("admin", "GET", f"/api/projects/{project_id}"))
        require(uploaded.get("name") == self.state["marker"], "project_ownership_marker_mismatch")
        own = self.find("user", "/api/history/uploads", lambda item: item.get("id") == upload_id)
        require(own is not None and own.get("client_record_id") == self.state["client_record_id"]
                and own.get("project_id") == project_id, "uploaded_receipt_missing_or_mismatched")
        other = self.find("other", "/api/history/uploads", lambda item: item.get("id") == upload_id)
        require(other is None, "upload_visible_to_other_account")
        upload_package, _ = self.package(self.request("admin", "GET", f"/api/admin/history/uploads/{upload_id}/package"), self.state["upload_sha256"])
        require(attachment_fingerprints(upload_package) == self.state["source_attachments"], "uploaded_attachments_changed")
        addressed = self.find("admin", "/api/admin/history/deliveries", lambda item: item.get("id") == delivery_id,
                              recipient_user_id=self.state["recipient_user_id"])
        require(addressed is not None and addressed.get("project_id") == project_id
                and addressed.get("sender_user_id") == self.state["admin_user_id"], "delivery_ownership_mismatch")
        received = self.find("other", "/api/history/inbox", lambda item: item.get("id") == delivery_id)
        require(received is not None, "delivery_missing_from_recipient_inbox")
        if self.state.get("phase") == "received":
            require(received.get("status") == "received" and received.get("received_at"), "receipt_not_persisted")
        self.request("user", "GET", f"/api/history/inbox/{delivery_id}/package", expected=(404,))
        self.request("user", "POST", f"/api/history/inbox/{delivery_id}/ack", expected=(404,),
                     json={"package_sha256": self.state["delivery_sha256"]})
        package, _ = self.package(self.request("other", "GET", f"/api/history/inbox/{delivery_id}/package"), self.state["delivery_sha256"])
        require(attachment_fingerprints(package) == self.state["source_attachments"], "delivered_attachments_changed")
        for source in package.get("sources", []):
            result_id, source_id = uid(source["check_result_id"]), uid(source["id"])
            endpoint = f"/api/evaluate/results/{result_id}/sources/{source_id}"
            raw = self.request("admin", "GET", endpoint)
            require(hashlib.sha256(raw.content).hexdigest() == source["sha256"], "stored_attachment_bytes_changed")
            self.request("user", "GET", endpoint, expected=(403,))
        self.mark("record_attachment_and_recipient_isolation")

    def resume(self):
        try:
            state = json.loads(self.args.verify_state.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise SmokeFailure("state_file_unreadable_or_invalid") from None
        require(isinstance(state, dict) and state.get("schema_version") == 1, "invalid_state_file")
        require(state.get("base_url") == self.base, "state_belongs_to_different_server")
        require(state.get("marker") == "history-smoke-" + uid(state.get("run_id")), "invalid_state_marker")
        for key, role in (("sender_user_id", "user"), ("recipient_user_id", "other"), ("admin_user_id", "admin")):
            require(state.get(key) == self.identities[role]["id"], "state_belongs_to_different_accounts")
        require(state.get("phase") == "received", "state_is_not_a_retained_received_delivery")
        # Keep only this tool's known fields: never echo arbitrary input state.
        keys = ("schema_version", "base_url", "run_id", "marker", "client_record_id", "sender_user_id",
                "recipient_user_id", "admin_user_id", "source_attachments", "phase", "upload_id",
                "project_id", "upload_sha256", "delivery_id", "delivery_sha256")
        self.state = {key: state[key] for key in keys}
        for key in ("client_record_id", "upload_id", "project_id", "delivery_id"):
            self.state[key] = uid(self.state[key])
        for key in ("upload_sha256", "delivery_sha256"):
            self.state[key] = sha256_value(self.state[key])
        fingerprints = []
        for item in self.state["source_attachments"]:
            require(isinstance(item.get("bytes"), int) and 0 <= item["bytes"] <= 32 * 1024 * 1024,
                    "invalid_state_attachment_size")
            fingerprints.append({"sha256": sha256_value(item.get("sha256")), "bytes": item["bytes"]})
        self.state["source_attachments"] = fingerprints
        self.state.update({"checks": {"authentication_and_roles": "passed"}, "cleanup": {}})
        self.verify_records()
        self.cleanup_authorized = True
        self.record_path = self.args.record or self.args.verify_state
        self.mark("persistence_after_restart")

    def cleanup(self):
        if not self.cleanup_authorized or not self.state.get("client_record_id"):
            return
        # Recover a committed upload when the client lost the POST response.
        if not self.state.get("upload_id"):
            own = self.find("user", "/api/history/uploads", lambda item: item.get("client_record_id") == self.state["client_record_id"])
            if own:
                self.state.update({"upload_id": uid(own["id"]), "project_id": uid(own["project_id"])})
        if not self.state.get("upload_id"):
            return
        upload_id, project_id = uid(self.state["upload_id"]), uid(self.state["project_id"])
        project = self.body(self.request("admin", "GET", f"/api/projects/{project_id}"))
        require(project.get("name") == self.state["marker"], "cleanup_refused_project_marker_mismatch")
        delivery_id = self.state.get("delivery_id")
        if not delivery_id:
            addressed = self.find("admin", "/api/admin/history/deliveries",
                lambda item: item.get("project_id") == project_id and item.get("sender_user_id") == self.state["admin_user_id"],
                recipient_user_id=self.state["recipient_user_id"])
            if addressed:
                delivery_id = uid(addressed["id"])
                self.state.update({"delivery_id": delivery_id, "delivery_sha256": sha256_value(addressed["package_sha256"])})
        if delivery_id:
            delivery_id = uid(delivery_id)
            revoked = self.body(self.request("admin", "POST", f"/api/admin/history/deliveries/{delivery_id}/revoke"))
            require(revoked.get("status") == "revoked", "revoke_did_not_persist")
            self.request("other", "GET", f"/api/history/inbox/{delivery_id}/package", expected=(410,))
            self.request("other", "POST", f"/api/history/inbox/{delivery_id}/ack", expected=(410,), json={
                "package_sha256": self.state["delivery_sha256"],
            })
            self.state["cleanup"]["delivery"] = "revoked_receipt_retained"
            self.state["checks"]["revocation"] = "passed"
        self.request("admin", "DELETE", f"/api/admin/history/uploads/{upload_id}", expected=(200, 204))
        self.request("admin", "GET", f"/api/projects/{project_id}", expected=(404,))
        self.request("admin", "GET", f"/api/admin/history/uploads/{upload_id}/package", expected=(404,))
        self.state["cleanup"]["upload_and_project"] = "deleted"
        self.state["phase"] = "cleaned"
        self.mark("owned_test_project_cleanup")

    def run(self):
        passed = False
        try:
            self.authenticate()
            if self.args.verify_state:
                self.resume()
            else:
                self.create()
            passed = True
        except SmokeFailure as exc:
            self.state["error"] = str(exc)
        except Exception as exc:
            self.state["error"] = "unexpected_" + type(exc).__name__
        finally:
            if not (passed and self.args.keep_state):
                try:
                    self.cleanup()
                except SmokeFailure as exc:
                    self.state["cleanup"]["error"] = str(exc)
                    passed = False
                except Exception as exc:
                    self.state["cleanup"]["error"] = "unexpected_" + type(exc).__name__
                    passed = False
            else:
                self.state["cleanup"]["status"] = "retained_for_restart_verification"
            self.state["status"] = "passed" if passed else "failed"
            try:
                self.save()
            except OSError:
                self.state["status"] = "failed"
                self.state["error"] = "state_file_write_failed"
                passed = False
            for session in self.sessions.values():
                session.close()
            print(json.dumps(self.state, ensure_ascii=False, indent=2))
        return 0 if passed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://10.60.150.179:8000")
    parser.add_argument("--admin-token-file", type=Path, required=True)
    parser.add_argument("--user-token-file", type=Path, required=True)
    parser.add_argument("--other-token-file", type=Path, required=True)
    parser.add_argument("--package-file", type=Path, help="Optional locally exported RecordPackage JSON")
    parser.add_argument("--record", type=Path, help="Write a verification report and restart state; contains no tokens")
    parser.add_argument("--keep-state", action="store_true", help="Retain received delivery and uploaded project after success")
    parser.add_argument("--verify-state", type=Path, help="Verify a retained report after restarting the service")
    args = parser.parse_args(argv)
    if args.keep_state and not (args.record or args.verify_state):
        parser.error("--keep-state requires --record or --verify-state")
    if args.package_file and args.verify_state:
        parser.error("--package-file cannot be combined with --verify-state")
    try:
        protected_paths = {path.resolve() for path in (args.admin_token_file, args.user_token_file,
                           args.other_token_file, args.package_file) if path}
        for output in (args.record, args.verify_state):
            require(output is None or output.resolve() not in protected_paths, "output_path_conflicts_with_input_file")
        return HistorySmoke(args).run()
    except SmokeFailure as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": "unexpected_" + type(exc).__name__}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
