"""Regression tests for the security / integrity hardening pass.

Covers pure, side-effect free logic only:
- platform detection on complete domain labels
- SSRF guard for user supplied URLs
- GIF document classification
- server-side XO board sessions
- group command / mention addressing
"""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import CallbackQuery, Chat, Document, Message
from aiogram.types import User as TgUser
from app.callbacks.game import GameCB
from app.config import get_settings
from app.features.downloader.router import _context_owner_mismatch
from app.features.group_media.router import GroupMediaInvocationFilter
from app.media.downloader import (
    detect_platform,
    is_safe_remote_url,
    is_supported_media_url,
)
from app.media.kinds import MediaKind, detect_media_kind

# `app.features.games.router` is re-exported as an aiogram Router by the
# package __init__, so fetch the real module object for patching.
games_router = import_module("app.features.games.router")

USER_ID = 424_242_424


def _private_message() -> Message:
    return Message(
        message_id=42,
        date=datetime.now(UTC),
        chat=Chat(id=USER_ID, type="private"),
        text="دوز",
    )


def _group_message(text: str) -> Message:
    return Message(
        message_id=7,
        date=datetime.now(UTC),
        chat=Chat(id=-1001234567890, type="supergroup"),
        text=text,
    )


def _document(name: str, mime: str) -> Document:
    return Document(
        file_id=f"file_{name}",
        file_unique_id=f"uniq_{name}",
        file_name=name,
        mime_type=mime,
        file_size=1024,
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Domains that merely contain a platform name must not match.
        ("https://www.netflix.com/watch/80100172", "other"),
        ("https://notyoutube.com/watch?v=1", "other"),
        ("https://youtube.com.evil.example/watch?v=1", "other"),
        ("https://x.com.example.org/user/status/1", "other"),
        # Real hosts and subdomains still match.
        ("https://m.youtube.com/watch?v=1", "youtube"),
        ("https://music.youtube.com/watch?v=1", "youtube"),
        ("https://youtube-nocookie.com/embed/1", "youtube"),
        ("https://x.com/user/status/1", "twitter"),
        ("https://mobile.twitter.com/user/status/1", "twitter"),
        ("https://open.spotify.com/track/1", "spotify"),
        ("https://spoti.fi/abc", "spotify"),
        ("https://on.soundcloud.com/abc", "soundcloud"),
        ("https://de.pinterest.com/pin/1", "pinterest"),
        ("https://pinterest.co.uk/pin/1", "pinterest"),
        ("https://fb.me/abc", "facebook"),
        ("https://www.facebook.com/watch?v=1", "facebook"),
        ("https://redd.it/abc", "reddit"),
        ("https://www.aparat.com/v/abcd", "aparat"),
        ("https://example.com/clip.mp4", "other"),
    ],
)
def test_detect_platform_uses_domain_boundaries(url: str, expected: str) -> None:
    assert detect_platform(url)[0] == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "ftp://example.com/clip.mp4",
        "file:///etc/passwd",
        "http://localhost/clip.mp4",
        "http://localhost:8080/clip.mp4",
        "http://foo.localhost/clip.mp4",
        "http://127.0.0.1/clip.mp4",
        "http://127.1.2.3/clip.mp4",
        "http://127.1/clip.mp4",
        "http://0177.0.0.1/clip.mp4",
        "http://10.0.0.5/clip.mp4",
        "http://172.16.5.4/clip.mp4",
        "http://192.168.1.10/clip.mp4",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/clip.mp4",
        "http://[fd00::1]/clip.mp4",
        "http://postgres:5432/clip.mp4",
        "http://redis:6379/0",
        "http://bot-api:8081/clip.mp4",
        "http://printer.local/clip.mp4",
        "http://intranet.internal/clip.mp4",
        "https://user:pass@example.com/clip.mp4",
    ],
)
def test_is_safe_remote_url_rejects_internal_targets(url: str) -> None:
    assert is_safe_remote_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://example.com/clip.mp4",
        "http://example.com/clip.mp4",
        "https://8.8.8.8/clip.mp4",
        "HTTPS://Example.COM/clip.mp4",
        "https://example.com./clip.mp4",
    ],
)
def test_is_safe_remote_url_allows_public_targets(url: str) -> None:
    assert is_safe_remote_url(url) is True


def test_supported_media_url_never_accepts_internal_hosts() -> None:
    # Even a supported-looking direct media extension must not bypass the
    # SSRF guard.
    assert is_supported_media_url("http://localhost/clip.mp4") is False
    assert is_supported_media_url("http://192.168.0.10/clip.mp4") is False
    assert is_supported_media_url("http://bot-api:8081/file.mp4") is False
    assert is_supported_media_url("https://example.com/clip.mp4") is True


def test_download_context_owner_binding() -> None:
    ctx = {"url": "https://example.com", "user_id": 100, "chat_id": 200}
    assert _context_owner_mismatch(ctx, 100, 200) is False
    assert _context_owner_mismatch(ctx, 101, 200) is True
    assert _context_owner_mismatch(ctx, 100, 201) is True
    # Tokens stored before ownership binding must keep working.
    assert _context_owner_mismatch({"url": "https://example.com"}, 1, 2) is False


