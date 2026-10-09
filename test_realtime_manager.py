import asyncio
import unittest

from app.websocket.manager import RedisRealtimeBroker, WSManager
from app.websocket import endpoints


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.messages = []

    async def accept(self, subprotocol=None):
        self.accepted = True

    async def send_json(self, payload):
        self.messages.append(payload)


class FakeBrokerHub:
    def __init__(self):
        self.brokers = []

    def broker(self):
        broker = FakeBroker(self)
        self.brokers.append(broker)
        return broker

    async def publish(self, user_id, payload):
        for broker in list(self.brokers):
            if broker.handler is not None:
                await broker.handler(user_id, payload)

    def connection_count(self, user_id):
        return sum(
            broker.presence.get(user_id, 0)
            for broker in self.brokers
        )

    def connected_users(self):
        return {
            user_id
            for broker in self.brokers
            for user_id, count in broker.presence.items()
            if count > 0
        }


class FakeBroker:
    def __init__(self, hub):
        self.hub = hub
        self.handler = None
        self.presence = {}

    async def start(self, handler):
        self.handler = handler

    async def stop(self):
        self.handler = None
        self.presence.clear()

    async def publish(self, user_id, payload):
        await self.hub.publish(user_id, payload)

    async def set_local_presence(self, user_id, count):
        if count > 0:
            self.presence[user_id] = count
        else:
            self.presence.pop(user_id, None)

    async def global_connection_count(self, user_id):
        return self.hub.connection_count(user_id)

    async def connected_users(self):
        return self.hub.connected_users()

    async def healthy(self):
        return True

    async def snapshot(self):
        return {
            "healthy": True,
            "subscribers": len(self.hub.brokers),
            "online_users": len(self.hub.connected_users()),
            "listener_errors": 0,
        }


class FailingPublishBroker(FakeBroker):
    async def publish(self, user_id, payload):
        raise ConnectionError("broker unavailable")


class FakeRedisClient:
    async def ping(self):
        return True

    async def pubsub_numsub(self, channel):
        return [(channel, 2)]

    async def scan_iter(self, match):
        if False:
            yield match


class RealtimeManagerTests(unittest.TestCase):
    def test_presence_event_reaches_every_conversation_peer(self):
        async def scenario():
            delivered = []
            original_peers = endpoints._conversation_peer_ids
            original_manager = endpoints.ws_manager

            async def fake_peers(user_id):
                self.assertEqual(user_id, 42)
                return [7, 9]

            class Manager:
                async def send_to_user(self, user_id, payload):
                    delivered.append((user_id, payload))

            endpoints._conversation_peer_ids = fake_peers
            endpoints.ws_manager = Manager()
            try:
                await endpoints._notify_presence(
                    42,
                    is_online=False,
                    last_seen_at="2026-07-27T11:16:00+00:00",
                )
            finally:
                endpoints._conversation_peer_ids = original_peers
                endpoints.ws_manager = original_manager

            self.assertEqual([item[0] for item in delivered], [7, 9])
            self.assertEqual(
                delivered[0][1],
                {
                    "type": "presence_changed",
                    "data": {
                        "user_id": 42,
                        "is_online": False,
                        "last_seen_at": "2026-07-27T11:16:00+00:00",
                    },
                },
            )

        asyncio.run(scenario())

    def test_redis_snapshot_reports_health_and_subscribers(self):
        async def scenario():
            broker = RedisRealtimeBroker("redis://unused")
            broker._client = FakeRedisClient()

            snapshot = await broker.snapshot()

            self.assertTrue(snapshot["healthy"])
            self.assertEqual(snapshot["subscribers"], 2)
            self.assertEqual(snapshot["online_users"], 0)

        asyncio.run(scenario())

    def test_message_reaches_socket_owned_by_another_process(self):
        async def scenario():
            hub = FakeBrokerHub()
            first = WSManager(hub.broker())
            second = WSManager(hub.broker())
            await first.start()
            await second.start()
            socket = FakeWebSocket()
            await second.connect(42, socket)

            payload = {"type": "message_new", "data": {"id": 7}}
            await first.send_to_user(42, payload)

            self.assertEqual(socket.messages, [payload])
            self.assertEqual(await first.connection_count(42), 1)
            self.assertIn(42, await first.get_connected_users())
            snapshot = await first.snapshot()
            self.assertEqual(snapshot["broker"]["subscribers"], 2)
            self.assertEqual(snapshot["published_events"], 1)

            await second.disconnect(42, socket)
            self.assertEqual(await first.connection_count(42), 0)
            self.assertNotIn(42, await first.get_connected_users())
            await first.stop()
            await second.stop()

        asyncio.run(scenario())

    def test_multiple_processes_each_receive_one_copy(self):
        async def scenario():
            hub = FakeBrokerHub()
            first = WSManager(hub.broker())
            second = WSManager(hub.broker())
            await first.start()
            await second.start()
            first_socket = FakeWebSocket()
            second_socket = FakeWebSocket()
            await first.connect(9, first_socket)
            await second.connect(9, second_socket)

            payload = {"type": "messages_read", "data": {"id": 3}}
            await first.send_to_user(9, payload)

            self.assertEqual(first_socket.messages, [payload])
            self.assertEqual(second_socket.messages, [payload])
            self.assertEqual(await first.connection_count(9), 2)
            await first.stop()
            await second.stop()

        asyncio.run(scenario())

    def test_broker_outage_falls_back_to_local_socket(self):
        async def scenario():
            hub = FakeBrokerHub()
            manager = WSManager(FailingPublishBroker(hub))
            await manager.start()
            socket = FakeWebSocket()
            await manager.connect(5, socket)
            payload = {"type": "message_new", "data": {"id": 11}}

            with self.assertLogs("grade_book.realtime", level="ERROR"):
                await manager.send_to_user(5, payload)

            self.assertEqual(socket.messages, [payload])
            snapshot = await manager.snapshot()
            self.assertEqual(snapshot["publish_failures"], 1)
            self.assertEqual(snapshot["local_deliveries"], 1)
            await manager.stop()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
