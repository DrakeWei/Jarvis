from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
import app.models  # noqa: F401


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, future=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_session_columns()
    _migrate_tool_execution_columns()


def create_session():
    return SessionLocal()


def _migrate_session_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {column["name"] for column in inspector.get_columns("sessions")}
    except Exception:
        return
    if "hidden" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE sessions ADD COLUMN hidden BOOLEAN NOT NULL DEFAULT 0"))


def _migrate_tool_execution_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {column["name"] for column in inspector.get_columns("tool_executions")}
    except Exception:
        return

    statements: list[str] = []
    if "tool_source" not in columns:
        statements.append("ALTER TABLE tool_executions ADD COLUMN tool_source VARCHAR(20) NOT NULL DEFAULT 'local'")
    if "server_name" not in columns:
        statements.append("ALTER TABLE tool_executions ADD COLUMN server_name VARCHAR(80)")
    if "latency_ms" not in columns:
        statements.append("ALTER TABLE tool_executions ADD COLUMN latency_ms INTEGER")
    if "remote_request_id" not in columns:
        statements.append("ALTER TABLE tool_executions ADD COLUMN remote_request_id VARCHAR(80)")

    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