@pytest.mark.parametrize(
    ("name", "mime", "expected"),
    [
        ("animation.gif", "image/gif", MediaKind.DOCUMENT_VIDEO),
        ("animation.gif", "application/octet-stream", MediaKind.DOCUMENT_VIDEO),
        ("frame.png", "image/png", MediaKind.DOCUMENT_IMAGE),
        ("clip.webm", "video/webm", MediaKind.DOCUMENT_VIDEO),
        ("clip.mov", "video/quicktime", MediaKind.DOCUMENT_VIDEO),
    ],
)
def test_gif_documents_are_treated_as_video(name: str, mime: str, expected: MediaKind) -> None:
    message = Message(
        message_id=9,
        date=datetime.now(UTC),
        chat=Chat(id=USER_ID, type="private"),
        document=_document(name, mime),
    )
    detected = detect_media_kind(message)
    assert detected is not None
    assert detected[0] == expected


async def test_xo_session_memory_fallback_roundtrip() -> None:
    with patch.object(games_router, "get_redis", side_effect=RuntimeError("redis offline")):
        await games_router.save_xo_session(1, 2, "X________", "easy")
        assert await games_router.load_xo_session(1, 2) == {
            "board": "X________",
            "diff": "easy",
        }

        await games_router.drop_xo_session(1, 2)
        assert await games_router.load_xo_session(1, 2) is None


async def test_xo_session_uses_redis_when_available() -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str) -> str | None:
            return self.store.get(key)

        async def set(self, key: str, value: str, ex: int | None = None) -> None:
            self.store[key] = value

        async def delete(self, key: str) -> None:
            self.store.pop(key, None)

    redis = FakeRedis()
    with patch.object(games_router, "get_redis", return_value=redis):
        await games_router.save_xo_session(10, 20, "XO_______", "hard")
        assert await games_router.load_xo_session(10, 20) == {
            "board": "XO_______",
            "diff": "hard",
        }
        await games_router.drop_xo_session(10, 20)
        assert await games_router.load_xo_session(10, 20) is None


async def test_forged_xo_board_is_rejected_before_touching_stats() -> None:
    query = CallbackQuery(
        id="cb_forged",
        from_user=TgUser(id=USER_ID, is_bot=False, first_name="Tester"),
        message=_private_message(),
        chat_instance="ci",
        data=GameCB(action="move", diff="easy", cell=2, b="XX_OO____").pack(),
    )
    answers: list[str] = []

    async def fake_answer(self: object, text: str = "", show_alert: bool = False) -> None:
        answers.append(text)

    with (
        patch.object(CallbackQuery, "answer", fake_answer),
        patch.object(games_router, "load_xo_session", AsyncMock(return_value=None)),
        patch.object(games_router, "UserRepo") as user_repo,
        patch.object(games_router, "GameRepo") as game_repo,
    ):
        await games_router.cb_handle_move(
            query,
            GameCB(action="move", diff="easy", cell=2, b="XX_OO____"),
            AsyncMock(),
        )

    assert answers and "منقضی" in answers[0]
    user_repo.assert_not_called()
    game_repo.assert_not_called()


async def test_stale_xo_board_mismatch_is_rejected() -> None:
    query = CallbackQuery(
        id="cb_stale",
        from_user=TgUser(id=USER_ID, is_bot=False, first_name="Tester"),
        message=_private_message(),
        chat_instance="ci",
        data=GameCB(action="move", diff="easy", cell=1, b="X________").pack(),
    )
    answers: list[str] = []

    async def fake_answer(self: object, text: str = "", show_alert: bool = False) -> None:
        answers.append(text)

    stored = {"board": "_________", "diff": "easy"}
    with (
        patch.object(CallbackQuery, "answer", fake_answer),
        patch.object(games_router, "load_xo_session", AsyncMock(return_value=stored)),
        patch.object(games_router, "UserRepo") as user_repo,
        patch.object(games_router, "GameRepo") as game_repo,
    ):
        await games_router.cb_handle_move(
            query,
            GameCB(action="move", diff="easy", cell=1, b="X________"),
            AsyncMock(),
        )

    assert answers and "تغییر کرده" in answers[0]
    user_repo.assert_not_called()
    game_repo.assert_not_called()


async def test_group_command_addressed_to_other_bot_is_ignored() -> None:
    filter_obj = GroupMediaInvocationFilter()
    settings = get_settings()
    bot_username = settings.bot_username

    assert await filter_obj(_group_message("/media@SomeOtherBot"), settings) is False
    assert await filter_obj(_group_message("سلام"), settings) is False
    assert await filter_obj(_group_message("سلام @SomeOtherBot"), settings) is False
    assert await filter_obj(_group_message(f"/media@{bot_username}"), settings) is True
    assert await filter_obj(_group_message("/media"), settings) is True
    assert await filter_obj(_group_message(f"سلام @{bot_username}"), settings) is True
    # Documented legacy username stays a valid alias.
    assert await filter_obj(_group_message("سلام @Mewvia_bot"), settings) is True


async def test_group_mention_requires_exact_username() -> None:
    filter_obj = GroupMediaInvocationFilter()
    settings = get_settings()

    assert await filter_obj(_group_message(f"سلام @{settings.bot_username}News"), settings) is False
    assert (
        await filter_obj(_group_message(f"سلام @{settings.bot_username}، این را ببین"), settings)
        is True
    )
