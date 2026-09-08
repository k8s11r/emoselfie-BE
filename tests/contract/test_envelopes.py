import json
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.api.errors import install_error_handlers
from app.core.errors import AppError

FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures/errors.json").read_text())


def test_http_and_socket_error_fixtures():
    assert AppError("ROOM_FULL", detail={"capacity": 12}).envelope() == FIXTURES["http"]
    assert AppError("NOT_A_VIEWER").socket_ack() == FIXTURES["socket"]


async def test_errors_are_consistent_and_do_not_echo_inputs():
    app = FastAPI()
    install_error_handlers(app)

    class Input(BaseModel):
        count: int

    @app.post("/validate")
    def validate(body: Input):
        return body

    @app.get("/limited")
    def limited():
        raise AppError("RATE_LIMITED", retry_after=17)

    @app.get("/broken")
    def broken():
        raise RuntimeError("secret-image-and-token")

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.post("/validate", json={"count": "secret-image-and-token"})
        assert response.status_code == 422
        assert "secret-image-and-token" not in response.text
        assert response.json() == AppError("INVALID_REQUEST").envelope()
        assert (await client.get("/missing")).json() == AppError("NOT_FOUND").envelope()
        assert (await client.get("/validate")).status_code == 405
        limited = await client.get("/limited")
        assert limited.status_code == 429
        assert limited.headers["Retry-After"] == "17"
        assert (await client.get("/broken")).json() == AppError("INTERNAL_ERROR").envelope()
