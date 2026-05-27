from __future__ import annotations

from dataclasses import dataclass
import json
import re

import app.services.task_service as task_service


CONTINUATION_MARKERS = {
    "继续",
    "继续吧",
    "接着",
    "接着做",
    "接着来",
    "继续做",
    "继续执行",
    "继续处理",
    "继续下去",
    "继续一下",
    "go on",
    "continue",
    "keep going",
    "carry on",
    "go ahead",
    "proceed",
    "resume",
}


REFERENTIAL_PREFIXES = (
    "那",
    "那你",
    "你之前",
    "这个",
    "它",
    "直接",
    "然后",
    "再",
    "还有",
    "另外",
    "顺便",
)


@dataclass(frozen=True)
class TaskRoutingDecision:
    decision: str
    target_task_id: int | None
    confidence: int
    reason_codes: list[str]
    rationale: dict[str, object]

    def rationale_json(self) -> str:
        return json.dumps(self.rationale, ensure_ascii=True)


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def _tokenize(value: str) -> list[str]:
    return [token for token in re.split(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", _normalize_text(value).lower()) if len(token) >= 2]


def _is_continuation_only_request(text: str) -> bool:
    normalized = text.strip().lower().strip(" \t\r\n.,!?;:，。！？；：")
    return normalized in CONTINUATION_MARKERS if normalized else False


def _has_referential_prefix(text: str) -> bool:
    normalized = _normalize_text(text)
    return any(normalized.startswith(prefix) for prefix in REFERENTIAL_PREFIXES)


def _task_text(task) -> str:
    parts = [
        getattr(task, "title", "") or "",
        getattr(task, "summary", "") or "",
        getattr(task, "subject", "") or "",
        getattr(task, "description", "") or "",
    ]
    return "\n".join(part for part in parts if part)


def _score_match(content: str, task) -> int:
    tokens = _tokenize(content)
    haystack = _normalize_text(_task_text(task)).lower()
    if not haystack:
        return 0
    substring_bonus = 120 if _normalize_text(content).lower() in haystack else 0
    token_bonus = sum(25 for token in tokens if token in haystack)
    title = str(getattr(task, "title", "") or "").lower()
    title_bonus = 40 if title and title in _normalize_text(content).lower() else 0
    return substring_bonus + token_bonus + title_bonus


def classify_message(session_id: str, content: str) -> TaskRoutingDecision:
    normalized = _normalize_text(content)
    active = task_service.get_active_task(session_id)
    suspended = task_service.list_session_tasks(session_id, statuses=(task_service.TASK_STATUS_SUSPENDED,), limit=8)

    if _is_continuation_only_request(normalized):
        if active is not None:
            return TaskRoutingDecision(
                decision="continue_active_task",
                target_task_id=active.id,
                confidence=95,
                reason_codes=["continuation_only", "active_task_exists"],
                rationale={"summary": "Continuation-only request routed to the current active task."},
            )
        if suspended:
            return TaskRoutingDecision(
                decision="resume_suspended_task",
                target_task_id=suspended[0].id,
                confidence=90,
                reason_codes=["continuation_only", "resume_latest_suspended"],
                rationale={"summary": "Continuation-only request resumed the most recent suspended task."},
            )

    if active is None:
        return TaskRoutingDecision(
            decision="create_new_task",
            target_task_id=None,
            confidence=90,
            reason_codes=["no_active_task"],
            rationale={"summary": "No active task existed, so a new task was created."},
        )

    if _has_referential_prefix(normalized):
        return TaskRoutingDecision(
            decision="continue_active_task",
            target_task_id=active.id,
            confidence=82,
            reason_codes=["referential_followup", "active_task_exists"],
            rationale={"summary": "Referential follow-up was routed to the current active task."},
        )

    active_score = _score_match(normalized, active)
    suspended_scores = [(task.id, _score_match(normalized, task)) for task in suspended]
    best_suspended_id = None
    best_suspended_score = -1
    if suspended_scores:
        best_suspended_id, best_suspended_score = max(suspended_scores, key=lambda item: (item[1], item[0]))

    if active_score >= max(50, best_suspended_score):
        return TaskRoutingDecision(
            decision="continue_active_task",
            target_task_id=active.id,
            confidence=min(95, 55 + active_score // 4),
            reason_codes=["semantic_match_active"],
            rationale={
                "summary": "The message matched the active task better than suspended tasks.",
                "active_score": active_score,
                "best_suspended_score": best_suspended_score,
            },
        )

    if best_suspended_id is not None and best_suspended_score >= max(60, active_score + 15):
        return TaskRoutingDecision(
            decision="resume_suspended_task",
            target_task_id=best_suspended_id,
            confidence=min(92, 55 + best_suspended_score // 4),
            reason_codes=["semantic_match_suspended"],
            rationale={
                "summary": "The message matched a suspended task better than the current active task.",
                "active_score": active_score,
                "best_suspended_score": best_suspended_score,
            },
        )

    return TaskRoutingDecision(
        decision="create_new_task",
        target_task_id=None,
        confidence=70,
        reason_codes=["no_safe_match"],
        rationale={
            "summary": "No safe task match was found, so a new task was created.",
            "active_score": active_score,
            "best_suspended_score": best_suspended_score,
        },
    )
