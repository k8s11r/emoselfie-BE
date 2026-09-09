import asyncio
import ipaddress
import json
import socket
from contextlib import AsyncExitStack
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
import socketio
import uvicorn
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import delete, select

from app.core import clock
from app.core.redis import (
    SCHEDULER_TIMERS,
    job_lock_key,
    pod_key,
    rate_limit_key,
    room_key,
    round_key,
    socket_key,
    user_socket_key,
)
from app.core.security import (
    COOKIE_NAME,
    private_rate_subject,
    sign_cookie,
    verify_capture_token,
)
from app.db.models import Participant, Reaction, Room, Round, RoundSkip, Submission, User
from app.domain.enums import (
    ConnectionStatus,
    ParticipantStatus,
    RoomStatus,
    RoundStatus,
    SubmissionStatus,
)
from app.domain.room import service as room_service
from app.domain.scheduler.service import Job, due_at, schedule
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
                        # Deployment needs the same bound: a live Socket.IO connection would
                        # otherwise hold graceful shutdown open forever (BE-063).
                        timeout_graceful_shutdown=2,
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
                client.on("game:started", lambda data: queue.put_nowait(("started", data)))
                client.on("round:revealed", lambda data: queue.put_nowait(("revealed", data)))
                client.on("round:missed", lambda data: queue.put_nowait(("missed", data)))
                client.on(
                    "round:missedUpdate", lambda data: queue.put_nowait(("missedUpdate", data))
                )
                client.on("round:voided", lambda data: queue.put_nowait(("voided", data)))
                client.on("game:finished", lambda data: queue.put_nowait(("finished", data)))
                client.on("round:finalized", lambda data: queue.put_nowait(("finalized", data)))
                client.on("round:closed", lambda data: queue.put_nowait(("closed_round", data)))
                client.on("reaction:updated", lambda data: queue.put_nowait(("reaction", data)))
                client.on("round:skipStatus", lambda data: queue.put_nowait(("skipStatus", data)))
                client.on("submission:scored", lambda data: queue.put_nowait(("scored", data)))
                client.on("submission:status", lambda data: queue.put_nowait(("subStatus", data)))
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
            # Idle keep-alive connections delay Uvicorn's graceful shutdown, so close them first.
            for http_client in clients:
                await http_client.aclose()
            # Graceful Uvicorn shutdown waits for network handlers before closing resources.
            for server in servers:
                server.should_exit = True
            for task in tasks:
                await asyncio.wait_for(task, timeout=15)
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
                    round_ids = list(
                        await session.scalars(
                            select(Round.id).where(Round.room_id.in_([room.id for room in rooms]))
                        )
                    )
                    participant_ids = list(
                        await session.scalars(
                            select(Participant.id).where(
                                Participant.room_id.in_([room.id for room in rooms])
                            )
                        )
                    )
                    await session.execute(delete(Room).where(Room.host_user_id.in_(users)))
                    await session.execute(delete(User).where(User.uuid.in_(users)))
                if participant_ids:
                    await redis.zrem(
                        SCHEDULER_TIMERS,
                        *[Job("participant_left", value).member for value in participant_ids],
                    )
                for round_id in round_ids:
                    await redis.zrem(
                        SCHEDULER_TIMERS,
                        *[
                            Job(kind, round_id).member
                            for kind in (
                                "round_deadline",
                                "round_scoring_guard",
                                "round_viewing_end",
                            )
                        ],
                    )
                keys = [user_socket_key(str(uid)) for uid in users]
                keys += [
                    round_key(round_id, kind)
                    for round_id in round_ids
                    for kind in ("submitted", "scores", "skips")
                ]
                keys += [
                    job_lock_key(Job(kind, round_id).lock_id)
                    for round_id in round_ids
                    for kind in ("round_deadline", "round_scoring_guard", "round_viewing_end")
                ]
                keys += [
                    job_lock_key(Job("participant_left", value).lock_id)
                    for value in participant_ids
                ]
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


