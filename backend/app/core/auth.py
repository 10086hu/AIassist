"""Database-backed bearer sessions, shared by all business routers."""
from datetime import datetime
import hashlib
import re

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuthSession, User
from app.db.session import get_db


bearer = HTTPBearer(auto_error=False, scheme_name="BearerAuth")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def unauthorized() -> HTTPException:
    return HTTPException(401, "请登录，或重新登录以获取有效令牌", headers={"WWW-Authenticate": "Bearer"})


def get_current_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> AuthSession:
    if credentials is None or re.fullmatch(r"[A-Za-z0-9_-]{43}", credentials.credentials) is None:
        raise unauthorized()
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(credentials.credentials)))
    if session is None or session.expires_at <= datetime.utcnow() or not session.user.is_active:
        raise unauthorized()
    return session


def get_current_user(session: AuthSession = Depends(get_current_session)) -> User:
    return session.user
