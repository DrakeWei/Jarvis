from __future__ import annotations

import asyncio
from collections import defaultdict
from unittest import IsolatedAsyncioTestCase

from app.realtime_redis_broker import RedisEventBroker
from app.schemas.events import TimelineEvent


class FakePubSub:
    def __init__(self, bus) -> None:
        self._bus = bus
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._channels: set[str] = set()

    async def subscribe(self, channel: str) -> None:
        self._channels.add(channel)
        self._bus.subscribers[channel].append(self._queue)

    async def unsubscribe(self, channel: str) -> None:
        if channel in self._channels and self._queue in self._bus.subscribers[channel]:
            self._bus.subscribers[channel].remove(self._queue)
        self._channels.discard(channel)

    async def aclose(self) -> None:
        for channel in list(self._channels):
            await self.unsubscribe(channel)

    async def listen(self):
        while True:
            channel, data = await self._queue.get()
            yield {"type": "message", "channel": channel, "data": data}


class FakeRedis:
    def __init__(self) -> None:
        self.subscribers: dict[str, list[asyncio.Queue[tuple[str, str]]]] = defaultdict(list)

    def pubsub(self) -> FakePubSub:
        return FakePubSub(self)

    async def publish(self, channel: str, payload: str) -> None:
        for queue in list(self.subscribers[channel]):
            await queue.put((channel, payload))


class RedisEventBrokerTests(IsolatedAsyncioTestCase):
    async def test_publish_and_subscribe_roundtrip(self) -> None:
        broker = RedisEventBroker(FakeRedis())
        queue = broker.subscribe("session-1")
        try:
            await asyncio.sleep(0)
            event = TimelineEvent(event_id=1, session_id="session-1", type="message.assistant.delta", content="hello")
            await broker.publish(event)
            received = await asyncio.wait_for(queue.get(), timeout=1)
        finally:
            broker.unsubscribe("session-1", queue)

        self.assertEqual(received.event_id, 1)
        self.assertEqual(received.content, "hello")
