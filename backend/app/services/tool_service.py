from sqlalchemy import select

from app.db.session import create_session
from app.models import ToolExecutionRecord
from app.schemas.tools import ToolExecutionSummary


def list_tool_executions(session_id: str | None = None) -> list[ToolExecutionSummary]:
    with create_session() as db:
        stmt = select(ToolExecutionRecord).order_by(
            ToolExecutionRecord.created_at.desc(),
            ToolExecutionRecord.id.desc(),
        )
        if session_id:
            stmt = stmt.where(ToolExecutionRecord.session_id == session_id)
        rows = db.scalars(stmt).all()
        return [
            ToolExecutionSummary(
                id=row.id,
                session_id=row.session_id,
                tool_name=row.tool_name,
                status=row.status,
                input_json=row.input_json,
                output_text=row.output_text,
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ]


def create_tool_execution(
    session_id: str,
    tool_name: str,
    status: str,
    input_json: str | None,
    output_text: str | None,
) -> ToolExecutionSummary:
    with create_session() as db:
        row = ToolExecutionRecord(
            session_id=session_id,
            tool_name=tool_name,
            status=status,
            input_json=input_json,
            output_text=output_text,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return ToolExecutionSummary(
            id=row.id,
            session_id=row.session_id,
            tool_name=row.tool_name,
            status=row.status,
            input_json=row.input_json,
            output_text=row.output_text,
            created_at=row.created_at.isoformat(),
        )
