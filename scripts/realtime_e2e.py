#!/usr/bin/env python3
import argparse
import asyncio
import base64
import json
import os
import secrets
import sys
import uuid
from urllib.parse import urlparse

from redis.asyncio import Redis

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.security import create_access_token
from app.db.connection import close_db_pool
from app.db.user_operations import get_user


class RawWebSocket:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer

    @classmethod
    async def connect(cls, url: str, token: str, host_header: str):
        parsed = urlparse(url)
        if parsed.scheme != "ws":
            raise ValueError("The production container probe requires a ws:// URL")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        reader, writer = await asyncio.open_connection(host, port)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Protocol: gradebook.jwt, {token}\r\n"
            "\r\n"
        )
        writer.write(request.encode("ascii"))
        await writer.drain()
        response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        status_line = response.split(b"\r\n", 1)[0]
        if b" 101 " not in status_line:
            try:
                body = await asyncio.wait_for(reader.read(1024), timeout=0.2)
            except TimeoutError:
                body = b""
            writer.close()
            await writer.wait_closed()
            raise RuntimeError(
                "WebSocket handshake failed: "
                f"{status_line.decode('ascii', 'replace')} "
                f"{body.decode('utf-8', 'replace').strip()}"
            )
        return cls(reader, writer)

    async def recv_json(self) -> dict:
        first, second = await asyncio.wait_for(
            self.reader.readexactly(2),
            timeout=5,
        )
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = int.from_bytes(await self.reader.readexactly(2), "big")
        elif length == 127:
            length = int.from_bytes(await self.reader.readexactly(8), "big")
        if second & 0x80:
            mask = await self.reader.readexactly(4)
            data = bytearray(await self.reader.readexactly(length))
            for index in range(length):
                data[index] ^= mask[index % 4]
            payload = bytes(data)
        else:
            payload = await self.reader.readexactly(length)
        if opcode != 1:
            raise RuntimeError(f"Expected a text frame, received opcode {opcode}")
        return json.loads(payload.decode("utf-8"))

    async def close(self) -> None:
        self.writer.close()
        await self.writer.wait_closed()


async def run(args) -> None:
    user = await get_user(args.username)
    if user is None:
        raise RuntimeError(f"User not found: {args.username}")
    user_id = user.id
    token = create_access_token({"sub": args.username})
    marker = uuid.uuid4().hex
    payload = {
        "type": "realtime_e2e",
        "data": {"marker": marker},
    }
    envelope = json.dumps(
        {"user_id": user_id, "payload": payload},
        separators=(",", ":"),
    )

    first, second = await asyncio.gather(
        RawWebSocket.connect(args.first_url, token, args.host_header),
        RawWebSocket.connect(args.second_url, token, args.host_header),
    )
    try:
        redis = Redis.from_url(args.redis_url, decode_responses=True)
        try:
            await redis.publish(args.channel, envelope)
        finally:
            await redis.aclose()

        first_event, second_event = await asyncio.gather(
            first.recv_json(),
            second.recv_json(),
        )
        if [first_event, second_event] != [payload, payload]:
            raise RuntimeError(
                f"Unexpected realtime payloads: {[first_event, second_event]!r}"
            )

        for socket in (first, second):
            try:
                await asyncio.wait_for(socket.recv_json(), timeout=0.5)
            except TimeoutError:
                continue
            raise RuntimeError("Realtime event was delivered more than once")
    finally:
        await asyncio.gather(first.close(), second.close())

    await close_db_pool()
    print(
        json.dumps(
            {
                "status": "ok",
                "marker": marker,
                "clients": 2,
                "deliveries": 2,
            }
        )
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--username",
        default=os.getenv("OWNER_USERNAME", "owner"),
    )
    parser.add_argument(
        "--redis-url",
        default=os.getenv("REALTIME_REDIS_URL", "redis://realtime:6379/0"),
    )
    parser.add_argument(
        "--channel",
        default=os.getenv("REALTIME_REDIS_CHANNEL", "gradebook:realtime"),
    )
    parser.add_argument("--host-header", default="api.gradebook.su")
    parser.add_argument(
        "--first-url",
        default="ws://127.0.0.1:8001/ws",
    )
    parser.add_argument(
        "--second-url",
        default="ws://127.0.0.1:8001/ws",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