async def test_game_start_broadcasts_once_and_reveals_a_personal_capture_token(network, settings):
    http, connect, apps = network
    host, host_id, host_cookie = await http(0)
    guest, guest_id, guest_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 15})).json()[
        "slug"
    ]
    await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    host_socket, host_events = await connect(0, slug, host_cookie)
    await next_event(host_events, "joined")

    # A lone host cannot start; PM-10 needs two active players.
    lonely = await host.post(f"/api/rooms/{slug}/start")
    assert lonely.status_code == 409 and lonely.json()["error"]["code"] == "NOT_ENOUGH_PLAYERS"

    joined = await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "참가자"})
    guest_participant = joined.json()["participantId"]
    guest_socket, guest_events = await connect(1, slug, guest_cookie)
    await next_event(guest_events, "joined")

    assert (await host.post(f"/api/rooms/{slug}/start")).status_code == 200

    started = await next_event(guest_events, "started")
    assert started["roundCount"] == 3 and started["timeLimitSec"] == 15
    assert guest_participant in started["participantIds"] and len(started["participantIds"]) == 2
    assert await next_event(host_events, "started") == started

    mine = await next_event(host_events, "revealed")
    theirs = await next_event(guest_events, "revealed")
    assert mine["roundId"] == theirs["roundId"] and mine["index"] == 1
    assert mine["activeCount"] == 2 and mine["roundCount"] == 3
    assert set(mine["emotion"]) == {"label", "displayName", "emoji", "color", "hint"}
    # §6.3: the capture token is personal, so the room broadcast never carries one.
    assert mine["captureToken"] != theirs["captureToken"]
    assert "captureToken" not in str(started)
    assert str(host_id) not in str(mine) and str(guest_id) not in str(theirs)

    # RD-03·08: absolute times only, three seconds of countdown then the configured limit.
    assert theirs["deadlineAtMs"] - theirs["countdownEndsAtMs"] == 15_000
    assert verify_capture_token(
        theirs["captureToken"],
        int(theirs["roundId"]),
        int(guest_participant),
        theirs["deadlineAtMs"],
        settings.capture_token_secret.get_secret_value(),
    )

    # A second start finds the room already playing, and settings freeze with it.
    repeated = await host.post(f"/api/rooms/{slug}/start")
    assert (
        repeated.status_code == 409 and repeated.json()["error"]["code"] == "GAME_ALREADY_STARTED"
    )
    frozen = await host.patch(f"/api/rooms/{slug}/settings", json={"roundCount": 7})
    assert frozen.status_code == 409

    async with apps[0].state.resources.sessions() as session:
        room = await session.scalar(select(Room).where(Room.invite_slug == slug))
        assert room.status == RoomStatus.PLAYING
        assert len(room.emotion_sequence) == 3 and len(set(room.emotion_sequence)) == 3
        current = await session.get(Round, room.current_round_id)
        assert current.index == 1 and current.status == RoundStatus.REVEALED
        assert current.target_emotion == room.emotion_sequence[0]


async def test_only_the_host_starts_and_a_waiting_joiner_waits_for_the_next_game(network):
    http, connect, apps = network
    host, _, host_cookie = await http(0)
    guest, _, guest_cookie = await http(1)
    latecomer, _, latecomer_cookie = await http(0)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 15})).json()[
        "slug"
    ]
    await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "참가자"})

    refused = await guest.post(f"/api/rooms/{slug}/start")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "NOT_HOST"
    assert (await host.post(f"/api/rooms/{slug}/start")).status_code == 200

    # RO-09: joining a playing room waits for the next game and never receives a capture token.
    late = await latecomer.post(f"/api/rooms/{slug}/participants", json={"nickname": "늦은사람"})
    assert late.json()["status"] == "waiting_next_game"
    with pytest.raises(socketio.exceptions.ConnectionError):
        await connect(0, slug, latecomer_cookie)

    async with apps[0].state.resources.sessions() as session:
        room = await session.scalar(select(Room).where(Room.invite_slug == slug))
        players = list(
            await session.scalars(select(Participant).where(Participant.room_id == room.id))
        )
    assert sorted(player.status.value for player in players) == [
        "active",
        "active",
        "waiting_next_game",
    ]


async def seed_submission(redis, round_id, participant_id, *, status, score=None, received_ms=None):
    """Stands in for the upload path (BE-014·016) that will write these keys."""
    await redis.hset(
        round_key(round_id, "submitted"),
        str(participant_id),
        str(received_ms or clock.now_ms()),
    )
    await redis.hset(
        round_key(round_id, "scores"),
        str(participant_id),
        json.dumps(
            {
                "status": status,
                "targetScore": score,
                "topEmotions": (
                    [{"label": "happy", "score": score}] if status == "submitted" else None
                ),
            }
        ),
    )


async def fire_now(redis, kind, round_id):
    """Move a scheduled timer into the past so the running loop claims it on its next tick."""
    assert await due_at(redis, Job(kind, round_id)) is not None
    await schedule(redis, Job(kind, round_id), clock.now_ms() - 1)


async def wait_for_round(sessions, room_id, predicate):
    async with asyncio.timeout(10):
        while True:
            async with sessions() as session:
                room = await session.get(Room, room_id)
                await session.refresh(room)
                current = (
                    await session.get(Round, room.current_round_id)
                    if room.current_round_id
                    else None
                )
                if predicate(room, current):
                    return room, current
            await asyncio.sleep(0.05)


