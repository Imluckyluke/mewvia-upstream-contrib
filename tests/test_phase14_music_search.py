"""Tests for direct music search feature (Spotify & SoundCloud)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User
from app.callbacks.music_search import MusicSearchCB
from app.config import get_settings
from app.features.music_search.router import (
    MusicSearchSG,
    cmd_music_search,
    select_and_download_track,
)
from app.media.music_search import (
    SearchTrack,
    create_search_session_id,
    get_search_session,
    search_soundcloud_sync,
    search_spotify_sync,
    store_search_session,
)
from sqlalchemy.ext.asyncio import AsyncSession


def test_search_track_dataclass():
    t = SearchTrack(
        id="sp123",
        platform="spotify",
        title="Test Track",
        artist="Test Artist",
        duration_str="3:45",
        duration_seconds=225,
        url="https://open.spotify.com/track/sp123",
        thumbnail_url="https://example.com/cover.jpg",
        album="Test Album",
    )
    d = t.to_dict()
    assert d["title"] == "Test Track"
    assert d["artist"] == "Test Artist"
    assert d["duration_seconds"] == 225

    t2 = SearchTrack.from_dict(d)
    assert t2 == t


@pytest.mark.asyncio
async def test_session_storage_roundtrip():
    sid = create_search_session_id()
    data = {
        "query": "Starboy",
        "platform": "spotify",
        "spotify_results": [
            {
                "id": "1",
                "platform": "spotify",
                "title": "A",
                "artist": "B",
                "duration_str": "1:00",
                "duration_seconds": 60,
                "url": "http://x",
            }
        ],
    }
    await store_search_session(sid, data)
    retrieved = await get_search_session(sid)
    assert retrieved is not None
    assert retrieved["query"] == "Starboy"
    assert retrieved["platform"] == "spotify"
    assert len(retrieved["spotify_results"]) == 1


def test_search_spotify_sync_mock():
    fake_response = {
        "data": {
            "searchV2": {
                "tracksV2": {
                    "items": [
                        {
                            "item": {
                                "data": {
                                    "uri": "spotify:track:4cOdK2wGLETKBW3PvgPWqT",
                                    "name": "Never Gonna Give You Up",
                                    "artists": {"items": [{"profile": {"name": "Rick Astley"}}]},
                                    "albumOfTrack": {
                                        "name": "Whenever You Need Somebody",
                                        "coverArt": {
                                            "sources": [{"url": "https://example.com/cover.jpg"}]
                                        },
                                    },
                                    "duration": {"totalMilliseconds": 213000},
                                }
                            }
                        }
                    ]
                }
            }
        }
    }

    with (
        patch("app.media.music_search._get_spotify_token_sync", return_value="fake_token"),
        patch("urllib.request.urlopen") as mock_url,
    ):
        mock_resp = MagicMock()
        import json

        mock_resp.read.return_value = json.dumps(fake_response).encode("utf-8")
        mock_url.return_value.__enter__.return_value = mock_resp

        results = search_spotify_sync("Rick Astley", limit=1)
        assert len(results) == 1
        assert results[0].title == "Never Gonna Give You Up"
        assert results[0].artist == "Rick Astley"
        assert results[0].duration_seconds == 213
        assert "open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT" in results[0].url


def test_search_soundcloud_sync_mock():
    fake_res = {
        "entries": [
            {
                "title": "SC Test Track",
                "uploader": "SC Artist",
                "duration": 180,
                "url": "https://soundcloud.com/artist/track",
                "thumbnail": "https://example.com/sc.jpg",
            }
        ]
    }

    mock_ydl = MagicMock()
    mock_ydl.extract_info.return_value = fake_res
    with patch("app.media.music_search._make_ydl") as mock_maker:
        mock_maker.return_value.__enter__.return_value = mock_ydl

        results = search_soundcloud_sync("Test Query", limit=1)
        assert len(results) == 1
        assert results[0].title == "SC Test Track"
        assert results[0].artist == "SC Artist"
        assert results[0].duration_seconds == 180
        assert results[0].platform == "soundcloud"


@pytest.mark.asyncio
async def test_cmd_music_search_prompt():
    msg = MagicMock(spec=Message)
    msg.message_id = 1
    msg.text = "/music"
    msg.reply = AsyncMock()

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=12345, user_id=12345)
    state = FSMContext(storage=storage, key=key)

    cmd = MagicMock()
    cmd.args = ""

    await cmd_music_search(msg, cmd, state)
    msg.reply.assert_awaited_once()
    assert await state.get_state() == MusicSearchSG.waiting_for_query.state


@pytest.mark.asyncio
async def test_select_and_download_spotify_track(session: AsyncSession):
    user = User(id=7575366278, is_bot=False, first_name="TestAdmin")
    chat = Chat(id=7575366278, type="private")
    reply_msg = MagicMock(spec=Message)
    reply_msg.message_id = 5

    panel_msg = MagicMock(spec=Message)
    panel_msg.message_id = 10
    panel_msg.chat = chat
    panel_msg.reply_to_message = reply_msg

    status_msg = MagicMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    status_msg.delete = AsyncMock()
    panel_msg.reply = AsyncMock(return_value=status_msg)

    cq = MagicMock(spec=CallbackQuery)
    cq.from_user = user
    cq.message = panel_msg
    cq.answer = AsyncMock()

    sid = "sidtest1"
    session_data = {
        "query": "Test Track",
        "platform": "spotify",
        "spotify_results": [
            {
                "id": "trk1",
                "platform": "spotify",
                "title": "Selected Song",
                "artist": "Selected Artist",
                "duration_str": "3:20",
                "duration_seconds": 200,
                "url": "https://open.spotify.com/track/trk1",
                "thumbnail_url": "https://example.com/cover.jpg",
                "album": "Album",
            }
        ],
    }
    await store_search_session(sid, session_data)

    bot = AsyncMock()
    bot.send_audio = AsyncMock()
    settings = get_settings()

    cb_data = MusicSearchCB(action="select", platform="spotify", idx=1, sid=sid)

    with tempfile.TemporaryDirectory() as td:
        fake_audio = Path(td) / "audio.mp3"
        fake_audio.write_bytes(b"dummy mp3 data")

        with patch(
            "app.features.music_search.router.download_spotify_track",
            new_callable=AsyncMock,
            return_value=fake_audio,
        ):
            await select_and_download_track(cq, cb_data, bot, session, settings)

            bot.send_audio.assert_awaited_once()
            call_kwargs = bot.send_audio.await_args.kwargs
            assert call_kwargs["title"] == "Selected Song"
            assert call_kwargs["performer"] == "Selected Artist"
            assert "Selected Song" in call_kwargs["caption"]


@pytest.mark.asyncio
async def test_cmd_music_search_group_no_query():
    msg = MagicMock(spec=Message)
    msg.message_id = 1
    msg.text = "/song"
    msg.chat = MagicMock()
    msg.chat.type = "supergroup"
    msg.reply_to_message = None
    msg.reply = AsyncMock()

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=-10012345, user_id=12345)
    state = FSMContext(storage=storage, key=key)

    cmd = MagicMock()
    cmd.args = ""

    await cmd_music_search(msg, cmd, state)
    msg.reply.assert_awaited_once()
    reply_text = msg.reply.await_args[0][0]
    assert "جستجوی موزیک در گروه" in reply_text
    # Ensure FSM state is NOT set in group chatter
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_cmd_music_search_group_reply_query():
    msg = MagicMock(spec=Message)
    msg.message_id = 2
    msg.text = "/song"
    msg.chat = MagicMock()
    msg.chat.type = "supergroup"

    replied = MagicMock(spec=Message)
    replied.text = "Mohsen Chavoshi Hosein"
    replied.caption = None
    msg.reply_to_message = replied

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=-10012345, user_id=12345)
    state = FSMContext(storage=storage, key=key)

    cmd = MagicMock()
    cmd.args = ""

    with patch(
        "app.features.music_search.router._execute_search", new_callable=AsyncMock
    ) as mock_exec:
        await cmd_music_search(msg, cmd, state)
        mock_exec.assert_awaited_once_with(
            msg, "Mohsen Chavoshi Hosein", platform="spotify", state=state
        )

