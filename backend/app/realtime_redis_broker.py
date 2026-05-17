from __future__ import annotations

import asyncio
import json
from collections import defaultdict

from app.core.config import settings
from app.schemas.events import TimelineEvent


class RedisEventBroker:
    def __init__(self, redis_client) -> None:
        self.backend_name = "redis"
        self._redis = redis_client
        self._queues: dict[str, list[asyncio.Queue[TimelineEvent]]] = defaultdict(list)
        self._reader_tasks: dict[asyncio.Queue[TimelineEvent], asyncio.Task[None]] = {}
        self.dropped_events_total = 0

    def _channel(self, session_id: str) -> str:
        return f"{settings.jarvis_redis_channel_prefix}:session:{session_id}:events"

    async def publish(self, event: TimelineEvent) -> None:
        payload = json.dumps(event.model_dump(), ensure_ascii=False)
        await self._redis.publish(self._channel(event.session_id), payload)

    def subscribe(self, session_id: str) -> asyncio.Queue[TimelineEvent]:
        queue: asyncio.Queue[TimelineEvent] = asyncio.Queue(maxsize=max(1, settings.jarvis_ephemeral_event_queue_size))
        self._queues[session_id].append(queue)
        self._reader_tasks[queue] = asyncio.create_task(self._reader_loop(session_id, queue))
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[TimelineEvent]) -> None:
        queues = self._queues.get(session_id, [])
        if queue in queues:
            queues.remove(queue)
        task = self._reader_tasks.pop(queue, None)
        if task is not None:
            task.cancel()

    def total_subscribers(self) -> int:
        return sum(len(queues) for queues in self._queues.values())

    async def _reader_loop(self, session_id: str, queue: asyncio.Queue[TimelineEvent]) -> None:
        pubsub = self._redis.pubsub()
        channel = self._channel(session_id)
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                raw = message.get("data")
                if not isinstance(raw, str):
                    continue
                event = TimelineEvent(**json.loads(raw))
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
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await pubsub.unsubscribe(channel)
            except Exception:
                pass
            try:
                await pubsub.aclose()
            except Exception:
                pass