async def test_a_full_three_round_game_scores_advances_and_finishes(network):
    http, connect, apps = network
    runtime = apps[0].state.resources
    redis = runtime.redis
    host, _, host_cookie = await http(0)
    guest, _, guest_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 15})).json()[
        "slug"
    ]
    host_id = (
        await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    ).json()["participantId"]
    guest_id = (
        await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "참가자"})
    ).json()["participantId"]
    host_socket, host_events = await connect(0, slug, host_cookie)
    guest_socket, guest_events = await connect(1, slug, guest_cookie)
    await next_event(host_events, "joined")
    await next_event(guest_events, "joined")

    assert (await host.post(f"/api/rooms/{slug}/start")).status_code == 200
    first = await next_event(host_events, "revealed")
    room_id = int(
        (await next_event(guest_events, "revealed")) and (await _room_id(runtime.sessions, slug))
    )
    round_one = int(first["roundId"])

    # The deadline timer is armed from the committed round, not from the request time.
    assert await due_at(redis, Job("round_deadline", round_one)) == first["deadlineAtMs"]

    # Round 1: both players are scored, so the round finalizes and opens a viewing stage.
    await seed_submission(redis, round_one, int(host_id), status="submitted", score=91.3)
    await seed_submission(
        redis, round_one, int(guest_id), status="submitted", score=40.0, received_ms=clock.now_ms()
    )
    await fire_now(redis, "round_deadline", round_one)
    _, _ = await wait_for_round(
        runtime.sessions, room_id, lambda room, current: current.status == RoundStatus.FINALIZED
    )

    async with runtime.sessions() as session:
        rows = list(
            await session.scalars(select(Submission).where(Submission.round_id == round_one))
        )
        by_participant = {row.participant_id: row for row in rows}
        assert by_participant[int(host_id)].rank == 1
        assert by_participant[int(host_id)].rank_points == 100
        assert by_participant[int(host_id)].target_score == Decimal("91.3")
        assert by_participant[int(guest_id)].rank == 2
        assert by_participant[int(guest_id)].rank_points == 70
        players = {
            player.id: player
            for player in await session.scalars(
                select(Participant).where(Participant.room_id == room_id)
            )
        }
        assert players[int(host_id)].total_points == 100
        assert players[int(guest_id)].total_points == 70
        current = await session.get(Round, round_one)
        assert current.viewing_ends_at is not None

    # RS-08: two viewers earn 10 + 2 x 2 seconds of viewing before the next round.
    await fire_now(redis, "round_viewing_end", round_one)
    second = await next_event(host_events, "revealed", lambda data: data["index"] == 2)
    round_two = int(second["roundId"])
    assert round_two != round_one

    # Round 2: nobody submits, so both players are missed and the round carries no viewing stage.
    await fire_now(redis, "round_deadline", round_two)
    missed = await next_event(guest_events, "missed")
    assert missed["roundId"] == str(round_two) and missed["phase"] == "scoring"
    assert "targetScore" not in str(missed) and "results" not in str(missed)
    third = await next_event(guest_events, "revealed", lambda data: data["index"] == 3)
    round_three = int(third["roundId"])

    async with runtime.sessions() as session:
        rows = list(
            await session.scalars(select(Submission).where(Submission.round_id == round_two))
        )
        assert {row.status for row in rows} == {SubmissionStatus.MISSED}
        assert all(row.rank is None and row.rank_points == 0 for row in rows)
        assert all(row.received_at is None for row in rows)
        players = {
            player.id: player.total_points
            for player in await session.scalars(
                select(Participant).where(Participant.room_id == room_id)
            )
        }
        assert players == {int(host_id): 100, int(guest_id): 70}

    # Round 3: every submitter fails, so D-5 voids the round and nobody gains points.
    await seed_submission(redis, round_three, int(host_id), status="failed")
    await seed_submission(redis, round_three, int(guest_id), status="failed")
    await fire_now(redis, "round_deadline", round_three)

    voided = await next_event(host_events, "voided")
    assert voided["roundId"] == str(round_three) and voided["reason"] == "engine_unavailable"
    finished = await next_event(guest_events, "finished")
    assert finished["aborted"] is False and finished["reason"] is None
    assert [entry["participantId"] for entry in finished["ranking"]] == [host_id, guest_id]
    assert [entry["totalPoints"] for entry in finished["ranking"]] == [100, 70]
    assert [entry["rank"] for entry in finished["ranking"]] == [1, 2]
    assert finished["mostLoved"] is None

    async with runtime.sessions() as session:
        room = await session.get(Room, room_id)
        assert room.status == RoomStatus.FINISHED
        assert room.current_round_id is None
        assert room.consecutive_voided == 1
        last = await session.get(Round, round_three)
        assert last.status == RoundStatus.VOIDED
        rows = list(
            await session.scalars(select(Submission).where(Submission.round_id == round_three))
        )
        assert {row.status for row in rows} == {SubmissionStatus.FAILED}
        assert all(row.rank_points == 0 for row in rows)

    # RO-13: the finished room releases the owner slot immediately.
    again = await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 15})
    assert again.status_code == 201 and again.json()["slug"] != slug


