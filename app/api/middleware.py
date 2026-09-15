from http.cookies import SimpleCookie
from urllib.parse import urlsplit
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import AppError
from app.core.security import COOKIE_MAX_AGE, COOKIE_NAME, sign_cookie, verify_cookie


class SessionMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # `/media` needs the same cookie identity, but it never mints or refreshes one.
        api = scope["type"] == "http" and scope["path"].startswith("/api/")
        if not api and not (scope["type"] == "http" and scope["path"].startswith("/media/")):
            await self.app(scope, receive, send)
            return
        settings = scope["app"].state.resources.settings
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            try:
                parsed = urlsplit(origin)
                same_origin = parsed.scheme == scope["scheme"] and parsed.netloc == headers.get(
                    "host"
                )
            except ValueError:
                same_origin = False
            if not same_origin:
                response = JSONResponse(AppError("FORBIDDEN_ORIGIN").envelope(), status_code=403)
                await response(scope, receive, send)
                return
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get("cookie", ""))
            morsel = cookies.get(COOKIE_NAME)
            identity = verify_cookie(morsel.value if morsel else None, settings)
        except Exception:
            identity = None
        user_id, refresh = identity if identity is not None else (uuid4(), True)
        scope.setdefault("state", {})["user_id"] = user_id
        scope["state"]["authenticated"] = identity is not None
        refresh = refresh and api

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                if api:
                    response_headers["Cache-Control"] = "no-store"
                if refresh:
                    cookie_response = Response()
                    cookie_response.set_cookie(
                        COOKIE_NAME,
                        sign_cookie(user_id, settings.cookie_secret.get_secret_value()),
                        max_age=COOKIE_MAX_AGE,
                        httponly=True,
                        secure=not (settings.allow_insecure_cookie and scope["scheme"] == "http"),
                        samesite="lax",
                        path="/",
                    )
                    response_headers.append("Set-Cookie", cookie_response.headers["set-cookie"])
            await send(message)

        await self.app(scope, receive, send_with_cookie)
