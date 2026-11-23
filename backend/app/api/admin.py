"""Administrator account directory used when addressing history deliveries."""
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.auth import UserResponse
from app.core.auth import get_current_admin
from app.db.models import AuditLog, AuthSession, User
from app.db.session import get_db


router = APIRouter(dependencies=[Depends(get_current_admin)])


class UserPage(BaseModel):
    items: list[UserResponse]
    total: int
    limit: int
    offset: int


class UserStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: StrictBool


@router.get("/users", response_model=UserPage)
def list_users(
    q: str = Query(default="", max_length=64),
    role: Literal["user", "admin"] | None = None,
    is_active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    filters = []
    if q.strip():
        filters.append(User.username.contains(q.strip().lower(), autoescape=True))
    if role is not None:
        filters.append(User.role == role)
    if is_active is not None:
        filters.append(User.is_active == is_active)
    total = db.scalar(select(func.count()).select_from(User).where(*filters)) or 0
    users = db.scalars(select(User).where(*filters).order_by(User.username, User.id).limit(limit).offset(offset)).all()
    return {"items": users, "total": total, "limit": limit, "offset": offset}


@router.patch("/users/{user_id}", response_model=UserResponse)
def set_user_status(
    user_id: str,
    payload: UserStatusUpdate,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    target = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if target is None:
        raise HTTPException(404, "用户不存在")
    if target.id == admin.id and not payload.is_active:
        raise HTTPException(409, "管理员不能停用自己的账号")
    if target.role == "admin" and not payload.is_active:
        # Two already-authorized administrators must not disable one another in
        # concurrent requests and leave the server with no active administrator.
        # The operator CLI serializes role changes and protects the last admin.
        raise HTTPException(409, "请先通过运维命令将该账号降为普通用户，再停用账号")
    if target.is_active != payload.is_active:
        target.is_active = payload.is_active
        if not payload.is_active:
            db.execute(delete(AuthSession).where(AuthSession.user_id == target.id))
        db.add(AuditLog(action="set_user_status", operator=admin.id, module="admin", detail=json.dumps({
            "user_id": target.id, "is_active": payload.is_active,
        })))
        db.commit()
        db.refresh(target)
    return target
