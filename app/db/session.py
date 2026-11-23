from pathlib import Path

from sqlalchemy import create_engine, inspect, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.models import Base, CheckRun, MemoryReviewJob


if settings.database_url.startswith("sqlite:///"):
    db_file = settings.database_url.replace("sqlite:///", "", 1)
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _upgrade_mysql_report_columns() -> None:
    """Widen legacy report fields without recreating tables or touching rows.

    create_all does not alter existing MySQL TEXT/BLOB columns.  The transfer
    package and issue ledger can exceed their old 64 KiB limit, so preserve
    content while upgrading only these known, application-owned columns.
    """
    if engine.dialect.name not in {"mysql", "mariadb"}:
        return
    targets = {
        "check_results": {"reference_data": "LONGTEXT"},
        "review_memory_revisions": {"snapshot_json": "LONGTEXT"},
        "source_artifacts": {"content": "LONGBLOB"},
        "history_uploads": {"package_json": "LONGTEXT"},
        "history_deliveries": {"package_json": "LONGTEXT"},
        "project_memory_profiles": {"context_json": "LONGTEXT"},
        "issue_feedback_events": {"reason": "LONGTEXT", "evidence": "LONGTEXT"},
        "memory_review_jobs": {"items_json": "LONGTEXT"},
    }
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, columns in targets.items():
        if table not in tables:
            continue
        actual = {column["name"]: column for column in inspector.get_columns(table)}
        for name, wanted in columns.items():
            column = actual.get(name)
            if column is None or str(column["type"]).upper() == wanted:
                continue
            nullable = "NULL" if column.get("nullable", True) else "NOT NULL"
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE `{table}` MODIFY COLUMN `{name}` {wanted} {nullable}"))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    # Existing users are ordinary users; administrator grants are explicit.
    user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    if "role" not in user_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'user'"))
    if "check_results" not in inspect(engine).get_table_names():
        return

    columns = {column["name"] for column in inspect(engine).get_columns("check_results")}
    if "check_run_id" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE check_results ADD COLUMN check_run_id VARCHAR(36)"))

    _upgrade_mysql_report_columns()

    # A process crash cannot execute the normal task finalizer. Recover those
    # batches on the next startup instead of exposing them as permanently running.
    with SessionLocal.begin() as db:
        db.execute(
            update(CheckRun)
            .where(CheckRun.status == "running")
            .values(status="interrupted")
        )
        # No worker survives process restart; the next explicit continue can
        # recover its last persisted item instead of leaving a running badge.
        db.execute(update(MemoryReviewJob).where(MemoryReviewJob.status == "running")
                   .values(status="paused", lease_token=None, lease_until=None))


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
