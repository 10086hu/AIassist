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
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import CheckResult, CheckRun, Project, SourceArtifact


MAX_PACKAGE_BYTES = 48 * 1024 * 1024
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCES = 500
MAX_RESULTS = 2000
MAX_RUNS = 200
MYSQL_TEXT_MAX_BYTES = 65535
_ACTIVE_STATUSES = {"running", "queued", "pending", "processing"}
_SOURCE_URL = re.compile(r"^/api/evaluate/results/([^/]+)/sources/([^/?#]+)(?:/preview)?(?:[?#].*)?$")
_COMPLETENESS_URL = re.compile(r"^/api/completeness/reports/([^/]+)/sources/(\d+)(?:[?#].*)?$")


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


class RecordPackage(PackageModel):
    schema_version: Literal[1] = 1
    project: PackageProject
    check_runs: list[PackageRun] = Field(default_factory=list, max_length=MAX_RUNS)
    results: list[PackageResult] = Field(min_length=1, max_length=MAX_RESULTS)
    sources: list[PackageSource] = Field(default_factory=list, max_length=MAX_SOURCES)

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
        if len(canonical_package_bytes(self)) > MAX_PACKAGE_BYTES:
            raise ValueError("传输包不能超过 48 MiB")
        return self


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
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def package_sha256(package: RecordPackage) -> str:
    return hashlib.sha256(canonical_package_bytes(package)).hexdigest()


def _fields(row: Any, names: str) -> dict[str, Any]:
    return {name: getattr(row, name) for name in names.split()}


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
        return RecordPackage.model_validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(409, "历史记录结构不完整或超过传输限制，无法导出") from exc


def _rewrite_references(value: Any, mapping: dict[str, str], key: str | None = None) -> Any:
    if isinstance(value, list):
        return [_rewrite_references(item, mapping, key) for item in value]
    if isinstance(value, dict):
        return {field: _rewrite_references(item, mapping, field) for field, item in value.items()}
    if isinstance(value, str):
        if key in {"id", "project_id", "check_run_id", "result_id", "check_result_id", "source_id", "item_id", "related_item_id"}:
            return mapping.get(value, value)
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
    for model, rows in groups:
        keys = [mapping[str(row.id)] for row in rows]
        if keys and db.scalar(select(model.id).where(model.id.in_(keys)).limit(1)) is not None:
            raise HTTPException(409, "目标已有该记录，请使用原上传回执重试，不允许覆盖")
    project_id = mapping[str(package.project.id)]
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
        values["reference_data"] = json.dumps(_rewrite_references(result.reference_data, mapping), ensure_ascii=False, allow_nan=False)
        db.add(CheckResult(**values))
    db.flush()
    for source in package.sources:
        values = _db_values(source, {"content_base64", "sha256"})
        values.update(id=mapping[str(source.id)], check_result_id=mapping[str(source.check_result_id)], content=source.decoded_content())
        db.add(SourceArtifact(**values))
    db.flush()
    return {"project_id": project_id,
            "check_run_ids": [mapping[str(run.id)] for run in package.check_runs],
            "result_ids": [mapping[str(result.id)] for result in package.results],
            "source_ids": [mapping[str(source.id)] for source in package.sources],
            "id_map": mapping}
