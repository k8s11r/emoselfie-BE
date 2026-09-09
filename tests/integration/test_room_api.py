import asyncio
from contextlib import AsyncExitStack
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, select, update

from app.core.redis import rate_limit_key
from app.core.security import COOKIE_NAME, private_rate_subject, verify_cookie
from app.db.models import Participant, Room, User
from app.domain.enums import ParticipantStatus, RoomStatus
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
async def clients(settings):
    app = create_app(settings)
    clients = []
    user_ids = set()
    ips = []
    async with app.router.lifespan_context(app), AsyncExitStack() as stack:
        runtime = app.state.resources

        async def make_client():
            ip = f"test-{uuid4()}"
            ips.append(ip)
            client = await stack.enter_async_context(
                AsyncClient(
                    transport=ASGITransport(app=app, client=(ip, 12345)), base_url="https://test"
                )
            )
            await client.get("/api/me")
            clients.append(client)
            user_ids.add(verify_cookie(client.cookies.get(COOKIE_NAME), settings)[0])
            return client

        try:
            yield make_client, runtime
        finally:
            for client in clients:
                identity = verify_cookie(client.cookies.get(COOKIE_NAME), settings)
                if identity:
                    user_ids.add(identity[0])
            async with runtime.sessions.begin() as session:
                await session.execute(delete(Room).where(Room.host_user_id.in_(user_ids)))
                await session.execute(delete(User).where(User.uuid.in_(user_ids)))
            keys = [
                rate_limit_key("room:create:user", private_rate_subject(str(uid)))
                for uid in user_ids
            ]
            keys.extend(
                rate_limit_key(scope, private_rate_subject(ip))
                for ip in ips
                for scope in [
                    "room:create:ip",
                    "room:join:ip",
                    "room:preview:ip",
                ]
            )
            if keys:
                await runtime.redis.delete(*keys)


async def create_room(client):
    response = await client.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 15})
    assert response.status_code == 201, response.text
    return response.json()["slug"]


async def test_session_cookie_tamper_rotation_and_no_uuid_payload(clients, settings):
    make, _ = clients
    client = await make()
    original = client.cookies.get(COOKIE_NAME)
    response = await client.patch("/api/me", json={"nickname": "  지수  "})
    assert response.json() == {"nickname": "지수"}
    restored = await client.get("/api/me")
    assert restored.json() == {"nickname": "지수", "hasActiveRoom": False, "activeRoomSlug": None}
    assert original[:36] not in restored.text
    assert restored.headers["cache-control"] == "no-store"
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, original[:-1] + ("A" if original[-1] != "A" else "B"))
    changed = await client.get("/api/me")
    assert changed.json()["nickname"] is None
    cookie = changed.headers["set-cookie"]
    assert all(
        attribute in cookie
        for attribute in ["HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=31536000"]
    )
    # Replace a manually set hostless cookie with the newly issued server cookie.
    client.cookies.clear()
    client.cookies.extract_cookies(changed)
    assert (
        verify_cookie(client.cookies.get(COOKIE_NAME), settings)[0]
        != verify_cookie(original, settings)[0]
    )


async def test_waiting_room_flow_permissions_and_slot_release(clients):
    make, _ = clients
    host, guest = await make(), await make()
    slug = await create_room(host)
    assert (await host.get("/api/me")).json()["activeRoomSlug"] == slug
    preview = await guest.get(f"/api/rooms/{slug}")
    assert set(preview.json()) == {"exists", "status", "isFull", "isHost", "settings"}
    assert (await host.get(f"/api/rooms/{slug}/state")).status_code == 403
    host_join = await host.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    guest_join = await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "방장"})
    assert host_join.status_code == guest_join.status_code == 201
    assert host_join.json()["participantId"] != guest_join.json()["participantId"]
    assert host_join.json()["isHost"] is True
    assert guest_join.json()["isHost"] is False
    denied = await guest.patch(f"/api/rooms/{slug}/settings", json={"roundCount": 7})
    assert denied.status_code == 403
    updated = await host.patch(f"/api/rooms/{slug}/settings", json={"roundCount": 7})
    assert updated.json() == {"roundCount": 7, "timeLimitSec": 15, "emotionSet": "full"}
    state = await guest.get(f"/api/rooms/{slug}/state")
    assert state.json()["room"]["settings"]["roundCount"] == 7
    assert len(state.json()["participants"]) == 2
    assert state.json()["game"] is None
    assert isinstance(state.json()["serverTimeMs"], int)
    assert (await guest.post(f"/api/rooms/{slug}/close")).status_code == 403
    assert (await host.post(f"/api/rooms/{slug}/close")).json() == {"ok": True}
    assert (await host.post(f"/api/rooms/{slug}/close")).json() == {"ok": True}
    assert (await guest.get(f"/api/rooms/{slug}")).status_code == 410
    assert (await host.get("/api/me")).json()["hasActiveRoom"] is False
    assert await create_room(host) != slug


async def test_previous_signing_key_preserves_identity_and_refreshes_cookie(clients, settings):
    make, runtime = clients
    client = await make()
    original = client.cookies.get(COOKIE_NAME)
    user_id = verify_cookie(original, settings)[0]
    await client.patch("/api/me", json={"nickname": "기존사용자"})
    runtime.settings.cookie_secret_previous = settings.cookie_secret
    runtime.settings.cookie_secret = SecretStr("rotated-secret" * 4)
    response = await client.get("/api/me")
    assert response.json()["nickname"] == "기존사용자"
    assert "set-cookie" in response.headers
    assert verify_cookie(client.cookies.get(COOKIE_NAME), runtime.settings) == (user_id, False)


