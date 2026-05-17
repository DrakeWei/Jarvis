from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.providers import TextBlock, ToolUseBlock
import app.services.approval_service as approval_service
import app.services.background_job_service as background_job_service
import app.services.ingestion_job_service as ingestion_job_service
import app.services.lease_service as lease_service
import app.services.turn_service as turn_service
from app.db.base import Base
from app.models import ExecutionLeaseRecord, SessionAssetRecord, SessionRecord, TurnRecord
from app.runtime.manager import RuntimeManager


class Phase1DurableStateTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

        with self._create_session() as db:
            db.add(
                SessionRecord(
                    id="session-1",
                    title="Session",
                    workspace_mode="bound",
                    canonical_workspace_path="/tmp/workspace",
                    workspace_fingerprint="workspace-fp",
                    workspace_label="workspace",
                    status="idle",
                )
            )
            db.add(
                SessionAssetRecord(
                    id="asset-1",
                    session_id="session-1",
                    kind="pdf",
                    mime_type="application/pdf",
                    filename="report.pdf",
                    size_bytes=100,
                    sha256="abc",
                    storage_path="/tmp/report.pdf",
                    status="uploaded",
                )
            )
            db.add(
                TurnRecord(
                    id=12,
                    session_id="session-1",
                    user_message_id=None,
                    workspace_path="/tmp/workspace",
                    workspace_fingerprint="workspace-fp",
                    status="running",
                )
            )
            db.commit()

    @contextmanager
    def _create_session(self):
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def test_execution_lease_blocks_second_owner_until_release(self) -> None:
        with patch.object(lease_service, "create_session", self._create_session):
            self.assertTrue(lease_service.try_acquire("turn", "1", "owner-a", ttl_seconds=60))
            self.assertFalse(lease_service.try_acquire("turn", "1", "owner-b", ttl_seconds=60))
            self.assertTrue(lease_service.renew("turn", "1", "owner-a", ttl_seconds=60))
            self.assertTrue(lease_service.release("turn", "1", "owner-a"))
            self.assertTrue(lease_service.try_acquire("turn", "1", "owner-b", ttl_seconds=60))

    def test_expired_execution_lease_can_be_reacquired(self) -> None:
        expired_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        with self._create_session() as db:
            db.add(
                ExecutionLeaseRecord(
                    scope_type="turn",
                    scope_key="99",
                    owner_id="owner-a",
                    status="active",
                    acquired_at=expired_at,
                    heartbeat_at=expired_at,
                    expires_at=expired_at,
                )
            )
            db.commit()

        with patch.object(lease_service, "create_session", self._create_session):
            self.assertTrue(lease_service.try_acquire("turn", "99", "owner-b", ttl_seconds=60))
            leases = lease_service.list_leases(scope_type="turn", status="active")

        reacquired = next((lease for lease in leases if lease.scope_key == "99"), None)
        self.assertEqual(reacquired.owner_id if reacquired else None, "owner-b")

    def test_force_release_execution_lease_marks_it_released(self) -> None:
        with patch.object(lease_service, "create_session", self._create_session):
            self.assertTrue(lease_service.try_acquire("turn", "1", "owner-a", ttl_seconds=60))
            leases = lease_service.list_leases(scope_type="turn")
            released = lease_service.force_release(leases[0].id)

        self.assertEqual(released.status if released else None, "released")
        self.assertEqual(released.owner_id if released else None, "operator")

    def test_pending_approval_context_is_recoverable_without_in_memory_cache(self) -> None:
        with patch.object(approval_service, "create_session", self._create_session):
            created = approval_service.create_approval(
                session_id="session-1",
                approval_type="bash",
                prompt="bash\nls",
                context={"turn_id": 12, "workspace": "/tmp/workspace"},
            )
            recovered = approval_service.get_pending_runtime_context(created.id)

        self.assertIsNotNone(recovered)
        session_id, context = recovered or (None, None)
        self.assertEqual(session_id, "session-1")
        self.assertEqual(context["turn_id"], 12)

    def test_repeat_approval_decision_is_idempotent(self) -> None:
        with patch.object(approval_service, "create_session", self._create_session):
            created = approval_service.create_approval(
                session_id="session-1",
                approval_type="bash",
                prompt="bash\nls",
                context={"turn_id": 12},
            )
            first, first_changed = approval_service.apply_approval_decision(created.id, True, "approved")
            second, second_changed = approval_service.apply_approval_decision(created.id, False, "rejected")

        self.assertTrue(first_changed)
        self.assertEqual(first.status if first else None, "approved")
        self.assertFalse(second_changed)
        self.assertEqual(second.status if second else None, "approved")

    def test_ingestion_job_lifecycle_is_durable(self) -> None:
        with patch.object(ingestion_job_service, "create_session", self._create_session):
            job = ingestion_job_service.create_job("session-1", "asset-1")
            running = ingestion_job_service.update_job_running(job.id, "runtime-a")
            completed = ingestion_job_service.update_job_completed(job.id)

        self.assertEqual(job.status, "queued")
        self.assertEqual(running.status if running else None, "running")
        self.assertEqual(running.owner_id if running else None, "runtime-a")
        self.assertEqual(completed.status if completed else None, "completed")

    def test_recover_orphaned_running_turns_skips_turn_with_active_lease(self) -> None:
        now = datetime.now(timezone.utc)
        with self._create_session() as db:
            db.add(
                ExecutionLeaseRecord(
                    scope_type="turn",
                    scope_key="12",
                    owner_id="runtime-a",
                    status="active",
                    acquired_at=now,
                    heartbeat_at=now,
                    expires_at=now + timedelta(seconds=60),
                )
            )
            db.commit()

        with patch.object(turn_service, "create_session", self._create_session):
            recovered = turn_service.recover_orphaned_running_turns()
            still_running = turn_service.get_turn(12)

        self.assertEqual(recovered, [])
        self.assertEqual(still_running.status if still_running else None, "running")

    def test_turn_cancel_request_is_durable(self) -> None:
        with patch.object(turn_service, "create_session", self._create_session):
            requested = turn_service.request_turn_cancel(12)
            self.assertTrue(requested.cancel_requested if requested else False)
            self.assertTrue(turn_service.is_cancel_requested(12))

    def test_has_newer_turn_detects_later_turns(self) -> None:
        with self._create_session() as db:
            db.add(
                TurnRecord(
                    id=13,
                    session_id="session-1",
                    user_message_id=None,
                    workspace_path="/tmp/workspace",
                    workspace_fingerprint="workspace-fp",
                    status="queued",
                )
            )
            db.commit()

        with patch.object(turn_service, "create_session", self._create_session):
            self.assertTrue(turn_service.has_newer_turn("session-1", 12))
            self.assertFalse(turn_service.has_newer_turn("session-1", 13))