async def test_a_stranded_inference_is_failed_by_the_guard_and_never_blocks_the_round(network):
    http, connect, apps = network
    runtime = apps[0].state.resources
    redis = runtime.redis
    host, _, host_cookie = await http(0)
    guest, _, guest_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 15})).json()[
        "slug"
    ]
    host_id = (
        await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    ).json()["participantId"]
    guest_id = (
        await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "참가자"})
    ).json()["participantId"]
    host_socket, host_events = await connect(0, slug, host_cookie)
    await connect(1, slug, guest_cookie)
    await next_event(host_events, "joined")
    await host.post(f"/api/rooms/{slug}/start")
    first = await next_event(host_events, "revealed")
    round_one = int(first["roundId"])
    room_id = await _room_id(runtime.sessions, slug)

    # One upload is scored, the other is still inside the engine when the deadline arrives.
    await seed_submission(redis, round_one, int(host_id), status="submitted", score=70.0)
    await redis.hset(round_key(round_one, "submitted"), str(guest_id), str(clock.now_ms()))
    await fire_now(redis, "round_deadline", round_one)

    async with asyncio.timeout(10):
        while True:
            if await due_at(redis, Job("round_scoring_guard", round_one)) is not None:
                break
            await asyncio.sleep(0.02)
    async with runtime.sessions() as session:
        current = await session.get(Round, round_one)
        assert current.status == RoundStatus.SCORING  # §10.3 waits for the pending result

    await fire_now(redis, "round_scoring_guard", round_one)
    await wait_for_round(
        runtime.sessions, room_id, lambda room, current: current.status == RoundStatus.FINALIZED
    )

    async with runtime.sessions() as session:
        rows = {
            row.participant_id: row
            for row in await session.scalars(
                select(Submission).where(Submission.round_id == round_one)
            )
        }
        assert rows[int(guest_id)].status == SubmissionStatus.FAILED
        assert rows[int(guest_id)].rank is None
        # D-2: the stranded player is compensated with the awarded average, never penalised.
        assert rows[int(guest_id)].rank_points == 100
        assert rows[int(host_id)].rank_points == 100


async def _room_id(sessions, slug):
    async with sessions() as session:
        room = await session.scalar(select(Room).where(Room.invite_slug == slug))
        return room.id


def sample_jpeg(width=320, height=240, color=(200, 140, 90)):
    with Image.new("RGB", (width, height), color) as image, BytesIO() as buffer:
        image.save(buffer, format="JPEG", quality=80)
        return buffer.getvalue()


def multipart(image, boundary="emoselfie-test-boundary"):
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image"; filename="capture.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode()
        + image
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


async def upload(client, slug, round_id, token, image=None):
    body, headers = multipart(image if image is not None else sample_jpeg())
    return await client.post(
        f"/api/rooms/{slug}/rounds/{round_id}/submissions",
        content=body,
        headers={**headers, "X-Capture-Token": token},
    )


async def test_uploads_are_scored_delivered_to_viewers_only_and_close_the_round(network):
    http, connect, apps = network
    runtime = apps[0].state.resources
    host, _, host_cookie = await http(0)
    guest, _, guest_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 30})).json()[
        "slug"
    ]
    host_id = (
        await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    ).json()["participantId"]
    guest_id = (
        await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "참가자"})
    ).json()["participantId"]
    host_socket, host_events = await connect(0, slug, host_cookie)
    guest_socket, guest_events = await connect(1, slug, guest_cookie)
    await next_event(host_events, "joined")
    await next_event(guest_events, "joined")
    await host.post(f"/api/rooms/{slug}/start")
    mine = await next_event(host_events, "revealed")
    theirs = await next_event(guest_events, "revealed")
    round_id = int(mine["roundId"])

    accepted = await upload(host, slug, round_id, mine["captureToken"])
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert body["status"] == "processing" and body["submissionId"].isdigit()
    assert body["acceptedAtMs"] < mine["deadlineAtMs"]

    counted = await next_event(guest_events, "subStatus")
    assert counted == {"roundId": str(round_id), "submitted": 1, "total": 2}
    assert "targetScore" not in str(counted)  # RS-09

    scored = await next_event(host_events, "scored")
    assert scored["participantId"] == host_id and scored["submissionId"] == body["submissionId"]
    assert scored["status"] == "submitted" and scored["scoredTotal"] == 2
    assert scored["mediaToken"]
    # RS-12: the guest has not submitted, so no result of any kind reaches them.
    assert guest_events.qsize() == 0 or all(
        event != "scored" for event, _ in list(guest_events._queue)
    )

    # CP-03·§8.3: the capture slot and token are single use.
    repeated = await upload(host, slug, round_id, mine["captureToken"])
    assert repeated.status_code == 403
    assert repeated.json()["error"]["code"] == "INVALID_CAPTURE_TOKEN"
    forged = await upload(guest, slug, round_id, mine["captureToken"])
    assert forged.status_code == 403

    # RD-07: the last upload closes the round early, without waiting for the deadline.
    assert (await upload(guest, slug, round_id, theirs["captureToken"])).status_code == 202
    guest_scored = await next_event(
        guest_events, "scored", lambda data: data["participantId"] == guest_id
    )
    assert guest_scored["mediaToken"] != scored["mediaToken"]
    assert (await host.get(f"/media/{guest_scored['mediaToken']}")).status_code == 403

    finalized = await next_event(host_events, "finalized")
    assert {entry["participantId"] for entry in finalized["results"]} == {host_id, guest_id}
    assert all(entry["rank"] is not None for entry in finalized["results"])
    assert finalized["viewingEndsAtMs"] > clock.now_ms()
    await next_event(guest_events, "finalized")

    # §8.4: the photo opens for the viewer it was issued to and for nobody else.
    image = await host.get(f"/media/{scored['mediaToken']}")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert image.headers["cache-control"] == "private, no-store"
    assert image.content.startswith(b"\xff\xd8")
    assert (await guest.get(f"/media/{scored['mediaToken']}")).status_code == 403

    async with runtime.sessions() as session:
        rows = {
            row.participant_id: row
            for row in await session.scalars(
                select(Submission).where(Submission.round_id == round_id)
            )
        }
        assert rows[int(host_id)].status == SubmissionStatus.SUBMITTED
        assert rows[int(host_id)].top_emotions[0]["label"] == "happy"
        assert str(rows[int(host_id)].id) == body["submissionId"]

    # PV-01: closing the round drops the cached photo before its safety TTL.
    await fire_now(runtime.redis, "round_viewing_end", round_id)
    await next_event(host_events, "revealed", lambda data: data["index"] == 2)
    expired = await host.get(f"/media/{scored['mediaToken']}")
    assert expired.status_code == 410 and expired.json()["error"]["code"] == "MEDIA_EXPIRED"


