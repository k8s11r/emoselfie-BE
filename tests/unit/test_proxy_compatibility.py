from types import SimpleNamespace

import pytest
import socketio
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.middleware import SessionMiddleware
from app.core.config import Settings
from app.realtime.server import SocketGateway


@pytest.fixture
def socket_app():
    server = socketio.AsyncServer(async_mode="asgi", monitor_clients=False)
    app = FastAPI()
    app.state.resources = SimpleNamespace(
        realtime=SimpleNamespace(asgi=socketio.ASGIApp(server, socketio_path=""))
    )
    app.mount("/socket.io", SocketGateway())
    return app


@pytest.mark.parametrize(
    ("forwarded_proto", "origin_scheme"),
    [("ws", "http"), ("wss", "https"), ("http", "http"), ("https", "https")],
)
def test_socket_handshake_accepts_same_origin_with_port(socket_app, forwarded_proto, origin_scheme):
    with TestClient(socket_app, base_url="http://localhost:8080") as client:
        with client.websocket_connect(
            "ws://localhost:8080/socket.io/?EIO=4&transport=websocket",
            headers={
                "Upgrade": "websocket",
                "Origin": f"{origin_scheme}://localhost:8080",
                "X-Forwarded-Proto": forwarded_proto,
            },
        ) as socket:
            assert socket.receive_text().startswith("0")


@pytest.mark.parametrize(
    "origin", ["https://evil.test", "http://localhost:8080", "https://localhost", "null"]
)
def test_socket_handshake_still_rejects_foreign_origins(socket_app, origin):
    with TestClient(socket_app, base_url="http://localhost:8080") as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "ws://localhost:8080/socket.io/?EIO=4&transport=websocket",
                headers={"Upgrade": "websocket", "Origin": origin, "X-Forwarded-Proto": "wss"},
            ):
                pytest.fail("Foreign origin accepted")


@pytest.mark.parametrize(
    ("environment", "allow_insecure", "scheme", "secure"),
    [
        ("production", False, "http", True),
        ("production", False, "https", True),
        ("development", False, "http", True),
        ("development", True, "http", False),
        ("development", True, "https", True),
    ],
)
async def test_cookie_policy_preserves_identity_and_https_security(
    settings, environment, allow_insecure, scheme, secure
):
    configured = Settings.model_validate(
        {
            **settings.model_dump(),
            "app_env": environment,
            "inference_backend": "real",
            "allow_insecure_cookie": allow_insecure,
        }
    )
    app = FastAPI()
    app.state.resources = SimpleNamespace(settings=configured)
    app.add_middleware(SessionMiddleware)

    @app.get("/api/check")
    async def check(request: Request):
        return {"id": str(request.state.user_id), "authenticated": request.state.authenticated}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=f"{scheme}://localhost:8080"
    ) as client:
        response = await client.get("/api/check")
        cookie = response.headers["set-cookie"]
        assert ("Secure" in cookie) is secure
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
        if not secure or scheme == "https":
            restored = await client.get("/api/check")
            assert restored.json() == {"id": response.json()["id"], "authenticated": True}
            assert "set-cookie" not in restored.headers


@pytest.mark.parametrize("environment", ["production", "test"])
def test_insecure_cookie_option_is_restricted_to_development(settings, environment):
    with pytest.raises(ValidationError, match="development"):
        Settings.model_validate(
            {
                **settings.model_dump(),
                "app_env": environment,
                "inference_backend": "real",
                "allow_insecure_cookie": True,
            }
        )


@pytest.mark.parametrize(
    ("peer", "forwarded_proto", "origin", "status", "secure"),
    [
        ("127.0.0.1", "https", "https://localhost:8080", 200, True),
        ("127.0.0.1", "http", "http://localhost:8080", 200, False),
        ("127.0.0.1", "https", "http://localhost:8080", 403, None),
        ("127.0.0.1", "https", "https://localhost", 403, None),
        ("127.0.0.1", "https", "https://evil.test", 403, None),
        ("192.0.2.1", "https", "https://localhost:8080", 403, None),
    ],
)
async def test_api_origin_and_cookie_use_trusted_proxy_scheme(
    settings, peer, forwarded_proto, origin, status, secure
):
    configured = Settings.model_validate(
        {**settings.model_dump(), "app_env": "development", "allow_insecure_cookie": True}
    )
    app = FastAPI()
    app.state.resources = SimpleNamespace(settings=configured)
    app.add_middleware(SessionMiddleware)

    @app.post("/api/check")
    async def check():
        return {"ok": True}

    proxy = ProxyHeadersMiddleware(app, trusted_hosts=["127.0.0.1"])
    async with AsyncClient(
        transport=ASGITransport(app=proxy, client=(peer, 12345)),
        base_url="http://localhost:8080",
    ) as client:
        response = await client.post(
            "/api/check", headers={"Origin": origin, "X-Forwarded-Proto": forwarded_proto}
        )
    assert response.status_code == status
    if status == 200:
        assert ("Secure" in response.headers["set-cookie"]) is secure
    else:
        assert response.json()["error"]["code"] == "FORBIDDEN_ORIGIN"
        assert "set-cookie" not in response.headers