class Phase1ConcurrentDurableStateTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmpdir = TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "concurrency.db"
        self.engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

        with self._create_session() as db:
            db.add(
                SessionRecord(
                    id="session-1",
                    title="Session",
                    workspace_mode="bound",
                    canonical_workspace_path="/tmp/workspace",
                    workspace_fingerprint="workspace-fp",
                    workspace_label="workspace",
                    status="idle",
                )
            )
            db.commit()

    def tearDown(self) -> None:
        try:
            self.engine.dispose()
        finally:
            self.tmpdir.cleanup()
        super().tearDown()

    @contextmanager
    def _create_session(self):
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def test_concurrent_lease_acquire_has_single_winner(self) -> None:
        barrier = threading.Barrier(2)

        def attempt(owner_id: str) -> bool:
            barrier.wait(timeout=5)
            return lease_service.try_acquire("turn", "concurrent-1", owner_id, ttl_seconds=60)

        with patch.object(lease_service, "create_session", self._create_session), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(attempt, "owner-a"),
                pool.submit(attempt, "owner-b"),
            ]
            results = [future.result(timeout=5) for future in futures]
            leases = lease_service.list_leases(scope_type="turn", status="active")

        self.assertEqual(sum(1 for item in results if item), 1)
        winner = "owner-a" if results[0] else "owner-b"
        active = next((lease for lease in leases if lease.scope_key == "concurrent-1"), None)
        self.assertEqual(active.owner_id if active else None, winner)

    def test_concurrent_approval_decisions_allow_single_state_change(self) -> None:
        with patch.object(approval_service, "create_session", self._create_session):
            created = approval_service.create_approval(
                session_id="session-1",
                approval_type="bash",
                prompt="bash\nls",
                context={"turn_id": 12},
            )

            barrier = threading.Barrier(2)

            def decide(approve: bool) -> tuple[str | None, bool]:
                barrier.wait(timeout=5)
                summary, changed = approval_service.apply_approval_decision(created.id, approve, "feedback")
                return (summary.status if summary else None, changed)

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(decide, True),
                    pool.submit(decide, False),
                ]
                results = [future.result(timeout=5) for future in futures]

        changed_count = sum(1 for _status, changed in results if changed)
        statuses = {status for status, _changed in results}
        self.assertEqual(changed_count, 1)
        self.assertEqual(len(statuses), 1)
        self.assertIn(next(iter(statuses)), {"approved", "rejected"})

    def test_cross_runtime_session_turn_lane_conflict_requeues_second_job(self) -> None:
        with self._create_session() as db:
            db.add(
                TurnRecord(
                    id=12,
                    session_id="session-1",
                    user_message_id=None,
                    workspace_path="/tmp/workspace",
                    workspace_fingerprint="workspace-fp",
                    status="queued",
                )
            )
            db.commit()

        with patch.object(background_job_service, "create_session", self._create_session), patch.object(
            lease_service,
            "create_session",
            self._create_session,
        ), patch.object(turn_service, "create_session", self._create_session):
            job = background_job_service.create_job(
                session_id="session-1",
                job_type="turn_execution",
                payload={"session_id": "session-1", "turn_id": 12, "content": "Inspect repo"},
                command="turn_execution:12",
            )
            runtime_a = RuntimeManager()
            runtime_b = RuntimeManager()

            self.assertTrue(lease_service.try_acquire("session_turn_lane", "session-1", runtime_a.instance_id, ttl_seconds=60))

            with patch.object(runtime_b, "_lease_heartbeat_loop", return_value=None):
                asyncio.run(runtime_b._run_background_job(job.id))

            summary = background_job_service.get_job_summary(job.id)

        self.assertEqual(summary.status if summary else None, "queued")
        self.assertEqual(summary.attempts if summary else None, 1)
        self.assertIn("active turn lane", summary.output_text if summary and summary.output_text else "")

    def test_cross_runtime_approval_resolution_triggers_single_enqueue(self) -> None:
        with self._create_session() as db:
            db.add(
                TurnRecord(
                    id=12,
                    session_id="session-1",
                    user_message_id=None,
                    workspace_path="/tmp/workspace",
                    workspace_fingerprint="workspace-fp",
                    status="waiting_approval",
                )
            )
            db.commit()

        with patch.object(approval_service, "create_session", self._create_session), patch.object(
            lease_service,
            "create_session",
            self._create_session,
        ), patch.object(turn_service, "create_session", self._create_session):
            approval = approval_service.create_approval(
                session_id="session-1",
                turn_id=12,
                approval_type="bash",
                prompt="bash\nls",
                context={"turn_id": 12, "workspace": "/tmp/workspace"},
            )

            runtime_a = RuntimeManager()
            runtime_b = RuntimeManager()
            enqueue_calls: list[tuple[str, int | None, str]] = []
            publish_calls: list[str] = []
            enqueue_lock = threading.Lock()
            publish_lock = threading.Lock()

            async def fake_publish(event):
                with publish_lock:
                    publish_calls.append(event.type)
                return event

            def fake_enqueue(session_id: str, turn_id: int | None, context: dict[str, object], phase: str) -> bool:
                with enqueue_lock:
                    enqueue_calls.append((session_id, turn_id, phase))
                return True

            runtime_a.publish = fake_publish
            runtime_b.publish = fake_publish
            runtime_a._enqueue_turn_resume_job = fake_enqueue
            runtime_b._enqueue_turn_resume_job = fake_enqueue

            barrier = threading.Barrier(2)

            def decide(runtime: RuntimeManager):
                barrier.wait(timeout=5)
                return asyncio.run(runtime.decide_approval(approval.id, approve=True, feedback="approved"))

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(decide, runtime_a),
                    pool.submit(decide, runtime_b),
                ]
                results = [future.result(timeout=5) for future in futures]

            resolved = approval_service.get_approval(approval.id)

        self.assertEqual(resolved.status if resolved else None, "approved")
        self.assertEqual(len(enqueue_calls), 1)
        self.assertEqual(enqueue_calls[0], ("session-1", 12, "waiting_approval"))
        self.assertEqual(publish_calls.count("approval.resolved"), 1)
        self.assertGreaterEqual(sum(1 for item in results if item is not None), 1)


