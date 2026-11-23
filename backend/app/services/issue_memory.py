"""Cross-revision issue tracking. This module never changes checker verdicts.

Matching is restricted to a project/report/module and one-to-one. Absence of a
match is not evidence of remediation. Closure needs an explicit successful rule
or, for literal sensitive terms, a readable complete document without the term.
Snapshots are immutable per check result so parallel/branched reviews and deleted
history do not overwrite another revision's state.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from sqlalchemy.orm import Session

from app.db.models import CheckResult, CheckRun, ReviewMemoryRevision, ReviewRunLink
from app.services import issue_memory_retrieval as retrieval
from app.services.issue_memory_evidence import EvidenceIndex, evidence_text, read_source, verification

MEMORY_VERSION = "issue-memory-v1"
LABELS = {"new": "首次发现", "persistent": "仍然存在", "changed": "内容有变化",
          "resolved": "已解决", "unconfirmed": "待确认", "reopened": "再次出现"}
_history: ContextVar[list[dict] | None] = ContextVar("issue_review_history", default=None)
_selection: ContextVar[list[str] | None] = ContextVar("issue_review_selection", default=None)
_project_context: ContextVar[dict | None] = ContextVar("issue_project_context", default=None)


def _load_project_context(db: Session, project_id: str) -> dict:
    """Project annotations are bounded reference data; never checker instructions."""
    try:
        from app.services.memory_workspace import project_context, project_feedback
        with db.begin_nested():
            data = dict(project_context(db, project_id) or {})
            data["feedback"] = project_feedback(db, project_id)
        return data
    except ImportError:
        return {}
    except Exception:
        # Optional annotations must not abort the existing rule pipeline.
        return {"facts": [], "aliases": [], "feedback": [], "load_status": "unavailable"}


def _reference_context(context: dict, issue_id: str | None = None) -> dict:
    values: dict[str, Any] = {"reference_only": True, "facts": [], "feedback": []}
    budget = 2800
    for fact in context.get("facts", [])[:12]:
        if not isinstance(fact, dict):
            continue
        item = {key: str(fact.get(key) or "")[:limit] for key, limit in (("key", 80), ("value", 400), ("source", 160))}
        if len(_json(item)) <= budget:
            values["facts"].append(item)
            budget -= len(_json(item))
    if issue_id:
        for note in context.get("feedback", []):
            if not isinstance(note, dict) or str(note.get("issue_id") or "") != issue_id:
                continue
            item = {key: str(note.get(key) or "")[:limit] for key, limit in
                    (("action", 64), ("reason", 300), ("evidence", 300), ("source", 100))}
            if len(_json(item)) <= budget and len(values["feedback"]) < 6:
                values["feedback"].append(item)
                budget -= len(_json(item))
    return values


def _baseline_review_times(db: Session, project_id: str, baseline_id: str | None, snapshot: dict) -> dict:
    """Overlay completed sidecar AI reviews on a detached baseline, never on stored ledgers."""
    if not baseline_id:
        return snapshot
    try:
        from app.services.memory_workspace import project_review_times
        with db.begin_nested():
            times = project_review_times(db, project_id, baseline_id)
    except (ImportError, AttributeError):
        return snapshot
    except Exception:
        return snapshot
    if not times:
        return snapshot
    issues = []
    for issue in snapshot.get("issues") or []:
        copied = dict(issue)
        reviewed_at = times.get(issue.get("issue_id"))
        if reviewed_at and str(reviewed_at) > str(issue.get("last_reviewed_at") or ""):
            copied["last_reviewed_at"] = reviewed_at
        issues.append(copied)
    return {**snapshot, "issues": issues}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _load(value: str | None) -> dict:
    try:
        data = json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def norm(value: Any) -> str:
    # Preserve digits, negation and qualifiers; they can change the verdict.
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", unicodedata.normalize("NFKC", str(value or "")).lower())


def report_key(filename: str) -> str:
    # Do not remove years, phases or revision suffixes automatically.
    return norm(Path(filename.replace("\\", "/")).stem)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def link_run(db: Session, run: CheckRun, baseline_id: str | None = None) -> ReviewRunLink:
    existing = db.get(ReviewRunLink, run.id)
    if existing:
        return existing
    baseline = None
    mode = "new" if baseline_id == "new" else "explicit" if baseline_id else "auto"
    if mode == "explicit":
        baseline = db.get(CheckRun, baseline_id)
        if baseline is None or baseline.project_id != run.project_id or baseline.id == run.id:
            raise ValueError("问题记忆基准不存在或不属于当前项目")
        if baseline.created_at and run.created_at and baseline.created_at >= run.created_at:
            raise ValueError("问题记忆基准必须是此前的检测版本")
    elif mode == "auto":
        target = report_key(run.source_filename or run.report_name)
        candidates = db.query(CheckRun).filter(
            CheckRun.project_id == run.project_id, CheckRun.id != run.id,
            CheckRun.created_at < run.created_at,
            CheckRun.check_results.any(CheckResult.check_subtype == "aggregate"),
        ).order_by(CheckRun.created_at.desc(), CheckRun.id.desc()).all()
        baseline = next((item for item in candidates
                         if report_key(item.source_filename or item.report_name) == target), None)
    previous_link = db.get(ReviewRunLink, baseline.id) if baseline else None
    track = previous_link.track_key if previous_link else _digest(
        f"{run.project_id}:{baseline.id if baseline else run.id}"
    )
    link = ReviewRunLink(check_run_id=run.id, baseline_run_id=baseline.id if baseline else None,
                         mode=mode, track_key=track)
    db.add(link)
    db.flush()
    return link


def finding_refs(result: dict) -> list[dict]:
    """All surface copies, including subrules and uncapped adapter findings."""
    refs = [item for item in result.get("memory_findings", []) if isinstance(item, dict)]
    refs.extend(item for item in result.get("findings", []) if isinstance(item, dict))
    for rule in result.get("rule_results", []):
        if isinstance(rule, dict):
            refs.extend(item for item in rule.get("findings", []) if isinstance(item, dict))
    return refs


def _text(finding: dict, *keys: str) -> str:
    return next((str(finding[key]) for key in keys if finding.get(key)), "")


def descriptor(finding: dict) -> dict:
    if isinstance(finding.get("memory_descriptor"), dict):
        return finding["memory_descriptor"]
    location = finding.get("source_location") or {}
    if not isinstance(location, dict):
        location = {}
    evidence = finding.get("evidence") or finding.get("context") or finding.get("evidence_examples") or finding.get("context_examples") or finding.get("evidence_summary") or ""
    if isinstance(evidence, (dict, list)):
        evidence = _json(evidence)
    return {
        "rule": norm(_text(finding, "rule_id", "rule_name", "rule_basis", "matched_rule")),
        "kind": norm(_text(finding, "memory_kind", "issue_type", "raw_issue_type", "matched_rule", "display_title", "title")),
        "entity": norm(_text(finding, "memory_entity", "hit_text", "raw_item", "feature", "item", "resource_name", "item_name")),
        "section": norm(_text(finding, "source_section", "section") or location.get("section")),
        "evidence": norm(evidence),
        "comparison": str(finding.get("comparison_type") or ""),
    }


def fingerprint(finding: dict) -> str:
    return _digest(_json(descriptor(finding)))


def unique_findings(result: dict) -> list[dict]:
    # Same findings are often repeated at top level and in rule_results.
    seen: set[str] = set()
    values = []
    for finding in finding_refs(result):
        key = fingerprint(finding)
        if key not in seen:
            values.append(finding)
            seen.add(key)
    return values


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a[:2400], b[:2400], autojunk=False).ratio()


def _raw_guard(finding: dict, aliases: dict | None = None) -> tuple:
    # descriptor normalization intentionally removes punctuation, so protect decimals here.
    data = finding.get("evidence") or finding.get("context") or finding.get("evidence_examples") or finding.get("context_examples") or finding.get("evidence_summary") or ""
    evidence = _json(data) if isinstance(data, (dict, list)) else str(data)
    entity = _text(finding, "memory_entity", "hit_text", "raw_item", "feature", "item", "resource_name", "item_name")
    alias_map = aliases or {}
    names = tuple(sorted(set(re.findall(r"[a-z]+\d*", retrieval.canonical(evidence, alias_map)))))
    quoted = tuple(sorted(set(retrieval.canonical(value, alias_map)
                              for value in re.findall(r"[“「《]([^”」》]{2,160})[”」》]", evidence))))
    return retrieval.critical_signature(evidence), retrieval.critical_signature(entity), names, quoted


def _raw_identity_conflict(current: dict, previous: dict, aliases: dict | None = None) -> str | None:
    return "changed_number_negation_or_phase" if _raw_guard(current, aliases) != _raw_guard(previous, aliases) else None


def match_score(current: dict, previous: dict, *, project_context: dict | None = None) -> tuple[float, str]:
    conflict = _raw_identity_conflict(current, previous, retrieval.safe_aliases(project_context))
    if conflict:
        return 0.0, conflict
    return _descriptor_score(descriptor(current), descriptor(previous), project_context=project_context)


def _descriptor_score(a: dict, b: dict, *, project_context: dict | None = None, aliases: dict | None = None) -> tuple[float, str]:
    if not a["rule"] or a["rule"] != b["rule"] or a["comparison"] != b["comparison"]:
        return 0.0, "different_rule"
    if a == b:
        return 1.0, "exact_fingerprint"
    aliases = retrieval.safe_aliases(project_context) if aliases is None else aliases
    conflict = retrieval.identity_conflict(a, b, aliases)
    if conflict:
        return 0.0, conflict
    original_a, original_b = a, b
    a, b = retrieval.canonical_descriptor(a, aliases), retrieval.canonical_descriptor(b, aliases)
    entity = _similar(a["entity"], b["entity"])
    if a["entity"] and b["entity"] and entity < .92:
        return 0.0, "different_entity"
    evidence = _similar(a["evidence"], b["evidence"])
    kind = _similar(a["kind"], b["kind"])
    section = _similar(a["section"], b["section"])
    if a["entity"] and b["entity"]:
        if a["entity"] == b["entity"] and (kind >= .7 or evidence >= .75) and (evidence >= .5 or retrieval.token_overlap(a["evidence"], b["evidence"]) >= .3):
            return .88 + .08 * evidence, ("project_alias_lexical" if original_a != a or original_b != b else "rule_entity_evidence")
        if entity < .92 or evidence < .85:
            return 0.0, "different_entity"
    # Rule/heading/paragraph number alone is never an identity.
    if evidence >= .9 and kind >= .8:
        return .80 + .12 * evidence + .04 * section, "rule_text_similarity"
    lexical = retrieval.token_overlap(a["evidence"], b["evidence"])
    if lexical >= .78 and kind >= .85 and min(len(a["evidence"]), len(b["evidence"])) >= 12:
        return .88 + .06 * lexical, "project_alias_lexical" if original_a != a or original_b != b else "rule_lexical_rewrite"
    return 0.0, "insufficient_evidence"


def match_findings(findings: list[dict], issues: list[dict], *, project_context: dict | None = None) -> dict[int, tuple[int, float, str]]:
    assigned: dict[int, tuple[int, float, str]] = {}
    used: set[int] = set()
    current_descriptors = [descriptor(item) for item in findings]
    old_descriptors = [descriptor(item.get("finding") or {}) for item in issues]
    aliases = retrieval.safe_aliases(project_context)
    current_guards = [_raw_guard(item, aliases) for item in findings]
    old_guards = [_raw_guard(item.get("finding") or {}, aliases) for item in issues]
    index = retrieval.LexicalIndex([retrieval.canonical_descriptor(item, aliases) for item in old_descriptors])
    by_fingerprint: dict[str, list[int]] = {}
    for j, item in enumerate(old_descriptors):
        by_fingerprint.setdefault(_digest(_json(item)), []).append(j)
    for i, item in enumerate(current_descriptors):
        exact = by_fingerprint.get(_digest(_json(item))) or []
        if item["rule"] and exact:
            eligible = next((j for j in exact if j not in used and current_guards[i] == old_guards[j]), None)
            if eligible is None:
                continue
            j = eligible
            exact.remove(j)
            assigned[i] = (j, 1.0, "exact_fingerprint")
            used.add(j)
    candidates: list[tuple[float, int, int, str]] = []
    for i, item in enumerate(current_descriptors):
        if i in assigned:
            continue
        pool = index.candidates(retrieval.canonical_descriptor(item, aliases), used)
        scores = sorted(((_descriptor_score(item, old_descriptors[j], aliases=aliases), j)
                         for j in pool if j not in used and current_guards[i] == old_guards[j]), reverse=True)
        if not scores or scores[0][0][0] < .88:
            continue
        # Ambiguous fuzzy matches remain separate; exact duplicates are safe.
        if len(scores) > 1 and scores[0][0][0] < 1 and scores[0][0][0] - scores[1][0][0] < .05:
            continue
        (score, method), j = scores[0]
        candidates.append((score, i, j, method))
    competing: dict[int, list[float]] = {}
    for score, _, j, _ in candidates:
        competing.setdefault(j, []).append(score)
    ambiguous_old = {j for j, scores in competing.items() if len(scores) > 1
                     and sorted(scores, reverse=True)[0] - sorted(scores, reverse=True)[1] < .05}
    for score, i, j, method in sorted(candidates, key=lambda x: (-x[0], x[1], x[2])):
        if i not in assigned and j not in used and j not in ambiguous_old:
            assigned[i] = (j, score, method)
            used.add(j)
    return assigned


def baseline_for(db: Session, row: CheckResult, filename: str) -> tuple[dict, str | None, str]:
    link = None
    run = db.get(CheckRun, row.check_run_id) if row.check_run_id else None
    if run:
        link = link_run(db, run)
        if link.mode == "new":
            return {}, None, link.track_key
        ancestor = link.baseline_run_id
        visited: set[str] = set()
        while ancestor and ancestor not in visited:
            visited.add(ancestor)
            old = db.query(CheckResult).filter(
                CheckResult.project_id == row.project_id, CheckResult.check_run_id == ancestor,
                CheckResult.module == row.module, CheckResult.check_subtype == "aggregate",
            ).order_by(CheckResult.created_at.desc(), CheckResult.id.desc()).first()
            if old:
                return _snapshot_of(old), old.id, link.track_key
            ancestor_link = db.get(ReviewRunLink, ancestor)
            ancestor = ancestor_link.baseline_run_id if ancestor_link else None
        # A new stream must not silently attach to another same-named stream.
        if link.baseline_run_id:
            return {}, None, link.track_key
    candidates = db.query(CheckResult).filter(
        CheckResult.project_id == row.project_id, CheckResult.module == row.module,
        CheckResult.check_subtype == "aggregate", CheckResult.id != row.id,
        CheckResult.created_at < row.created_at,
    ).order_by(CheckResult.created_at.desc(), CheckResult.id.desc()).all()
    for old in candidates:
        payload = _load(old.reference_data)
        if report_key(str(payload.get("report_name") or "")) != report_key(filename):
            continue
        snapshot = _snapshot_of(old)
        return snapshot, old.id, link.track_key if link else snapshot.get("track_key") or _digest(f"{row.project_id}:{report_key(filename)}")
    return {}, None, link.track_key if link else _digest(f"{row.project_id}:{report_key(filename)}")


def _snapshot_of(row: CheckResult) -> dict:
    if row.memory_revision:
        return _load(row.memory_revision.snapshot_json)
    result = _load(row.reference_data).get("result") or {}
    return {"issues": [{"issue_id": str(uuid5(NAMESPACE_URL, row.id + fingerprint(item))), "status": "persistent", "finding": item,
                        "first_result_id": row.id, "seen_count": 1}
                       for item in unique_findings(result)], "scope": result.get("memory_scope") or {},
            "legacy": True}


@contextmanager
def review_history(db: Session, project_id: str, module: str, filename: str, check_run_id: str | None = None,
                   selected_rule_ids: list[str] | None = None, *, project_context: dict | None = None):
    # Synthetic anchor is read-only; the real result is stored after detection.
    from datetime import datetime
    anchor = CheckResult(id="pending", project_id=project_id, module=module,
                         check_run_id=check_run_id, created_at=datetime.utcnow())
    snapshot, baseline_id, _ = baseline_for(db, anchor, filename)
    snapshot = _baseline_review_times(db, project_id, baseline_id, snapshot)
    token = _history.set(snapshot.get("issues") or [])
    selection_token = _selection.set(selected_rule_ids or [])
    context_token = _project_context.set(project_context if project_context is not None else _load_project_context(db, project_id))
    try:
        yield
    finally:
        _history.reset(token)
        _selection.reset(selection_token)
        _project_context.reset(context_token)


def prioritize_findings(module: str, findings: list[dict]) -> None:
    history = _history.get() or []
    context = _project_context.get() or {}
    matches = match_findings(findings, history, project_context=context)
    for i, finding in enumerate(findings):
        finding.setdefault("memory_descriptor", descriptor(finding))
        if i in matches:
            old = history[matches[i][0]]
            finding["history_priority"] = 1 if old.get("status") == "resolved" else 0
            finding["history_review_order"] = (old.get("last_reviewed_at") or "") if matches[i][1] == 1 else ""
            finding["previous_issue"] = {
                "issue_id": old.get("issue_id"), "status": old.get("status"),
                "review_opinion": _text(old.get("finding") or {}, "review_opinion", "reason"),
                "evidence": _text(old.get("finding") or {}, "evidence", "evidence_summary"),
            }
        else:
            finding["history_priority"] = 2
        reference = _reference_context(context, str(history[matches[i][0]].get("issue_id") or "") if i in matches else None)
        if reference["facts"] or reference["feedback"]:
            finding["project_memory_context"] = reference


def _read_text(content: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".docx":
            with ZipFile(io.BytesIO(content)) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            texts = [node.text or "" for node in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
            return "\n".join(texts)
        if suffix in {".txt", ".csv", ".md"}:
            return content.decode("utf-8-sig")
        if suffix in {".xlsx", ".xlsm"}:
            from openpyxl import load_workbook
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            try:
                return "\n".join(" ".join(str(cell) for cell in row if cell is not None)
                                 for sheet in workbook for row in sheet.iter_rows(values_only=True))
            finally:
                workbook.close()
    except (ValueError, KeyError, OSError, UnicodeError, ElementTree.ParseError, BadZipFile):
        pass
    return ""


def _passed_rules(result: dict) -> dict[str, dict]:
    raw = result.get("raw_result") if isinstance(result.get("raw_result"), dict) else {}
    values = [*(result.get("rule_results") or []), *(raw.get("results") or []),
              *(result.get("memory_rule_outcomes") or [])]
    return {norm(rule.get("rule_id") or rule.get("rule_name")): rule for rule in values
            if isinstance(rule, dict) and rule.get("passed") is True
            and not rule.get("skipped")
            and not rule.get("findings") and not rule.get("issues")
            and str(rule.get("status") or "").lower() not in {"failed", "skipped", "timeout", "error", "跳过", "未完成"}}


@lru_cache(maxsize=32)
def checker_signature(module: str) -> str:
    folder = {"basis": "shanghai_review", "security": "shanghai_review",
              "data_reasonableness": "data_rules", "price_reference": "price"}.get(module, module)
    root = Path(__file__).resolve().parents[1] / "modules" / folder
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*")
                       if path.is_file() and path.suffix.lower() in {".py", ".json", ".yaml", ".yml", ".csv"}):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def make_scope(module: str, result: dict) -> dict:
    # Source code signatures invalidate closure when checker definitions change.
    rules = result.get("rules_used") or []
    return {"module": module, "checker_signature": checker_signature(module),
            "selected_rule_ids": sorted(_selection.get() or []),
            "rules": sorted({_json(rule) for rule in rules if isinstance(rule, dict)}),
            "comparison_scope": result.get("comparison_scope"),
            "decision_source": (result.get("summary") or {}).get("rule_source")
                if module in {"security", "duplicate"} else None}


def _can_resolve(old: dict, result: dict, module: str, text: str, scope: dict, old_scope: dict,
                 current_rules: set[str], passed_rules: dict, normalized_text: str,
                 source_hash: str) -> tuple[bool, str]:
    finding = old.get("finding") or {}
    status = str(result.get("status") or "").lower()
    if any(token in status for token in ("failed", "interrupted", "error", "partial", "timeout", "失败", "跳过", "未完成", "中断", "部分完成")):
        return False, "本次检测未完整完成，无法确认历史问题是否解决。"
    if result.get("notices") or result.get("memory_scope", {}).get("limitation") or (result.get("summary") or {}).get("parse_warnings"):
        return False, "本次存在检测范围或解析限制，保留历史问题待确认。"
    if not old_scope or scope != old_scope:
        return False, "检测规则或选择范围发生变化，不能将未检出视为已解决。"
    if old.get("source_hash") == source_hash:
        return False, "文件与该问题上次检出时完全相同，不能仅因本次结论变化认定已修改解决。"
    if not text.strip():
        return False, "未能完整读取本次文档正文，保留历史问题待确认。"
    rule = descriptor(finding)["rule"]
    passed = passed_rules.get(rule)
    if passed and rule not in current_rules:
        return True, "该问题所属规则本次已完成检查并明确通过。"
    term = _text(finding, "hit_text")
    if module == "sensitive_word" and term and len(normalized_text) > 100 and norm(term) not in normalized_text:
        return True, "已重新执行相同范围检查，可读正文中不再包含上次命中的词语。"
    return False, "本次未检出该项，但尚无充分证据确认已解决，需继续核验。"


def record_memory(db: Session, row: CheckResult, content: bytes, filename: str, *, project_context: dict | None = None) -> dict:
    existing = db.get(ReviewMemoryRevision, row.id)
    if existing:
        return _load(existing.snapshot_json)
    payload = _load(row.reference_data)
    result = payload.get("result") or {}
    previous, baseline_id, track = baseline_for(db, row, filename)
    previous = _baseline_review_times(db, row.project_id, baseline_id, previous)
    old_issues = previous.get("issues") or []
    findings = unique_findings(result)
    context = project_context if project_context is not None else _project_context.get()
    if context is None:
        context = _load_project_context(db, row.project_id)
    matches = match_findings(findings, old_issues, project_context=context)
    matched_old = {item[0] for item in matches.values()}
    scope = result.get("memory_scope") or {}
    digest = hashlib.sha256(content).hexdigest()
    source = read_source(content, filename)
    evidence_index = EvidenceIndex(source, context)
    text = source.text if source.complete else ""
    normalized_text = norm(text)
    current_rules = {descriptor(item)["rule"] for item in findings}
    passed_rules = _passed_rules(result)
    copies_by_key: dict[str, list[dict]] = {}
    for copy in finding_refs(result):
        copies_by_key.setdefault(fingerprint(copy), []).append(copy)
    counts = {key: 0 for key in LABELS}
    issues = []
    for index, finding in enumerate(findings):
        old = old_issues[matches[index][0]] if index in matches else None
        if old:
            state = ("reopened" if old.get("status") == "resolved" else
                     "persistent" if fingerprint(finding) == fingerprint(old.get("finding") or {}) else "changed")
            score, method = matches[index][1:]
        else:
            state, score, method = "new", 0.0, "no_reliable_match"
        issue_id = old.get("issue_id") if old else str(uuid4())
        seen_count = (old.get("seen_count", 0) + 1) if old else 1
        finding_memory = {
            "issue_id": issue_id, "status": state, "label": LABELS[state],
            "match_score": round(score, 4), "match_method": method,
            "previous_result_id": baseline_id if old else None,
            "previous_finding": old.get("finding") if old else None,
            "seen_count": seen_count,
        }
        check = verification(old, finding, state=state, reason="本次规则检查检出对应问题。",
                             index=evidence_index, previous_result_id=baseline_id if old else None,
                             result_id=row.id, source_hash=digest, match_method=method)
        finding_memory.update(change_summary=check["summary"], previous_evidence=check["previous_evidence"],
                              current_evidence=check["current_evidence"], verification=check)
        for copy in copies_by_key[fingerprint(finding)]:
            copy["issue_memory"] = finding_memory
        clean_finding = {k: v for k, v in finding.items() if k not in {"issue_memory", "previous_issue", "history_priority", "history_review_order", "project_memory_context"}}
        issue = {"issue_id": issue_id, "status": state, "finding": clean_finding,
                 "scope": scope,
                 "source_hash": digest,
                 "first_result_id": old.get("first_result_id") if old else row.id,
                 "seen_count": seen_count,
                 "match_score": round(score, 4), "match_method": method,
                 "last_seen_result_id": row.id,
                 "last_reviewed_at": row.created_at.isoformat() if finding.get("llm_refined")
                     else (old.get("last_reviewed_at") if old and score == 1 else None),
                 "verification": check, "review_delta": check}
        issues.append(issue)
        counts[state] += 1
    for index, old in enumerate(old_issues):
        if index in matched_old:
            continue
        if old.get("status") == "resolved":
            check = verification(old, None, state="historically_resolved",
                                 reason="此前版本已确认解决，本次未再次检出。", index=evidence_index,
                                 previous_result_id=baseline_id, result_id=row.id, source_hash=digest,
                                 match_method="historical_resolution_retained")
            check["summary"] = "该问题此前已确认解决，本次保留历史解决状态；原文候选仅供对照，不代表本次重新确认。"
            check["remaining_requirements"] = []
            issues.append({**old, "verification": check, "review_delta": check})
            continue
        resolved, reason = _can_resolve(old, result, row.module, text, scope,
                                        old.get("scope") or previous.get("scope") or {},
                                        current_rules, passed_rules, normalized_text, digest)
        state = "resolved" if resolved else "unconfirmed"
        check = verification(old, None, state=state, reason=reason, index=evidence_index,
                             previous_result_id=baseline_id, result_id=row.id, source_hash=digest,
                             match_method=("literal_term_absent" if row.module == "sensitive_word" and "词语" in reason
                                           else "explicit_rule_pass") if resolved else "source_candidate_only")
        issues.append({**old, "status": state, "reason": reason,
                       "scope": old.get("scope") or previous.get("scope") or {},
                       "verification": check, "review_delta": check})
        counts[state] += 1
    summary = {
        "version": MEMORY_VERSION, "baseline_result_id": baseline_id,
        "has_baseline": bool(baseline_id), "same_file": bool(previous.get("source_hash") == digest),
        "baseline_open_count": sum(old.get("status") != "resolved" for old in old_issues),
        **{f"{key}_count": count for key, count in counts.items()},
        "tracked_open_count": sum(item["status"] != "resolved" for item in issues),
        "message": "已关联上次检测，优先跟踪历史问题。" if baseline_id else "首次建立该报告的问题记忆。",
    }
    progress = {"baseline_open_count": summary["baseline_open_count"],
                "resolved_this_revision": counts["resolved"], "remaining_open_count": summary["tracked_open_count"],
                "new_count": counts["new"], "unconfirmed_count": counts["unconfirmed"],
                "source_read_complete": source.complete, "source_limitation": source.limitation,
                "message": "修订进展按本次规则和证据核验统计；新增问题仍会正常报告。"}
    project_summary = {"reference_only": True, "fact_count": len(context.get("facts") or []),
                       "alias_count": len(context.get("aliases") or []),
                       "feedback_count": len(context.get("feedback") or [])}
    snapshot = {"summary": summary, "issues": issues, "scope": scope, "progress": progress,
                "project_summary": project_summary, "track_key": track, "source_hash": digest}
    result["issue_memory"] = {**summary, "progress": progress,
        "project_summary": project_summary,
        "revision_reviews": [{"issue_id": item["issue_id"], "label": LABELS[item["status"]],
            "status": item["status"], "previous_result_id": baseline_id,
            "previous_evidence": item.get("verification", {}).get("previous_evidence", ""),
            "current_evidence": item.get("verification", {}).get("current_evidence", ""),
            "summary": item.get("verification", {}).get("summary", ""),
            "reason": item.get("reason") or item.get("verification", {}).get("reason", ""),
            "source_locations": item.get("verification", {}).get("source_locations", []),
            "remaining_requirements": item.get("verification", {}).get("remaining_requirements", []),
            "match_method": item.get("verification", {}).get("method", "")}
            for item in issues if item["status"] != "new"], "historical_items": [
        {**item, "label": LABELS[item["status"]], "previous_result_id": baseline_id}
        for item in issues if item["status"] in {"resolved", "unconfirmed"}
    ]}
    payload["result"] = result
    row.reference_data = _json(payload)
    db.add(ReviewMemoryRevision(result_id=row.id, project_id=row.project_id, module=row.module,
                               track_key=track, baseline_result_id=baseline_id, source_hash=digest,
                               snapshot_json=_json(snapshot)))
    db.flush()
    return snapshot