async def test_create_and_join_requests_are_idempotent_under_race(clients):
    make, _ = clients
    host = await make()
    responses = await asyncio.gather(
        *(host.post("/api/rooms", json={"roundCount": 3, "timeLimitSec": 15}) for _ in range(4))
    )
    assert sorted(response.status_code for response in responses) == [200, 200, 200, 201]
    assert len({response.json()["slug"] for response in responses}) == 1
    slug = responses[0].json()["slug"]
    joins = await asyncio.gather(
        *(host.post(f"/api/rooms/{slug}/participants", json={"nickname": "중복"}) for _ in range(4))
    )
    assert {response.status_code for response in joins} == {201}
    assert len({response.json()["participantId"] for response in joins}) == 1


async def test_last_room_slot_is_atomic_and_repeat_join_works_when_full(clients):
    make, _ = clients
    participants = [await make() for _ in range(14)]
    slug = await create_room(participants[0])
    results = await asyncio.gather(
        *(
            client.post(f"/api/rooms/{slug}/participants", json={"nickname": "테스터"})
            for client in participants
        )
    )
    assert [response.status_code for response in results].count(201) == 12
    assert [response.status_code for response in results].count(409) == 2
    joined = [response.json() for response in results if response.status_code == 201]
    assert {row["colorTag"] for row in joined} == set(range(12))
    winner = next(
        client
        for client, response in zip(participants, results, strict=True)
        if response.status_code == 201
    )
    replay = await winner.post(f"/api/rooms/{slug}/participants", json={"nickname": "바꾼이름"})
    assert replay.status_code == 201
    assert replay.json()["nickname"] == "테스터"
    assert (await winner.get(f"/api/rooms/{slug}")).json()["isFull"] is True


async def test_left_reentry_preserves_points_but_rechecks_capacity_and_color(clients):
    make, runtime = clients
    host, returning, newcomer = await make(), await make(), await make()
    slug = await create_room(host)
    joined = (
        await returning.post(f"/api/rooms/{slug}/participants", json={"nickname": "복귀자"})
    ).json()
    async with runtime.sessions.begin() as session:
        await session.execute(
            update(Participant)
            .where(Participant.id == int(joined["participantId"]))
            .values(
                status=ParticipantStatus.LEFT,
                total_points=170,
            )
        )
    assert (await returning.get(f"/api/rooms/{slug}/state")).status_code == 403
    new = (
        await newcomer.post(f"/api/rooms/{slug}/participants", json={"nickname": "새참가"})
    ).json()
    restored = (
        await returning.post(f"/api/rooms/{slug}/participants", json={"nickname": "복귀자"})
    ).json()
    assert restored["participantId"] == joined["participantId"]
    assert restored["colorTag"] != new["colorTag"]
    assert (await returning.get(f"/api/rooms/{slug}/state")).json()["me"]["totalPoints"] == 170


async def test_playing_room_admission_cannot_view_game_or_change_settings(clients):
    make, runtime = clients
    host, guest = await make(), await make()
    slug = await create_room(host)
    async with runtime.sessions.begin() as session:
        await session.execute(
            update(Room).where(Room.invite_slug == slug).values(status=RoomStatus.PLAYING)
        )
    result = await guest.post(f"/api/rooms/{slug}/participants", json={"nickname": "대기자"})
    assert result.json()["status"] == "waiting_next_game"
    state = await guest.get(f"/api/rooms/{slug}/state")
    assert state.status_code == 503  # In-game snapshots are not exposed until BE-039.
    assert "participants" not in state.json()
    assert (
        await host.patch(f"/api/rooms/{slug}/settings", json={"timeLimitSec": 30})
    ).status_code == 409


async def test_invalid_settings_origin_and_rate_limit(clients):
    make, _ = clients
    host = await make()
    for body in [{"roundCount": 4}, {"timeLimitSec": 45}, {"isHost": True}]:
        assert (await host.post("/api/rooms", json=body)).status_code == 422
    for origin in ["https://evil.test", "null", "https://["]:
        assert (
            await host.patch("/api/me", headers={"Origin": origin}, json={"nickname": "해커"})
        ).status_code == 403
    for _ in range(5):
        assert (await host.post("/api/rooms", json={})).status_code in {200, 201}
    blocked = await host.post("/api/rooms", json={})
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["retry-after"]) <= 720
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


async def test_missing_closed_finished_and_participant_identity_not_forgeable(clients):
    make, runtime = clients
    host, outsider = await make(), await make()
    assert (await outsider.get("/api/rooms/missing-room-slug")).status_code == 404
    slug = await create_room(host)
    assert (
        await outsider.get(f"/api/rooms/{slug}/state?isHost=true&participantId=1")
    ).status_code == 403
    async with runtime.sessions.begin() as session:
        room = await session.scalar(select(Room).where(Room.invite_slug == slug))
        room.status = RoomStatus.FINISHED
    assert (await outsider.get(f"/api/rooms/{slug}")).json()["error"]["code"] == "ROOM_FINISHED"