async def test_uploads_are_rejected_after_the_deadline_and_over_the_size_limit(network):
    http, connect, apps = network
    runtime = apps[0].state.resources
    host, _, host_cookie = await http(0)
    guest, _, guest_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 30})).json()[
        "slug"
    ]
    await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "참가자"})
    host_socket, host_events = await connect(0, slug, host_cookie)
    await connect(1, slug, guest_cookie)
    await next_event(host_events, "joined")
    await host.post(f"/api/rooms/{slug}/start")
    mine = await next_event(host_events, "revealed")
    round_id = int(mine["roundId"])

    # A body over 2MB is refused, and §8.3 spends the slot before the body is read.
    oversized = await upload(
        host, slug, round_id, mine["captureToken"], image=b"\xff\xd8" + b"0" * 2_200_000
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

    # A body that is not a JPEG is refused as unsupported media, not as a failure.
    unsupported = await upload(guest, slug, round_id, "x" * 32)
    assert unsupported.status_code == 403  # the forged token is rejected before the body

    async with runtime.sessions() as session:
        room = await session.scalar(select(Room).where(Room.invite_slug == slug))
        room_id = room.id
    await fire_now(runtime.redis, "round_deadline", round_id)
    await wait_for_round(runtime.sessions, room_id, lambda room, current: current.index == 2)
    stale = await upload(host, slug, round_id, mine["captureToken"])
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "NOT_CURRENT_ROUND"


async def play_round(http_clients, sockets, slug, runtime, index=1):
    """Both players upload, so the round finalizes and enters its viewing stage."""
    host, guest = http_clients
    host_events, guest_events = sockets
    mine = await next_event(host_events, "revealed", lambda data: data["index"] == index)
    theirs = await next_event(guest_events, "revealed", lambda data: data["index"] == index)
    round_id = int(mine["roundId"])
    first = await upload(host, slug, round_id, mine["captureToken"])
    second = await upload(guest, slug, round_id, theirs["captureToken"])
    assert (first.status_code, second.status_code) == (202, 202)
    await next_event(host_events, "finalized")
    await next_event(guest_events, "finalized")
    return round_id, first.json()["submissionId"], second.json()["submissionId"]


async def test_reactions_toggle_do_not_move_scores_and_are_stored_when_the_round_closes(network):
    http, connect, apps = network
    runtime = apps[0].state.resources
    host, _, host_cookie = await http(0)
    guest, _, guest_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 30})).json()[
        "slug"
    ]
    host_id = (
        await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    ).json()["participantId"]
    await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "참가자"})
    host_socket, host_events = await connect(0, slug, host_cookie)
    guest_socket, guest_events = await connect(1, slug, guest_cookie)
    await next_event(host_events, "joined")
    await next_event(guest_events, "joined")
    await host.post(f"/api/rooms/{slug}/start")
    round_id, host_submission, guest_submission = await play_round(
        (host, guest), (host_events, guest_events), slug, runtime
    )

    # RX-05: the owner cannot react to their own photo.
    assert await host_socket.call(
        "reaction:sent", {"submissionId": host_submission, "type": "like"}, timeout=5
    ) == {
        "ok": False,
        "error": {"code": "SELF_REACTION", "message": "내 사진에는 누를 수 없어요", "detail": None},
    }

    ack = await guest_socket.call(
        "reaction:sent", {"submissionId": host_submission, "type": "like"}, timeout=5
    )
    assert ack == {"ok": True}
    updated = await next_event(host_events, "reaction")
    assert updated == {
        "roundId": str(round_id),
        "submissionId": host_submission,
        "like": 1,
        "question": 0,
    }
    assert "participantId" not in str(updated)  # RX-09 never says who pressed

    # RX-04: the same command toggles off, and like and question are independent.
    await guest_socket.call(
        "reaction:sent", {"submissionId": host_submission, "type": "like"}, timeout=5
    )
    assert (await next_event(host_events, "reaction"))["like"] == 0
    await guest_socket.call(
        "reaction:sent", {"submissionId": host_submission, "type": "like"}, timeout=5
    )
    await next_event(host_events, "reaction")
    await guest_socket.call(
        "reaction:sent", {"submissionId": host_submission, "type": "question"}, timeout=5
    )
    both = await next_event(host_events, "reaction", lambda data: data["question"] == 1)
    assert (both["like"], both["question"]) == (1, 1)

    async with runtime.sessions() as session:
        rows = {
            row.participant_id: row
            for row in await session.scalars(
                select(Submission).where(Submission.round_id == round_id)
            )
        }
        # SC-06·RX-01: reactions never touch the score ledger.
        assert rows[int(host_id)].rank_points in (100, 70)
        assert rows[int(host_id)].like_count == 0

    await fire_now(runtime.redis, "round_viewing_end", round_id)
    closed = await next_event(host_events, "closed_round")
    assert closed["roundId"] == str(round_id)
    assert {entry["submissionId"] for entry in closed["reactions"]} == {host_submission}
    assert closed["reactions"][0] == {"submissionId": host_submission, "like": 1, "question": 1}
    assert closed["nextRoundAtMs"] > 0

    async with runtime.sessions() as session:
        row = await session.get(Submission, int(host_submission))
        assert (row.like_count, row.question_count) == (1, 1)
        stored = list(await session.scalars(select(Reaction).where(Reaction.round_id == round_id)))
        assert {reaction.type.value for reaction in stored} == {"like", "question"}
        assert all(reaction.target_submission_id == int(host_submission) for reaction in stored)

    # RX-10: the closed round refuses further reactions.
    assert (
        await guest_socket.call(
            "reaction:sent", {"submissionId": host_submission, "type": "like"}, timeout=5
        )
    )["error"]["code"] == "REACTION_CLOSED"


