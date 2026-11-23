"""Versioned, self-contained review history packages for SQLite/MySQL transfer.

This module does not authorize requests or commit transactions. Callers authorize
the source/recipient, choose a trusted namespace, and commit the receipt and all
imported rows together. Client IDs never become destination primary keys.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Literal
from urllib.parse import parse_qsl, unquote_plus, urlsplit, urlunsplit
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_serializer, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (CheckResult, CheckRun, IssueFeedbackEvent, Project, ProjectMemoryProfile,
                           ReviewMemoryRevision, ReviewRunLink, SourceArtifact)
from app.services.memory_workspace import validate_context


MAX_PACKAGE_BYTES = 48 * 1024 * 1024
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCES = 500
MAX_RESULTS = 2000
MAX_RUNS = 200
MYSQL_TEXT_MAX_BYTES = 65535
_ACTIVE_STATUSES = {"running", "queued", "pending", "processing"}
_SOURCE_URL = re.compile(r"^/api/evaluate/results/([^/]+)/sources/([^/?#]+)(?:/preview)?(?:[?#].*)?$")
_COMPLETENESS_URL = re.compile(r"^/api/completeness/reports/([^/]+)/sources/(\d+)(?:[?#].*)?$")
_MEMORY_RESULT_KEYS = {"baseline_result_id", "previous_result_id", "first_result_id", "last_seen_result_id"}
_MEMORY_RUN_KEYS = {"baseline_run_id"}


class PackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @field_validator("created_at", "updated_at", check_fields=False)
    @classmethod
    def utc_dates(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    @field_validator("description", "reason", "suggestion", check_fields=False)
    @classmethod
    def mysql_text_size(cls, value: str | None) -> str | None:
        # These destination columns are TEXT, not the LONGTEXT used for the
        # complete result JSON. Reject incompatible SQLite rows before import;
        # MySQL may otherwise raise DataError or silently truncate the content.
        if value is not None and len(value.encode("utf-8")) > MYSQL_TEXT_MAX_BYTES:
            raise ValueError("字段内容超过 MySQL TEXT 的 65,535 UTF-8 字节限制")
        return value


class PackageProject(PackageModel):
    id: UUID
    name: str = Field(min_length=1, max_length=200)
    department: str | None = Field(default=None, max_length=200)
    description: str | None = None
    status: str = Field(max_length=32)
    created_at: datetime
    updated_at: datetime


class PackageRun(PackageModel):
    id: UUID
    report_name: str = Field(min_length=1, max_length=300)
    source_filename: str | None = Field(default=None, max_length=300)
    status: str = Field(max_length=32)
    created_at: datetime
    updated_at: datetime

    @field_validator("status")
    @classmethod
    def finished_run(cls, value: str) -> str:
        if value.lower() in _ACTIVE_STATUSES:
            raise ValueError("运行中的审查批次不能传输")
        return value


class PackageResult(PackageModel):
    id: UUID
    check_run_id: UUID | None = None
    module: str = Field(min_length=1, max_length=32)
    check_subtype: str = Field(min_length=1, max_length=64)
    item_id: UUID | None = None
    related_item_id: UUID | None = None
    severity: str = Field(max_length=32)
    score: float | None = None
    result_label: str = Field(max_length=64)
    reason: str
    suggestion: str | None = None
    reference_data: dict[str, JsonValue]
    model_name: str = Field(max_length=100)
    created_at: datetime


class PackageSource(PackageModel):
    id: UUID
    check_result_id: UUID
    role: str = Field(max_length=32)
    stage: str | None = Field(default=None, max_length=64)
    filename: str = Field(min_length=1, max_length=300)
    media_type: str = Field(max_length=120)
    content_base64: str = Field(max_length=4 * ((MAX_SOURCE_BYTES + 2) // 3))
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime

    def decoded_content(self) -> bytes:
        try:
            raw = base64.b64decode(self.content_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("附件内容不是有效的 Base64") from exc
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError("附件总大小不能超过 32 MiB")
        if hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError("附件 SHA-256 校验失败")
        return raw


class PackageRunLink(PackageModel):
    check_run_id: UUID
    baseline_run_id: UUID | None = None
    mode: Literal["auto", "explicit", "new"]
    track_key: str = Field(pattern=r"^[a-f0-9]{64}$")


class PackageMemoryRevision(PackageModel):
    result_id: UUID
    module: str = Field(min_length=1, max_length=32)
    track_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline_result_id: UUID | None = None
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    snapshot: dict[str, JsonValue]
    created_at: datetime

    @field_validator("snapshot")
    @classmethod
    def valid_ledger(cls, snapshot):
        if not isinstance(snapshot.get("issues"), list) or not isinstance(snapshot.get("scope"), dict):
            raise ValueError("问题记忆快照必须包含问题列表和原检测范围")
        for issue in snapshot["issues"]:
            if (not isinstance(issue, dict) or not isinstance(issue.get("finding"), dict)
                    or issue.get("status") not in {"new", "persistent", "changed", "resolved", "unconfirmed", "reopened"}):
                raise ValueError("问题记忆状态或证据格式无效")
            UUID(str(issue.get("issue_id")))
        return snapshot


class PackageMemoryGap(PackageModel):
    kind: Literal["run", "result"]
    id: UUID


class PackageMemoryProfile(PackageModel):
    facts: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=50)
    aliases: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=50)
    actor: str = Field(default="", max_length=256)
    updated_at: datetime

    @model_validator(mode="after")
    def valid_context(self):
        clean = validate_context({"facts": self.facts, "aliases": self.aliases})
        self.facts, self.aliases = clean["facts"], clean["aliases"]
        return self


class PackageFeedback(PackageModel):
    id: UUID
    result_id: UUID
    issue_id: UUID
    action: Literal["false_positive", "resolved", "not_applicable", "needs_review", "revoke"]
    reason: str = Field(min_length=1, max_length=5000)
    evidence: str = Field(default="", max_length=5000)
    actor: str = Field(default="", max_length=256)
    source: Literal["local", "imported"] = "local"
    revokes_event_id: UUID | None = None
    created_at: datetime

    @model_validator(mode="after")
    def valid_feedback(self):
        if not self.reason.strip() or (self.action == "resolved" and not self.evidence.strip()):
            raise ValueError("反馈需填写说明，已修改反馈还需修改佐证")
        if (self.action == "revoke") != (self.revokes_event_id is not None):
            raise ValueError("撤回反馈必须明确指向原反馈事件")
        return self


class PackageMemory(PackageModel):
    run_links: list[PackageRunLink] = Field(default_factory=list, max_length=MAX_RUNS)
    revisions: list[PackageMemoryRevision] = Field(default_factory=list, max_length=MAX_RESULTS)
    missing_references: list[PackageMemoryGap] = Field(default_factory=list, max_length=MAX_RESULTS + MAX_RUNS)
    profile: PackageMemoryProfile | None = None
    feedback: list[PackageFeedback] = Field(default_factory=list, max_length=10000)


class RecordPackage(PackageModel):
    schema_version: Literal[1, 2, 3] = 1
    project: PackageProject
    check_runs: list[PackageRun] = Field(default_factory=list, max_length=MAX_RUNS)
    results: list[PackageResult] = Field(min_length=1, max_length=MAX_RESULTS)
    sources: list[PackageSource] = Field(default_factory=list, max_length=MAX_SOURCES)
    memory: PackageMemory | None = None

    @model_serializer(mode="wrap")
    def serialize_version(self, handler):
        if self.schema_version < 3 and self.memory and (self.memory.profile is not None or self.memory.feedback):
            raise ValueError("人工背景与反馈不能降级传输")
        payload = handler(self)
        # Keep the v1 wire shape and digest exactly compatible with older peers.
        if self.schema_version == 1:
            payload.pop("memory", None)
        elif self.schema_version == 2 and payload.get("memory") is not None:
            # These new defaults must never change an existing v2 receipt hash.
            payload["memory"].pop("profile", None)
            payload["memory"].pop("feedback", None)
        return payload

    @model_validator(mode="after")
    def complete_graph(self) -> "RecordPackage":
        all_ids = [self.project.id, *(r.id for r in self.check_runs),
                   *(r.id for r in self.results), *(s.id for s in self.sources)]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("传输包内的 ID 必须唯一")
        run_ids = {run.id for run in self.check_runs}
        result_ids = {result.id for result in self.results}
        if any(result.check_run_id and result.check_run_id not in run_ids for result in self.results):
            raise ValueError("检测结果引用了包外的审查批次")
        if any(source.check_result_id not in result_ids for source in self.sources):
            raise ValueError("附件引用了包外的检测结果")
        if any(not any(result.check_run_id == run.id for result in self.results) for run in self.check_runs):
            raise ValueError("审查批次没有检测结果")
        if self.schema_version == 1 and self.memory is not None:
            raise ValueError("问题记忆需要版本 2 的传输协议")
        if self.schema_version in {2, 3}:
            if self.memory is None:
                raise ValueError("版本 2 的传输包缺少问题记忆数据")
            if self.schema_version == 2 and (self.memory.profile is not None or self.memory.feedback):
                raise ValueError("人工背景与反馈需要版本 3，不能降级丢弃")
            if self.schema_version == 3 and self.memory.profile is None and not self.memory.feedback:
                raise ValueError("版本 3 应包含人工背景或反馈")
            self._validate_memory(run_ids, result_ids)
        total = 0
        for source in self.sources:
            total += len(source.decoded_content())
            if total > MAX_SOURCE_BYTES:
                raise ValueError("附件总大小不能超过 32 MiB")
        for result in self.results:
            sources = {str(source.id): source for source in self.sources if source.check_result_id == result.id}
            inventory = result.reference_data.get("inventory")
            _validate_references(result.reference_data, str(result.id), sources,
                                 project_id=str(self.project.id), inventory=inventory)
            # Legacy snapshots may refer to history outside their package, but
            # malformed structured IDs must still fail before any DB writes.
            for _ in _memory_references(result.reference_data):
                pass
        if len(canonical_package_bytes(self)) > MAX_PACKAGE_BYTES:
            raise ValueError("传输包不能超过 48 MiB")
        return self

    def _validate_memory(self, run_ids: set[UUID], result_ids: set[UUID]) -> None:
        memory = self.memory
        links = {item.check_run_id: item for item in memory.run_links}
        revisions = {item.result_id: item for item in memory.revisions}
        if len(links) != len(memory.run_links) or len(revisions) != len(memory.revisions):
            raise ValueError("问题记忆存在重复版本记录")
        gaps = {(item.kind, item.id) for item in memory.missing_references}
        if any((kind == "run" and ident in run_ids) or (kind == "result" and ident in result_ids)
               for kind, ident in gaps):
            raise ValueError("问题记忆不能把包内记录标记为缺失")
        result_map = {item.id: item for item in self.results}
        run_map = {item.id: item for item in self.check_runs}
        for link in links.values():
            if link.check_run_id not in run_ids:
                raise ValueError("问题记忆引用了包外的审查批次")
            if link.baseline_run_id:
                if link.baseline_run_id not in run_ids and ("run", link.baseline_run_id) not in gaps:
                    raise ValueError("问题记忆缺少历史基准批次")
                if link.baseline_run_id in run_map and run_map[link.baseline_run_id].created_at >= run_map[link.check_run_id].created_at:
                    raise ValueError("问题记忆基准必须早于当前批次")
            if link.mode == "new" and link.baseline_run_id is not None:
                raise ValueError("新报告不能关联历史基准")
        for revision in revisions.values():
            if revision.result_id not in result_ids or result_map[revision.result_id].module != revision.module:
                raise ValueError("问题记忆与检测结果不对应")
            if (revision.snapshot.get("track_key") != revision.track_key
                    or revision.snapshot.get("source_hash") != revision.source_hash):
                raise ValueError("问题记忆快照的版本标识不一致")
            owner = result_map[revision.result_id]
            if owner.check_run_id in links and links[owner.check_run_id].track_key != revision.track_key:
                raise ValueError("问题记忆与所属批次的版本链不一致")
            if revision.baseline_result_id:
                baseline = result_map.get(revision.baseline_result_id)
                if baseline is None and ("result", revision.baseline_result_id) not in gaps:
                    raise ValueError("问题记忆缺少历史基准结果")
                if baseline is not None and (baseline.module != revision.module or baseline.created_at >= result_map[revision.result_id].created_at):
                    raise ValueError("问题记忆基准结果的模块或时间不符合要求")
            for kind, ident in _memory_references(revision.snapshot):
                pool = run_ids if kind == "run" else result_ids
                if UUID(ident) not in pool and (kind, UUID(ident)) not in gaps:
                    raise ValueError("问题记忆快照引用了未声明的包外历史")
        for result in self.results:
            for kind, ident in _memory_references(result.reference_data):
                pool = run_ids if kind == "run" else result_ids
                if UUID(ident) not in pool and (kind, UUID(ident)) not in gaps:
                    raise ValueError("检测结果引用了未声明的记忆历史")
        events = {event.id: event for event in memory.feedback}
        if len(events) != len(memory.feedback) or any(ident in set(run_ids) | set(result_ids) | {self.project.id}
                                                    | {item.id for item in self.sources} for ident in events):
            raise ValueError("人工反馈事件 ID 重复")
        for event in events.values():
            revision = revisions.get(event.result_id)
            if revision is None or str(event.issue_id) not in {str(issue.get("issue_id")) for issue in revision.snapshot["issues"]}:
                raise ValueError("反馈引用的问题不属于当前结果的快照")
            if event.revokes_event_id:
                target = events.get(event.revokes_event_id)
                if (target is None or target.action == "revoke"
                        or target.issue_id != event.issue_id or target.created_at > event.created_at
                        or target.id == event.id):
                    raise ValueError("撤回反馈引用了不对应的原事件")


def _validate_references(value: Any, result_id: str, sources: dict[str, PackageSource], depth: int = 0,
                         *, project_id: str, inventory: Any = None) -> None:
    """Validate structured source links without interpreting review prose as IDs."""
    if depth > 64:
        raise ValueError("检测结果 JSON 嵌套过深")
    if isinstance(value, list):
        for item in value:
            _validate_references(item, result_id, sources, depth + 1, project_id=project_id, inventory=inventory)
    elif isinstance(value, dict):
        documents = value.get("source_documents")
        if documents is not None:
            if not isinstance(documents, list):
                raise ValueError("source_documents 必须是数组")
            for document in documents:
                if not isinstance(document, dict) or str(document.get("id")) not in sources:
                    raise ValueError("原始报告引用缺少对应附件")
        for key, item in value.items():
            if key in {"download_url", "preview_url"} and item:
                if not isinstance(item, str):
                    raise ValueError("附件链接必须是相对 API 路径")
                match = _SOURCE_URL.fullmatch(item)
                complete = _COMPLETENESS_URL.fullmatch(item)
                if match:
                    if match[1] != result_id or match[2] not in sources:
                        raise ValueError("附件链接引用了其他审查记录")
                    if key == "preview_url" and not urlsplit(item).path.endswith("/preview"):
                        raise ValueError("原文预览必须使用安全预览接口")
                elif complete:
                    if key == "preview_url":
                        raise ValueError("完整性附件只能作为原始文件下载")
                    # Completeness documents are indexed by their stored inventory.
                    index = int(complete[2])
                    if (complete[1] != result_id or not isinstance(inventory, list) or index >= len(inventory)
                            or not isinstance(inventory[index], dict)
                            or inventory[index].get("filename") not in {source.filename for source in sources.values()}):
                        raise ValueError("完整性附件链接引用了其他审查记录")
                else:
                    raise ValueError("附件链接必须指向包内的原始报告")
                if any(name == "project_id" and argument != project_id
                       for name, argument in parse_qsl(urlsplit(item).query)):
                    raise ValueError("附件链接引用了其他项目")
            _validate_references(item, result_id, sources, depth + 1, project_id=project_id, inventory=inventory)


def canonical_package_bytes(package: RecordPackage) -> bytes:
    """Stable bytes: UUIDs/timestamps and entity ordering are normalized."""
    payload = package.model_dump(mode="json")
    for key in ("check_runs", "results", "sources"):
        payload[key] = sorted(payload[key], key=lambda row: row["id"])
    if package.schema_version in {2, 3}:
        payload["memory"]["run_links"].sort(key=lambda row: row["check_run_id"])
        payload["memory"]["revisions"].sort(key=lambda row: row["result_id"])
        payload["memory"]["missing_references"].sort(key=lambda row: (row["kind"], row["id"]))
        if package.schema_version == 3:
            payload["memory"]["feedback"].sort(key=lambda row: (row["created_at"], row["id"]))
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def package_sha256(package: RecordPackage) -> str:
    return hashlib.sha256(canonical_package_bytes(package)).hexdigest()


def _fields(row: Any, names: str) -> dict[str, Any]:
    return {name: getattr(row, name) for name in names.split()}


def _memory_references(value: Any, depth: int = 0):
    """Only structured ledger references are identifiers; never rewrite prose."""
    if depth > 64:
        raise ValueError("问题记忆 JSON 嵌套过深")
    if isinstance(value, list):
        for item in value:
            yield from _memory_references(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in _MEMORY_RESULT_KEYS | _MEMORY_RUN_KEYS and item:
                # Bad legacy IDs must be diagnosed instead of silently linking
                # to an unrelated row at the destination.
                yield ("run" if key in _MEMORY_RUN_KEYS else "result", str(UUID(str(item))))
            else:
                yield from _memory_references(item, depth + 1)


def _include_memory_ancestors(db: Session, project_id: str, rows: list[CheckResult], runs: list[CheckRun]):
    """A batch export includes its dependent history, keeping version chains closed."""
    result_map = {row.id: row for row in rows}
    run_map = {run.id: run for run in runs}
    links, revisions, gaps = {}, {}, set()
    queue = [("result", ident) for ident in result_map] + [("run", ident) for ident in run_map]
    visited = set()
    while queue:
        kind, ident = queue.pop()
        if (kind, ident) in visited:
            continue
        visited.add((kind, ident))
        model, pool = (CheckRun, run_map) if kind == "run" else (CheckResult, result_map)
        row = pool.get(ident) or db.get(model, ident)
        if row is None:
            gaps.add((kind, ident))
            continue
        if row.project_id != project_id:
            raise ValueError("问题记忆引用了其他项目")
        pool[ident] = row
        if len(result_map) > MAX_RESULTS or len(run_map) > MAX_RUNS:
            raise HTTPException(413, "问题记忆依赖的历史过多，请拆分或归档后传输")
        if kind == "run":
            if row.status.lower() in _ACTIVE_STATUSES:
                raise HTTPException(409, "历史基准批次尚未结束，不能传输")
            # Also retain sibling module results in an included ancestor batch.
            siblings = list(db.scalars(select(CheckResult).where(CheckResult.check_run_id == ident)
                                       .limit(MAX_RESULTS + 1)).all())
            if not siblings:
                # Empty batches cannot serve as useful version dependencies.
                run_map.pop(ident, None)
                gaps.add(("run", ident))
                continue
            queue.extend(("result", item.id) for item in siblings)
            link = db.get(ReviewRunLink, ident)
            if link:
                links[ident] = link
                if link.baseline_run_id:
                    queue.append(("run", link.baseline_run_id))
        else:
            if row.check_run_id:
                queue.append(("run", row.check_run_id))
            revision = db.get(ReviewMemoryRevision, ident)
            if revision:
                if revision.project_id != project_id or revision.module != row.module:
                    raise ValueError("问题记忆与检测结果不一致")
                revisions[ident] = revision
                if revision.baseline_result_id:
                    queue.append(("result", revision.baseline_result_id))
                queue.extend(_memory_references(json.loads(revision.snapshot_json)))
            queue.extend(_memory_references(json.loads(row.reference_data)))
    return list(result_map.values()), list(run_map.values()), links, revisions, gaps


def export_record_package(db: Session, project_id: str, check_run_id: str | None = None) -> RecordPackage:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "项目不存在")
    statement = select(CheckResult).where(CheckResult.project_id == project_id)
    if check_run_id:
        run = db.get(CheckRun, check_run_id)
        if run is None or run.project_id != project_id:
            raise HTTPException(404, "审查批次不存在或不属于当前项目")
        statement = statement.where(CheckResult.check_run_id == check_run_id)
        runs = [run]
    else:
        runs = list(db.scalars(select(CheckRun).where(CheckRun.project_id == project_id)).all())
    if any(run.status.lower() in _ACTIVE_STATUSES for run in runs):
        raise HTTPException(409, "运行中的审查批次不能传输，请等待审查结束")
    rows = list(db.scalars(statement.limit(MAX_RESULTS + 1)).all())
    if not rows:
        raise HTTPException(409, "没有可传输的审查结果")
    if len(rows) > MAX_RESULTS or len(runs) > MAX_RUNS:
        raise HTTPException(413, "审查记录过多，请按批次传输")
    # Empty historical batches are not review records and need not travel.
    used_runs = {row.check_run_id for row in rows}
    runs = [run for run in runs if run.id in used_runs]
    try:
        rows, runs, links, revisions, gaps = _include_memory_ancestors(db, project_id, rows, runs)
        # A current-version revoke may target a prior-version event. Retain
        # that event's result and ledger as a real, validated dependency even
        # when exporting a single batch with an unusual version selection.
        while True:
            selected = {row.id for row in rows}
            events = db.scalars(select(IssueFeedbackEvent).where(IssueFeedbackEvent.project_id == project_id,
                                IssueFeedbackEvent.result_id.in_(selected)).limit(10001)).all()
            if len(events) > 10000:
                raise HTTPException(413, "人工反馈记录过多，请按批次传输")
            extra = {}
            for event in events:
                if event.revokes_event_id:
                    target = db.get(IssueFeedbackEvent, event.revokes_event_id)
                    if target is None or target.project_id != project_id or target.issue_id != event.issue_id:
                        raise ValueError("撤回反馈的来源记录不完整")
                    if target.result_id not in selected:
                        target_result = db.get(CheckResult, target.result_id)
                        if target_result is None or target_result.project_id != project_id:
                            raise ValueError("撤回反馈的来源结果不存在")
                        extra[target_result.id] = target_result
            if not extra:
                break
            rows, runs, links, revisions, gaps = _include_memory_ancestors(db, project_id, rows + list(extra.values()), runs)
    except (ValueError, TypeError) as exc:
        raise HTTPException(409, "问题记忆结构不完整，无法安全传输") from exc
    source_filter = SourceArtifact.check_result_id.in_([row.id for row in rows])
    source_count, source_bytes = db.execute(select(
        func.count(SourceArtifact.id), func.coalesce(func.sum(func.length(SourceArtifact.content)), 0)
    ).where(source_filter)).one()
    # Check the byte budget before loading potentially large database BLOBs.
    if source_count > MAX_SOURCES or source_bytes > MAX_SOURCE_BYTES:
        raise HTTPException(413, "附件过多或总大小超过 32 MiB，请按批次传输")
    sources = list(db.scalars(select(SourceArtifact).where(source_filter).limit(MAX_SOURCES + 1)).all())
    if len(sources) > MAX_SOURCES or sum(len(source.content) for source in sources) > MAX_SOURCE_BYTES:
        raise HTTPException(413, "附件过多或总大小超过 32 MiB，请按批次传输")
    profile = db.get(ProjectMemoryProfile, project_id)
    feedback = list(db.scalars(select(IssueFeedbackEvent).where(IssueFeedbackEvent.project_id == project_id,
                               IssueFeedbackEvent.result_id.in_([row.id for row in rows]))
                               .order_by(IssueFeedbackEvent.created_at, IssueFeedbackEvent.id).limit(10001)).all())
    if len(feedback) > 10000:
        raise HTTPException(413, "人工反馈记录过多，请按批次传输")
    try:
        payload = {
            "schema_version": 1,
            "project": _fields(project, "id name department description status created_at updated_at"),
            "check_runs": [_fields(run, "id report_name source_filename status created_at updated_at") for run in runs],
            "results": [{**_fields(row, "id check_run_id module check_subtype item_id related_item_id severity score result_label reason suggestion model_name created_at"),
                         "reference_data": json.loads(row.reference_data)} for row in rows],
            "sources": [{**_fields(source, "id check_result_id role stage filename media_type created_at"),
                         "content_base64": base64.b64encode(source.content).decode("ascii"),
                         "sha256": hashlib.sha256(source.content).hexdigest()} for source in sources],
        }
        if links or revisions or gaps or profile or feedback:
            payload.update(schema_version=2, memory={
                "run_links": [_fields(link, "check_run_id baseline_run_id mode track_key") for link in links.values()],
                "revisions": [{**_fields(revision, "result_id module track_key baseline_result_id source_hash created_at"),
                               "snapshot": json.loads(revision.snapshot_json)} for revision in revisions.values()],
                "missing_references": [{"kind": kind, "id": ident} for kind, ident in sorted(gaps)],
            })
        if profile or feedback:
            payload["schema_version"] = 3
            payload["memory"]["profile"] = ({**json.loads(profile.context_json), "actor": profile.actor,
                                              "updated_at": profile.updated_at} if profile else None)
            payload["memory"]["feedback"] = [_fields(event, "id result_id issue_id action reason evidence actor source revokes_event_id created_at")
                                              for event in feedback]
        return RecordPackage.model_validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(409, "历史记录结构不完整或超过传输限制，无法导出") from exc


def _rewrite_references(value: Any, mapping: dict[str, str], key: str | None = None,
                        *, namespace_id: UUID | None = None) -> Any:
    if isinstance(value, list):
        return [_rewrite_references(item, mapping, key, namespace_id=namespace_id) for item in value]
    if isinstance(value, dict):
        return {field: _rewrite_references(item, mapping, field, namespace_id=namespace_id) for field, item in value.items()}
    if isinstance(value, str):
        if namespace_id is not None and value and key in _MEMORY_RESULT_KEYS | _MEMORY_RUN_KEYS:
            return mapping.get(value, str(uuid5(namespace_id, value)))
        if key in {"id", "project_id", "check_run_id", "result_id", "check_result_id", "source_id", "item_id", "related_item_id"} | _MEMORY_RESULT_KEYS | _MEMORY_RUN_KEYS:
            return mapping.get(value, value)
        if namespace_id is not None and value and key == "issue_id":
            return str(uuid5(namespace_id, "issue:" + value))
        if namespace_id is not None and value and key == "track_key":
            return hashlib.sha256((str(namespace_id) + ":track:" + value).encode()).hexdigest()
        if key in {"download_url", "preview_url"} and value.startswith("/api/"):
            # Only complete URL path components are IDs; prose and URL parameters
            # containing excerpts remain untouched, including UUID-like text.
            url = urlsplit(value)
            path = "/".join(mapping.get(part, part) for part in url.path.split("/"))
            query = []
            for part in url.query.split("&"):
                name, separator, argument = part.partition("=")
                if unquote_plus(name) in {"project_id", "check_run_id", "result_id", "check_result_id", "source_id"}:
                    argument = mapping.get(unquote_plus(argument), argument)
                query.append(name + separator + argument)
            return urlunsplit((url.scheme, url.netloc, path, "&".join(query), url.fragment))
    return value


def _db_values(model: PackageModel, exclude: set[str] | None = None) -> dict[str, Any]:
    values = model.model_dump(exclude=exclude or set())
    return {key: (str(value) if isinstance(value, UUID) else value.astimezone(timezone.utc).replace(tzinfo=None)
                  if isinstance(value, datetime) else value) for key, value in values.items()}


def import_record_package(db: Session, package: RecordPackage, namespace: str) -> dict[str, Any]:
    """Insert an isolated copy; caller commits this and its idempotency receipt."""
    if not namespace:
        raise ValueError("导入必须指定可信命名空间")
    # Revalidate even when a caller mutated a previously parsed model.
    package = RecordPackage.model_validate(package.model_dump())
    groups = ((Project, [package.project]), (CheckRun, package.check_runs),
              (CheckResult, package.results), (SourceArtifact, package.sources))
    namespace_id = uuid5(NAMESPACE_URL, "aiassist:record-package:" + namespace)
    mapping = {str(row.id): str(uuid5(namespace_id, str(row.id))) for _, rows in groups for row in rows}
    if package.memory:
        for gap in package.memory.missing_references:
            mapping[str(gap.id)] = str(uuid5(namespace_id, str(gap.id)))
        for event in package.memory.feedback:
            mapping[str(event.id)] = str(uuid5(namespace_id, "feedback:" + str(event.id)))
    for model, rows in groups:
        keys = [mapping[str(row.id)] for row in rows]
        if keys and db.scalar(select(model.id).where(model.id.in_(keys)).limit(1)) is not None:
            raise HTTPException(409, "目标已有该记录，请使用原上传回执重试，不允许覆盖")
    project_id = mapping[str(package.project.id)]
    imported_result_ids = {mapping[str(result.id)] for result in package.results}
    db.add(Project(**{**_db_values(package.project), "id": project_id}))
    db.flush()
    for run in package.check_runs:
        db.add(CheckRun(**{**_db_values(run), "id": mapping[str(run.id)], "project_id": project_id}))
    db.flush()
    for result in package.results:
        values = _db_values(result, {"reference_data"})
        for field in ("id", "check_run_id", "item_id", "related_item_id"):
            values[field] = mapping.get(values[field], values[field]) if values[field] is not None else None
        values["project_id"] = project_id
        payload = _rewrite_references(result.reference_data, mapping, namespace_id=namespace_id)
        result_data = payload.get("result")
        if isinstance(result_data, dict) and (package.memory and package.memory.missing_references
                                            or package.schema_version == 1 and result_data.get("issue_memory")):
            memory = result_data.get("issue_memory")
            if isinstance(memory, dict):
                memory["transfer_status"] = "partial_history" if package.schema_version >= 2 else "legacy_snapshot"
                hint = ("部分历史原记录已不存在，已保留问题快照；缺失的历史记录无法打开。"
                        if package.schema_version >= 2 else "旧版传输仅包含历史快照，未携带完整问题记忆版本链。")
                message = str(memory.get("message") or "")
                memory["message"] = message if hint in message else (message + " " + hint).strip()
                missing = {mapping[str(gap.id)] for gap in package.memory.missing_references
                           if gap.kind == "result"} if package.memory else {
                               ident for kind, ident in _memory_references(payload)
                               if kind == "result" and ident not in imported_result_ids}
                _clear_missing_history_buttons(result_data, missing)
        values["reference_data"] = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        db.add(CheckResult(**values))
    db.flush()
    for source in package.sources:
        values = _db_values(source, {"content_base64", "sha256"})
        values.update(id=mapping[str(source.id)], check_result_id=mapping[str(source.check_result_id)], content=source.decoded_content())
        db.add(SourceArtifact(**values))
    if package.memory:
        for link in package.memory.run_links:
            values = _db_values(link)
            values = _rewrite_references(values, mapping, namespace_id=namespace_id)
            db.add(ReviewRunLink(**values))
        for revision in package.memory.revisions:
            values = _db_values(revision, {"snapshot"})
            values = _rewrite_references(values, mapping, namespace_id=namespace_id)
            values.update(project_id=project_id, snapshot_json=json.dumps(
                _rewrite_references(revision.snapshot, mapping, namespace_id=namespace_id),
                ensure_ascii=False, allow_nan=False))
            db.add(ReviewMemoryRevision(**values))
        if package.memory.profile:
            profile = package.memory.profile
            db.add(ProjectMemoryProfile(project_id=project_id,
                                         context_json=json.dumps({"facts": profile.facts, "aliases": profile.aliases}, ensure_ascii=False),
                                         actor=("导入来源：" + profile.actor)[:256],
                                         updated_at=_db_values(profile)["updated_at"]))
        for event in package.memory.feedback:
            values = _db_values(event)
            values.update(id=mapping[str(event.id)], result_id=mapping[str(event.result_id)],
                          project_id=project_id, issue_id=str(uuid5(namespace_id, "issue:" + str(event.issue_id))),
                          revokes_event_id=mapping[str(event.revokes_event_id)] if event.revokes_event_id else None,
                          actor=("导入来源：" + event.actor)[:256], source="imported")
            db.add(IssueFeedbackEvent(**values))
    db.flush()
    return {"project_id": project_id,
            "check_run_ids": [mapping[str(run.id)] for run in package.check_runs],
            "result_ids": [mapping[str(result.id)] for result in package.results],
            "source_ids": [mapping[str(source.id)] for source in package.sources],
            "id_map": mapping}


def _clear_missing_history_buttons(value: Any, missing: set[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _clear_missing_history_buttons(item, missing)
    elif isinstance(value, dict):
        # These copies power the history-navigation buttons. The immutable
        # ledger retains its remapped missing IDs so tracking facts survive.
        for key in ("previous_result_id", "last_seen_result_id", "first_result_id"):
            if value.get(key) in missing:
                value[key] = None
                value["history_unavailable"] = True
        for item in value.values():
            _clear_missing_history_buttons(item, missing)
