from __future__ import annotations

from types import SimpleNamespace
import sys
import types
from unittest import TestCase
from unittest.mock import patch

from app.runtime.manager import EventBroker, RuntimeManager, build_event_broker
from app.realtime_redis_broker import RedisEventBroker


class RuntimeObservabilityTests(TestCase):
    def test_build_event_broker_falls_back_to_local_without_redis_config(self) -> None:
        with patch("app.runtime.manager.settings.jarvis_event_bus_backend", "redis"), patch(
            "app.runtime.manager.settings.jarvis_redis_url",
            "",
        ):
            broker = build_event_broker()

        self.assertIsInstance(broker, EventBroker)

    def test_build_event_broker_can_activate_redis_backend(self) -> None:
        fake_asyncio_module = types.ModuleType("redis.asyncio")
        fake_asyncio_module.from_url = lambda *args, **kwargs: object()
        fake_redis_module = types.ModuleType("redis")
        fake_redis_module.asyncio = fake_asyncio_module
        with patch.dict(sys.modules, {"redis": fake_redis_module}), patch(
            "app.runtime.manager.settings.jarvis_event_bus_backend",
            "redis",
        ), patch("app.runtime.manager.settings.jarvis_redis_url", "redis://localhost:6379/0"):
            broker = build_event_broker()

        self.assertIsInstance(broker, RedisEventBroker)

    def test_observability_summary_aggregates_runtime_state(self) -> None:
        runtime = RuntimeManager()
        queue = runtime.events.subscribe("session-1")
        runtime.events.dropped_events_total = 3
        try:
            with patch.object(runtime, "list_sessions", return_value=[SimpleNamespace(session_id="session-1")]), patch.object(
                runtime,
                "list_turns",
                return_value=[
                    SimpleNamespace(status="running"),
                    SimpleNamespace(status="running"),
                    SimpleNamespace(status="waiting_approval"),
                ],
            ), patch("app.runtime.manager.background_job_service.status_counts", return_value={"queued": 2, "dead_lettered": 1}), patch(
                "app.runtime.manager.ingestion_job_service.status_counts",
                return_value={"queued": 1, "running": 1},
            ), patch(
                "app.runtime.manager.background_job_service.oldest_queued_age_seconds",
                return_value=12.5,
            ), patch(
                "app.runtime.manager.ingestion_job_service.oldest_queued_age_seconds",
                return_value=4.0,
            ), patch(
                "app.runtime.manager.background_job_service.oldest_running_age_seconds",
                return_value=22.0,
            ), patch(
                "app.runtime.manager.ingestion_job_service.oldest_running_age_seconds",
                return_value=8.0,
            ), patch(
                "app.runtime.manager.turn_service.oldest_running_turn_age_seconds",
                return_value=31.0,
            ), patch(
                "app.runtime.manager.background_job_service.retrying_count",
                return_value=5,
            ), patch(
                "app.runtime.manager.ingestion_job_service.retrying_count",
                return_value=2,
            ):
                runtime.scheduled_background_job_ids.update({1, 2})
                runtime.scheduled_ingestion_job_ids.update({3})
                summary = runtime.observability_summary()
        finally:
            runtime.events.unsubscribe("session-1", queue)

        self.assertEqual(summary.total_sessions, 1)
        self.assertEqual(summary.total_ws_subscribers, 1)
        self.assertEqual(summary.configured_event_bus_backend, "local")
        self.assertEqual(summary.effective_event_bus_backend, "local")
        self.assertEqual(summary.ephemeral_events_dropped, 3)
        self.assertEqual(summary.scheduled_background_jobs, 2)
        self.assertEqual(summary.scheduled_ingestion_jobs, 1)
        self.assertEqual(summary.turns_by_status["running"], 2)
        self.assertEqual(summary.background_jobs_by_status["queued"], 2)
        self.assertEqual(summary.ingestion_jobs_by_status["queued"], 1)
        self.assertEqual(summary.retrying_turn_jobs, 5)
        self.assertEqual(summary.retrying_ingestion_jobs, 2)
        self.assertEqual(summary.oldest_queued_turn_job_age_seconds, 12.5)
        self.assertEqual(summary.oldest_running_turn_job_age_seconds, 22.0)
        self.assertEqual(summary.oldest_running_ingestion_job_age_seconds, 8.0)
        self.assertEqual(summary.oldest_running_turn_age_seconds, 31.0)