async def test_a_host_skip_ends_the_viewing_stage_immediately(network):
    http, connect, apps = network
    runtime = apps[0].state.resources
    host, _, host_cookie = await http(0)
    guest, _, guest_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 30})).json()[
        "slug"
    ]
    await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "참가자"})
    host_socket, host_events = await connect(0, slug, host_cookie)
    guest_socket, guest_events = await connect(1, slug, guest_cookie)
    await next_event(host_events, "joined")
    await next_event(guest_events, "joined")
    await host.post(f"/api/rooms/{slug}/start")
    round_id, _, _ = await play_round((host, guest), (host_events, guest_events), slug, runtime)

    # A guest vote alone is not enough while another connected viewer has not voted.
    assert await guest_socket.call("round:skip", {"roundId": str(round_id)}, timeout=5) == {
        "ok": True
    }
    status = await next_event(guest_events, "skipStatus")
    assert status == {"roundId": str(round_id), "skipped": 1, "total": 2}
    async with runtime.sessions() as session:
        current = await session.get(Round, round_id)
        assert current.status == RoundStatus.FINALIZED

    # RS-08: the host ends viewing regardless of the denominator.
    assert await host_socket.call("round:skip", {"roundId": str(round_id)}, timeout=5) == {
        "ok": True
    }
    second = await next_event(guest_events, "revealed", lambda data: data["index"] == 2)
    assert int(second["roundId"]) != round_id

    async with runtime.sessions() as session:
        current = await session.get(Round, round_id)
        assert current.status == RoundStatus.CLOSED
        votes = list(await session.scalars(select(RoundSkip).where(RoundSkip.round_id == round_id)))
        assert len(votes) == 2


async def test_every_connected_viewer_skipping_closes_the_round(network):
    http, connect, apps = network
    runtime = apps[0].state.resources
    host, _, host_cookie = await http(0)
    guest, _, guest_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 30})).json()[
        "slug"
    ]
    await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "참가자"})
    host_socket, host_events = await connect(0, slug, host_cookie)
    guest_socket, guest_events = await connect(1, slug, guest_cookie)
    await next_event(host_events, "joined")
    await next_event(guest_events, "joined")
    await host.post(f"/api/rooms/{slug}/start")
    round_id, _, _ = await play_round((host, guest), (host_events, guest_events), slug, runtime)

    await guest_socket.call("round:skip", {"roundId": str(round_id)}, timeout=5)
    await next_event(guest_events, "skipStatus")
    # The guest leaves the vote standing; the host completing it reaches the denominator.
    await host_socket.call("round:skip", {"roundId": str(round_id)}, timeout=5)
    await next_event(guest_events, "revealed", lambda data: data["index"] == 2)

    async with runtime.sessions() as session:
        current = await session.get(Round, round_id)
        assert current.status == RoundStatus.CLOSED


