from __future__ import annotations

import app.services.memory_service as memory_service
import app.services.session_service as session_service


def build_turn_messages(session_id: str, *, recent_limit: int = 8) -> list[dict[str, object]]:
    return session_service.list_message_records(session_id, limit=recent_limit)


def build_session_memory_header(session_id: str) -> str:
    summary = memory_service.get_active_memory(session_id, memory_service.ROLLING_SUMMARY_KIND)
    goal = memory_service.get_active_memory(session_id, memory_service.GOAL_KIND)
    progress = memory_service.get_active_memory(session_id, memory_service.PROGRESS_KIND)
    constraint = memory_service.get_active_memory(session_id, memory_service.CONSTRAINT_KIND)
    open_question = memory_service.get_active_memory(session_id, memory_service.OPEN_QUESTION_KIND)
    decisions = memory_service.list_active_memories(session_id, memory_service.DECISION_KIND, limit=2)
    artifacts = memory_service.list_active_memories(session_id, memory_service.ARTIFACT_KIND, limit=2)
    if not summary and not goal and not progress and not constraint and not open_question and not decisions and not artifacts:
        return ""
    lines = ["Session memory:"]
    if summary:
        lines.append(memory_service.summarize_for_prompt(summary, 420))
    if goal:
        lines.append(f"Active goal: {memory_service.summarize_for_prompt(goal, 260)}")
    if constraint:
        lines.append(f"Active constraint: {memory_service.summarize_for_prompt(constraint, 220)}")
    if progress:
        lines.append(f"Latest progress: {memory_service.summarize_for_prompt(progress, 240)}")
    if open_question:
        lines.append(f"Open question: {memory_service.summarize_for_prompt(open_question, 220)}")
    if decisions:
        lines.append("Recent decisions:")
        lines.extend(f"- {memory_service.summarize_for_prompt(decision, 200)}" for decision in decisions)
    if artifacts:
        lines.append("Recent artifacts:")
        lines.extend(f"- {memory_service.summarize_for_prompt(artifact, 180)}" for artifact in artifacts)
    return "\n".join(lines)
