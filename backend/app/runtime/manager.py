import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.core import workspace as workspace_utils
from app.mcp import ToolDefinition, ToolExecutionResult, tool_registry
from app.providers import ProviderConfigError, ProviderRequestError, TextBlock, ToolUseBlock, create_client
from app.schemas.background_jobs import BackgroundJobSummary
from app.schemas.assets import SessionAssetSummary
from app.schemas.events import MessageCreate, SessionCreate, SessionSummary, TimelineEvent
from app.schemas.leases import ExecutionLeaseSummary
from app.schemas.observability import RuntimeObservabilitySummary
from app.schemas.session_state import SessionStateSummary
from app.schemas.subagents import SubagentRunCreate
from app.schemas.tasks import TaskCreate
from app.schemas.teammates import TeammateCreate
from app.schemas.workspace import WorkspaceResolveSummary
import app.services.context_assembler as context_assembler
import app.services.conversation_search_service as conversation_search_service
import app.services.checkpoint_service as checkpoint_service
import app.services.asset_ingestion_service as asset_ingestion_service
import app.services.image_generation_service as image_generation_service
import app.services.memory_service as memory_service
import app.services.memory_search_service as memory_search_service
import app.services.turn_service as turn_service
import app.services.worktree_service as worktree_service
from app.services import approval_service, asset_service, background_job_service, ingestion_job_service, lease_service, session_service, subagent_service, teammate_service, tool_service
from app.tools.broker import ToolBroker, broker


@dataclass
class SessionTurn:
    turn_id: int
    task: asyncio.Task[None]
    cancel_event: asyncio.Event
    partial_text: str = ""


@dataclass(frozen=True)
class AgentReply:
    text: str
    asset_ids: list[str]


class TurnCancelled(RuntimeError):
    pass


class EventBroker:
    def __init__(self) -> None:
        self.backend_name = "local"
        self._queues: dict[str, list[asyncio.Queue[TimelineEvent]]] = defaultdict(list)
        self.dropped_events_total = 0

    async def publish(self, event: TimelineEvent) -> None:
        for queue in list(self._queues[event.session_id]):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    self.dropped_events_total += 1
                    continue
                self.dropped_events_total += 1

    def subscribe(self, session_id: str) -> asyncio.Queue[TimelineEvent]:
        queue: asyncio.Queue[TimelineEvent] = asyncio.Queue(maxsize=max(1, settings.jarvis_ephemeral_event_queue_size))
        self._queues[session_id].append(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[TimelineEvent]) -> None:
        queues = self._queues.get(session_id, [])
        if queue in queues:
            queues.remove(queue)

    def total_subscribers(self) -> int:
        return sum(len(queues) for queues in self._queues.values())


def build_event_broker():
    if settings.jarvis_event_bus_backend != "redis" or not settings.jarvis_redis_url:
        return EventBroker()
    try:
        from redis import asyncio as redis_asyncio
        from app.realtime_redis_broker import RedisEventBroker

        client = redis_asyncio.from_url(settings.jarvis_redis_url, decode_responses=True)
        return RedisEventBroker(client)
    except Exception:
        return EventBroker()