class Phase1LeaseHeartbeatAsyncTests(IsolatedAsyncioTestCase):
    async def test_lease_heartbeat_loop_cancels_turn_when_renew_fails(self) -> None:
        runtime = RuntimeManager()
        stop_event = asyncio.Event()
        cancel_event = asyncio.Event()

        with patch("app.runtime.manager.settings.jarvis_execution_lease_heartbeat_seconds", 1), patch(
            "app.runtime.manager.lease_service.renew",
            return_value=False,
        ):
            await runtime._lease_heartbeat_loop(
                "turn",
                "12",
                stop_event,
                cancel_event=cancel_event,
            )

        self.assertTrue(cancel_event.is_set())

    async def test_queue_bash_approval_does_not_depend_on_in_memory_pending_state(self) -> None:
        runtime = RuntimeManager()

        class FakeToolBroker:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def serialize_input(self, tool_input: dict[str, object]) -> str:
                return str(tool_input)

        with patch("app.runtime.manager.ToolBroker", FakeToolBroker), patch.object(
            runtime,
            "_build_runtime_context",
            return_value={"turn_id": 12, "workspace": "/tmp/workspace"},
        ), patch.object(runtime, "_write_checkpoint", return_value=34), patch(
            "app.runtime.manager.approval_service.create_approval",
            return_value=SimpleNamespace(id=56),
        ), patch("app.runtime.manager.turn_service.update_turn_status"), patch(
            "app.runtime.manager.memory_service.remember_open_question"
        ), patch.object(runtime, "publish"):
            message = await runtime._queue_bash_approval(
                "session-1",
                Path("/tmp/workspace"),
                messages=[],
                tool_use_id="toolu_123",
                tool_input={"cmd": "ls"},
                turn_id=12,
                allowed_external_reads=[],
                write_enabled=True,
                allow_subagent_tool=False,
                agent_kind="lead",
                emit_stream_events=False,
                execution_mode="normal",
            )

        self.assertIn("Approval required before running `bash`", message)

    async def test_continue_agent_loop_writes_after_tools_checkpoint_with_write_flag(self) -> None:
        runtime = RuntimeManager()
        tool_definition = SimpleNamespace(
            name="list_skills",
            description="List skills.",
            input_schema={"type": "object", "properties": {}},
            source="local",
            server_name=None,
        )
        streamed_blocks = [
            [ToolUseBlock(id="toolu_1", name="list_skills", input={})],
            [TextBlock(text="done")],
        ]
        checkpoint_calls: list[tuple[str, bool]] = []

        def capture_checkpoint(
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
        ) -> int:
            checkpoint_calls.append((phase, write_enabled))
            return len(checkpoint_calls)

        with patch("app.runtime.manager.create_client", return_value=object()), patch(
            "app.runtime.manager.turn_service.is_cancel_requested",
            return_value=False,
        ), patch.object(
            runtime,
            "_autonomous_tool_definitions",
            return_value=[tool_definition],
        ), patch.object(
            runtime,
            "_stream_agent_response",
            side_effect=streamed_blocks,
        ), patch.object(
            runtime,
            "_execute_tool_definition",
            return_value=SimpleNamespace(status="completed", output="ok", remote_request_id=None),
        ), patch.object(runtime, "_write_checkpoint", side_effect=capture_checkpoint), patch.object(
            runtime,
            "publish",
        ), patch("app.runtime.manager.tool_service.create_tool_execution"):
            result = await runtime._continue_agent_loop(
                "session-1",
                Path("/tmp/workspace"),
                [{"role": "user", "content": "Inspect available skills."}],
                asyncio.Event(),
                turn_id=12,
                allowed_external_reads=[],
                write_enabled=False,
                allow_subagent_tool=False,
                agent_kind="subagent",
                emit_stream_events=False,
            )

        self.assertEqual(result.text, "done")
        self.assertEqual(result.asset_ids, [])
        self.assertIn(("after_tools", False), checkpoint_calls)

    async def test_resume_turn_enqueues_background_resume_job(self) -> None:
        runtime = RuntimeManager()
        checkpoint_row = SimpleNamespace(phase="before_model")
        turn = SimpleNamespace(id=12, session_id="session-1", status="interrupted")

        with patch("app.runtime.manager.turn_service.get_turn", return_value=turn), patch(
            "app.runtime.manager.checkpoint_service.latest_resumable_checkpoint_context",
            return_value=(checkpoint_row, {"workspace": "/tmp/workspace", "messages": []}),
        ), patch.object(runtime, "_enqueue_turn_resume_job", return_value=True) as enqueue_mock:
            resumed = await runtime.resume_turn(12)

        self.assertTrue(resumed)
        enqueue_mock.assert_called_once_with(
            "session-1",
            12,
            {"workspace": "/tmp/workspace", "messages": []},
            "before_model",
        )

    async def test_run_resumed_turn_uses_approval_resume_path_for_waiting_approval(self) -> None:
        runtime = RuntimeManager()
        reply = SimpleNamespace(text="approved output", asset_ids=[])

        with patch.object(
            runtime,
            "_resume_agent_loop_after_approval",
            return_value=reply,
        ) as approval_resume_mock, patch.object(
            runtime,
            "_publish_assistant_reply",
        ) as publish_reply_mock, patch(
            "app.runtime.manager.turn_service.update_turn_status"
        ), patch(
            "app.runtime.manager.turn_service.get_turn",
            return_value=SimpleNamespace(status="running"),
        ), patch.object(
            runtime,
            "publish",
        ), patch(
            "app.runtime.manager.lease_service.try_acquire",
            return_value=True,
        ), patch(
            "app.runtime.manager.lease_service.release"
        ), patch.object(
            runtime,
            "_lease_heartbeat_loop",
            return_value=None,
        ):
            await runtime._run_resumed_turn(
                "session-1",
                12,
                {"workspace": "/tmp/workspace", "messages": []},
                "waiting_approval",
                asyncio.Event(),
            )

        approval_resume_mock.assert_awaited_once_with("session-1", {"workspace": "/tmp/workspace", "messages": []})
        publish_reply_mock.assert_awaited_once()

    async def test_decide_approval_enqueues_resume_job_on_approve(self) -> None:
        runtime = RuntimeManager()
        decision = SimpleNamespace(session_id="session-1", status="approved", approval_type="bash")

        with patch("app.runtime.manager.lease_service.try_acquire", return_value=True), patch(
            "app.runtime.manager.approval_service.get_pending_runtime_context",
            return_value=("session-1", {"turn_id": 12, "workspace": "/tmp/workspace"}),
        ), patch(
            "app.runtime.manager.approval_service.apply_approval_decision",
            return_value=(decision, True),
        ), patch("app.runtime.manager.turn_service.update_turn_status"), patch.object(
            runtime,
            "_enqueue_turn_resume_job",
            return_value=True,
        ) as enqueue_mock, patch.object(runtime, "publish"), patch(
            "app.runtime.manager.lease_service.release"
        ):
            result = await runtime.decide_approval(56, approve=True, feedback="")

        self.assertIs(result, decision)
        enqueue_mock.assert_called_once_with(
            "session-1",
            12,
            {"turn_id": 12, "workspace": "/tmp/workspace"},
            "waiting_approval",
        )

    async def test_decide_approval_noops_when_already_resolved(self) -> None:
        runtime = RuntimeManager()
        decision = SimpleNamespace(session_id="session-1", status="approved")

        with patch("app.runtime.manager.lease_service.try_acquire", return_value=True), patch(
            "app.runtime.manager.approval_service.get_pending_runtime_context",
            return_value=None,
        ), patch(
            "app.runtime.manager.approval_service.apply_approval_decision",
            return_value=(decision, False),
        ), patch.object(runtime, "_enqueue_turn_resume_job") as enqueue_mock, patch.object(
            runtime,
            "publish",
        ) as publish_mock, patch("app.runtime.manager.lease_service.release"):
            result = await runtime.decide_approval(56, approve=True, feedback="")

        self.assertIs(result, decision)
        enqueue_mock.assert_not_called()
        publish_mock.assert_not_called()

    async def test_decide_plan_execution_approval_starts_plan_execution_flow(self) -> None:
        runtime = RuntimeManager()
        decision = SimpleNamespace(session_id="session-1", status="approved", approval_type="plan_execution")

        with patch("app.runtime.manager.lease_service.try_acquire", return_value=True), patch(
            "app.runtime.manager.approval_service.get_pending_runtime_context",
            return_value=(
                "session-1",
                {
                    "source_turn_id": 12,
                    "original_request": "Refactor the runtime manager",
                    "approved_plan": "1. Inspect files\n2. Edit them",
                },
            ),
        ), patch(
            "app.runtime.manager.approval_service.apply_approval_decision",
            return_value=(decision, True),
        ), patch.object(runtime, "_start_plan_execution_from_approval", return_value=True) as start_plan_mock, patch.object(
            runtime,
            "publish",
        ) as publish_mock, patch("app.runtime.manager.lease_service.release"):
            result = await runtime.decide_approval(56, approve=True, feedback="")

        self.assertIs(result, decision)
        start_plan_mock.assert_awaited_once_with(
            "session-1",
            {
                "source_turn_id": 12,
                "original_request": "Refactor the runtime manager",
                "approved_plan": "1. Inspect files\n2. Edit them",
            },
        )
        publish_mock.assert_awaited()
