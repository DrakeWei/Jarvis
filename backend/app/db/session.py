from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core import workspace as workspace_utils
from app.db.base import Base
import app.models  # noqa: F401


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, future=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_session_columns()
    _migrate_session_asset_columns()
    _migrate_message_asset_columns()
    _migrate_asset_chunk_columns()
    _migrate_turn_columns()
    _migrate_approval_columns()
    _migrate_tool_execution_columns()


def create_session():
    return SessionLocal()


def _migrate_session_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {column["name"] for column in inspector.get_columns("sessions")}
    except Exception:
        return
    statements: list[str] = []
    if "hidden" not in columns:
        statements.append("ALTER TABLE sessions ADD COLUMN hidden BOOLEAN NOT NULL DEFAULT 0")
    if "workspace_mode" not in columns:
        statements.append("ALTER TABLE sessions ADD COLUMN workspace_mode VARCHAR(20) NOT NULL DEFAULT 'bound'")
    if "canonical_workspace_path" not in columns:
        statements.append("ALTER TABLE sessions ADD COLUMN canonical_workspace_path TEXT")
    if "workspace_fingerprint" not in columns:
        statements.append("ALTER TABLE sessions ADD COLUMN workspace_fingerprint VARCHAR(40)")
    if "workspace_label" not in columns:
        statements.append("ALTER TABLE sessions ADD COLUMN workspace_label VARCHAR(160)")
    if "status" not in columns:
        statements.append("ALTER TABLE sessions ADD COLUMN status VARCHAR(40) NOT NULL DEFAULT 'idle'")

    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))

    default_workspace = workspace_utils.normalize_workspace_path(settings.project_root)
    default_path = default_workspace.as_posix()
    default_label = workspace_utils.workspace_label(default_workspace)
    default_fingerprint = workspace_utils.workspace_fingerprint(default_workspace)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE sessions
                SET workspace_mode = COALESCE(workspace_mode, 'bound'),
                    canonical_workspace_path = COALESCE(canonical_workspace_path, :path),
                    workspace_fingerprint = COALESCE(workspace_fingerprint, :fingerprint),
                    workspace_label = COALESCE(workspace_label, :label),
                    status = COALESCE(status, 'idle')
                """
            ),
            {
                "path": default_path,
                "fingerprint": default_fingerprint,
                "label": default_label,
            },
        )


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


def _migrate_session_asset_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {column["name"] for column in inspector.get_columns("session_assets")}
    except Exception:
        return

    statements: list[str] = []
    if "preview_path" not in columns:
        statements.append("ALTER TABLE session_assets ADD COLUMN preview_path TEXT")
    if "status" not in columns:
        statements.append("ALTER TABLE session_assets ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'uploaded'")
    if "error_message" not in columns:
        statements.append("ALTER TABLE session_assets ADD COLUMN error_message TEXT")
    if "hidden" not in columns:
        statements.append("ALTER TABLE session_assets ADD COLUMN hidden BOOLEAN NOT NULL DEFAULT 0")
    if "updated_at" not in columns:
        statements.append("ALTER TABLE session_assets ADD COLUMN updated_at DATETIME")
    if "sha256" not in columns:
        statements.append("ALTER TABLE session_assets ADD COLUMN sha256 VARCHAR(64) NOT NULL DEFAULT ''")
    if "size_bytes" not in columns:
        statements.append("ALTER TABLE session_assets ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0")

    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
            connection.execute(
                text(
                    """
                    UPDATE session_assets
                    SET status = COALESCE(status, 'uploaded'),
                        hidden = COALESCE(hidden, 0),
                        updated_at = COALESCE(updated_at, created_at),
                        sha256 = COALESCE(sha256, ''),
                        size_bytes = COALESCE(size_bytes, 0)
                    """
                )
            )


def _migrate_message_asset_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {column["name"] for column in inspector.get_columns("message_assets")}
    except Exception:
        return

    statements: list[str] = []
    if "created_at" not in columns:
        statements.append("ALTER TABLE message_assets ADD COLUMN created_at DATETIME")

    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))


def _migrate_asset_chunk_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {column["name"] for column in inspector.get_columns("asset_chunks")}
    except Exception:
        return

    statements: list[str] = []
    if "page_number" not in columns:
        statements.append("ALTER TABLE asset_chunks ADD COLUMN page_number INTEGER")
    if "sheet_name" not in columns:
        statements.append("ALTER TABLE asset_chunks ADD COLUMN sheet_name VARCHAR(160)")
    if "slide_number" not in columns:
        statements.append("ALTER TABLE asset_chunks ADD COLUMN slide_number INTEGER")
    if "section_path" not in columns:
        statements.append("ALTER TABLE asset_chunks ADD COLUMN section_path TEXT")
    if "summary" not in columns:
        statements.append("ALTER TABLE asset_chunks ADD COLUMN summary TEXT")
    if "char_count" not in columns:
        statements.append("ALTER TABLE asset_chunks ADD COLUMN char_count INTEGER NOT NULL DEFAULT 0")
    if "created_at" not in columns:
        statements.append("ALTER TABLE asset_chunks ADD COLUMN created_at DATETIME")

    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
            connection.execute(
                text(
                    """
                    UPDATE asset_chunks
                    SET char_count = COALESCE(char_count, LENGTH(COALESCE(content, '')))
                    """
                )
            )


def _migrate_turn_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {column["name"] for column in inspector.get_columns("turns")}
    except Exception:
        return

    statements: list[str] = []
    if "user_message_id" not in columns:
        statements.append("ALTER TABLE turns ADD COLUMN user_message_id INTEGER")
    if "workspace_path" not in columns:
        statements.append("ALTER TABLE turns ADD COLUMN workspace_path TEXT")
    if "workspace_fingerprint" not in columns:
        statements.append("ALTER TABLE turns ADD COLUMN workspace_fingerprint VARCHAR(40)")
    if "updated_at" not in columns:
        statements.append("ALTER TABLE turns ADD COLUMN updated_at DATETIME")
    if "last_checkpoint_seq" not in columns:
        statements.append("ALTER TABLE turns ADD COLUMN last_checkpoint_seq INTEGER NOT NULL DEFAULT 0")
    if "resume_hint" not in columns:
        statements.append("ALTER TABLE turns ADD COLUMN resume_hint TEXT")
    if "error_summary" not in columns:
        statements.append("ALTER TABLE turns ADD COLUMN error_summary TEXT")

    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
            connection.execute(text("UPDATE turns SET updated_at = COALESCE(updated_at, started_at)"))
            connection.execute(text("UPDATE turns SET status = COALESCE(status, 'queued')"))


def _migrate_approval_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {column["name"] for column in inspector.get_columns("approvals")}
    except Exception:
        return

    statements: list[str] = []
    if "turn_id" not in columns:
        statements.append("ALTER TABLE approvals ADD COLUMN turn_id INTEGER")
    if "checkpoint_id" not in columns:
        statements.append("ALTER TABLE approvals ADD COLUMN checkpoint_id INTEGER")

    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
