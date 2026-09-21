"""Separate history namespace, reusing project results and immutable source snapshots."""
from datetime import datetime
import html
import json
import mimetypes
import threading
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.db.models import CheckResult, Project, SourceArtifact
from app.db.session import get_db
from app.modules.completeness.service import check_completeness

router = APIRouter()
_save_lock = threading.Lock()
MAX_BYTES = 100 * 1024 * 1024


def _payload(row):
    value = json.loads(row.reference_data)
    return {**value, "id": row.id, "project_id": row.project_id,
            "created_at": row.created_at.isoformat() + "Z"}


def _get(db, project_id, result_id):
    row = db.query(CheckResult).filter_by(id=result_id, project_id=project_id,
                                        check_subtype="completeness_report").first()
    if row is None:
        raise HTTPException(404, "完整性报告不存在或不属于当前项目")
    return row


@router.post("/reports")
def create_report(project_id: str = Form(...), request_id: str = Form(...),
                  file: UploadFile = File(...), attachments: list[UploadFile] = File(default=[]),
                  db: Session = Depends(get_db)):
    if db.get(Project, project_id) is None:
        raise HTTPException(404, "项目不存在")
    try:
        UUID(request_id)
    except ValueError:
        raise HTTPException(400, "请求编号无效")
    if len(attachments) > 20:
        raise HTTPException(400, "一次最多提交20个附件")
    sources, total = [], 0
    for index, upload in enumerate([file, *attachments]):
        raw = upload.file.read(MAX_BYTES + 1)
        total += len(raw)
        if total > MAX_BYTES:
            raise HTTPException(413, "本次材料总大小不能超过100MB")
        name = (upload.filename or "未命名文件").replace("\\", "/").split("/")[-1]
        sources.append((name, raw, "report" if index == 0 else "attachment"))
    if len({n for n, _, _ in sources}) != len(sources):
        raise HTTPException(400, "本次材料存在同名文件，请重命名以明确附件对应关系")
    # Serializing creation also makes retries with one request ID idempotent.
    # Running checks use a synchronous endpoint (FastAPI worker thread), not the event loop.
    with _save_lock:
        old = db.query(CheckResult).filter_by(project_id=project_id, item_id=request_id,
                                              check_subtype="completeness_report").first()
        if old:
            return _payload(old)
        report = check_completeness(sources[0][1], sources[0][0], [(n, r) for n, r, _ in sources[1:]])
        row = CheckResult(project_id=project_id, module="completeness", check_subtype="completeness_report",
                          item_id=request_id, result_label=report["label"], severity=report["status"],
                          reason=report["conclusion"], reference_data=json.dumps(report, ensure_ascii=False),
                          model_name=report["version"])
        db.add(row)
        db.flush()
        for name, raw, role in sources:
            db.add(SourceArtifact(check_result_id=row.id, role=role, filename=name, content=raw,
                                  media_type=mimetypes.guess_type(name)[0] or "application/octet-stream"))
        db.commit()
        db.refresh(row)
        return _payload(row)


@router.get("/reports")
def list_reports(project_id: str, offset: int = 0, limit: int = 30, db: Session = Depends(get_db)):
    limit, offset = max(1, min(limit, 100)), max(0, offset)
    query = db.query(CheckResult).filter_by(project_id=project_id, check_subtype="completeness_report")
    rows = query.order_by(CheckResult.created_at.desc(), CheckResult.id.desc()).offset(offset).limit(limit).all()
    items = []
    for row in rows:
        p = _payload(row)
        items.append({k: p[k] for k in ("id", "project_id", "created_at", "filename", "status", "label", "missing_count", "review_count")})
        # Older reports retain their original result; only absent display fields
        # receive defaults. Never reinterpret stored results using current rules.
        items[-1].update(version=p.get('version', 'completeness-1.0'), info_count=p.get('info_count', 0))
    return {"items": items, "total": query.count(), "offset": offset}


@router.get("/reports/{result_id}")
def get_report(result_id: str, project_id: str, db: Session = Depends(get_db)):
    return _payload(_get(db, project_id, result_id))


def render_html(report):
    esc = lambda value: html.escape(str(value))
    parts = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>完整性检查报告</title>",
             "<style>body{font:16px/1.7 'Microsoft YaHei',sans-serif;max-width:1000px;margin:40px auto;padding:20px;color:#25324a}h2{border-bottom:1px solid #ddd}article{border:1px solid #ddd;padding:16px;margin:12px 0}blockquote{background:#f6f8fb;padding:12px;white-space:pre-wrap}small{color:#64748b}@media print{body{margin:0}}</style>",
             "<h1>完整性检查报告</h1>", f"<p>{esc(report['filename'])}</p>",
             f"<p>结论：<b>{esc(report['label'])}</b>　主要缺失：{report['missing_count']}项　待核实：{report['review_count']}项　一般提示（不影响结论）：{report.get('info_count', 0)}项</p>",
             f"<p>检查时间：{esc(report.get('created_at', ''))}　规则版本：{esc(report['version'])}</p>",
             f"<p>{esc(report['scope'])}</p><h2>本次材料</h2>"]
    for entry in report["inventory"]:
        parts.append(f"<p>{esc(entry['filename'])}（{entry['size']}字节）<br><small>SHA256：{entry['sha256']}</small></p>")
    parts.append("<h2>检查结果与依据</h2>")
    for item in report["items"] + report["details"]:
        parts.append(f"<article><b>{esc(item['title'])}：{esc(item['label'])}</b><p>{esc(item['reason'])}</p>")
        for evidence in item["evidence"]:
            names = {"filename": "文件", "section": "章节", "paragraph": "正文段落", "table_index": "表格", "table_path": "嵌套表", "row_index": "行", "page": "页码", "line": "文本行", "sheet": "工作表"}
            loc = " · ".join(f"{names.get(k, k)}: {v}" for k, v in evidence.items() if k not in {"quote", "is_heading", "is_caption"})
            parts.append(f"<small>{esc(loc)}</small><blockquote>{esc(evidence['quote'])}</blockquote>")
        if item["advice"]:
            parts.append(f"<p>建议：{esc(item['advice'])}</p>")
        parts.append("</article>")
    parts.append(f"<p>{esc(report['disclaimer'])}</p></html>")
    return "\n".join(parts)


@router.get("/reports/{result_id}/export")
def export_report(result_id: str, project_id: str, db: Session = Depends(get_db)):
    report = _payload(_get(db, project_id, result_id))
    return Response(render_html(report), media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=completeness-report.html"})


@router.get("/reports/{result_id}/sources/{index}")
def get_source(result_id: str, index: int, project_id: str, db: Session = Depends(get_db)):
    row = _get(db, project_id, result_id)
    inventory = json.loads(row.reference_data)["inventory"]
    if index < 0 or index >= len(inventory):
        raise HTTPException(404, "原文件不存在")
    source = next((s for s in row.source_artifacts if s.filename == inventory[index]["filename"]), None)
    if source is None:
        raise HTTPException(404, "原文件快照不存在")
    return Response(source.content, media_type=source.media_type,
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(source.filename)})
