from uuid import uuid1, uuid4

import pytest

from app.core.errors import AppError
from app.core.security import sign_cookie, verify_cookie
from app.domain.user.service import normalize_nickname


def test_cookie_is_authenticated_and_supports_previous_key(settings):
    user_id = uuid4()
    token = sign_cookie(user_id, settings.cookie_secret.get_secret_value())
    assert len(token) == 59
    assert verify_cookie(token, settings) == (user_id, False)
    assert verify_cookie(token[:-1] + ("A" if token[-1] != "A" else "B"), settings) is None
    previous = settings.model_copy(update={"cookie_secret_previous": settings.cookie_secret})
    from pydantic import SecretStr

    previous.cookie_secret = SecretStr("new-cookie-secret" * 4)
    assert verify_cookie(token, previous) == (user_id, True)
    assert (
        verify_cookie(sign_cookie(uuid1(), settings.cookie_secret.get_secret_value()), settings)
        is None
    )


@pytest.mark.parametrize("token", [None, "", "a" * 10000, "가" * 59, "..", "invalid.signature"])
def test_malformed_cookies_are_new_sessions(settings, token):
    assert verify_cookie(token, settings) is None


@pytest.mark.parametrize(
    "nickname", ["", "가", "a" * 11, "hi<script>", "안\u200b녕", "a\nb", "😀😀"]
)
def test_nickname_input_rejects_unsupported_characters(nickname):
    with pytest.raises(AppError, match="INVALID_NICKNAME"):
        normalize_nickname(nickname)


def test_nickname_normalizes_hangul_and_trims():
    assert normalize_nickname("  \u1100\u1161\u1102\u1161  ") == "가나"
    assert normalize_nickname("지수 12") == "지수 12"
