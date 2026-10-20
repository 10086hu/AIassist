"""JSON authentication API; passwords and bearer tokens never appear in logs."""
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import get_current_session, get_current_user, token_digest
from app.core.config import settings
from app.core.passwords import DUMMY_PASSWORD_HASH, hash_password, verify_password
from app.db.models import AuthRateLimit, AuthSession, User
from app.db.session import get_db


router = APIRouter()


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class RegisterRequest(Credentials):
    password: SecretStr = Field(min_length=12, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("password")
    @classmethod
    def meaningful_password(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError("密码不能全为空白字符")
        return value

    @field_validator("display_name", mode="before")
    @classmethod
    def trim_display_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def meaningful_password(cls, value):
        return RegisterRequest.meaningful_password(value)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    display_name: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None

    @field_validator("created_at", "last_login_at", mode="after")
    @classmethod
    def utc_dates(cls, value):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    expires_at: datetime
    user: UserResponse


def consume_limit(db: Session, scope: str, principal: str, limit: int, window: int) -> None:
    """Atomic fixed-window counters that also work across server restarts/workers."""
    now = int(time.time())
    boundary = (now // window + 1) * window
    key = hashlib.sha256(f"{scope}:{principal}:{now // window}".encode()).hexdigest()
    values = {"key": key, "attempts": 1, "expires_at": datetime.fromtimestamp(boundary, timezone.utc).replace(tzinfo=None)}
    dialect = db.get_bind().dialect.name
    if dialect in {"mysql", "mariadb"}:
        statement = mysql_insert(AuthRateLimit).values(**values).on_duplicate_key_update(attempts=AuthRateLimit.attempts + 1)
    else:
        insert = sqlite_insert if dialect == "sqlite" else pg_insert
        statement = insert(AuthRateLimit).values(**values).on_conflict_do_update(
            index_elements=[AuthRateLimit.key], set_={"attempts": AuthRateLimit.attempts + 1},
        )
    # Native upsert acquires the write lock directly, including the concurrent
    # first-request case; a SELECT then INSERT can deadlock on MySQL.
    db.execute(statement)
    attempts = db.scalar(select(AuthRateLimit.attempts).where(AuthRateLimit.key == key))
    db.execute(delete(AuthRateLimit).where(AuthRateLimit.expires_at <= datetime.fromtimestamp(now, timezone.utc).replace(tzinfo=None)))
    db.commit()
    if attempts > limit:
        raise HTTPException(429, "请求过于频繁，请稍后重试", headers={"Retry-After": str(boundary - now)})


def client_ip(request: Request) -> str:
    # Uvicorn's trusted proxy setting controls request.client; do not trust
    # arbitrary X-Forwarded-For headers in application code.
    return request.client.host if request.client else "unknown"


@router.post("/register", response_model=UserResponse, status_code=201)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    consume_limit(db, "register-ip", client_ip(request), 10, 3600)
    if db.scalar(select(User.id).where(User.username == payload.username)):
        raise HTTPException(409, "用户名已存在")
    user = User(
        username=payload.username, display_name=payload.display_name or payload.username,
        password_hash=hash_password(payload.password.get_secret_value()),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "用户名已存在") from exc
    db.refresh(user)
    return user


@router.post("/login", response_model=LoginResponse)
def login(payload: Credentials, request: Request, db: Session = Depends(get_db)):
    consume_limit(db, "login-ip", client_ip(request), 30, 300)
    consume_limit(db, "login-account", payload.username, 10, 300)
    user = db.scalar(select(User).where(User.username == payload.username).with_for_update())
    valid = verify_password(payload.password.get_secret_value(), user.password_hash if user else DUMMY_PASSWORD_HASH)
    if not valid or user is None or not user.is_active:
        raise HTTPException(401, "用户名或密码错误", headers={"WWW-Authenticate": "Bearer"})
    now = datetime.utcnow()
    expires_in = max(1, min(settings.auth_session_hours, 720)) * 3600
    expires = now + timedelta(seconds=expires_in)
    token = secrets.token_urlsafe(32)
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    db.add(AuthSession(token_hash=token_digest(token), user_id=user.id, expires_at=expires))
    user.last_login_at = now
    db.commit()
    return LoginResponse(
        access_token=token, expires_in=expires_in, expires_at=expires.replace(tzinfo=timezone.utc),
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/logout", status_code=204)
def logout(session: AuthSession = Depends(get_current_session), db: Session = Depends(get_db)):
    db.delete(session)
    db.commit()
    return Response(status_code=204)


@router.post("/logout-all", status_code=204)
def logout_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.commit()
    return Response(status_code=204)


@router.put("/password", status_code=204)
def change_password(
    payload: PasswordChangeRequest, request: Request,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    consume_limit(db, "password-ip", client_ip(request), 30, 300)
    consume_limit(db, "password-account", user.id, 10, 300)
    user = db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    if not verify_password(payload.current_password.get_secret_value(), user.password_hash):
        raise HTTPException(400, "当前密码错误")
    if verify_password(payload.new_password.get_secret_value(), user.password_hash):
        raise HTTPException(400, "新密码不能与当前密码相同")
    user.password_hash = hash_password(payload.new_password.get_secret_value())
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.commit()
    return Response(status_code=204)
