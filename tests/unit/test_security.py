from uuid import uuid1, uuid4

import pytest

from app.core.errors import AppError
from app.core.security import (
    capture_token,
    media_token,
    sign_cookie,
    verify_capture_token,
    verify_cookie,
    verify_media_token,
)
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


def test_capture_token_binds_round_participant_and_deadline(settings):
    secret = settings.capture_token_secret.get_secret_value()
    token = capture_token(87, 42, 1_788_800_000_000, secret)

    assert len(token) == 32 and token.isascii()
    assert verify_capture_token(token, 87, 42, 1_788_800_000_000, secret)
    # Every field is inside the signed payload, so no other capture session accepts it.
    assert not verify_capture_token(token, 88, 42, 1_788_800_000_000, secret)
    assert not verify_capture_token(token, 87, 43, 1_788_800_000_000, secret)
    assert not verify_capture_token(token, 87, 42, 1_788_800_000_001, secret)
    assert not verify_capture_token(token, 87, 42, 1_788_800_000_000, "another" * 8)


@pytest.mark.parametrize("token", [None, "", "a" * 31, "a" * 33, "가" * 32])
def test_malformed_capture_tokens_are_rejected(settings, token):
    secret = settings.capture_token_secret.get_secret_value()
    assert not verify_capture_token(token, 87, 42, 1_788_800_000_000, secret)


def test_capture_tokens_differ_per_participant_and_never_reuse_the_cookie_secret(settings):
    capture = settings.capture_token_secret.get_secret_value()
    mine = capture_token(87, 42, 1_788_800_000_000, capture)
    theirs = capture_token(87, 43, 1_788_800_000_000, capture)

    assert mine != theirs
    assert mine != capture_token(
        87, 42, 1_788_800_000_000, settings.cookie_secret.get_secret_value()
    )


def test_media_token_carries_the_viewer_and_expiry_it_was_signed_for(settings):
    secret = settings.media_token_secret.get_secret_value()
    token = media_token(87, 913, 42, 1_788_800_060_000, secret)

    assert verify_media_token(token, secret) == (87, 913, 42, 1_788_800_060_000)
    assert verify_media_token(token, "another" * 8) is None
    # The viewer is inside the signature, so a leaked link opens for nobody else (§8.4).
    assert media_token(87, 913, 43, 1_788_800_060_000, secret) != token


@pytest.mark.parametrize(
    "token",
    [
        "",
        "no-dot",
        "a.b",
        "a" * 300,
        "가.나",
        "eyJ.tampered",
    ],
)
def test_malformed_media_tokens_are_rejected(settings, token):
    assert verify_media_token(token, settings.media_token_secret.get_secret_value()) is None


def test_a_media_token_cannot_be_edited_without_the_secret(settings):
    secret = settings.media_token_secret.get_secret_value()
    token = media_token(87, 913, 42, 1_788_800_060_000, secret)
    payload, signature = token.split(".")

    forged = media_token(87, 913, 43, 1_788_800_060_000, "guess" * 8).split(".")[0]
    assert verify_media_token(f"{forged}.{signature}", secret) is None
    assert verify_media_token(f"{payload}.{'A' * 32}", secret) is None