async def test_a_late_submitter_receives_the_results_scored_before_them(network):
    """RS-02·04: joining the viewers late must not hide the cards settled while shooting."""
    http, connect, _ = network
    host, _, host_cookie = await http(0)
    guest, _, guest_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 30})).json()[
        "slug"
    ]
    host_id = (
        await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "먼저"})
    ).json()["participantId"]
    guest_id = (
        await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "나중"})
    ).json()["participantId"]
    host_socket, host_events = await connect(0, slug, host_cookie)
    guest_socket, guest_events = await connect(1, slug, guest_cookie)
    await next_event(host_events, "joined")
    await next_event(guest_events, "joined")
    await host.post(f"/api/rooms/{slug}/start")
    mine = await next_event(host_events, "revealed")
    theirs = await next_event(guest_events, "revealed")
    round_id = int(mine["roundId"])

    first = await upload(host, slug, round_id, mine["captureToken"])
    early = await next_event(host_events, "scored")
    assert early["participantId"] == host_id and early["currentRank"] == 1

    # The guest was still shooting, so nothing about that card has reached them yet.
    assert all(event != "scored" for event, _ in list(guest_events._queue))

    second = await upload(guest, slug, round_id, theirs["captureToken"])
    assert second.status_code == 202

    replayed = await next_event(
        guest_events, "scored", lambda data: data["participantId"] == host_id
    )
    assert replayed["submissionId"] == first.json()["submissionId"]
    assert replayed["status"] == early["status"]
    assert replayed["targetScore"] == early["targetScore"]
    assert replayed["topEmotions"] == early["topEmotions"]
    assert replayed["currentRank"] is not None
    # §8.4: the replay is addressed to this viewer, so it carries their own token.
    assert replayed["mediaToken"] != early["mediaToken"]
    assert (await guest.get(f"/media/{replayed['mediaToken']}")).status_code == 200
    assert (await host.get(f"/media/{replayed['mediaToken']}")).status_code == 403

    own = await next_event(guest_events, "scored", lambda data: data["participantId"] == guest_id)
    assert own["scoredTotal"] == 2 and own["scoredCount"] == 2
    await next_event(guest_events, "finalized")


async def test_a_non_submitter_receives_no_backlog(network):
    """RS-12: the replay uses the same viewer gate, so a missed player still sees nothing."""
    http, connect, apps = network
    runtime = apps[0].state.resources
    host, _, host_cookie = await http(0)
    guest, _, guest_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 30})).json()[
        "slug"
    ]
    await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "제출자"})
    await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "미제출자"})
    host_socket, host_events = await connect(0, slug, host_cookie)
    guest_socket, guest_events = await connect(1, slug, guest_cookie)
    await next_event(host_events, "joined")
    await next_event(guest_events, "joined")
    await host.post(f"/api/rooms/{slug}/start")
    mine = await next_event(host_events, "revealed")
    await next_event(guest_events, "revealed")
    round_id = int(mine["roundId"])

    await upload(host, slug, round_id, mine["captureToken"])
    await next_event(host_events, "scored")
    await fire_now(runtime.redis, "round_deadline", round_id)
    await next_event(guest_events, "missed")
    await next_event(host_events, "finalized")

    delivered = [event for event, _ in list(guest_events._queue)]
    assert "scored" not in delivered and "finalized" not in delivered


async def test_a_cookieless_client_cannot_take_a_slot(network):
    """A session that never kept its cookie can never connect (§6.2), so it is refused."""
    http, _, _ = network
    host, _, _ = await http(0)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 15})).json()[
        "slug"
    ]

    async with AsyncClient(base_url=str(host.base_url)) as stranger:
        refused = await stranger.post(
            f"/api/rooms/{slug}/participants", json={"nickname": "쿠키없음"}
        )

    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "SESSION_REQUIRED"

    # Repeating it cannot fill the room either: no participant was ever created.
    preview = await host.get(f"/api/rooms/{slug}")
    assert preview.json()["isFull"] is False


