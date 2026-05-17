from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fastapi import WebSocketDisconnect

import app.api.ws as ws_api
import app.services.session_service as session_service
from app.db.base import Base
from app.models import SessionRecord
from app.runtime.manager import EventBroker, RuntimeManager
from app.schemas.events import TimelineEvent


class DurableEventRecordTests(TestCase):
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

    def test_list_event_records_supports_event_id_and_after_cursor(self) -> None:
        with patch.object(session_service, "create_session", self._create_session):
            first = session_service.create_event_record(
                TimelineEvent(session_id="session-1", type="message.user", content="hello")
            )
            second = session_service.create_event_record(
                TimelineEvent(session_id="session-1", type="message.assistant", content="world")
            )
            events = session_service.list_event_records("session-1")
            tail = session_service.list_event_records("session-1", after_id=first.event_id)

        self.assertEqual([event.event_id for event in events], [first.event_id, second.event_id])
        self.assertEqual([event.event_id for event in tail], [second.event_id])

    def test_ephemeral_records_are_excluded_from_normal_timeline_but_available_with_flag(self) -> None:
        with patch.object(session_service, "create_session", self._create_session):
            session_service.create_event_record(
                TimelineEvent(session_id="session-1", type="message.user", content="hello")
            )
            ephemeral = session_service.create_event_record(
                TimelineEvent(session_id="session-1", type="message.assistant.delta", content="partial"),
                ephemeral=True,
            )
            normal = session_service.list_event_records("session-1")
            with_ephemeral = session_service.list_event_records("session-1", include_ephemeral=True)

        self.assertFalse(any(event.event_id == ephemeral.event_id for event in normal))
        self.assertTrue(any(event.event_id == ephemeral.event_id for event in with_ephemeral))


class EphemeralEventBrokerTests(IsolatedAsyncioTestCase):
    async def test_bounded_ephemeral_queue_drops_oldest(self) -> None:
        broker = EventBroker()
        with patch("app.runtime.manager.settings.jarvis_ephemeral_event_queue_size", 2):
            queue = broker.subscribe("session-1")
            await broker.publish(TimelineEvent(session_id="session-1", type="a", content="1"))
            await broker.publish(TimelineEvent(session_id="session-1", type="b", content="2"))
            await broker.publish(TimelineEvent(session_id="session-1", type="c", content="3"))

        first = await asyncio.wait_for(queue.get(), timeout=1)
        second = await asyncio.wait_for(queue.get(), timeout=1)
        self.assertEqual([first.type, second.type], ["b", "c"])


class RuntimeEventDeliveryTests(IsolatedAsyncioTestCase):
    async def test_publish_forwards_durable_events_to_live_broker(self) -> None:
        runtime = RuntimeManager()
        stored = TimelineEvent(event_id=7, session_id="session-1", type="message.assistant", content="done")

        with patch("app.runtime.manager.session_service.create_event_record", return_value=stored), patch.object(
            runtime.events,
            "publish",
        ) as publish_mock:
            result = await runtime.publish(TimelineEvent(session_id="session-1", type="message.assistant", content="done"))

        self.assertEqual(result.event_id, 7)
        publish_mock.assert_awaited_once_with(stored)

    def test_list_timeline_since_uses_durable_replay_only(self) -> None:
        runtime = RuntimeManager()

        with patch("app.runtime.manager.session_service.list_event_records", return_value=[]) as list_mock:
            result = runtime.list_timeline_since("session-1", after_id=5, limit=10)

        self.assertEqual(result, [])
        list_mock.assert_called_once_with("session-1", after_id=5, limit=10)

    async def test_websocket_replays_durable_backlog_then_skips_duplicate_live_event(self) -> None:
        backlog_event = TimelineEvent(event_id=1, session_id="session-1", type="message.user", content="hello")
        duplicate_live_event = TimelineEvent(event_id=1, session_id="session-1", type="message.user", content="hello")
        new_live_event = TimelineEvent(event_id=2, session_id="session-1", type="message.assistant", content="world")
        queue: asyncio.Queue[TimelineEvent] = asyncio.Queue()
        await queue.put(duplicate_live_event)
        await queue.put(new_live_event)
        unsubscribed: list[tuple[str, asyncio.Queue[TimelineEvent]]] = []

        class FakeWebSocket:
            def __init__(self) -> None:
                self.query_params = {"since_event_id": "0"}
                self.sent: list[dict[str, object]] = []
                self.accepted = False

            async def accept(self) -> None:
                self.accepted = True

            async def close(self, code: int) -> None:
                raise AssertionError(f"unexpected close {code}")

            async def send_json(self, payload: dict[str, object]) -> None:
                self.sent.append(payload)
                if len(self.sent) >= 2:
                    raise WebSocketDisconnect()

        fake_runtime = SimpleNamespace(
            session_exists=lambda session_id: True,
            list_timeline_since=lambda session_id, after_id=None, limit=None: [backlog_event] if after_id in (0, None) else [],
            events=SimpleNamespace(
                subscribe=lambda session_id: queue,
                unsubscribe=lambda session_id, q: unsubscribed.append((session_id, q)),
            ),
        )
        websocket = FakeWebSocket()

        with patch.object(ws_api, "runtime", fake_runtime):
            await ws_api.session_events(websocket, "session-1")

        self.assertTrue(websocket.accepted)
        self.assertEqual([item["event_id"] for item in websocket.sent], [1, 2])
        self.assertEqual(unsubscribed, [("session-1", queue)])
