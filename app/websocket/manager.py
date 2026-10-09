import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Dict, Protocol, Set

from fastapi import WebSocket

from app.core.config import settings

logger = logging.getLogger("grade_book.realtime")
MessageHandler = Callable[[int, dict], Awaitable[None]]


class RealtimeBroker(Protocol):
    async def start(self, handler: MessageHandler) -> None: ...

    async def stop(self) -> None: ...

    async def publish(self, user_id: int, payload: dict) -> None: ...

    async def set_local_presence(self, user_id: int, count: int) -> None: ...

    async def global_connection_count(self, user_id: int) -> int: ...

    async def connected_users(self) -> Set[int]: ...

    async def healthy(self) -> bool: ...

    async def snapshot(self) -> dict: ...


class RedisRealtimeBroker:
    def __init__(
        self,
        url: str,
        *,
        channel: str = "gradebook:realtime",
        presence_ttl_seconds: int = 90,
    ):
        self._url = url
        self._channel = channel
        self._presence_prefix = f"{channel}:presence"
        self._presence_ttl = max(30, presence_ttl_seconds)
        self._instance_id = uuid.uuid4().hex
        self._client: Any = None
        self._pubsub: Any = None
        self._listener_task: asyncio.Task | None = None
        self._presence_task: asyncio.Task | None = None
        self._handler: MessageHandler | None = None
        self._local_presence: Dict[int, int] = {}
        self._listener_errors = 0

    async def start(self, handler: MessageHandler) -> None:
        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise RuntimeError(
                "redis package is required when REALTIME_REDIS_URL is configured"
            ) from exc

        self._handler = handler
        self._client = Redis.from_url(
            self._url,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
        )
        await self._client.ping()
        self._pubsub = self._client.pubsub()
        await self._pubsub.subscribe(self._channel)
        self._listener_task = asyncio.create_task(
            self._listen(),
            name="realtime-redis-listener",
        )
        self._presence_task = asyncio.create_task(
            self._refresh_presence(),
            name="realtime-presence-refresh",
        )
        logger.info("Redis realtime broker connected")

    async def stop(self) -> None:
        tasks = [
            task
            for task in (self._listener_task, self._presence_task)
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        if self._client is not None:
            keys = [
                self._presence_key(user_id)
                for user_id in self._local_presence
            ]
            if keys:
                await self._client.delete(*keys)
        self._local_presence.clear()

        if self._pubsub is not None:
            await self._pubsub.aclose()
        if self._client is not None:
            await self._client.aclose()
        self._listener_task = None
        self._presence_task = None
        self._pubsub = None
        self._client = None

    async def publish(self, user_id: int, payload: dict) -> None:
        if self._client is None:
            raise RuntimeError("Realtime broker is not started")
        envelope = json.dumps(
            {"user_id": user_id, "payload": payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        await self._client.publish(self._channel, envelope)

    async def set_local_presence(self, user_id: int, count: int) -> None:
        if self._client is None:
            return
        key = self._presence_key(user_id)
        if count <= 0:
            self._local_presence.pop(user_id, None)
            await self._client.delete(key)
            return
        self._local_presence[user_id] = count
        await self._client.set(key, count, ex=self._presence_ttl)

    async def global_connection_count(self, user_id: int) -> int:
        if self._client is None:
            return 0
        total = 0
        async for key in self._client.scan_iter(
            match=f"{self._presence_prefix}:*:{user_id}",
        ):
            value = await self._client.get(key)
            if value is not None:
                total += int(value)
        return total

    async def connected_users(self) -> Set[int]:
        if self._client is None:
            return set()
        users: Set[int] = set()
        async for key in self._client.scan_iter(
            match=f"{self._presence_prefix}:*:*",
        ):
            try:
                users.add(int(str(key).rsplit(":", 1)[1]))
            except (ValueError, IndexError):
                continue
        return users

    async def healthy(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def snapshot(self) -> dict:
        healthy = await self.healthy()
        subscribers = 0
        online_users = 0
        if healthy:
            subscriber_rows = await self._client.pubsub_numsub(self._channel)
            if subscriber_rows:
                subscribers = int(subscriber_rows[0][1])
            online_users = len(await self.connected_users())
        return {
            "healthy": healthy,
            "subscribers": subscribers,
            "online_users": online_users,
            "listener_errors": self._listener_errors,
        }

    async def _listen(self) -> None:
        while True:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message and message.get("type") == "message":
                    await self._dispatch(message.get("data"))
            except asyncio.CancelledError:
                raise
            except Exception:
                self._listener_errors += 1
                logger.exception("Redis realtime listener failed; reconnecting")
                await asyncio.sleep(1)

    async def _dispatch(self, raw: Any) -> None:
        if self._handler is None:
            return
        try:
            envelope = json.loads(raw)
            user_id = int(envelope["user_id"])
            payload = envelope["payload"]
            if not isinstance(payload, dict):
                raise TypeError("payload must be an object")
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            logger.warning("Ignoring malformed realtime broker message")
            return
        await self._handler(user_id, payload)

    async def _refresh_presence(self) -> None:
        interval = max(10, self._presence_ttl // 3)
        while True:
            await asyncio.sleep(interval)
            for user_id, count in list(self._local_presence.items()):
                try:
                    await self.set_local_presence(user_id, count)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Failed to refresh realtime presence")

    def _presence_key(self, user_id: int) -> str:
        return (
            f"{self._presence_prefix}:{self._instance_id}:{user_id}"
        )


class WSManager:
    """WebSocket connections owned by one process plus an optional shared broker."""

    def __init__(self, broker: RealtimeBroker | None = None):
        self.active: Dict[int, Set[WebSocket]] = {}
        self._broker = broker
        self._published_events = 0
        self._publish_failures = 0
        self._local_deliveries = 0

    async def start(self) -> None:
        if self._broker is not None:
            await self._broker.start(self._send_local)

    async def stop(self) -> None:
        if self._broker is not None:
            await self._broker.stop()

    async def connect(
        self,
        user_id: int,
        ws: WebSocket,
        subprotocol: str | None = None,
    ) -> None:
        await ws.accept(subprotocol=subprotocol)
        self.active.setdefault(user_id, set()).add(ws)
        await self._update_presence(user_id)

    async def connection_count(self, user_id: int) -> int:
        if self._broker is None:
            return len(self.active.get(user_id, set()))
        try:
            return await self._broker.global_connection_count(user_id)
        except Exception:
            logger.exception("Failed to read global WebSocket count")
            return len(self.active.get(user_id, set()))

    async def disconnect(self, user_id: int, ws: WebSocket) -> None:
        conns = self.active.get(user_id)
        if conns:
            conns.discard(ws)
            if not conns:
                self.active.pop(user_id, None)
        await self._update_presence(user_id)

    async def send_to_user(self, user_id: int, payload: dict) -> None:
        if self._broker is None:
            await self._send_local(user_id, payload)
            return
        try:
            await self._broker.publish(user_id, payload)
            self._published_events += 1
        except Exception:
            self._publish_failures += 1
            logger.exception("Realtime publish failed; using local delivery")
            await self._send_local(user_id, payload)

    async def get_connected_users(self) -> Set[int]:
        local_users = set(self.active.keys())
        if self._broker is None:
            return local_users
        try:
            return local_users | await self._broker.connected_users()
        except Exception:
            logger.exception("Failed to read global realtime presence")
            return local_users

    async def healthy(self) -> bool:
        if self._broker is None:
            return not settings.REALTIME_BROKER_REQUIRED
        return await self._broker.healthy()

    async def snapshot(self) -> dict:
        local_connections = sum(len(items) for items in self.active.values())
        broker = (
            await self._broker.snapshot()
            if self._broker is not None
            else {
                "healthy": not settings.REALTIME_BROKER_REQUIRED,
                "subscribers": 0,
                "online_users": len(self.active),
                "listener_errors": 0,
            }
        )
        return {
            "broker": broker,
            "local_connections": local_connections,
            "local_users": len(self.active),
            "published_events": self._published_events,
            "publish_failures": self._publish_failures,
            "local_deliveries": self._local_deliveries,
        }

    async def _send_local(self, user_id: int, payload: dict) -> None:
        for ws in list(self.active.get(user_id, [])):
            try:
                await ws.send_json(payload)
                self._local_deliveries += 1
            except Exception:
                await self.disconnect(user_id, ws)

    async def _update_presence(self, user_id: int) -> None:
        if self._broker is None:
            return
        try:
            await self._broker.set_local_presence(
                user_id,
                len(self.active.get(user_id, set())),
            )
        except Exception:
            logger.exception("Failed to update realtime presence")


def _create_broker() -> RealtimeBroker | None:
    if not settings.REALTIME_REDIS_URL:
        if settings.REALTIME_BROKER_REQUIRED:
            raise RuntimeError(
                "REALTIME_REDIS_URL is required in production"
            )
        return None
    return RedisRealtimeBroker(
        settings.REALTIME_REDIS_URL,
        channel=settings.REALTIME_REDIS_CHANNEL,
        presence_ttl_seconds=settings.REALTIME_PRESENCE_TTL_SECONDS,
    )


ws_manager = WSManager(_create_broker())
