"""Account-scoped history uploads and administrator-addressed, durable deliveries."""
from datetime import datetime, timezone
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, get_current_admin
from app.core.config import settings
from app.db.models import AuditLog, HistoryDelivery, HistoryImport, HistoryUpload, Project, User
from app.db.session import get_db
from app.services.record_packages import (
    RecordPackage, canonical_package_bytes, export_record_package,
    import_record_package, package_sha256,
)


router = APIRouter(dependencies=[Depends(get_current_user)])
admin_router = APIRouter(dependencies=[Depends(get_current_admin)])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UploadRequest(StrictRequest):
    client_record_id: UUID
    package: RecordPackage


class DeliveryRequest(StrictRequest):
    recipient_user_id: UUID
    project_id: UUID
    check_run_id: UUID | None = None


class AcknowledgeRequest(StrictRequest):
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ImportRequest(AcknowledgeRequest):
    delivery_id: UUID
    package: RecordPackage


def require_mode(mode: str) -> None:
    if settings.backend_mode != mode:
        raise HTTPException(403, "此接口仅供服务器后端使用" if mode == "server" else "此接口仅供本地后端使用")


def utc(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def upload_info(row: HistoryUpload, duplicate: bool = False) -> dict:
    return {"id": row.id, "user_id": row.user_id, "client_record_id": row.client_record_id,
            "project_id": row.project_id, "package_sha256": row.package_sha256,
            "created_at": utc(row.created_at), "duplicate": duplicate}


def delivery_info(row: HistoryDelivery, duplicate: bool = False) -> dict:
    return {"id": row.id, "recipient_user_id": row.recipient_user_id, "sender_user_id": row.sender_user_id,
            "project_id": row.project_id, "check_run_id": row.check_run_id,
            "package_sha256": row.package_sha256, "status": row.status,
            "created_at": utc(row.created_at), "received_at": utc(row.received_at),
            "revoked_at": utc(row.revoked_at), "duplicate": duplicate}


def audit(db: Session, user: User, action: str, **detail) -> None:
    # Do not store passwords, bearer tokens, report bodies, or attachment bytes.
    db.add(AuditLog(action=action, operator=user.id, module="history",
                    detail=json.dumps(detail, ensure_ascii=False)))


def package_response(package: RecordPackage) -> Response:
    return Response(canonical_package_bytes(package), media_type="application/json",
                    headers={"X-Package-SHA256": package_sha256(package), "Cache-Control": "no-store"})


def same_package(row, digest: str) -> None:
    if row.package_sha256 != digest:
        raise HTTPException(409, "该传输编号已有不同内容，请为新的审查版本使用新的传输编号")


def existing_upload(db: Session, row: HistoryUpload, digest: str) -> dict:
    same_package(row, digest)
    if db.get(Project, row.project_id) is None:
        raise HTTPException(409, "该上传对应的服务器项目已被删除，请联系管理员")
    return upload_info(row, True)


@router.get("/exports/{project_id}", response_model=RecordPackage)
def export_history(project_id: UUID, check_run_id: UUID | None = None,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if settings.backend_mode == "server" and user.role != "admin":
        raise HTTPException(403, "服务器记录仅允许管理员导出，请从收件箱领取分配的记录")
    return package_response(export_record_package(db, str(project_id), str(check_run_id) if check_run_id else None))


@router.post("/uploads")
def upload_history(payload: UploadRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_mode("server")
    source_id = str(payload.client_record_id)
    digest = package_sha256(payload.package)
    query = select(HistoryUpload).where(HistoryUpload.user_id == user.id, HistoryUpload.client_record_id == source_id)
    existing = db.scalar(query)
    if existing:
        return existing_upload(db, existing, digest)
    try:
        # Reserve the unique receipt before importing any business rows. Concurrent
        # retries wait here, then observe the winning committed receipt.
        row = HistoryUpload(user_id=user.id, client_record_id=source_id, package_sha256=digest,
                            package_json=canonical_package_bytes(payload.package).decode(), project_id="pending")
        db.add(row)
        db.flush()
        imported = import_record_package(db, payload.package, f"upload:{user.id}:{source_id}")
        row.project_id = imported["project_id"]
        audit(db, user, "history.upload", upload_id=row.id, project_id=row.project_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(query)
        if existing:
            return existing_upload(db, existing, digest)
        raise HTTPException(409, "导入记录存在编号冲突")
    except Exception:
        db.rollback()
        raise
    return upload_info(row)


@router.get("/uploads")
def my_uploads(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_mode("server")
    rows = db.scalars(select(HistoryUpload).where(HistoryUpload.user_id == user.id)
                      .order_by(HistoryUpload.created_at.desc(), HistoryUpload.id).offset(offset).limit(limit)).all()
    return {"items": [upload_info(row) for row in rows], "offset": offset, "limit": limit}


@admin_router.get("/uploads")
def admin_uploads(user_id: UUID | None = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                  db: Session = Depends(get_db)):
    require_mode("server")
    query = select(HistoryUpload)
    if user_id:
        query = query.where(HistoryUpload.user_id == str(user_id))
    rows = db.scalars(query.order_by(HistoryUpload.created_at.desc(), HistoryUpload.id).offset(offset).limit(limit)).all()
    return {"items": [upload_info(row) for row in rows], "offset": offset, "limit": limit}


@admin_router.get("/uploads/{upload_id}/package", response_model=RecordPackage)
def uploaded_package(upload_id: UUID, db: Session = Depends(get_db)):
    require_mode("server")
    row = db.get(HistoryUpload, str(upload_id))
    if row is None:
        raise HTTPException(404, "上传记录不存在")
    return package_response(RecordPackage.model_validate_json(row.package_json))


@admin_router.delete("/uploads/{upload_id}")
def delete_upload(upload_id: UUID, user: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    require_mode("server")
    row = db.scalar(select(HistoryUpload).where(HistoryUpload.id == str(upload_id)).with_for_update())
    if row is None:
        raise HTTPException(404, "上传记录不存在")
    project = db.get(Project, row.project_id)
    if project:
        db.delete(project)
    audit(db, user, "history.delete_upload", upload_id=row.id, project_id=row.project_id)
    db.delete(row)
    db.commit()
    return {"deleted_id": str(upload_id)}


@admin_router.post("/deliveries")
def dispatch_history(payload: DeliveryRequest, user: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    require_mode("server")
    recipient_id = str(payload.recipient_user_id)
    recipient = db.get(User, recipient_id)
    if recipient is None or not recipient.is_active:
        raise HTTPException(404, "接收用户不存在或已停用")
    project_id = str(payload.project_id)
    run_id = str(payload.check_run_id) if payload.check_run_id else None
    package = export_record_package(db, project_id, run_id)
    digest = package_sha256(package)
    query = select(HistoryDelivery).where(HistoryDelivery.recipient_user_id == recipient_id,
                                          HistoryDelivery.package_sha256 == digest)
    def reuse_delivery():
        # Do not gap-lock a missing unique key on MySQL: two first-time
        # dispatches can deadlock when upgrading the gap lock to an insert lock.
        existing = db.scalar(query)
        if existing is None:
            return None
        # Refresh the identity map from a current locked read, including when
        # another request revoked this row after the initial snapshot read.
        existing = db.scalar(select(HistoryDelivery).where(HistoryDelivery.id == existing.id)
                             .with_for_update().execution_options(populate_existing=True))
        if existing is None:
            return None
        if existing.status == "revoked":
            existing.status = "pending"
            existing.revoked_at = None
            existing.received_at = None
            existing.sender_user_id = user.id
            audit(db, user, "history.redispatch", delivery_id=existing.id, recipient_user_id=recipient_id)
            db.commit()
        return delivery_info(existing, True)

    reused = reuse_delivery()
    if reused is not None:
        return reused
    row = HistoryDelivery(recipient_user_id=recipient_id, sender_user_id=user.id, project_id=project_id,
                          check_run_id=run_id, package_sha256=digest,
                          package_json=canonical_package_bytes(package).decode(), status="pending")
    try:
        db.add(row)
        db.flush()
        audit(db, user, "history.dispatch", delivery_id=row.id, recipient_user_id=recipient_id, project_id=project_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        reused = reuse_delivery()
        if reused is not None:
            return reused
        raise HTTPException(409, "分发记录存在编号冲突")
    return delivery_info(row)


@admin_router.get("/deliveries")
def admin_deliveries(recipient_user_id: UUID | None = None, offset: int = Query(0, ge=0),
                     limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db)):
    require_mode("server")
    query = select(HistoryDelivery)
    if recipient_user_id:
        query = query.where(HistoryDelivery.recipient_user_id == str(recipient_user_id))
    rows = db.scalars(query.order_by(HistoryDelivery.created_at.desc(), HistoryDelivery.id).offset(offset).limit(limit)).all()
    return {"items": [delivery_info(row) for row in rows], "offset": offset, "limit": limit}


@admin_router.post("/deliveries/{delivery_id}/revoke")
def revoke_delivery(delivery_id: UUID, user: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    require_mode("server")
    row = db.scalar(select(HistoryDelivery).where(HistoryDelivery.id == str(delivery_id)).with_for_update())
    if row is None:
        raise HTTPException(404, "分发记录不存在")
    if row.status != "revoked":
        row.status = "revoked"
        row.revoked_at = datetime.utcnow()
        audit(db, user, "history.revoke", delivery_id=row.id)
        db.commit()
    return delivery_info(row)


def recipient_delivery(db: Session, delivery_id: str, user_id: str, lock: bool = False) -> HistoryDelivery:
    query = select(HistoryDelivery).where(HistoryDelivery.id == delivery_id, HistoryDelivery.recipient_user_id == user_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, "分发记录不存在")
    if row.status == "revoked":
        raise HTTPException(410, "该分发已被管理员撤回")
    return row


@router.get("/inbox")
def inbox(status: Literal["pending", "received", "revoked"] | None = None, offset: int = Query(0, ge=0),
          limit: int = Query(50, ge=1, le=100), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_mode("server")
    query = select(HistoryDelivery).where(HistoryDelivery.recipient_user_id == user.id)
    if status:
        query = query.where(HistoryDelivery.status == status)
    rows = db.scalars(query.order_by(HistoryDelivery.created_at.desc(), HistoryDelivery.id).offset(offset).limit(limit)).all()
    return {"items": [delivery_info(row) for row in rows], "offset": offset, "limit": limit}


@router.get("/inbox/{delivery_id}/package", response_model=RecordPackage)
def download_delivery(delivery_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_mode("server")
    row = recipient_delivery(db, str(delivery_id), user.id)
    return package_response(RecordPackage.model_validate_json(row.package_json))


@router.post("/inbox/{delivery_id}/ack")
def acknowledge_delivery(delivery_id: UUID, payload: AcknowledgeRequest,
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_mode("server")
    row = recipient_delivery(db, str(delivery_id), user.id, lock=True)
    same_package(row, payload.package_sha256)
    if row.status != "received":
        row.status = "received"
        row.received_at = datetime.utcnow()
        audit(db, user, "history.receive", delivery_id=row.id)
        db.commit()
    return delivery_info(row)


@router.post("/imports")
def import_delivery(payload: ImportRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_mode("local")
    digest = package_sha256(payload.package)
    if digest != payload.package_sha256:
        raise HTTPException(422, "传输包校验值不一致")
    delivery_id = str(payload.delivery_id)
    query = select(HistoryImport).where(HistoryImport.user_id == user.id, HistoryImport.delivery_id == delivery_id)

    def info(row, duplicate):
        return {"id": row.id, "delivery_id": row.delivery_id, "project_id": row.project_id,
                "package_sha256": row.package_sha256, "duplicate": duplicate}

    existing = db.scalar(query)
    if existing:
        same_package(existing, digest)
        if db.get(Project, existing.project_id) is None:
            raise HTTPException(409, "此记录已导入但本地项目已删除")
        return info(existing, True)
    try:
        row = HistoryImport(user_id=user.id, delivery_id=delivery_id, package_sha256=digest, project_id="pending")
        db.add(row)
        db.flush()
        imported = import_record_package(db, payload.package, f"delivery:{user.id}:{delivery_id}")
        row.project_id = imported["project_id"]
        audit(db, user, "history.import", delivery_id=delivery_id, project_id=row.project_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(query)
        if existing:
            same_package(existing, digest)
            return info(existing, True)
        raise HTTPException(409, "本地导入记录存在编号冲突")
    except Exception:
        db.rollback()
        raise
    return info(row, False)
