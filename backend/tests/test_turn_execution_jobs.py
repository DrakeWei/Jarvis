from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.background_job_service as background_job_service
from app.db.base import Base
from app.models import SessionRecord
from app.runtime.manager import RuntimeManager
from app.schemas.events import MessageCreate


class BackgroundJobServiceTests(TestCase):
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
            db.commit()

    @contextmanager
    def _create_session(self):
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def test_create_turn_execution_job_persists_payload(self) -> None:
        with patch.object(background_job_service, "create_session", self._create_session):
            job = background_job_service.create_job(
                session_id="session-1",
                job_type="turn_execution",
                payload={"session_id": "session-1", "turn_id": 12, "content": "Inspect repo"},
                command="turn_execution:12",
            )
            decoded = background_job_service.payload_dict(job)

        self.assertEqual(job.job_type, "turn_execution")
        self.assertEqual(job.status, "queued")
        self.assertEqual(decoded["turn_id"], 12)

    def test_requeue_job_sets_future_attempt_time(self) -> None:
        with patch.object(background_job_service, "create_session", self._create_session):
            job = background_job_service.create_job(
                session_id="session-1",
                job_type="turn_execution",
                payload={"session_id": "session-1", "turn_id": 12, "content": "Inspect repo"},
                command="turn_execution:12",
            )
            retried = background_job_service.requeue_job(job.id, "retry later", delay_seconds=7)

        self.assertEqual(retried.status if retried else None, "queued")
        self.assertIsNotNone(retried.next_attempt_at if retried else None)
        next_attempt_at = retried.next_attempt_at
        if next_attempt_at is not None and next_attempt_at.tzinfo is None:
            next_attempt_at = next_attempt_at.replace(tzinfo=timezone.utc)
        self.assertGreater(next_attempt_at, datetime.now(timezone.utc))

    def test_dead_lettered_job_is_terminal(self) -> None:
        with patch.object(background_job_service, "create_session", self._create_session):
            job = background_job_service.create_job(
                session_id="session-1",
                job_type="turn_execution",
                payload={"session_id": "session-1", "turn_id": 12, "content": "Inspect repo"},
                command="turn_execution:12",
            )
            dead = background_job_service.update_job_dead_lettered(job.id, "permanent infra failure")

        self.assertEqual(dead.status if dead else None, "dead_lettered")
        self.assertEqual(dead.output_text if dead else None, "permanent infra failure")
        self.assertIsNone(dead.next_attempt_at if dead else "not-none")

    def test_list_job_summaries_can_filter_by_status(self) -> None:
        with patch.object(background_job_service, "create_session", self._create_session):
            queued = background_job_service.create_job(
                session_id="session-1",
                job_type="turn_execution",
                payload={"session_id": "session-1", "turn_id": 12, "content": "Inspect repo"},
                command="turn_execution:12",
            )
            failed = background_job_service.update_job_dead_lettered(queued.id, "permanent infra failure")
            queued_two = background_job_service.create_job(
                session_id="session-1",
                job_type="turn_execution",
                payload={"session_id": "session-1", "turn_id": 13, "content": "Inspect repo two"},
                command="turn_execution:13",
            )
            results = background_job_service.list_job_summaries(status="queued")

        self.assertTrue(any(item.id == queued_two.id for item in results))
        self.assertFalse(any(item.id == failed.id for item in results))

    def test_retry_job_now_resets_dead_lettered_job_to_queued(self) -> None:
        with patch.object(background_job_service, "create_session", self._create_session):
            job = background_job_service.create_job(
                session_id="session-1",
                job_type="turn_execution",
                payload={"session_id": "session-1", "turn_id": 12, "content": "Inspect repo"},
                command="turn_execution:12",
            )
            background_job_service.update_job_dead_lettered(job.id, "permanent infra failure")
            retried = background_job_service.retry_job_now(job.id)

        self.assertEqual(retried.status if retried else None, "queued")
        self.assertEqual(retried.attempts if retried else None, 0)
        self.assertIsNone(retried.completed_at if retried else "not-none")

    def test_cancel_job_marks_job_terminal(self) -> None:
        with patch.object(background_job_service, "create_session", self._create_session):
            job = background_job_service.create_job(
                session_id="session-1",
                job_type="turn_execution",
                payload={"session_id": "session-1", "turn_id": 12, "content": "Inspect repo"},
                command="turn_execution:12",
            )
            cancelled = background_job_service.cancel_job(job.id, "operator cancelled")

        self.assertEqual(cancelled.status if cancelled else None, "cancelled")
        self.assertEqual(cancelled.output_text if cancelled else None, "operator cancelled")