async def test_an_entry_that_never_connects_returns_its_slot(network):
    """D-6: a retry storm from a broken client must not hold seats it can never use."""
    http, connect, apps = network
    runtime = apps[0].state.resources
    host, _, host_cookie = await http(0)
    stranded, _, _ = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 15})).json()[
        "slug"
    ]
    await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    host_socket, host_events = await connect(0, slug, host_cookie)
    await next_event(host_events, "joined")

    joined = await stranded.post(f"/api/rooms/{slug}/participants", json={"nickname": "미연결"})
    stranded_id = int(joined.json()["participantId"])
    await next_event(host_events, "joined", lambda data: len(data["participants"]) == 2)

    # The grace timer is armed at entry, not only at disconnect.
    assert await due_at(runtime.redis, Job("participant_left", stranded_id)) is not None
    async with runtime.sessions.begin() as session:
        member = await session.get(Participant, stranded_id)
        assert member.connection_status == ConnectionStatus.DISCONNECTED
        member.disconnected_at = clock.now_utc() - timedelta(
            seconds=runtime.settings.participant_grace_sec + 1
        )
    await fire_now(runtime.redis, "participant_left", stranded_id)

    released = await next_event(host_events, "joined", lambda data: len(data["participants"]) == 1)
    assert [member["nickname"] for member in released["participants"]] == ["방장"]

    async with runtime.sessions() as session:
        member = await session.get(Participant, stranded_id)
        assert member.status == ParticipantStatus.LEFT
        room = await session.scalar(select(Room).where(Room.invite_slug == slug))
        assert await room_service.occupied_count(session, room.id) == 1

    # The freed seat is immediately usable by somebody else.
    newcomer, _, newcomer_cookie = await http(0)
    admitted = await newcomer.post(f"/api/rooms/{slug}/participants", json={"nickname": "새사람"})
    assert admitted.status_code == 201


async def test_a_connected_participant_keeps_its_slot(network):
    """The socket arrives inside the grace window, so the timer must be cancelled."""
    http, connect, apps = network
    runtime = apps[0].state.resources
    host, _, host_cookie = await http(0)
    guest, _, guest_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 15})).json()[
        "slug"
    ]
    await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    joined = await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "참가자"})
    guest_id = int(joined.json()["participantId"])
    await connect(0, slug, host_cookie)
    guest_socket, guest_events = await connect(1, slug, guest_cookie)
    await next_event(guest_events, "joined")

    assert await due_at(runtime.redis, Job("participant_left", guest_id)) is None

    # Dropping the socket re-arms it, and the participant is released when it fires.
    await guest_socket.disconnect()
    async with asyncio.timeout(5):
        while True:
            if await due_at(runtime.redis, Job("participant_left", guest_id)) is not None:
                break
            await asyncio.sleep(0.05)


async def test_a_socket_left_by_a_dead_pod_returns_its_slot(network):
    """G-09: a crashed Pod leaves its keys behind, so key existence cannot mean connected."""
    http, connect, apps = network
    runtime = apps[0].state.resources
    host, _, host_cookie = await http(0)
    guest, guest_identity, guest_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 15})).json()[
        "slug"
    ]
    await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    joined = await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "참가자"})
    guest_id = int(joined.json()["participantId"])
    host_socket, host_events = await connect(0, slug, host_cookie)
    guest_socket, guest_events = await connect(1, slug, guest_cookie)
    await next_event(guest_events, "joined")
    await next_event(host_events, "joined", lambda data: len(data["participants"]) == 2)

    # A live socket is left alone, and no grace timer is armed for it.
    assert await runtime.rounds.reconcile_connections() == 0
    assert await due_at(runtime.redis, Job("participant_left", guest_id)) is None

    # The guest's Pod dies: its liveness marker disappears while the socket keys survive.
    pod_id = apps[1].state.resources.realtime.pod_id
    await runtime.redis.delete(pod_key(pod_id))
    assert await runtime.redis.get(user_socket_key(str(guest_identity))) is not None

    assert await runtime.rounds.reconcile_connections() == 1

    async with runtime.sessions() as session:
        member = await session.get(Participant, guest_id)
        assert member.connection_status == ConnectionStatus.DISCONNECTED
        assert member.disconnected_at is not None
    assert await due_at(runtime.redis, Job("participant_left", guest_id)) is not None

    # The grace then returns the seat exactly as a normal disconnect would.
    async with runtime.sessions.begin() as session:
        member = await session.get(Participant, guest_id)
        member.disconnected_at = clock.now_utc() - timedelta(
            seconds=runtime.settings.participant_grace_sec + 1
        )
    await fire_now(runtime.redis, "participant_left", guest_id)
    released = await next_event(host_events, "joined", lambda data: len(data["participants"]) == 1)
    assert [member["nickname"] for member in released["participants"]] == ["방장"]


async def test_a_live_pod_keeps_refreshing_its_marker(network):
    """The marker is server owned, so a client that never sends presence pings stays connected."""
    http, connect, apps = network
    runtime = apps[0].state.resources
    host, _, host_cookie = await http(0)
    guest, _, guest_cookie = await http(1)
    slug = (await host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 15})).json()[
        "slug"
    ]
    await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "참가자"})
    await connect(0, slug, host_cookie)
    guest_socket, guest_events = await connect(1, slug, guest_cookie)
    await next_event(guest_events, "joined")

    for app in apps:
        marker = pod_key(app.state.resources.realtime.pod_id)
        assert await runtime.redis.exists(marker)
        assert 0 < await runtime.redis.ttl(marker) <= 60

    assert await runtime.rounds.reconcile_connections() == 0
