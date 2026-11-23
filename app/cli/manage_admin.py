"""Explicitly grant/revoke administrator access; never promote public signups.

Usage after the backend has applied its schema migration:
    python -m app.cli.manage_admin USERNAME --role admin
    python -m app.cli.manage_admin USERNAME --role user

Use the same DATABASE_URL/environment as the intended backend. This command is
also available inside the backend container. No password is required or printed.
"""
import argparse
import json

from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, AuthSession, User
from app.db.session import SessionLocal, engine


def set_role(db: Session, username: str, role: str) -> User:
    if role not in {"admin", "user"}:
        raise ValueError("角色必须是 admin 或 user")
    # Serialize concurrent role changes affecting the administrator group.
    admins = db.scalars(select(User).where(User.role == "admin", User.is_active.is_(True)).order_by(User.id).with_for_update()).all()
    target = db.scalar(select(User).where(User.username == username.strip().lower()).with_for_update())
    if target is None:
        raise ValueError("用户不存在，请先通过注册接口创建账号")
    if target.role == role:
        return target
    if target.role == "admin" and target.is_active and role == "user" and len(admins) <= 1:
        raise ValueError("不能降级最后一个启用中的管理员")
    previous = target.role
    target.role = role
    db.execute(delete(AuthSession).where(AuthSession.user_id == target.id))
    db.add(AuditLog(action="set_user_role", operator="local-cli", module="admin", detail=json.dumps({
        "user_id": target.id, "previous_role": previous, "role": role,
    })))
    db.flush()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="显式设置已注册用户的管理员角色（使用当前后端数据库配置）")
    parser.add_argument("username")
    parser.add_argument("--role", required=True, choices=("admin", "user"))
    args = parser.parse_args()
    if not inspect(engine).has_table("users") or "role" not in {c["name"] for c in inspect(engine).get_columns("users")}:
        parser.exit(2, "数据库尚未升级，请先启动新版后端完成迁移。\n")
    try:
        with SessionLocal.begin() as db:
            user = set_role(db, args.username, args.role)
            result = {"id": user.id, "username": user.username, "role": user.role}
    except ValueError as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