class RuntimeTurnQueueTests(IsolatedAsyncioTestCase):
    async def test_append_message_enqueues_turn_execution_job(self) -> None:
        runtime = RuntimeManager()
        created_message = SimpleNamespace(id=101)
        created_turn = SimpleNamespace(id=12)
        created_job = SimpleNamespace(id=88)

        with patch("app.runtime.manager.session_service.create_message_record", return_value=created_message), patch(
            "app.runtime.manager.turn_service.latest_cancellable_turn",
            return_value=None,
        ), patch(
            "app.runtime.manager.turn_service.create_turn",
            return_value=created_turn,
        ), patch("app.runtime.manager.memory_service.remember_goal"), patch(
            "app.runtime.manager.memory_service.refresh_rolling_summary"
        ), patch("app.runtime.manager.RuntimeManager._capture_user_memory_signals"), patch(
            "app.runtime.manager.RuntimeManager._should_autoname_session",
            return_value=False,
        ), patch(
            "app.runtime.manager.background_job_service.create_job",
            return_value=created_job,
        ) as create_job_mock, patch.object(
            runtime,
            "_signal_dispatcher",
        ) as signal_dispatcher_mock, patch.object(
            runtime,
            "_ensure_dispatcher_started",
        ), patch.object(
            runtime,
            "publish",
        ):
            await runtime.append_message(
                "session-1",
                MessageCreate(role="user", content="Inspect repo"),
            )

        create_job_mock.assert_called_once()
        signal_dispatcher_mock.assert_called_once()

    async def test_retry_background_job_signals_dispatcher(self) -> None:
        runtime = RuntimeManager()
        summary = SimpleNamespace(id=77, session_id="session-1", job_type="turn_execution")
        with patch("app.runtime.manager.background_job_service.retry_job_now", return_value=SimpleNamespace(id=77)), patch(
            "app.runtime.manager.background_job_service.get_job_summary",
            return_value=summary,
        ), patch.object(runtime, "_ensure_dispatcher_started") as ensure_mock, patch.object(
            runtime,
            "_signal_dispatcher",
        ) as signal_mock, patch.object(runtime, "publish"):
            result = await runtime.retry_background_job(77)

        self.assertEqual(result.id, 77)
        ensure_mock.assert_called_once()
        signal_mock.assert_called_once()

    async def test_cancel_background_job_requests_turn_cancel(self) -> None:
        runtime = RuntimeManager()
        job_row = SimpleNamespace(id=77, job_type="turn_execution", session_id="session-1")
        job_summary = SimpleNamespace(id=77, session_id="session-1", job_type="turn_execution")

        with patch("app.runtime.manager.background_job_service.get_job", return_value=job_row), patch(
            "app.runtime.manager.background_job_service.payload_dict",
            return_value={"session_id": "session-1", "turn_id": 12, "content": "Inspect repo"},
        ), patch("app.runtime.manager.background_job_service.cancel_job", return_value=job_row), patch(
            "app.runtime.manager.background_job_service.get_job_summary",
            return_value=job_summary,
        ), patch("app.runtime.manager.turn_service.request_turn_cancel") as request_cancel_mock, patch.object(
            runtime,
            "publish",
        ):
            result = await runtime.cancel_background_job(77)

        self.assertEqual(result.id, 77)
        request_cancel_mock.assert_called_once_with(12)

    async def test_append_message_requests_cancel_on_previous_turn(self) -> None:
        runtime = RuntimeManager()
        created_message = SimpleNamespace(id=101)
        previous_turn = SimpleNamespace(id=11)
        created_turn = SimpleNamespace(id=12)
        created_job = SimpleNamespace(id=88)

        with patch("app.runtime.manager.session_service.create_message_record", return_value=created_message), patch(
            "app.runtime.manager.turn_service.latest_cancellable_turn",
            return_value=previous_turn,
        ), patch("app.runtime.manager.turn_service.request_turn_cancel") as request_cancel_mock, patch(
            "app.runtime.manager.turn_service.create_turn",
            return_value=created_turn,
        ), patch("app.runtime.manager.memory_service.remember_goal"), patch(
            "app.runtime.manager.memory_service.refresh_rolling_summary"
        ), patch("app.runtime.manager.RuntimeManager._capture_user_memory_signals"), patch(
            "app.runtime.manager.RuntimeManager._should_autoname_session",
            return_value=False,
        ), patch(
            "app.runtime.manager.background_job_service.create_job",
            return_value=created_job,
        ), patch.object(runtime, "_signal_dispatcher"), patch.object(runtime, "_ensure_dispatcher_started"), patch.object(
            runtime,
            "publish",
        ):
            await runtime.append_message("session-1", MessageCreate(role="user", content="New request"))

        request_cancel_mock.assert_called_once_with(11)

    async def test_dispatch_recoverable_jobs_starts_unscheduled_turn_job(self) -> None:
        runtime = RuntimeManager()
        queued_job = SimpleNamespace(id=77, job_type="turn_execution", session_id="session-a")
        with patch("app.runtime.manager.background_job_service.list_recoverable_jobs", return_value=[queued_job]), patch(
            "app.runtime.manager.ingestion_job_service.list_recoverable_jobs",
            return_value=[],
        ), patch("app.runtime.manager.lease_service.is_active", return_value=False), patch.object(
            runtime,
            "_start_background_job",
        ) as start_job_mock:
            runtime._dispatch_recoverable_jobs()

        start_job_mock.assert_called_once_with(77)

    async def test_dispatch_recoverable_jobs_skips_already_scheduled_job(self) -> None:
        runtime = RuntimeManager()
        runtime.scheduled_background_job_ids.add(77)
        queued_job = SimpleNamespace(id=77, job_type="turn_execution", session_id="session-a")
        with patch("app.runtime.manager.background_job_service.list_recoverable_jobs", return_value=[queued_job]), patch(
            "app.runtime.manager.ingestion_job_service.list_recoverable_jobs",
            return_value=[],
        ), patch("app.runtime.manager.lease_service.is_active", return_value=False), patch.object(
            runtime,
            "_start_background_job",
        ) as start_job_mock:
            runtime._dispatch_recoverable_jobs()

        start_job_mock.assert_not_called()

    async def test_dispatch_recoverable_jobs_skips_non_due_queued_job(self) -> None:
        runtime = RuntimeManager()
        with patch("app.runtime.manager.background_job_service.list_recoverable_jobs", return_value=[]), patch(
            "app.runtime.manager.ingestion_job_service.list_recoverable_jobs",
            return_value=[],
        ), patch.object(
            runtime,
            "_start_background_job",
        ) as start_job_mock:
            runtime._dispatch_recoverable_jobs()

        start_job_mock.assert_not_called()

    async def test_dispatch_recoverable_jobs_respects_turn_job_budget(self) -> None:
        runtime = RuntimeManager()
        queued_jobs = [
            SimpleNamespace(id=1, job_type="turn_execution", session_id="session-a"),
            SimpleNamespace(id=2, job_type="turn_execution", session_id="session-b"),
            SimpleNamespace(id=3, job_type="turn_execution", session_id="session-c"),
        ]
        with patch("app.runtime.manager.settings.jarvis_max_concurrent_turn_jobs", 2), patch(
            "app.runtime.manager.background_job_service.list_recoverable_jobs",
            return_value=queued_jobs,
        ), patch(
            "app.runtime.manager.ingestion_job_service.list_recoverable_jobs",
            return_value=[],
        ), patch("app.runtime.manager.lease_service.is_active", return_value=False), patch.object(
            runtime,
            "_start_background_job",
        ) as start_job_mock:
            runtime._dispatch_recoverable_jobs()

        self.assertEqual(start_job_mock.call_count, 2)

    async def test_dispatch_recoverable_jobs_enforces_basic_session_fairness(self) -> None:
        runtime = RuntimeManager()
        queued_jobs = [
            SimpleNamespace(id=1, job_type="turn_execution", session_id="session-a"),
            SimpleNamespace(id=2, job_type="turn_execution", session_id="session-a"),
            SimpleNamespace(id=3, job_type="turn_execution", session_id="session-b"),
        ]
        with patch("app.runtime.manager.settings.jarvis_max_concurrent_turn_jobs", 4), patch(
            "app.runtime.manager.background_job_service.list_recoverable_jobs",
            return_value=queued_jobs,
        ), patch(
            "app.runtime.manager.ingestion_job_service.list_recoverable_jobs",
            return_value=[],
        ), patch("app.runtime.manager.lease_service.is_active", return_value=False), patch.object(
            runtime,
            "_start_background_job",
        ) as start_job_mock:
            runtime._dispatch_recoverable_jobs()

        started_ids = [call.args[0] for call in start_job_mock.call_args_list]
        self.assertEqual(started_ids, [1, 3])

    async def test_dispatch_recoverable_jobs_skips_session_with_active_turn_lane(self) -> None:
        runtime = RuntimeManager()
        queued_job = SimpleNamespace(id=77, job_type="turn_execution", session_id="session-a")

        def is_active(scope_type: str, scope_key: str) -> bool:
            return scope_type == "session_turn_lane" and scope_key == "session-a"

        with patch("app.runtime.manager.background_job_service.list_recoverable_jobs", return_value=[queued_job]), patch(
            "app.runtime.manager.ingestion_job_service.list_recoverable_jobs",
            return_value=[],
        ), patch("app.runtime.manager.lease_service.is_active", side_effect=is_active), patch.object(
            runtime,
            "_start_background_job",
        ) as start_job_mock:
            runtime._dispatch_recoverable_jobs()

        start_job_mock.assert_not_called()

    async def test_run_background_job_requeues_when_session_turn_lane_busy(self) -> None:
        runtime = RuntimeManager()
        job_row = SimpleNamespace(id=77, job_type="turn_execution", session_id="session-a", attempts=0, status="queued")

        def try_acquire(scope_type: str, scope_key: str, owner_id: str) -> bool:
            if scope_type == "background_job":
                return True
            if scope_type == "session_turn_lane":
                return False
            return True

        with patch("app.runtime.manager.background_job_service.get_job", return_value=job_row), patch(
            "app.runtime.manager.lease_service.try_acquire",
            side_effect=try_acquire,
        ), patch.object(
            runtime,
            "_lease_heartbeat_loop",
            return_value=None,
        ), patch("app.runtime.manager.background_job_service.update_job_running", return_value=job_row), patch(
            "app.runtime.manager.background_job_service.payload_dict",
            return_value={"session_id": "session-a", "turn_id": 12, "content": "Inspect repo"},
        ), patch(
            "app.runtime.manager.turn_service.get_turn",
            return_value=SimpleNamespace(id=12, status="queued"),
        ), patch("app.runtime.manager.background_job_service.requeue_job") as requeue_job_mock, patch.object(
            runtime,
            "_start_background_turn",
        ) as start_turn_mock, patch("app.runtime.manager.lease_service.release"):
            await runtime._run_background_job(77)

        requeue_job_mock.assert_called_once()
        start_turn_mock.assert_not_called()

    async def test_run_background_job_marks_superseded_turn_job_completed(self) -> None:
        runtime = RuntimeManager()
        job_row = SimpleNamespace(id=77, job_type="turn_execution", session_id="session-a", attempts=0, status="queued")

        with patch("app.runtime.manager.background_job_service.get_job", return_value=job_row), patch(
            "app.runtime.manager.lease_service.try_acquire",
            return_value=True,
        ), patch.object(
            runtime,
            "_lease_heartbeat_loop",
            return_value=None,
        ), patch("app.runtime.manager.background_job_service.update_job_running", return_value=job_row), patch(
            "app.runtime.manager.background_job_service.payload_dict",
            return_value={"session_id": "session-a", "turn_id": 12, "content": "Inspect repo"},
        ), patch(
            "app.runtime.manager.turn_service.get_turn",
            return_value=SimpleNamespace(id=12, status="queued"),
        ), patch("app.runtime.manager.turn_service.has_newer_turn", return_value=True), patch(
            "app.runtime.manager.turn_service.update_turn_status"
        ) as update_turn_mock, patch("app.runtime.manager.background_job_service.update_job_completed") as update_job_mock, patch(
            "app.runtime.manager.lease_service.release"
        ):
            await runtime._run_background_job(77)

        update_turn_mock.assert_called_once()
        update_job_mock.assert_called_once_with(77, "superseded")