class RuntimeManager:
    PLAN_MODE_ALLOWED_TOOLS = frozenset(
        {
            "get_session_git_state",
            "list_files",
            "read_file",
            "list_skills",
            "load_skill",
            "memory_search",
            "conversation_search",
            "list_session_assets",
            "read_asset_summary",
            "search_asset_chunks",
            "read_asset_chunk",
        }
    )

    def __init__(self) -> None:
        self.instance_id = f"runtime-{uuid4()}"
        self.events = build_event_broker()
        self.background_tasks: set[asyncio.Task[None]] = set()
        self.session_turns: dict[str, SessionTurn] = {}
        self.dispatch_signal = asyncio.Event()
        self.dispatcher_task: asyncio.Task[None] | None = None
        self.scheduled_background_job_ids: set[int] = set()
        self.scheduled_ingestion_job_ids: set[int] = set()
        self._last_housekeeping_at = 0.0

    def restore_state(self) -> None:
        for recovered in turn_service.recover_orphaned_running_turns():
            memory_service.refresh_rolling_summary(recovered.session_id, recovered.id)
            session_service.create_event_record(
                TimelineEvent(
                    session_id=recovered.session_id,
                    type="turn.interrupted",
                    content=recovered.resume_hint or "Runtime restarted while a turn was in progress.",
                )
            )
        turn_service.refresh_waiting_approval_sessions()

    def list_sessions(self) -> list[SessionSummary]:
        return session_service.list_sessions()

    def resolve_workspace(self, content: str) -> WorkspaceResolveSummary | None:
        path = workspace_utils.resolve_workspace_from_text(content, settings.project_root)
        if path is None:
            return None
        return WorkspaceResolveSummary(
            workspace_path=path.as_posix(),
            workspace_label=workspace_utils.workspace_label(path),
            workspace_fingerprint=workspace_utils.workspace_fingerprint(path),
        )

    def get_session_state(self, session_id: str) -> SessionStateSummary | None:
        session = session_service.get_session(session_id)
        if session is None or session.hidden:
            return None
        sessions = {item.session_id: item for item in self.list_sessions()}
        summary = sessions.get(session_id)
        if summary is None:
            return None
        turns = self.list_turns(session_id)
        active_turn = next((turn for turn in turns if turn.status in {"queued", "running"}), None)
        latest_interrupted_turn = next((turn for turn in turns if turn.status == "interrupted"), None)
        latest_waiting_turn = next((turn for turn in turns if turn.status == "waiting_approval"), None)
        if latest_interrupted_turn and checkpoint_service.latest_resumable_checkpoint_context(latest_interrupted_turn.id):
            latest_interrupted_turn = latest_interrupted_turn.model_copy(update={"resumable": True})
        rolling_summary = memory_service.get_active_memory(session_id)
        return SessionStateSummary(
            session=summary,
            active_turn=active_turn,
            latest_interrupted_turn=latest_interrupted_turn,
            latest_waiting_approval_turn=latest_waiting_turn,
            rolling_summary=rolling_summary,
        )

    async def rename_session(self, session_id: str, title: str) -> SessionSummary | None:
        updated = session_service.update_session_title(session_id, title)
        if updated:
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="session.renamed",
                    content=title,
                )
            )
        return updated

    async def soft_delete_session(self, session_id: str) -> bool:
        deleted = session_service.soft_delete_session(session_id)
        if deleted:
            self.session_turns.pop(session_id, None)
        return deleted

    def session_exists(self, session_id: str) -> bool:
        return session_service.get_session(session_id) is not None

    def list_timeline(self, session_id: str) -> list[TimelineEvent]:
        return session_service.list_event_records(session_id)

    def list_timeline_since(self, session_id: str, *, after_id: int | None = None, limit: int | None = None) -> list[TimelineEvent]:
        return session_service.list_event_records(session_id, after_id=after_id, limit=limit)

    def list_tool_executions(self, session_id: str | None = None):
        return tool_service.list_tool_executions(session_id)

    def list_background_jobs(
        self,
        *,
        session_id: str | None = None,
        job_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[BackgroundJobSummary]:
        return background_job_service.list_job_summaries(
            session_id=session_id,
            job_type=job_type,
            status=status,
            limit=limit,
        )

    def get_background_job(self, job_id: int) -> BackgroundJobSummary | None:
        return background_job_service.get_job_summary(job_id)

    def list_execution_leases(self, *, scope_type: str | None = None, status: str | None = None) -> list[ExecutionLeaseSummary]:
        return lease_service.list_leases(scope_type=scope_type, status=status)

    def force_release_execution_lease(self, lease_id: int) -> ExecutionLeaseSummary | None:
        return lease_service.force_release(lease_id)

    def observability_summary(self) -> RuntimeObservabilitySummary:
        sessions = self.list_sessions()
        turns = self.list_turns()
        turns_by_status: dict[str, int] = {}
        for turn in turns:
            turns_by_status[turn.status] = turns_by_status.get(turn.status, 0) + 1
        background_jobs_by_status = background_job_service.status_counts("turn_execution")
        ingestion_jobs_by_status = ingestion_job_service.status_counts()
        return RuntimeObservabilitySummary(
            runtime_role=settings.jarvis_runtime_role,
            instance_id=self.instance_id,
            configured_event_bus_backend=settings.jarvis_event_bus_backend,
            effective_event_bus_backend=getattr(self.events, "backend_name", "local"),
            dispatcher_running=self.dispatcher_running(),
            total_sessions=len(sessions),
            total_ws_subscribers=self.events.total_subscribers(),
            ephemeral_events_dropped=self.events.dropped_events_total,
            scheduled_background_jobs=len(self.scheduled_background_job_ids),
            scheduled_ingestion_jobs=len(self.scheduled_ingestion_job_ids),
            background_jobs_by_status=background_jobs_by_status,
            ingestion_jobs_by_status=ingestion_jobs_by_status,
            retrying_turn_jobs=background_job_service.retrying_count("turn_execution"),
            retrying_ingestion_jobs=ingestion_job_service.retrying_count(),
            oldest_queued_turn_job_age_seconds=background_job_service.oldest_queued_age_seconds("turn_execution"),
            oldest_queued_ingestion_job_age_seconds=ingestion_job_service.oldest_queued_age_seconds(),
            oldest_running_turn_job_age_seconds=background_job_service.oldest_running_age_seconds("turn_execution"),
            oldest_running_ingestion_job_age_seconds=ingestion_job_service.oldest_running_age_seconds(),
            oldest_running_turn_age_seconds=turn_service.oldest_running_turn_age_seconds(),
            turns_by_status=turns_by_status,
        )

    async def retry_background_job(self, job_id: int) -> BackgroundJobSummary | None:
        retried = background_job_service.retry_job_now(job_id)
        if retried is None:
            return None
        self._ensure_dispatcher_started()
        self._signal_dispatcher()
        summary = background_job_service.get_job_summary(job_id)
        if summary and summary.session_id:
            await self.publish(
                TimelineEvent(
                    session_id=summary.session_id,
                    type="background_job.retried",
                    content=f"Retried background job #{summary.id} ({summary.job_type}).",
                )
            )
        return summary

    async def cancel_background_job(self, job_id: int) -> BackgroundJobSummary | None:
        existing = background_job_service.get_job(job_id)
        if existing is None:
            return None
        summary_before = background_job_service.get_job_summary(job_id)
        payload = background_job_service.payload_dict(existing)
        cancelled = background_job_service.cancel_job(job_id)
        if cancelled is None:
            return None
        if cancelled.job_type == "turn_execution":
            turn_id = payload.get("turn_id")
            session_id = str(payload.get("session_id") or cancelled.session_id or "").strip()
            if isinstance(turn_id, int) and session_id:
                turn_service.request_turn_cancel(turn_id)
                local_turn = self.session_turns.get(session_id)
                if local_turn and local_turn.turn_id == turn_id:
                    local_turn.cancel_event.set()
        summary = background_job_service.get_job_summary(job_id)
        if summary and summary.session_id:
            await self.publish(
                TimelineEvent(
                    session_id=summary.session_id,
                    type="background_job.cancelled",
                    content=f"Cancelled background job #{summary.id} ({summary.job_type}).",
                )
            )
        return summary or summary_before

    def list_memory(self, session_id: str):
        return memory_service.list_memory(session_id)

    def list_assets(self, session_id: str) -> list[SessionAssetSummary]:
        return asset_service.list_assets(session_id)

    def get_asset(self, session_id: str, asset_id: str) -> SessionAssetSummary | None:
        return asset_service.get_asset(asset_id, session_id=session_id)

    def list_approvals(self, session_id: str | None = None):
        return approval_service.list_approvals(session_id)

    def list_teammates(self, session_id: str | None = None):
        return teammate_service.list_teammates(session_id)

    def list_teammate_messages(self, agent_id: int):
        return teammate_service.list_teammate_messages(agent_id)

    def list_subagents(self, session_id: str | None = None):
        return subagent_service.list_subagents(session_id)

    def list_turns(self, session_id: str | None = None):
        return turn_service.list_turns(session_id)

    def get_turn(self, turn_id: int):
        return turn_service.get_turn(turn_id)

    async def resume_turn(self, turn_id: int) -> bool:
        turn = turn_service.get_turn(turn_id)
        if turn is None or turn.status != "interrupted":
            return False
        existing = self.session_turns.get(turn.session_id)
        if existing and not existing.task.done():
            return False
        checkpoint = checkpoint_service.latest_resumable_checkpoint_context(turn_id)
        if checkpoint is None:
            return False
        checkpoint_row, context = checkpoint
        self._enqueue_turn_resume_job(turn.session_id, turn_id, context, checkpoint_row.phase)
        return True

    async def decide_approval(self, approval_id: int, approve: bool, feedback: str = ""):
        if not lease_service.try_acquire("approval", str(approval_id), self.instance_id):
            return None
        try:
            pending_context = approval_service.get_pending_runtime_context(approval_id)
            decision, changed = approval_service.apply_approval_decision(approval_id, approve=approve, feedback=feedback)
            if not decision:
                return None
            if not changed:
                return decision
            pending_session_id = pending_context[0] if pending_context else None
            pending_payload = pending_context[1] if pending_context else None
            turn_id = pending_payload.get("turn_id") if isinstance(pending_payload, dict) else None
            summary_session_id = decision.session_id or pending_session_id
            if decision.approval_type == "plan_execution":
                if decision.session_id:
                    await self.publish(
                        TimelineEvent(
                            session_id=decision.session_id,
                            type="approval.resolved",
                            content=f"Approval #{approval_id} {decision.status}.",
                        )
                    )
                if approve and pending_session_id and isinstance(pending_payload, dict):
                    await self._start_plan_execution_from_approval(pending_session_id, pending_payload)
                return decision
            if isinstance(turn_id, int):
                if approve:
                    turn_service.update_turn_status(turn_id, "queued", resume_hint="Bash approval granted; queued to resume.")
                else:
                    turn_service.update_turn_status(turn_id, "interrupted", resume_hint="Bash approval was rejected.")
                    if summary_session_id:
                        memory_service.remember_constraint(
                            summary_session_id,
                            "Shell commands require explicit approval. The last bash request was rejected.",
                            source_turn_id=turn_id,
                        )
                    if summary_session_id:
                        memory_service.refresh_rolling_summary(summary_session_id, turn_id)
            if decision.session_id:
                await self.publish(
                    TimelineEvent(
                        session_id=decision.session_id,
                        type="approval.resolved",
                        content=f"Approval #{approval_id} {decision.status}.",
                    )
                )
            if approve and pending_session_id and pending_payload:
                self._enqueue_turn_resume_job(
                    pending_session_id,
                    turn_id if isinstance(turn_id, int) else None,
                    pending_payload,
                    "waiting_approval",
                )
            return decision
        finally:
            lease_service.release("approval", str(approval_id), self.instance_id)

    async def create_teammate(self, payload: TeammateCreate):
        teammate = teammate_service.create_teammate(payload)
        await self.publish(
            TimelineEvent(
                session_id=payload.session_id,
                type="teammate.created",
                content=f"Teammate '{payload.name}' ({payload.role}) joined the session.",
            )
        )
        return teammate

    async def _start_plan_execution_from_approval(self, session_id: str, context: dict[str, object]) -> bool:
        original_request = str(context.get("original_request") or "").strip()
        approved_plan = str(context.get("approved_plan") or "").strip()
        source_turn_id = context.get("source_turn_id")
        if not original_request or not approved_plan:
            return False
        self._ensure_dispatcher_started()
        workspace = self._session_workspace(session_id)
        turn = turn_service.create_turn(
            session_id=session_id,
            user_message_id=None,
            workspace_path=workspace.as_posix(),
            workspace_fingerprint=workspace_utils.workspace_fingerprint(workspace),
            execution_mode="normal",
        )
        background_job_service.create_job(
            session_id=session_id,
            job_type="turn_execution",
            payload={
                "session_id": session_id,
                "turn_id": turn.id,
                "content": original_request,
                "execution_mode": "normal",
                "plan_context": {
                    "approved_plan": approved_plan,
                    "original_request": original_request,
                    "source_turn_id": source_turn_id,
                },
            },
            command=f"turn_execution:{turn.id}",
        )
        self._signal_dispatcher()
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="plan.execution_queued",
                content=f"Approved plan queued for execution as turn #{turn.id}.",
            )
        )
        return True

    async def send_teammate_message(self, agent_id: int, content: str):
        teammate = teammate_service.get_teammate(agent_id)
        if not teammate:
            return None
        teammate_service.update_teammate_status(agent_id, "working")
        outbound = teammate_service.add_teammate_message(agent_id, "to_agent", content)
        if teammate.session_id:
            await self.publish(
                TimelineEvent(
                    session_id=teammate.session_id,
                    type="teammate.message",
                    content=f"Sent to {teammate.name}: {content}",
                )
            )
        reply_text = self._generate_teammate_reply(teammate.name, teammate.role, content)
        inbound = teammate_service.add_teammate_message(agent_id, "from_agent", reply_text)
        teammate_service.update_teammate_status(agent_id, "idle")
        if teammate.session_id:
            await self.publish(
                TimelineEvent(
                    session_id=teammate.session_id,
                    type="teammate.reply",
                    content=f"{teammate.name}: {reply_text}",
                )
            )
        return {"sent": outbound, "reply": inbound}

    async def run_subagent(self, payload: SubagentRunCreate):
        session_workspace = self._session_workspace(payload.session_id)
        isolation_mode = self._normalize_subagent_isolation_mode(payload.isolation_mode)
        subagent = subagent_service.create_subagent(
            payload.session_id,
            payload.name,
            base_workspace_path=session_workspace.as_posix(),
            isolation_mode=isolation_mode,
        )
        execution_workspace = session_workspace
        worktree_context: worktree_service.WorktreeExecutionContext | None = None

        if isolation_mode == "worktree":
            try:
                worktree_context = worktree_service.prepare_subagent_worktree(session_workspace, subagent.id, payload.name)
            except worktree_service.WorktreeIsolationError as exc:
                summary = f"Subagent could not start in worktree mode: {exc}"
                subagent_service.add_subagent_summary(subagent.id, summary)
                subagent_service.finish_subagent(
                    subagent.id,
                    status="failed",
                    execution_workspace_path=None,
                    cleanup_status="pending",
                    preserved_reason=exc.code,
                )
                await self.publish(
                    TimelineEvent(
                        session_id=payload.session_id,
                        type="subagent.summary",
                        content=f"{payload.name}: {summary}",
                    )
                )
                raise ValueError(summary) from exc

            execution_workspace = worktree_context.execution_workspace_path
            subagent = (
                subagent_service.update_subagent_execution(
                    subagent.id,
                    execution_workspace_path=execution_workspace.as_posix(),
                    git_branch=worktree_context.branch_name,
                    git_base_revision=worktree_context.base_revision,
                    cleanup_status="pending",
                )
                or subagent
            )
        else:
            subagent = (
                subagent_service.update_subagent_execution(
                    subagent.id,
                    execution_workspace_path=session_workspace.as_posix(),
                    cleanup_status="pending",
                )
                or subagent
            )

        await self.publish(
            TimelineEvent(
                session_id=payload.session_id,
                type="subagent.started",
                content=self._subagent_started_event_content(payload.name, subagent),
            )
        )

        try:
            summary = await self._run_subagent_task(payload.session_id, payload.prompt, workspace=execution_workspace)
        except Exception as exc:
            summary = f"Subagent failed: {exc}"
            cleanup = (
                worktree_service.finalize_subagent_worktree(worktree_context, run_failed=True)
                if worktree_context is not None
                else None
            )
            subagent_service.add_subagent_summary(subagent.id, summary)
            failed = subagent_service.finish_subagent(
                subagent.id,
                status="failed",
                execution_workspace_path=(cleanup.execution_workspace_path.as_posix() if cleanup else execution_workspace.as_posix()),
                git_branch=cleanup.branch_name if cleanup else subagent.git_branch,
                git_base_revision=cleanup.base_revision if cleanup else subagent.git_base_revision,
                cleanup_status=cleanup.cleanup_status if cleanup else "pending",
                preserved_reason=cleanup.preserved_reason if cleanup else None,
            )
            await self.publish(
                TimelineEvent(
                    session_id=payload.session_id,
                    type="subagent.summary",
                    content=self._subagent_summary_event_content(payload.name, summary, failed or subagent),
                )
            )
            return {"subagent": failed or subagent, "summary": summary}

        cleanup = worktree_service.finalize_subagent_worktree(worktree_context) if worktree_context is not None else None
        subagent_service.add_subagent_summary(subagent.id, summary)
        completed = subagent_service.finish_subagent(
            subagent.id,
            status="completed",
            execution_workspace_path=(cleanup.execution_workspace_path.as_posix() if cleanup else execution_workspace.as_posix()),
            git_branch=cleanup.branch_name if cleanup else subagent.git_branch,
            git_base_revision=cleanup.base_revision if cleanup else subagent.git_base_revision,
            cleanup_status=cleanup.cleanup_status if cleanup else "pending",
            preserved_reason=cleanup.preserved_reason if cleanup else None,
        )
        await self.publish(
            TimelineEvent(
                session_id=payload.session_id,
                type="subagent.summary",
                content=self._subagent_summary_event_content(payload.name, summary, completed or subagent),
            )
        )
        return {"subagent": completed or subagent, "summary": summary}

    async def _run_subagent_task(self, session_id: str, prompt: str, *, workspace: Path) -> str:
        workspace_mode = self._session_workspace_mode(session_id)
        messages: list[dict[str, object]] = [{"role": "user", "content": prompt}]
        reply = await self._continue_agent_loop(
            session_id,
            workspace,
            messages,
            asyncio.Event(),
            write_enabled=workspace_mode != "default",
            allow_subagent_tool=False,
            agent_kind="subagent",
            emit_stream_events=False,
        )
        return reply.text

    async def _queue_plan_execution_approval(
        self,
        *,
        session_id: str,
        turn_id: int,
        original_request: str,
        plan_text: str,
    ) -> None:
        approval = approval_service.create_approval(
            session_id=session_id,
            turn_id=turn_id,
            approval_type="plan_execution",
            prompt=f"Execute the approved plan from turn #{turn_id}?",
            context={
                "turn_id": turn_id,
                "source_turn_id": turn_id,
                "original_request": original_request,
                "approved_plan": plan_text,
            },
        )
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="approval.requested",
                content=f"Approval #{approval.id} requested for plan execution.",
            )
        )

    async def publish(self, event: TimelineEvent) -> TimelineEvent:
        stored = session_service.create_event_record(event)
        await self.events.publish(stored)
        return stored

    async def emit_ephemeral(self, event: TimelineEvent) -> TimelineEvent:
        stored = session_service.create_event_record(event, ephemeral=True)
        await self.events.publish(stored)
        return stored

    def _ensure_dispatcher_started(self) -> None:
        if self.dispatcher_task and not self.dispatcher_task.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self.dispatcher_task = asyncio.create_task(self._dispatch_loop())
        self.background_tasks.add(self.dispatcher_task)
        self.dispatcher_task.add_done_callback(self.background_tasks.discard)

    def dispatcher_running(self) -> bool:
        return bool(self.dispatcher_task and not self.dispatcher_task.done())

    def start_dispatcher(self) -> None:
        self._ensure_dispatcher_started()
        self._signal_dispatcher()

    async def stop_dispatcher(self) -> None:
        task = self.dispatcher_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _signal_dispatcher(self) -> None:
        self.dispatch_signal.set()

    async def _dispatch_loop(self) -> None:
        poll_seconds = max(0.1, settings.jarvis_job_dispatch_poll_seconds)
        while True:
            try:
                await asyncio.wait_for(self.dispatch_signal.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass
            self.dispatch_signal.clear()
            self._run_housekeeping_if_due()
            self._dispatch_recoverable_jobs()

    def _run_housekeeping_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_housekeeping_at < 60:
            return
        self._last_housekeeping_at = now
        session_service.purge_expired_ephemeral_events()
        background_job_service.purge_terminal_jobs()
        ingestion_job_service.purge_terminal_jobs()

    def _dispatch_recoverable_jobs(self) -> None:
        active_turn_sessions = {session_id for session_id, turn in self.session_turns.items() if not turn.task.done()}
        available_turn_slots = max(0, settings.jarvis_max_concurrent_turn_jobs - len(self.scheduled_background_job_ids))
        seen_turn_sessions = set(active_turn_sessions)
        if available_turn_slots > 0:
            for job in background_job_service.list_recoverable_jobs("turn_execution"):
                if available_turn_slots <= 0:
                    break
                if job.id in self.scheduled_background_job_ids:
                    continue
                if lease_service.is_active("background_job", str(job.id)):
                    continue
                if job.session_id and lease_service.is_active("session_turn_lane", job.session_id):
                    continue
                if job.session_id and job.session_id in seen_turn_sessions:
                    continue
                self._start_background_job(job.id)
                available_turn_slots -= 1
                if job.session_id:
                    seen_turn_sessions.add(job.session_id)

        available_ingestion_slots = max(0, settings.jarvis_max_concurrent_ingestion_jobs - len(self.scheduled_ingestion_job_ids))
        if available_ingestion_slots > 0:
            for job in ingestion_job_service.list_recoverable_jobs():
                if available_ingestion_slots <= 0:
                    break
                if job.id in self.scheduled_ingestion_job_ids:
                    continue
                if lease_service.is_active("asset_ingestion_job", str(job.id)):
                    continue
                self._start_asset_ingestion(job.session_id, job.id)
                available_ingestion_slots -= 1

    async def _lease_heartbeat_loop(
        self,
        scope_type: str,
        scope_key: str,
        stop_event: asyncio.Event,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        interval = max(1, settings.jarvis_execution_lease_heartbeat_seconds)
        while True:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                renewed = await asyncio.to_thread(
                    lease_service.renew,
                    scope_type,
                    scope_key,
                    self.instance_id,
                )
                if renewed:
                    continue
                if cancel_event is not None:
                    cancel_event.set()
                return

    def _should_cancel_turn(self, turn_id: int | None, cancel_event: asyncio.Event) -> bool:
        if cancel_event.is_set():
            return True
        if turn_id is None:
            return False
        if turn_service.is_cancel_requested(turn_id):
            cancel_event.set()
            return True
        return False

    def _start_background_turn(
        self,
        session_id: str,
        content: str,
        turn_id: int,
        *,
        execution_mode: str = "normal",
        plan_context: dict[str, object] | None = None,
    ) -> None:
        existing = self.session_turns.get(session_id)
        if existing and not existing.task.done():
            turn_service.update_turn_status(
                existing.turn_id,
                "cancelled",
                resume_hint="A newer user message replaced this unfinished turn.",
                completed=True,
            )
            existing.cancel_event.set()
        if not lease_service.try_acquire("turn", str(turn_id), self.instance_id):
            return
        cancel_event = asyncio.Event()
        task = asyncio.create_task(
            self._run_lead_turn(
                session_id,
                turn_id,
                content,
                cancel_event,
                execution_mode=execution_mode,
                plan_context=plan_context,
            )
        )
        self.session_turns[session_id] = SessionTurn(turn_id=turn_id, task=task, cancel_event=cancel_event)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    def _start_background_job(self, job_id: int) -> None:
        self.scheduled_background_job_ids.add(job_id)
        task = asyncio.create_task(self._run_background_job(job_id))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        task.add_done_callback(lambda _: self.scheduled_background_job_ids.discard(job_id))

    def _start_asset_ingestion(self, session_id: str, job_id: int) -> None:
        self.scheduled_ingestion_job_ids.add(job_id)
        task = asyncio.create_task(self._run_asset_ingestion(session_id, job_id))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        task.add_done_callback(lambda _: self.scheduled_ingestion_job_ids.discard(job_id))

    def _start_resumed_turn(self, session_id: str, turn_id: int, context: dict[str, object], phase: str) -> None:
        if not lease_service.try_acquire("turn", str(turn_id), self.instance_id):
            return
        cancel_event = asyncio.Event()
        task = asyncio.create_task(self._run_resumed_turn(session_id, turn_id, context, phase, cancel_event))
        self.session_turns[session_id] = SessionTurn(turn_id=turn_id, task=task, cancel_event=cancel_event)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def _run_asset_ingestion(self, session_id: str, job_id: int) -> None:
        if not lease_service.try_acquire("asset_ingestion_job", str(job_id), self.instance_id):
            return
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._lease_heartbeat_loop("asset_ingestion_job", str(job_id), heartbeat_stop)
        )
        try:
            job = ingestion_job_service.update_job_running(job_id, self.instance_id)
            if job is None:
                return
            asset = asset_service.get_asset(job.asset_id, session_id=session_id)
            if asset is None:
                ingestion_job_service.update_job_failed(job_id, f"Unknown session asset '{job.asset_id}'.")
                return
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="asset.processing",
                    content=f"Processing attachment '{asset.filename}'.",
                )
            )
            result = await asyncio.to_thread(asset_ingestion_service.ingest_asset, asset.id)
            if result.status == "ready":
                ingestion_job_service.update_job_completed(job_id)
                event_type = "asset.ready"
                content = f"Attachment '{result.filename}' is ready."
            else:
                ingestion_job_service.update_job_failed(job_id, result.error_message or "Unknown error.")
                event_type = "asset.failed"
                content = f"Attachment '{result.filename}' failed to process: {result.error_message or 'Unknown error.'}"
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type=event_type,
                    content=content,
                )
            )
        finally:
            heartbeat_stop.set()
            await heartbeat_task
            lease_service.release("asset_ingestion_job", str(job_id), self.instance_id)

    async def _run_background_job(self, job_id: int) -> None:
        if not lease_service.try_acquire("background_job", str(job_id), self.instance_id):
            return
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._lease_heartbeat_loop("background_job", str(job_id), heartbeat_stop)
        )
        session_lane_acquired = False
        session_lane_task: asyncio.Task[None] | None = None
        session_lane_key = ""
        try:
            existing_job = background_job_service.get_job(job_id)
            if existing_job is None or existing_job.status == "cancelled":
                return
            job = background_job_service.update_job_running(job_id, self.instance_id)
            if job is None:
                return
            payload = background_job_service.payload_dict(job)
            if job.job_type != "turn_execution":
                background_job_service.update_job_dead_lettered(job_id, f"Unsupported background job type '{job.job_type}'.")
                return
            session_id = str(payload.get("session_id") or job.session_id or "").strip()
            turn_id = payload.get("turn_id")
            content = str(payload.get("content") or "")
            execution_mode = str(payload.get("execution_mode") or "normal").strip().lower() or "normal"
            plan_context = payload.get("plan_context")
            resume_context = payload.get("resume_context")
            resume_phase = str(payload.get("resume_phase") or "")
            if not session_id or not isinstance(turn_id, int):
                background_job_service.update_job_dead_lettered(job_id, "Turn execution job payload is incomplete.")
                return
            current_turn = turn_service.get_turn(turn_id)
            if current_turn is None:
                background_job_service.update_job_dead_lettered(job_id, f"Unknown turn #{turn_id}.")
                return
            if current_turn.status in {"completed", "cancelled"}:
                background_job_service.update_job_completed(job_id, current_turn.status)
                return
            if current_turn.status == "failed":
                background_job_service.update_job_failed(job_id, current_turn.error_summary or "Turn failed.")
                return
            if turn_service.has_newer_turn(session_id, turn_id):
                turn_service.update_turn_status(
                    turn_id,
                    "cancelled",
                    resume_hint="A newer user message replaced this unfinished turn.",
                    completed=True,
                )
                background_job_service.update_job_completed(job_id, "superseded")
                return
            session_lane_key = session_id
            if not lease_service.try_acquire("session_turn_lane", session_lane_key, self.instance_id):
                background_job_service.requeue_job(
                    job_id,
                    f"Session {session_lane_key} already has an active turn lane; retrying later.",
                    delay_seconds=settings.jarvis_background_job_base_backoff_seconds,
                )
                return
            session_lane_acquired = True
            session_lane_task = asyncio.create_task(
                self._lease_heartbeat_loop("session_turn_lane", session_lane_key, heartbeat_stop)
            )
            if lease_service.is_active("turn", str(turn_id)):
                background_job_service.requeue_job(
                    job_id,
                    f"Turn lease for {turn_id} is still active; retrying later.",
                    delay_seconds=settings.jarvis_background_job_base_backoff_seconds,
                )
                return
            if isinstance(resume_context, dict) and resume_phase:
                self._start_resumed_turn(session_id, turn_id, resume_context, resume_phase)
            else:
                self._start_background_turn(
                    session_id,
                    content,
                    turn_id,
                    execution_mode=execution_mode,
                    plan_context=plan_context if isinstance(plan_context, dict) else None,
                )
            turn = self.session_turns.get(session_id)
            if turn is not None and turn.turn_id == turn_id:
                try:
                    await turn.task
                except Exception:
                    pass
            latest_job = background_job_service.get_job(job_id)
            if latest_job and latest_job.status == "cancelled":
                return
            current_turn = turn_service.get_turn(turn_id)
            if current_turn and current_turn.status in {"completed", "cancelled"}:
                background_job_service.update_job_completed(job_id, current_turn.status)
            elif current_turn and current_turn.status == "failed":
                background_job_service.update_job_failed(job_id, current_turn.error_summary or "Turn failed.")
            else:
                if background_job_service.should_retry(job):
                    delay_seconds = settings.jarvis_background_job_base_backoff_seconds * max(1, int(job.attempts or 0))
                    background_job_service.requeue_job(
                        job_id,
                        "Turn execution ended without a terminal status; retrying.",
                        delay_seconds=delay_seconds,
                    )
                else:
                    background_job_service.update_job_dead_lettered(job_id, "Turn execution ended without a terminal status.")
        except Exception as exc:
            current_job = background_job_service.get_job(job_id)
            if background_job_service.should_retry(current_job):
                attempts = int(current_job.attempts or 1) if current_job else 1
                delay_seconds = settings.jarvis_background_job_base_backoff_seconds * max(1, attempts)
                background_job_service.requeue_job(
                    job_id,
                    f"Background job execution failed transiently: {exc}",
                    delay_seconds=delay_seconds,
                )
            else:
                background_job_service.update_job_dead_lettered(job_id, f"Background job execution failed: {exc}")
        finally:
            heartbeat_stop.set()
            await heartbeat_task
            if session_lane_task is not None:
                await session_lane_task
            if session_lane_acquired and session_lane_key:
                lease_service.release("session_turn_lane", session_lane_key, self.instance_id)
            lease_service.release("background_job", str(job_id), self.instance_id)

    async def cancel_session_turn(self, session_id: str) -> bool:
        latest_turn = turn_service.latest_cancellable_turn(session_id)
        if latest_turn is None:
            return False
        turn_service.request_turn_cancel(latest_turn.id)
        turn = self.session_turns.get(session_id)
        if turn is None or turn.task.done():
            if latest_turn.status == "waiting_approval":
                turn_service.update_turn_status(latest_turn.id, "cancelled", resume_hint="User stopped the current turn.", completed=True)
                await self.publish(
                    TimelineEvent(
                        session_id=session_id,
                        type="turn.cancelled",
                        content="Stopped the current turn.",
                    )
                )
                memory_service.refresh_rolling_summary(session_id, latest_turn.id)
            return True
        turn.cancel_event.set()
        return True

    async def create_session(self, payload: SessionCreate) -> SessionSummary:
        session = session_service.create_session_record(payload)
        await self.publish(
            TimelineEvent(
                session_id=session.session_id,
                type="session.created",
                content=f"Session '{payload.title}' created.",
            )
        )
        return session

    async def upload_assets(self, session_id: str, uploads: list[tuple[str, str | None, bytes]]) -> list[SessionAssetSummary]:
        uploaded: list[SessionAssetSummary] = []
        self._ensure_dispatcher_started()
        for filename, mime_type, data in uploads:
            asset = asset_ingestion_service.stage_uploaded_asset(session_id, filename, mime_type, data)
            uploaded.append(asset)
            job = ingestion_job_service.create_job(session_id, asset.id)
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="asset.uploaded",
                    content=f"Uploaded attachment '{asset.filename}'.",
                )
            )
            self._signal_dispatcher()
        return uploaded

    async def upload_asset_streams(self, session_id: str, uploads: list[asset_ingestion_service.AsyncUploadLike]) -> list[SessionAssetSummary]:
        uploaded: list[SessionAssetSummary] = []
        self._ensure_dispatcher_started()
        for upload in uploads:
            asset = await asset_ingestion_service.stage_uploaded_asset_stream(session_id, upload)
            uploaded.append(asset)
            job = ingestion_job_service.create_job(session_id, asset.id)
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="asset.uploaded",
                    content=f"Uploaded attachment '{asset.filename}'.",
                )
            )
            self._signal_dispatcher()
        return uploaded

    async def delete_asset(self, session_id: str, asset_id: str) -> bool:
        asset = asset_service.get_asset(asset_id, session_id=session_id)
        deleted = asset_service.hide_asset(asset_id, session_id=session_id)
        if deleted:
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="asset.removed",
                    content=f"Removed attachment '{asset.filename if asset else asset_id}'.",
                )
            )
        return deleted

    async def append_message(self, session_id: str, payload: MessageCreate) -> None:
        self._ensure_dispatcher_started()
        should_autoname = payload.role == "user" and bool(payload.content.strip()) and self._should_autoname_session(session_id)
        created_message = session_service.create_message_record(session_id, payload)
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type=f"message.{payload.role}",
                content=self._timeline_message_content(session_id, payload),
            )
        )
        if payload.role == "user":
            previous_turn = turn_service.latest_cancellable_turn(session_id)
            if previous_turn is not None:
                turn_service.request_turn_cancel(previous_turn.id)
                existing = self.session_turns.get(session_id)
                if existing and existing.turn_id == previous_turn.id:
                    existing.cancel_event.set()
            workspace = self._session_workspace(session_id)
            turn = turn_service.create_turn(
                session_id=session_id,
                user_message_id=created_message.id if created_message else None,
                workspace_path=workspace.as_posix(),
                workspace_fingerprint=workspace_utils.workspace_fingerprint(workspace),
                execution_mode=payload.execution_mode,
            )
            if payload.content.strip():
                memory_service.remember_goal(session_id, payload.content, source_turn_id=turn.id)
                self._capture_user_memory_signals(session_id, turn.id, payload.content)
            memory_service.refresh_rolling_summary(session_id, turn.id)
            if should_autoname:
                self._start_autoname_session(session_id, payload.content)
            job = background_job_service.create_job(
                session_id=session_id,
                job_type="turn_execution",
                payload={
                    "session_id": session_id,
                    "turn_id": turn.id,
                    "content": payload.content,
                    "execution_mode": payload.execution_mode,
                },
                command=f"turn_execution:{turn.id}",
            )
            self._signal_dispatcher()

    def _enqueue_turn_resume_job(
        self,
        session_id: str,
        turn_id: int | None,
        context: dict[str, object],
        phase: str,
    ) -> bool:
        if not session_id or not isinstance(turn_id, int):
            return False
        self._ensure_dispatcher_started()
        background_job_service.create_job(
            session_id=session_id,
            job_type="turn_execution",
            payload={
                "session_id": session_id,
                "turn_id": turn_id,
                "resume_context": context,
                "resume_phase": phase,
            },
            command=f"turn_resume:{turn_id}",
        )
        self._signal_dispatcher()
        return True

    def _timeline_message_content(self, session_id: str, payload: MessageCreate) -> str:
        text = payload.content.strip()
        if not payload.asset_ids:
            return text
        asset_names = [
            asset.filename
            for asset_id in payload.asset_ids
            for asset in [asset_service.get_asset(asset_id, session_id=session_id)]
            if asset is not None
        ]
        if text:
            return text
        if asset_names:
            return ", ".join(asset_names)
        return f"{len(payload.asset_ids)} attachment(s)"

    async def _finalize_cancelled_turn(self, session_id: str, turn_id: int) -> None:
        current_turn = turn_service.get_turn(turn_id)
        if current_turn is not None and current_turn.status == "cancelled":
            return
        turn = self.session_turns.get(session_id)
        partial_text = turn.partial_text.strip() if turn else ""
        if partial_text:
            session_service.create_message_record(session_id, MessageCreate(role="assistant", content=partial_text))
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="message.assistant",
                    content=partial_text,
                )
            )
        turn_service.update_turn_status(turn_id, "cancelled", resume_hint="User stopped the current turn.", completed=True)
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="turn.cancelled",
                content="Stopped the current turn.",
            )
        )
        memory_service.refresh_rolling_summary(session_id, turn_id)

    async def _run_lead_turn(
        self,
        session_id: str,
        turn_id: int,
        content: str,
        cancel_event: asyncio.Event,
        *,
        execution_mode: str = "normal",
        plan_context: dict[str, object] | None = None,
    ) -> None:
        if self._should_cancel_turn(turn_id, cancel_event):
            raise TurnCancelled
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._lease_heartbeat_loop("turn", str(turn_id), heartbeat_stop, cancel_event=cancel_event)
        )
        await self.emit_ephemeral(
            TimelineEvent(
                session_id=session_id,
                type="runtime.state",
                content="Lead runtime is evaluating the latest user turn.",
            )
        )
        turn_service.update_turn_status(turn_id, "running")
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="turn.started",
                content=f"Turn #{turn_id} started.",
            )
        )
        try:
            reply = await self._run_agent_task(
                session_id,
                turn_id,
                content,
                cancel_event,
                execution_mode=execution_mode,
                plan_context=plan_context,
            )
            await self._publish_assistant_reply(
                session_id,
                reply.text,
                source_turn_id=turn_id,
                emit_deltas=False,
                asset_ids=reply.asset_ids,
            )
            if execution_mode == "plan":
                await self._queue_plan_execution_approval(
                    session_id=session_id,
                    turn_id=turn_id,
                    original_request=content,
                    plan_text=reply.text,
                )
            current_turn = turn_service.get_turn(turn_id)
            if current_turn and current_turn.status == "running":
                turn_service.update_turn_status(turn_id, "completed", completed=True)
                await self.publish(
                    TimelineEvent(
                        session_id=session_id,
                        type="turn.completed",
                        content=f"Turn #{turn_id} completed.",
                    )
                )
            return
        except TurnCancelled:
            await self._finalize_cancelled_turn(session_id, turn_id)
            return
        except asyncio.CancelledError:
            return
        except Exception as exc:
            turn_service.update_turn_status(turn_id, "failed", error_summary=str(exc), resume_hint="Runtime failed during this turn.", completed=True)
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="turn.failed",
                    content=f"Turn #{turn_id} failed: {exc}",
                )
            )
            reply = f"Lead runtime failed: {exc}"
        finally:
            heartbeat_stop.set()
            await heartbeat_task
            current = self.session_turns.get(session_id)
            if current and current.cancel_event is cancel_event:
                self.session_turns.pop(session_id, None)
            lease_service.release("turn", str(turn_id), self.instance_id)

        await self._publish_assistant_reply(session_id, reply, source_turn_id=turn_id)

    async def _run_resumed_turn(
        self,
        session_id: str,
        turn_id: int,
        context: dict[str, object],
        phase: str,
        cancel_event: asyncio.Event,
    ) -> None:
        if self._should_cancel_turn(turn_id, cancel_event):
            raise TurnCancelled
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._lease_heartbeat_loop("turn", str(turn_id), heartbeat_stop, cancel_event=cancel_event)
        )
        await self.emit_ephemeral(
            TimelineEvent(
                session_id=session_id,
                type="runtime.state",
                content="Lead runtime is resuming an interrupted turn.",
            )
        )
        turn_service.update_turn_status(turn_id, "running", resume_hint=f"Resumed from {phase} checkpoint.")
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="turn.resumed",
                content=f"Turn #{turn_id} resumed from {phase}.",
            )
        )
        try:
            if phase == "waiting_approval":
                reply = await self._resume_agent_loop_after_approval(session_id, context)
                if reply is None:
                    reply = AgentReply(
                        text="Approval context is incomplete; unable to resume the approved action.",
                        asset_ids=[],
                    )
            else:
                reply = await self._resume_turn_from_context(session_id, turn_id, context, cancel_event)
            await self._publish_assistant_reply(
                session_id,
                reply.text,
                source_turn_id=turn_id,
                emit_deltas=False,
                asset_ids=reply.asset_ids,
            )
            current_turn = turn_service.get_turn(turn_id)
            if current_turn and current_turn.status == "running":
                turn_service.update_turn_status(turn_id, "completed", completed=True)
                await self.publish(
                    TimelineEvent(
                        session_id=session_id,
                        type="turn.completed",
                        content=f"Turn #{turn_id} completed.",
                    )
                )
            return
        except TurnCancelled:
            await self._finalize_cancelled_turn(session_id, turn_id)
            return
        except asyncio.CancelledError:
            return
        except Exception as exc:
            turn_service.update_turn_status(turn_id, "failed", error_summary=str(exc), resume_hint="Runtime failed while resuming this turn.", completed=True)
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="turn.failed",
                    content=f"Turn #{turn_id} failed while resuming: {exc}",
                )
            )
            reply = f"Lead runtime failed while resuming: {exc}"
        finally:
            heartbeat_stop.set()
            await heartbeat_task
            current = self.session_turns.get(session_id)
            if current and current.cancel_event is cancel_event:
                self.session_turns.pop(session_id, None)
            lease_service.release("turn", str(turn_id), self.instance_id)

        await self._publish_assistant_reply(session_id, reply, source_turn_id=turn_id)

    async def _run_agent_task(
        self,
        session_id: str,
        turn_id: int,
        latest_user_content: str,
        cancel_event: asyncio.Event,
        *,
        execution_mode: str = "normal",
        plan_context: dict[str, object] | None = None,
    ) -> AgentReply:
        workspace = self._session_workspace(session_id)
        workspace_mode = self._session_workspace_mode(session_id)
        execution_mode = self._normalize_execution_mode(execution_mode)
        explicit_external_reads = self._explicit_external_reads(workspace, latest_user_content)
        named_workspace = workspace_utils.detect_named_workspace_reference(latest_user_content, workspace)
        if workspace_mode == "default" and named_workspace is not None:
            workspace = named_workspace
            explicit_external_reads = []
        elif named_workspace is not None and not explicit_external_reads:
            turn_service.update_turn_status(
                turn_id,
                "interrupted",
                resume_hint=f"Turn stopped because the prompt referenced another workspace: {named_workspace.as_posix()}",
            )
            memory_service.remember_constraint(
                session_id,
                f"This session is bound to {workspace.as_posix()}. Cross-workspace writes require a new session or explicit rebind.",
                source_turn_id=turn_id,
            )
            memory_service.remember_open_question(
                session_id,
                f"Should this work continue in a new session bound to {named_workspace.as_posix()}?",
                source_turn_id=turn_id,
            )
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="turn.interrupted",
                    content=f"Turn #{turn_id} stopped because the prompt referenced another workspace.",
                )
            )
            return AgentReply(
                text=(
                f"当前会话绑定的工作目录是 `{workspace.as_posix()}`，"
                f"你这次提到的项目是 `{named_workspace.as_posix()}`。"
                "如果你要在那个项目里继续执行，请新开一个绑定到该目录的会话，"
                "或后续提供显式绝对路径做只读查看。"
                ),
                asset_ids=[],
            )

        messages = context_assembler.build_initial_loop_messages(session_id)
        if execution_mode == "normal" and isinstance(plan_context, dict):
            original_request = str(plan_context.get("original_request") or latest_user_content).strip()
            approved_plan = str(plan_context.get("approved_plan") or "").strip()
            if approved_plan:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The user approved the following plan. Execute it now.\n\n"
                            f"Original request:\n{original_request}\n\n"
                            f"Approved plan:\n{approved_plan}"
                        ),
                    }
                )
        return await self._continue_agent_loop(
            session_id,
            workspace,
            messages,
            cancel_event,
            turn_id=turn_id,
            allowed_external_reads=explicit_external_reads,
            write_enabled=workspace_mode != "default",
            execution_mode=execution_mode,
        )

    def _session_workspace(self, session_id: str) -> Path:
        session = session_service.get_session(session_id)
        if session is None:
            return settings.project_root
        return workspace_utils.normalize_workspace_path(session.canonical_workspace_path)

    def _session_workspace_mode(self, session_id: str) -> str:
        session = session_service.get_session(session_id)
        if session is None:
            return "bound"
        return session.workspace_mode

    def _normalize_subagent_isolation_mode(self, raw: str | None) -> str:
        value = (raw or "shared").strip().lower()
        return value if value in {"shared", "worktree"} else "shared"

    def _normalize_execution_mode(self, raw: str | None) -> str:
        value = (raw or "normal").strip().lower()
        return value if value in {"normal", "plan"} else "normal"

    def _session_git_prompt_section(self, session_id: str) -> str:
        session = session_service.get_session(session_id)
        if session is None or not getattr(session, "git_enabled", False):
            return ""
        lines = [
            f"Repository root: {session.repo_root or 'unknown'}",
            f"Lead branch: {session.lead_branch or '(detached or unknown)'}",
            f"Working tree status: {session.working_tree_status or 'unknown'}",
        ]
        if getattr(session, "detached_head", False):
            lines.append("The repository is currently in detached HEAD state.")
        return "\n".join(lines)

    def _session_git_state_tool_output(self, session_id: str) -> str:
        session = session_service.get_session(session_id)
        if session is None:
            return "Session not found."
        if not getattr(session, "git_enabled", False):
            return "The current session workspace is not inside a Git repository."

        lines = [
            f"Repository root: {session.repo_root or 'unknown'}",
            f"Lead branch: {session.lead_branch or '(detached or unknown)'}",
            f"HEAD revision: {session.head_revision or 'unknown'}",
            f"Working tree status: {session.working_tree_status or 'unknown'}",
            f"Detached HEAD: {'yes' if getattr(session, 'detached_head', False) else 'no'}",
        ]
        return "\n".join(lines)

    def _explicit_external_reads(self, workspace: Path, content: str) -> list[Path]:
        return [
            path
            for path in workspace_utils.explicit_paths_from_text(content)
            if not workspace_utils.path_within(workspace, path)
        ]

    def _capture_user_memory_signals(self, session_id: str, turn_id: int, content: str) -> None:
        normalized = content.strip()
        lowered = normalized.lower()
        decision_markers = ("认可", "同意", "采用", "就按", "按这个", "按此", "用这个方案", "开始执行")
        if normalized and any(marker in normalized for marker in decision_markers):
            memory_service.remember_decision(
                session_id,
                f"User confirmed the current direction: {normalized}",
                source_turn_id=turn_id,
            )
        if "？" in normalized or normalized.endswith("?"):
            memory_service.remember_open_question(session_id, normalized, source_turn_id=turn_id)
        elif lowered.startswith(("what ", "why ", "how ", "which ", "should ")):
            memory_service.remember_open_question(session_id, normalized, source_turn_id=turn_id)

    def _capture_assistant_memory_signals(self, session_id: str, turn_id: int | None, content: str) -> None:
        normalized = content.strip()
        if not normalized or turn_id is None:
            return
        if "？" in normalized or normalized.endswith("?"):
            memory_service.remember_open_question(session_id, normalized, source_turn_id=turn_id)

    def _build_runtime_context(
        self,
        *,
        workspace: Path,
        messages: list[dict[str, object]],
        turn_id: int | None,
        allowed_external_reads: list[Path] | None,
        write_enabled: bool,
        allow_subagent_tool: bool,
        agent_kind: str,
        emit_stream_events: bool,
        execution_mode: str,
        tool_use_id: str | None = None,
        tool_name: str | None = None,
        tool_input: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "mode": "agent_loop",
            "workspace": workspace.as_posix(),
            "turn_id": turn_id,
            "allowed_external_reads": [path.as_posix() for path in (allowed_external_reads or [])],
            "write_enabled": write_enabled,
            "messages": messages,
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "allow_subagent_tool": allow_subagent_tool,
            "agent_kind": agent_kind,
            "emit_stream_events": emit_stream_events,
            "execution_mode": execution_mode,
        }

    def _write_checkpoint(
        self,
        *,
        turn_id: int | None,
        phase: str,
        workspace: Path,
        messages: list[dict[str, object]],
        allowed_external_reads: list[Path] | None,
        write_enabled: bool,
        allow_subagent_tool: bool,
        agent_kind: str,
        emit_stream_events: bool,
        execution_mode: str,
        summary: str,
        tool_use_id: str | None = None,
        tool_name: str | None = None,
        tool_input: dict[str, object] | None = None,
    ) -> int | None:
        if turn_id is None:
            return None
        context = self._build_runtime_context(
            workspace=workspace,
            messages=messages,
            turn_id=turn_id,
            allowed_external_reads=allowed_external_reads,
            write_enabled=write_enabled,
            allow_subagent_tool=allow_subagent_tool,
            agent_kind=agent_kind,
            emit_stream_events=emit_stream_events,
            execution_mode=execution_mode,
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            tool_input=tool_input,
        )
        checkpoint = checkpoint_service.create_checkpoint(
            turn_id,
            phase=phase,
            context=context,
            pending_tool_name=tool_name,
            pending_tool_input=tool_input,
            summary=summary,
        )
        return checkpoint.id

    def _autonomous_tool_schemas(self, *, allow_subagent_tool: bool = True) -> list[dict[str, object]]:
        schemas = [
            {
                "name": "get_session_git_state",
                "description": "Read the current session's Git repository state, including repo root, lead branch, HEAD state, and working tree cleanliness.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "list_files",
                "description": "List files in the current target workspace.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "read_file",
                "description": "Read a file from the current target workspace.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {
                "name": "write_file",
                "description": "Create or overwrite a file in the current target workspace.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "edit_file",
                "description": "Replace exact text in a file in the current target workspace.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
            {
                "name": "bash",
                "description": "Run a shell command in the current target workspace. This requires approval before execution.",
                "input_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
            {
                "name": "list_skills",
                "description": "List locally installed skills available to the agent.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "load_skill",
                "description": "Load a local skill by name and read its SKILL.md instructions.",
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            {
                "name": "memory_search",
                "description": "Search structured session memory in the current session.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "kind": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "conversation_search",
                "description": "Search prior durable conversation messages in the current session.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "role": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "create_task",
                "description": "Create a lightweight task in the current session.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["subject"],
                },
            },
            {
                "name": "run_subagent",
                "description": "Delegate a bounded investigation or implementation subtask to a subagent. Use this for complex tasks, long investigations, or independent subproblems. The subagent returns a written summary of what it found or changed.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "prompt": {"type": "string"},
                        "isolation_mode": {"type": "string", "enum": ["shared", "worktree"]},
                    },
                    "required": ["prompt"],
                },
            },
            {
                "name": "create_teammate",
                "description": "Create a teammate agent for the current session.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": "string"},
                    },
                    "required": ["name", "role"],
                },
            },
            {
                "name": "message_teammate",
                "description": "Send a message to a teammate agent in the current session.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "integer"},
                        "content": {"type": "string"},
                    },
                    "required": ["agent_id", "content"],
                },
            },
        ]
        if not allow_subagent_tool:
            schemas = [schema for schema in schemas if schema["name"] != "run_subagent"]
        return schemas

    async def _autonomous_tool_definitions(self, *, allow_subagent_tool: bool = True) -> list[ToolDefinition]:
        return await tool_registry.list_tools(allow_subagent_tool=allow_subagent_tool)

    def _tool_schemas_from_definitions(self, definitions: list[ToolDefinition]) -> list[dict[str, object]]:
        return [
            {
                "name": definition.name,
                "description": definition.description,
                "input_schema": definition.input_schema,
            }
            for definition in definitions
        ]

    def _should_autoname_session(self, session_id: str) -> bool:
        session = session_service.get_session(session_id)
        if session is None:
            return False
        if not session.title.startswith("New Session") and session.title != "New Session":
            return False
        if session_service.has_user_messages(session_id):
            return False
        return True

    def _start_autoname_session(self, session_id: str, content: str) -> None:
        task = asyncio.create_task(self._autoname_session(session_id, content))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def _autoname_session(self, session_id: str, content: str) -> None:
        title = await asyncio.to_thread(self._generate_session_title, content)
        if title:
            updated = session_service.update_session_title(session_id, title)
            if updated:
                await self.publish(
                    TimelineEvent(
                        session_id=session_id,
                        type="session.renamed",
                        content=title,
                    )
                )

    def _generate_session_title(self, content: str) -> str:
        llm_title = self._generate_session_title_with_llm(content)
        if llm_title:
            return llm_title
        return self._summarize_session_title_fallback(content)

    def _generate_session_title_with_llm(self, content: str) -> str | None:
        if not settings.model_id:
            return None
        prompt = (
            "Summarize the user's first message into a concise session title.\n"
            "Return only the title.\n"
            "Keep it under 8 words in English or under 16 Chinese characters.\n"
            "No quotes. No punctuation unless needed.\n"
            "Make it specific to the task."
        )
        try:
            response = create_client().messages.create(
                model=settings.model_id,
                system=prompt,
                messages=[{"role": "user", "content": content[:1200]}],
                max_tokens=32,
            )
        except (ProviderConfigError, ProviderRequestError, Exception):
            return None

        text = " ".join(
            block.text.strip()
            for block in response.content
            if isinstance(block, TextBlock) and block.text.strip()
        ).strip()
        if not text:
            return None
        text = " ".join(text.split()).strip().strip("\"'` ")
        if not text:
            return None
        return text[:120]

    def _summarize_session_title_fallback(self, content: str) -> str:
        text = " ".join(content.strip().split())
        if not text:
            return "New Session"
        for prefix in ("请帮我", "帮我", "麻烦", "请", "能不能", "可以帮我", "can you ", "could you ", "please ", "help me "):
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()
                break
        text = text.strip(" .,!?:;，。！？：；\"'()[]{}")
        if not text:
            return "New Session"
        has_cjk = any("\u4e00" <= char <= "\u9fff" for char in text)
        if has_cjk:
            return text[:14]
        words = text.split()
        return " ".join(words[:5])[:36]

    def _generate_teammate_reply(self, name: str, role: str, content: str) -> str:
        return f"{name} ({role}) acknowledged the request and recommends tackling: {content[:120]}"

    def _generate_subagent_summary(self, prompt: str) -> str:
        files = broker.run("list_files", {})[1].splitlines()[:8]
        summary_parts = [f"Prompt: {prompt[:140]}"]
        if files:
            summary_parts.append("Workspace sample:\n" + "\n".join(files))
        if "readme" in prompt.lower() or "workspace" in prompt.lower() or "project" in prompt.lower():
            status, content = broker.run("read_file", {"path": "README.md"})
            if status == "completed":
                summary_parts.append("README snapshot:\n" + content[:500])
        return "\n\n".join(summary_parts)

    def _subagent_started_event_content(self, name: str, subagent) -> str:
        if getattr(subagent, "isolation_mode", "shared") != "worktree":
            return f"Subagent '{name}' started."
        details: list[str] = ["mode=worktree"]
        if getattr(subagent, "git_branch", None):
            details.append(f"branch={subagent.git_branch}")
        if getattr(subagent, "execution_workspace_path", None):
            details.append(f"path={subagent.execution_workspace_path}")
        return f"Subagent '{name}' started ({', '.join(details)})."

    def _subagent_summary_event_content(self, name: str, summary: str, subagent) -> str:
        suffix_parts: list[str] = []
        if getattr(subagent, "cleanup_status", "") == "preserved" and getattr(subagent, "execution_workspace_path", None):
            suffix_parts.append(f"preserved at {subagent.execution_workspace_path}")
        elif getattr(subagent, "cleanup_status", "") == "cleaned":
            suffix_parts.append("cleaned")
        elif getattr(subagent, "cleanup_status", "") == "cleanup_failed":
            suffix_parts.append("cleanup failed")
        if getattr(subagent, "git_branch", None):
            suffix_parts.append(f"branch={subagent.git_branch}")
        suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
        return f"{name}: {summary}{suffix}"

    def _skill_roots(self) -> list[Path]:
        roots = [
            settings.project_root / "skills",
            settings.project_root / ".agents" / "skills",
        ]
        return [root for root in roots if root.exists() and root.is_dir()]

    def _list_skills(self) -> str:
        entries: list[str] = []
        for root in self._skill_roots():
            for skill_file in sorted(root.glob("*/SKILL.md")):
                entries.append(f"{skill_file.parent.name} ({skill_file.parent.relative_to(settings.project_root)})")
        unique = sorted(dict.fromkeys(entries))
        return "\n".join(unique[:120]) if unique else "(no skills found)"

    def list_local_skills(self) -> list[dict[str, str]]:
        skills: list[dict[str, str]] = []
        for root in self._skill_roots():
            for skill_file in sorted(root.glob("*/SKILL.md")):
                skills.append(
                    {
                        "name": skill_file.parent.name,
                        "path": skill_file.parent.relative_to(settings.project_root).as_posix(),
                    }
                )
        seen: set[tuple[str, str]] = set()
        unique: list[dict[str, str]] = []
        for skill in skills:
            key = (skill["name"], skill["path"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(skill)
        return unique

    def _load_skill(self, name: str) -> tuple[str, str]:
        requested = name.strip().lower()
        if not requested:
            return "error", "load_skill requires a skill name."

        matches: list[Path] = []
        for root in self._skill_roots():
            for skill_file in root.glob("*/SKILL.md"):
                skill_name = skill_file.parent.name.lower()
                if requested == skill_name or requested in skill_name:
                    matches.append(skill_file)

        if not matches:
            return "error", f"Skill not found: {name}"

        skill_file = sorted(matches)[0]
        return "completed", skill_file.read_text()[:12000]

    async def _execute_autonomous_tool(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, object],
        broker_for_workspace: ToolBroker,
    ) -> tuple[str, str] | ToolExecutionResult | None:
        if tool_name == "bash":
            return None
        if tool_name in {"list_files", "read_file", "write_file", "edit_file"}:
            return broker_for_workspace.run(tool_name, tool_input)
        if tool_name == "get_session_git_state":
            return "completed", self._session_git_state_tool_output(session_id)
        if tool_name == "list_skills":
            return "completed", self._list_skills()
        if tool_name == "load_skill":
            return self._load_skill(str(tool_input.get("name", "")))
        if tool_name == "memory_search":
            query = str(tool_input.get("query", "")).strip()
            kind = str(tool_input.get("kind", "")).strip() or None
            raw_limit = tool_input.get("limit", 5)
            limit = raw_limit if isinstance(raw_limit, int) else 5
            if not query:
                return "error", "memory_search requires a non-empty query."
            return "completed", memory_search_service.search_memory_text(
                session_id,
                query=query,
                kind=kind,
                limit=max(1, min(limit, 10)),
            )
        if tool_name == "conversation_search":
            query = str(tool_input.get("query", "")).strip()
            role = str(tool_input.get("role", "")).strip() or None
            raw_limit = tool_input.get("limit", 5)
            limit = raw_limit if isinstance(raw_limit, int) else 5
            if not query:
                return "error", "conversation_search requires a non-empty query."
            return "completed", conversation_search_service.search_conversation_text(
                session_id,
                query=query,
                role=role,
                limit=max(1, min(limit, 10)),
            )
        if tool_name == "list_session_assets":
            assets = asset_service.list_assets(session_id)
            if not assets:
                return "completed", "No uploaded session attachments are available."
            lines = [
                f"- id={asset.id} name={asset.filename} kind={asset.kind} status={asset.status}"
                for asset in assets
            ]
            return "completed", "Session attachments:\n" + "\n".join(lines)
        if tool_name == "read_asset_summary":
            asset_id = str(tool_input.get("asset_id", "")).strip()
            if not asset_id:
                return "error", "read_asset_summary requires a non-empty asset_id."
            asset = asset_service.get_asset(asset_id, session_id=session_id)
            if asset is None:
                return "error", f"Unknown session asset '{asset_id}'."
            lines = [
                f"id={asset.id}",
                f"name={asset.filename}",
                f"kind={asset.kind}",
                f"mime_type={asset.mime_type}",
                f"status={asset.status}",
                f"size_bytes={asset.size_bytes}",
            ]
            if asset.error_message:
                lines.append(f"error={asset.error_message}")
            chunks = asset_service.list_asset_chunks(asset.id)[:2]
            if chunks:
                lines.append("Representative extracted content:")
                lines.extend(f"- {chunk.content[:500]}" for chunk in chunks)
            return "completed", "\n".join(lines)
        if tool_name == "search_asset_chunks":
            asset_id = str(tool_input.get("asset_id", "")).strip()
            query = str(tool_input.get("query", "")).strip()
            raw_limit = tool_input.get("limit", 3)
            limit = raw_limit if isinstance(raw_limit, int) else 3
            if not asset_id or not query:
                return "error", "search_asset_chunks requires both asset_id and query."
            asset = asset_service.get_asset(asset_id, session_id=session_id)
            if asset is None:
                return "error", f"Unknown session asset '{asset_id}'."
            chunks = asset_service.search_asset_chunks(asset.id, query, limit=max(1, min(limit, 8)))
            if not chunks:
                return "completed", f"No extracted chunks matched '{query}' for attachment '{asset.filename}'."
            lines = [f"Attachment '{asset.filename}' matching chunks:"]
            for chunk in chunks:
                location_bits: list[str] = []
                if chunk.page_number is not None:
                    location_bits.append(f"page={chunk.page_number}")
                if chunk.sheet_name:
                    location_bits.append(f"sheet={chunk.sheet_name}")
                if chunk.slide_number is not None:
                    location_bits.append(f"slide={chunk.slide_number}")
                location = f" ({', '.join(location_bits)})" if location_bits else ""
                lines.append(f"- chunk_index={chunk.chunk_index}{location}: {chunk.content[:600]}")
            return "completed", "\n".join(lines)
        if tool_name == "read_asset_chunk":
            asset_id = str(tool_input.get("asset_id", "")).strip()
            chunk_index = tool_input.get("chunk_index")
            if not asset_id or not isinstance(chunk_index, int):
                return "error", "read_asset_chunk requires asset_id and integer chunk_index."
            asset = asset_service.get_asset(asset_id, session_id=session_id)
            if asset is None:
                return "error", f"Unknown session asset '{asset_id}'."
            chunk = asset_service.get_asset_chunk_by_index(asset.id, chunk_index)
            if chunk is None:
                return "error", f"Chunk {chunk_index} not found for attachment '{asset.filename}'."
            return "completed", chunk.content
        if tool_name == "generate_image":
            prompt = str(tool_input.get("prompt", "")).strip()
            raw_asset_ids = tool_input.get("asset_ids", [])
            asset_ids = [str(asset_id).strip() for asset_id in raw_asset_ids] if isinstance(raw_asset_ids, list) else []
            mask_asset_id = str(tool_input.get("mask_asset_id", "")).strip() or None
            input_fidelity = str(tool_input.get("input_fidelity", "")).strip() or None
            size = str(tool_input.get("size", "")).strip() or None
            background = str(tool_input.get("background", "")).strip() or None
            quality = str(tool_input.get("quality", "")).strip() or None
            if not prompt:
                return "error", "generate_image requires a non-empty prompt."
            try:
                generated = image_generation_service.generate_image(
                    session_id,
                    prompt,
                    asset_ids=asset_ids,
                    mask_asset_id=mask_asset_id,
                    input_fidelity=input_fidelity,
                    size=size,
                    background=background,
                    quality=quality,
                )
            except image_generation_service.ImageGenerationError as exc:
                return "error", str(exc)
            action = "Edited image" if any(asset_ids) else "Generated image"
            summary = f"{action} asset {generated.asset.id} ({generated.asset.filename})."
            if generated.revised_prompt and generated.revised_prompt != generated.prompt:
                summary += f" Revised prompt: {generated.revised_prompt}"
            return ToolExecutionResult(
                status="completed",
                output=summary,
                payload={
                    "asset_ids": [generated.asset.id],
                    "assets": [asset_service.build_asset_reference(generated.asset)],
                    "model": generated.model,
                    "revised_prompt": generated.revised_prompt,
                },
            )
        if tool_name == "create_task":
            subject = str(tool_input.get("subject", "")).strip()
            description = str(tool_input.get("description", "")).strip()
            if not subject:
                return "error", "create_task requires a non-empty subject."
            task = task_service.create_task(
                TaskCreate(session_id=session_id, subject=subject, description=description)
            )
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="task.created",
                    content=f"Task '{task.subject}' created.",
                )
            )
            return "completed", f"Created task #{task.id}: {task.subject}"
        if tool_name == "run_subagent":
            prompt = str(tool_input.get("prompt", "")).strip()
            name = str(tool_input.get("name", "")).strip() or f"Explorer {len(self.list_subagents(session_id)) + 1}"
            isolation_mode = self._normalize_subagent_isolation_mode(str(tool_input.get("isolation_mode", "")).strip() or "shared")
            if not prompt:
                return "error", "run_subagent requires a non-empty prompt."
            try:
                result = await self.run_subagent(
                    SubagentRunCreate(
                        session_id=session_id,
                        name=name,
                        prompt=prompt,
                        isolation_mode=isolation_mode,
                    )
                )
            except ValueError as exc:
                return "error", str(exc)
            return "completed", result["summary"]
        if tool_name == "create_teammate":
            name = str(tool_input.get("name", "")).strip()
            role = str(tool_input.get("role", "")).strip()
            if not name or not role:
                return "error", "create_teammate requires both name and role."
            teammate = await self.create_teammate(TeammateCreate(session_id=session_id, name=name, role=role))
            return "completed", f"Created teammate #{teammate.id}: {teammate.name} ({teammate.role})"
        if tool_name == "message_teammate":
            agent_id = tool_input.get("agent_id")
            content = str(tool_input.get("content", "")).strip()
            if not isinstance(agent_id, int):
                return "error", "message_teammate requires an integer agent_id."
            if not content:
                return "error", "message_teammate requires non-empty content."
            result = await self.send_teammate_message(agent_id, content)
            if result is None:
                return "error", f"Unknown teammate #{agent_id}"
            return "completed", result["reply"].content
        return "error", f"Unknown tool '{tool_name}'"

    async def _execute_tool_definition(
        self,
        *,
        session_id: str,
        tool: ToolDefinition,
        tool_input: dict[str, object],
        broker_for_workspace: ToolBroker,
        execution_mode: str = "normal",
    ) -> ToolExecutionResult | None:
        if self._normalize_execution_mode(execution_mode) == "plan" and tool.name not in self.PLAN_MODE_ALLOWED_TOOLS:
            return ToolExecutionResult(
                status="blocked",
                output=f"Tool '{tool.name}' is not available in Plan Mode.",
            )
        if tool.name == "bash":
            return None
        if tool.source == "mcp":
            return await tool_registry.call_tool(tool, tool_input)
        result = await self._execute_autonomous_tool(
            session_id=session_id,
            tool_name=tool.name,
            tool_input=tool_input,
            broker_for_workspace=broker_for_workspace,
        )
        if result is None:
            return ToolExecutionResult(status="error", output=f"Tool '{tool.name}' returned no execution result.")
        if isinstance(result, ToolExecutionResult):
            return result
        status, output = result
        return ToolExecutionResult(status=status, output=output)

    async def _publish_assistant_reply(
        self,
        session_id: str,
        reply: str,
        *,
        source_turn_id: int | None = None,
        emit_deltas: bool = True,
        asset_ids: list[str] | None = None,
    ) -> None:
        resolved_asset_ids = self._normalize_asset_ids(asset_ids or [])
        text = reply.strip() or ("Generated image." if resolved_asset_ids else "LLM provider returned no text output.")
        if emit_deltas:
            for chunk in self._chunk_text(text):
                await self.emit_ephemeral(
                    TimelineEvent(
                        session_id=session_id,
                        type="message.assistant.delta",
                        content=chunk,
                    )
                )
                await asyncio.sleep(0.035)
        parts = self._build_assistant_message_parts(session_id, text, resolved_asset_ids)
        session_service.create_message_record(
            session_id,
            MessageCreate(role="assistant", content=text, asset_ids=resolved_asset_ids),
        )
        memory_service.remember_progress(session_id, text, source_turn_id=source_turn_id)
        self._capture_assistant_memory_signals(session_id, source_turn_id, text)
        memory_service.refresh_rolling_summary(session_id, source_turn_id)
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="message.assistant",
                content=text,
                parts=parts,
            )
        )

    def _build_assistant_message_parts(
        self,
        session_id: str,
        text: str,
        asset_ids: list[str],
    ) -> list[dict[str, object]] | None:
        parts: list[dict[str, object]] = []
        if text:
            parts.append({"type": "text", "text": text})
        for asset_id in asset_ids:
            asset = asset_service.get_asset(asset_id, session_id=session_id)
            if asset is None:
                continue
            parts.append(asset_service.build_asset_reference(asset))
        return parts or None

    def _normalize_asset_ids(self, asset_ids: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for asset_id in asset_ids:
            value = str(asset_id).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized

    def _collect_generated_asset_ids(self, messages: list[dict[str, object]]) -> list[str]:
        collected: list[str] = []
        seen: set[str] = set()
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "tool_result":
                    continue
                raw_ids = part.get("asset_ids")
                if not isinstance(raw_ids, list):
                    continue
                for asset_id in raw_ids:
                    value = str(asset_id).strip()
                    if not value or value in seen:
                        continue
                    seen.add(value)
                    collected.append(value)
        return collected

    def _build_agent_system_prompt(self, workspace: Path, session_memory_header: str = "", *, execution_mode: str = "normal") -> str:
        sections = [
                "You are Jarvis, a local desktop coding agent.",
                f"Target workspace: {workspace}",
                "You may answer directly when no tool is needed, but you should decide for yourself whether tools are necessary.",
                "When the user asks about files, directories, paths, project structure, README contents, code contents, or workspace state, inspect the workspace with tools first instead of guessing.",
                "When the user asks about the current repository, Git branch, HEAD state, or working tree cleanliness, use get_session_git_state instead of guessing or shelling out.",
                "When the session has uploaded attachments and the current prompt depends on them, use the session attachment tools to inspect extracted content instead of guessing from filenames alone.",
                "When the user asks you to create a brand-new image, render a visual, or edit an uploaded image, use the generate_image tool instead of claiming the image exists.",
                (
                    "When the user asks you to create or modify files, do the work directly inside the target workspace when it is safe."
                    if execution_mode != "plan"
                    else "Use this turn to inspect and plan only. Do not create or modify files in Plan Mode."
                ),
                "The current session is bound to exactly one canonical workspace. Do not treat another project name in the prompt as permission to silently switch workspaces.",
                "If the user explicitly mentions an absolute path outside the current session workspace, you may read it as a read-only external reference when useful.",
                "Do not write or edit paths outside the current session workspace. If the user wants that, explain that they need a session bound to the target workspace or an explicit rebind.",
                "For complex tasks, large investigations, or multiple mostly-independent subproblems, you should proactively use run_subagent to delegate bounded side work and then integrate the result.",
                "If the user explicitly writes command-like instructions such as `read ...`, `write ...`, `edit ...`, or `bash: ...`, treat them as direct tool intents.",
                "Do not ask the user to type explicit tool commands such as read or bash just because you need workspace facts.",
                "Use bash only when necessary; bash requires approval before execution.",
                "After using tools, answer the user's request directly.",
                "Do not mention that you used tools, inspected files, checked the workspace, or can continue using tools unless the user explicitly asks about your method or asks for next steps.",
                "For read-only questions, give the result directly instead of writing a work-log style summary.",
                "Do not append extra offers such as 'if you want I can continue...' unless the user asked for options or follow-up help.",
            ]
        if execution_mode == "plan":
            sections.extend(
                [
                    "This turn is in Plan Mode.",
                    "You may inspect and analyze, but you must not modify files, execute shell commands, or perform side effects.",
                    "Return a concrete plan instead of claiming the work is already done.",
                    "Structure the plan with: goal, findings or assumptions, execution steps, and risks or open questions.",
                ]
            )
        if session_memory_header:
            sections.append(session_memory_header)
        return "\n\n".join(sections)

    def _build_subagent_system_prompt(self, workspace: Path) -> str:
        return "\n\n".join(
            [
                "You are Jarvis running as a bounded subagent.",
                f"Target workspace: {workspace}",
                "You may use tools to inspect or modify the workspace when needed.",
                "If uploaded session attachments matter to the task, use the session attachment tools to inspect them.",
                "You may read explicit absolute paths outside the current session workspace only as read-only external references.",
                "You must not write outside the current session workspace.",
                "You must not spawn subagents.",
                "You should be concise and execution-focused.",
                "Return a useful summary of what you found or changed. Keep it focused on the outcome, key evidence, and any remaining blocker.",
            ]
        )

    async def _stream_agent_response(
        self,
        *,
        client,
        session_id: str,
        turn_id: int | None,
        system_prompt: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        cancel_event: asyncio.Event,
        emit_stream_events: bool,
    ) -> list[object] | None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, object | None]] = asyncio.Queue()

        def worker() -> None:
            try:
                for event in client.stream_response(
                    model=settings.model_id,
                    system=system_prompt,
                    messages=messages,
                    tools=tools,
                    max_tokens=settings.llm_max_tokens,
                ):
                    if cancel_event.is_set():
                        break
                    asyncio.run_coroutine_threadsafe(queue.put((str(event.get("type")), event)), loop).result()
            except Exception as exc:
                asyncio.run_coroutine_threadsafe(queue.put(("error", str(exc))), loop).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(("done", None)), loop).result()

        worker_task = asyncio.create_task(asyncio.to_thread(worker))
        text_parts: list[str] = []
        tool_blocks: list[object] = []
        event_text_buffer: list[str] = []

        async def flush_event_text_buffer() -> None:
            if not event_text_buffer:
                return
            chunk = "".join(event_text_buffer)
            event_text_buffer.clear()
            if not chunk:
                return
            if emit_stream_events:
                turn = self.session_turns.get(session_id)
                if turn and turn.cancel_event is cancel_event:
                    turn.partial_text += chunk
                await self.emit_ephemeral(
                    TimelineEvent(
                        session_id=session_id,
                        type="message.assistant.delta",
                        content=chunk,
                    )
                )

        try:
            while True:
                if self._should_cancel_turn(None, cancel_event):
                    raise TurnCancelled
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    if turn_id is not None and self._should_cancel_turn(turn_id, cancel_event):
                        raise TurnCancelled
                    continue
                if kind == "text_delta" and isinstance(payload, dict):
                    delta = str(payload.get("delta") or "")
                    if delta:
                        text_parts.append(delta)
                        event_text_buffer.append(delta)
                        should_flush = (
                            len("".join(event_text_buffer)) >= 48
                            or "\n" in delta
                            or delta.endswith((".", "!", "?", "。", "！", "？"))
                        )
                        if should_flush:
                            await flush_event_text_buffer()
                    continue
                if kind == "tool_use" and isinstance(payload, dict):
                    await flush_event_text_buffer()
                    tool_blocks.append(
                        ToolUseBlock(
                            id=str(payload.get("id") or ""),
                            name=str(payload.get("name") or ""),
                            input=dict(payload.get("input", {}) or {}),
                        )
                    )
                    continue
                if kind == "error":
                    await flush_event_text_buffer()
                    return [TextBlock(text=str(payload or "LLM provider request failed."))]
                if kind == "done":
                    break
        finally:
            if not cancel_event.is_set():
                await worker_task
        await flush_event_text_buffer()

        blocks: list[object] = []
        if text_parts:
            blocks.append(TextBlock(text="".join(text_parts)))
        blocks.extend(tool_blocks)
        return blocks

    async def _continue_agent_loop(
        self,
        session_id: str,
        workspace: Path,
        messages: list[dict[str, object]],
        cancel_event: asyncio.Event,
        *,
        turn_id: int | None = None,
        allowed_external_reads: list[Path] | None = None,
        write_enabled: bool = True,
        allow_subagent_tool: bool = True,
        agent_kind: str = "lead",
        emit_stream_events: bool = True,
        execution_mode: str = "normal",
    ) -> AgentReply:
        client = create_client()
        tool_definitions = await self._autonomous_tool_definitions(allow_subagent_tool=allow_subagent_tool)
        execution_mode = self._normalize_execution_mode(execution_mode)
        if execution_mode == "plan":
            tool_definitions = [tool for tool in tool_definitions if tool.name in self.PLAN_MODE_ALLOWED_TOOLS]
        tools = self._tool_schemas_from_definitions(tool_definitions)
        tool_map = {tool.name: tool for tool in tool_definitions}
        broker_for_workspace = ToolBroker(
            workspace,
            allowed_external_reads=allowed_external_reads,
            write_enabled=write_enabled,
        )
        base_system_prompt = (
            self._build_subagent_system_prompt(workspace)
            if agent_kind == "subagent"
            else self._build_agent_system_prompt(
                workspace,
                self._session_git_prompt_section(session_id),
                execution_mode=execution_mode,
            )
        )

        iteration_limit = (
            settings.jarvis_subagent_iteration_limit
            if agent_kind == "subagent"
            else settings.jarvis_agent_iteration_limit
        )
        generated_asset_ids = self._collect_generated_asset_ids(messages)

        for _ in range(iteration_limit):
            if self._should_cancel_turn(turn_id, cancel_event):
                raise TurnCancelled
            self._write_checkpoint(
                turn_id=turn_id,
                phase="before_model",
                workspace=workspace,
                messages=messages,
                allowed_external_reads=allowed_external_reads,
                write_enabled=write_enabled,
                allow_subagent_tool=allow_subagent_tool,
                agent_kind=agent_kind,
                emit_stream_events=emit_stream_events,
                execution_mode=execution_mode,
                summary="About to call the model for the next agent step.",
            )
            assembled = (
                context_assembler.assemble_context(
                    session_id=session_id,
                    workspace=workspace,
                    messages=messages,
                    base_system_prompt=base_system_prompt,
                    allowed_external_reads=allowed_external_reads,
                    max_tokens=settings.llm_max_tokens,
                )
                if agent_kind == "lead"
                else context_assembler.AssembledContext(
                    system_prompt=base_system_prompt,
                    messages=messages,
                    debug_meta={},
                )
            )
            streamed_blocks = await self._stream_agent_response(
                client=client,
                session_id=session_id,
                turn_id=turn_id,
                system_prompt=assembled.system_prompt,
                messages=assembled.messages,
                tools=tools,
                cancel_event=cancel_event,
                emit_stream_events=emit_stream_events,
            )
            if streamed_blocks is None:
                return AgentReply(text="任务执行失败：模型没有返回可用响应。", asset_ids=generated_asset_ids)
            messages.append({"role": "assistant", "content": self._serialize_content_blocks(streamed_blocks)})
            self._write_checkpoint(
                turn_id=turn_id,
                phase="after_model",
                workspace=workspace,
                messages=messages,
                allowed_external_reads=allowed_external_reads,
                write_enabled=write_enabled,
                allow_subagent_tool=allow_subagent_tool,
                agent_kind=agent_kind,
                emit_stream_events=emit_stream_events,
                execution_mode=execution_mode,
                summary="Model output received for the current agent step.",
            )
            tool_calls = [block for block in streamed_blocks if getattr(block, "type", "") == "tool_use"]
            if not tool_calls:
                text_blocks = [
                    block.text.strip()
                    for block in streamed_blocks
                    if isinstance(block, TextBlock) and block.text.strip()
                ]
                return AgentReply(
                    text="\n\n".join(text_blocks) if text_blocks else "任务已执行，但模型没有返回最终文本说明。",
                    asset_ids=generated_asset_ids,
                )

            results: list[dict[str, object]] = []
            for block in tool_calls:
                tool_definition = tool_map.get(block.name)
                if tool_definition is None:
                    output = f"Unknown tool '{block.name}'"
                    tool_service.create_tool_execution(
                        session_id=session_id,
                        tool_name=block.name,
                        tool_source="local",
                        server_name=None,
                        status="error",
                        input_json=broker_for_workspace.serialize_input(block.input),
                        output_text=output,
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": output,
                        }
                    )
                    continue

                if tool_definition.name == "bash":
                    return AgentReply(
                        text=await self._queue_bash_approval(
                            session_id,
                            workspace,
                            messages,
                            block.id,
                            block.input,
                            turn_id=turn_id,
                            allowed_external_reads=allowed_external_reads,
                            write_enabled=write_enabled,
                            allow_subagent_tool=allow_subagent_tool,
                            agent_kind=agent_kind,
                            emit_stream_events=emit_stream_events,
                            execution_mode=execution_mode,
                        ),
                        asset_ids=generated_asset_ids,
                    )

                started_at = time.perf_counter()
                executed = await self._execute_tool_definition(
                    session_id=session_id,
                    tool=tool_definition,
                    tool_input=block.input,
                    broker_for_workspace=broker_for_workspace,
                    execution_mode=execution_mode,
                )
                if executed is None:
                    return AgentReply(
                        text=await self._queue_bash_approval(
                            session_id,
                            workspace,
                            messages,
                            block.id,
                            block.input,
                            turn_id=turn_id,
                            allowed_external_reads=allowed_external_reads,
                            write_enabled=write_enabled,
                            allow_subagent_tool=allow_subagent_tool,
                            agent_kind=agent_kind,
                            emit_stream_events=emit_stream_events,
                            execution_mode=execution_mode,
                        ),
                        asset_ids=generated_asset_ids,
                    )
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                tool_service.create_tool_execution(
                    session_id=session_id,
                    tool_name=tool_definition.name,
                    tool_source=tool_definition.source,
                    server_name=tool_definition.server_name,
                    status=executed.status,
                    input_json=broker_for_workspace.serialize_input(block.input),
                    output_text=executed.output,
                    latency_ms=latency_ms,
                    remote_request_id=executed.remote_request_id,
                )
                await self.publish(
                    TimelineEvent(
                        session_id=session_id,
                        type="tool.execution",
                        content=f"{tool_definition.name} -> {executed.status}",
                    )
                )
                if (
                    turn_id is not None
                    and executed.status == "completed"
                    and tool_definition.name in {"write_file", "edit_file"}
                ):
                    target_path = str(block.input.get("path", "")).strip()
                    if target_path:
                        memory_service.remember_artifact(
                            session_id,
                            f"{tool_definition.name} updated {target_path}",
                            source_turn_id=turn_id,
                            path_ref=target_path,
                        )
                executed_payload = getattr(executed, "payload", None)
                tool_asset_ids = self._normalize_asset_ids(
                    list(executed_payload.get("asset_ids", []))
                    if isinstance(executed_payload, dict)
                    else []
                )
                for asset_id in tool_asset_ids:
                    if asset_id not in generated_asset_ids:
                        generated_asset_ids.append(asset_id)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": executed.output,
                        "asset_ids": tool_asset_ids,
                    }
                )
            messages.append({"role": "user", "content": results})
            self._write_checkpoint(
                turn_id=turn_id,
                phase="after_tools",
                workspace=workspace,
                messages=messages,
                allowed_external_reads=allowed_external_reads,
                write_enabled=write_enabled,
                allow_subagent_tool=allow_subagent_tool,
                agent_kind=agent_kind,
                emit_stream_events=emit_stream_events,
                execution_mode=execution_mode,
                summary="Tool execution results appended to the loop context.",
            )

        return AgentReply(
            text=f"任务执行达到了安全迭代上限（{iteration_limit} 轮），我先停在这里。你可以让我继续，或告诉我希望收敛到哪一步。",
            asset_ids=generated_asset_ids,
        )

    async def _queue_bash_approval(
        self,
        session_id: str,
        workspace: Path,
        messages: list[dict[str, object]],
        tool_use_id: str,
        tool_input: dict[str, object],
        *,
        turn_id: int | None,
        allowed_external_reads: list[Path] | None,
        write_enabled: bool,
        allow_subagent_tool: bool,
        agent_kind: str,
        emit_stream_events: bool,
        execution_mode: str,
    ) -> str:
        broker_for_workspace = ToolBroker(workspace, allowed_external_reads=allowed_external_reads, write_enabled=write_enabled)
        context = self._build_runtime_context(
            workspace=workspace,
            messages=messages,
            turn_id=turn_id,
            allowed_external_reads=allowed_external_reads,
            write_enabled=write_enabled,
            allow_subagent_tool=allow_subagent_tool,
            agent_kind=agent_kind,
            emit_stream_events=emit_stream_events,
            execution_mode=execution_mode,
            tool_use_id=tool_use_id,
            tool_name="bash",
            tool_input=tool_input,
        )
        checkpoint_id = self._write_checkpoint(
            turn_id=turn_id,
            phase="waiting_approval",
            workspace=workspace,
            messages=messages,
            allowed_external_reads=allowed_external_reads,
            write_enabled=write_enabled,
            allow_subagent_tool=allow_subagent_tool,
            agent_kind=agent_kind,
            emit_stream_events=emit_stream_events,
            execution_mode=execution_mode,
            summary="Waiting for bash approval.",
            tool_use_id=tool_use_id,
            tool_name="bash",
            tool_input=tool_input,
        )
        approval = approval_service.create_approval(
            session_id=session_id,
            turn_id=turn_id,
            checkpoint_id=checkpoint_id,
            approval_type="bash",
            prompt=f"bash\n{broker_for_workspace.serialize_input(tool_input)}",
            context=context,
        )
        if turn_id is not None:
            turn_service.update_turn_status(turn_id, "waiting_approval", resume_hint="Waiting for bash approval.")
            memory_service.remember_open_question(
                session_id,
                f"Approve the pending bash command for turn #{turn_id}?",
                source_turn_id=turn_id,
            )
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="turn.waiting_approval",
                    content=f"Turn #{turn_id} is waiting for bash approval.",
                )
            )
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="approval.requested",
                content=f"Approval #{approval.id} requested for bash.",
            )
        )
        return f"Approval required before running `bash`. Review approval #{approval.id} above the composer."

    async def _resume_agent_loop_after_approval(self, session_id: str, context: dict[str, object]) -> AgentReply | None:
        workspace_raw = context.get("workspace")
        messages = context.get("messages")
        turn_id = context.get("turn_id")
        tool_use_id = context.get("tool_use_id")
        tool_name = context.get("tool_name")
        tool_input = context.get("tool_input")
        allowed_external_reads_raw = context.get("allowed_external_reads")
        write_enabled = bool(context.get("write_enabled", True))
        allow_subagent_tool = bool(context.get("allow_subagent_tool", True))
        agent_kind = str(context.get("agent_kind", "lead"))
        emit_stream_events = bool(context.get("emit_stream_events", True))
        execution_mode = self._normalize_execution_mode(str(context.get("execution_mode", "normal")))

        if not isinstance(workspace_raw, str) or not isinstance(messages, list):
            return AgentReply(
                text="Approval context is incomplete; unable to resume the pending action.",
                asset_ids=[],
            )
        if not isinstance(tool_use_id, str) or not isinstance(tool_name, str) or not isinstance(tool_input, dict):
            return AgentReply(
                text="Approval context is incomplete; unable to execute the approved tool call.",
                asset_ids=[],
            )

        workspace = Path(workspace_raw)
        allowed_external_reads = (
            [Path(raw) for raw in allowed_external_reads_raw if isinstance(raw, str)]
            if isinstance(allowed_external_reads_raw, list)
            else []
        )
        broker_for_workspace = ToolBroker(workspace, allowed_external_reads=allowed_external_reads, write_enabled=write_enabled)
        status, output = broker_for_workspace.run(tool_name, tool_input)
        tool_service.create_tool_execution(
            session_id=session_id,
            tool_name=tool_name,
            tool_source="local",
            server_name=None,
            status=status,
            input_json=broker_for_workspace.serialize_input(tool_input),
            output_text=output,
        )
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="tool.execution",
                content=f"{tool_name} -> {status}",
            )
        )

        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": output,
                    }
                ],
            }
        )
        return await self._continue_agent_loop(
            session_id,
            workspace,
            messages,
            asyncio.Event(),
            turn_id=turn_id if isinstance(turn_id, int) else None,
            allowed_external_reads=allowed_external_reads,
            write_enabled=write_enabled,
            allow_subagent_tool=allow_subagent_tool,
            agent_kind=agent_kind,
            emit_stream_events=emit_stream_events,
            execution_mode=execution_mode,
        )

    async def _resume_turn_from_context(
        self,
        session_id: str,
        turn_id: int,
        context: dict[str, object],
        cancel_event: asyncio.Event,
    ) -> AgentReply:
        workspace_raw = context.get("workspace")
        messages = context.get("messages")
        allowed_external_reads_raw = context.get("allowed_external_reads")
        write_enabled = bool(context.get("write_enabled", True))
        allow_subagent_tool = bool(context.get("allow_subagent_tool", True))
        agent_kind = str(context.get("agent_kind", "lead"))
        emit_stream_events = bool(context.get("emit_stream_events", True))
        execution_mode = self._normalize_execution_mode(str(context.get("execution_mode", "normal")))

        if not isinstance(workspace_raw, str) or not isinstance(messages, list):
            return AgentReply(
                text="Resume context is incomplete; unable to continue the interrupted turn.",
                asset_ids=[],
            )

        workspace = Path(workspace_raw)
        allowed_external_reads = (
            [Path(raw) for raw in allowed_external_reads_raw if isinstance(raw, str)]
            if isinstance(allowed_external_reads_raw, list)
            else []
        )
        return await self._continue_agent_loop(
            session_id,
            workspace,
            messages,
            cancel_event,
            turn_id=turn_id,
            allowed_external_reads=allowed_external_reads,
            write_enabled=write_enabled,
            allow_subagent_tool=allow_subagent_tool,
            agent_kind=agent_kind,
            emit_stream_events=emit_stream_events,
            execution_mode=execution_mode,
        )

    def _serialize_content_blocks(self, blocks: list[object]) -> list[dict[str, object]]:
        serialized: list[dict[str, object]] = []
        for block in blocks:
            block_type = getattr(block, "type", None)
            if block_type == "text" and isinstance(getattr(block, "text", None), str):
                serialized.append({"type": "text", "text": block.text})
                continue
            if block_type == "tool_use":
                serialized.append(
                    {
                        "type": "tool_use",
                        "id": str(getattr(block, "id", "")),
                        "name": str(getattr(block, "name", "")),
                        "input": dict(getattr(block, "input", {}) or {}),
                    }
                )
        return serialized

    def _chunk_text(self, text: str) -> list[str]:
        chunks: list[str] = []
        cursor = 0
        while cursor < len(text):
            next_cursor = min(cursor + 24, len(text))
            chunks.append(text[cursor:next_cursor])
            cursor = next_cursor
        return chunks or [text]
