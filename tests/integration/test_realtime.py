import asyncio
import ipaddress
import socket
from contextlib import AsyncExitStack
from uuid import uuid4

import pytest
import socketio
import uvicorn
from httpx import AsyncClient
from sqlalchemy import delete, select

from app.core.redis import rate_limit_key, room_key, socket_key, user_socket_key
from app.core.security import COOKIE_NAME, private_rate_subject, sign_cookie
from app.db.models import Participant, Room, User
from app.domain.enums import ConnectionStatus
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
async def network(settings):
    apps = [create_app(settings), create_app(settings)]
    servers, tasks, listeners, ports, clients, users, sockets = [], [], [], [], [], [], []
    sid_values = []
    forwarded_ip = str(ipaddress.IPv6Address(uuid4().int))
    async with AsyncExitStack() as stack:
        try:
            for app in apps:
                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
                server = uvicorn.Server(
                    uvicorn.Config(
                        app,
                        host="127.0.0.1",
                        port=port,
                        log_level="error",
                        access_log=False,
                        ws="wsproto",
                        proxy_headers=True,
                        forwarded_allow_ips="127.0.0.1",
                    )
                )
                task = asyncio.create_task(server.serve(sockets=[listener]))
                servers.append(server)
                tasks.append(task)
                listeners.append(listener)
                ports.append(port)
                async with asyncio.timeout(5):
                    while not server.started:
                        if task.done():
                            await task
                        await asyncio.sleep(0.01)

            async def http(pod=0, identity=None):
                identity = identity or uuid4()
                users.append(identity)
                cookie = sign_cookie(identity, settings.cookie_secret.get_secret_value())
                client = await stack.enter_async_context(
                    AsyncClient(
                        base_url=f"http://127.0.0.1:{ports[pod]}",
                        headers={
                            "Cookie": f"{COOKIE_NAME}={cookie}",
                            "X-Forwarded-For": forwarded_ip,
                        },
                    )
                )
                clients.append(client)
                await client.get("/api/me")
                return client, identity, cookie

            async def connect(pod, slug, cookie):
                client = socketio.AsyncClient(reconnection=False)
                sockets.append(client)
                queue = asyncio.Queue()
                client.on("room:joined", lambda data: queue.put_nowait(("joined", data)))
                client.on("room:closed", lambda data: queue.put_nowait(("closed", data)))
                client.on("session:superseded", lambda data: queue.put_nowait(("superseded", data)))
                await client.connect(
                    f"http://127.0.0.1:{ports[pod]}?slug={slug}",
                    headers={"Cookie": f"{COOKIE_NAME}={cookie}"},
                    transports=["websocket"],
                )
                sid_values.append(client.get_sid())
                return client, queue

            yield http, connect, apps
        finally:
            for client in sockets:
                if client.connected:
                    await client.disconnect()
                await client.shutdown()
            # Graceful Uvicorn shutdown waits for network handlers before closing resources.
            for server in servers:
                server.should_exit = True
            for task in tasks:
                await asyncio.wait_for(task, timeout=5)
            for listener in listeners:
                listener.close()
            # Resources are closed now; use a separate test-owned connection for row cleanup.
            from redis.asyncio import Redis

            from app.db.session import create_engine, session_factory

            engine = create_engine(settings)
            redis = Redis.from_url(str(settings.redis_url))
            try:
                async with session_factory(engine).begin() as session:
                    rooms = list(
                        await session.scalars(select(Room).where(Room.host_user_id.in_(users)))
                    )
                    await session.execute(delete(Room).where(Room.host_user_id.in_(users)))
                    await session.execute(delete(User).where(User.uuid.in_(users)))
                keys = [user_socket_key(str(uid)) for uid in users]
                keys += [socket_key(sid) for sid in sid_values]
                keys += [rate_limit_key("socket", private_rate_subject(sid)) for sid in sid_values]
                keys += [room_key(room.id, "presence") for room in rooms]
                keys += [
                    rate_limit_key("room:create:user", private_rate_subject(str(uid)))
                    for uid in users
                ]
                keys += [
                    rate_limit_key(scope, private_rate_subject(forwarded_ip))
                    for scope in [
                        "room:create:ip",
                        "room:join:ip",
                        "room:preview:ip",
                    ]
                ]
                if keys:
                    await redis.delete(*keys)
            finally:
                await redis.aclose()
                await engine.dispose()


async def next_event(queue, kind, predicate=lambda data: True):
    async with asyncio.timeout(5):
        while True:
            event, data = await queue.get()
            if event == kind and predicate(data):
                return data


async def test_two_pods_lobby_updates_duplicate_socket_and_close(network):
    http, connect, apps = network
    host, host_id, host_cookie = await http(0)
    guest, guest_id, guest_cookie = await http(1)
    created = await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 15})
    slug = created.json()["slug"]
    await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    host_socket, host_events = await connect(0, slug, host_cookie)
    initial = await next_event(host_events, "joined")
    assert len(initial["participants"]) == 1
    assert str(host_id) not in str(initial)
    await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "참가자"})
    added = await next_event(host_events, "joined", lambda value: len(value["participants"]) == 2)
    assert str(guest_id) not in str(added)
    guest_socket, guest_events = await connect(1, slug, guest_cookie)
    await next_event(guest_events, "joined")
    assert await guest_socket.call("presence:ping", timeout=3) == {"ok": True}

    changed = await host.patch(f"/api/rooms/{slug}/settings", json={"roundCount": 7})
    assert changed.status_code == 200
    update = await next_event(
        guest_events, "joined", lambda value: value["room"]["settings"]["roundCount"] == 7
    )
    assert len(update["participants"]) == 2

    old_sid = host_socket.get_sid()
    replacement, replacement_events = await connect(1, slug, host_cookie)
    await next_event(host_events, "superseded")
    await next_event(replacement_events, "joined")
    assert await replacement.call("presence:ping", timeout=3) == {"ok": True}
    # A delayed disconnect from the old Pod must not remove the replacement mapping.
    await apps[0].state.resources.realtime.disconnect(old_sid)
    redis = apps[1].state.resources.redis
    assert await redis.get(user_socket_key(str(host_id))) == replacement.get_sid().encode()
    async with apps[1].state.resources.sessions() as session:
        participant = await session.scalar(
            select(Participant).where(Participant.user_id == host_id)
        )
        assert participant.connection_status == ConnectionStatus.CONNECTED

    assert (await host.post(f"/api/rooms/{slug}/close")).status_code == 200
    assert await next_event(guest_events, "closed") == {"reason": "host_closed"}
    assert await next_event(replacement_events, "closed") == {"reason": "host_closed"}


async def test_socket_requires_signed_cookie_and_current_participant(network):
    http, connect, _ = network
    host, _, cookie = await http(0)
    outsider, _, outsider_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={})).json()["slug"]
    for token in ["tampered", outsider_cookie, cookie]:
        with pytest.raises(socketio.exceptions.ConnectionError):
            await connect(1, slug, token)
    # Even the owner cannot subscribe before participant admission.
    assert (await outsider.get(f"/api/rooms/{slug}/state")).status_code == 403
