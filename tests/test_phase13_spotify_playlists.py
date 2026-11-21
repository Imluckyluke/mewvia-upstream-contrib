"""Tests for Spotify downloading and Playlist downloading (Spotify, YouTube, SoundCloud)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User
from app.callbacks.downloader import DownloaderCB
from app.config import get_settings
from app.features.downloader.router import (
    execute_download,
)
from app.media.downloader import (
    create_token,
    detect_platform,
    download_spotify_track,
    extract_spotify_metadata_sync,
    _probe_info_sync,
    store_download_context,
)
from sqlalchemy.ext.asyncio import AsyncSession


def test_detect_platform_spotify():
    for url in [
        "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
        "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
        "https://open.spotify.com/album/4eLPsYPBmXABThSJ821sqY",
        "https://spotify.link/AbCdEf123",
    ]:
        key, name, icon = detect_platform(url)
        assert key == "spotify"
        assert name == "اسپاتیفای"
        assert icon == "🟢"


def test_extract_spotify_metadata_track_mocked():
    sample_oembed = {
        "title": "Never Gonna Give You Up",
        "thumbnail_url": "https://example.com/cover.jpg",
    }
    sample_embed_html = """
    <script id="__NEXT_DATA__" type="application/json">
    {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "name": "Never Gonna Give You Up",
                            "artists": [{"name": "Rick Astley"}],
                            "duration": 213000
                        }
                    }
                }
            }
        }
    }
    </script>
    """

    class DummyResponse:
        def __init__(self, data: bytes):
            self.data = data

        def read(self, size=None):
            return self.data[:size]

        def decode(self, enc):
            return self.data.decode(enc)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    import json

    def mock_urlopen(req, timeout=10):
        url_str = req.full_url if hasattr(req, "full_url") else str(req)
        if "oembed" in url_str:
            return DummyResponse(json.dumps(sample_oembed).encode("utf-8"))
        return DummyResponse(sample_embed_html.encode("utf-8"))

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        data = extract_spotify_metadata_sync(
            "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        )
        assert data["title"] == "Never Gonna Give You Up"
        assert data["artist"] == "Rick Astley"
        assert data["duration_seconds"] == 213
        assert len(data["tracks"]) == 1


def test_extract_spotify_metadata_playlist_mocked():
    sample_oembed = {
        "title": "Top Hits 2026",
        "thumbnail_url": "https://example.com/pl_cover.jpg",
    }
    sample_embed_html = """
    <script id="__NEXT_DATA__" type="application/json">
    {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "name": "Top Hits 2026",
                            "trackList": [
                                {"title": "Track 1", "subtitle": "Artist A", "duration": 180000},
                                {"title": "Track 2", "subtitle": "Artist B", "duration": 200000}
                            ]
                        }
                    }
                }
            }
        }
    }
    </script>
    """

    class DummyResponse:
        def __init__(self, data: bytes):
            self.data = data

        def read(self, size=None):
            return self.data[:size]

        def decode(self, enc):
            return self.data.decode(enc)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    import json

    def mock_urlopen(req, timeout=10):
        url_str = req.full_url if hasattr(req, "full_url") else str(req)
        if "oembed" in url_str:
            return DummyResponse(json.dumps(sample_oembed).encode("utf-8"))
        return DummyResponse(sample_embed_html.encode("utf-8"))

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        data = extract_spotify_metadata_sync(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        )
        assert data["kind"] == "playlist"
        assert len(data["tracks"]) == 2
        assert data["tracks"][0]["title"] == "Track 1"
        assert data["tracks"][1]["artist"] == "Artist B"


@pytest.mark.asyncio
async def test_probe_media_url_spotify_playlist():
    fake_sp_data = {
        "kind": "playlist",
        "title": "My Awesome Playlist",
        "artist": "Curator",
        "thumbnail": "https://example.com/art.jpg",
        "duration_seconds": None,
        "tracks": [
            {"title": "Song 1", "artist": "Artist 1", "duration_seconds": 180},
            {"title": "Song 2", "artist": "Artist 2", "duration_seconds": 210},
        ],
    }
    with patch(
        "app.media.downloader.extract_spotify_metadata_sync",
        return_value=fake_sp_data,
    ):
        info = _probe_info_sync("https://open.spotify.com/playlist/12345")
        assert info.platform_key == "spotify"
        assert info.is_playlist is True
        assert info.playlist_count == 2
        assert len(info.playlist_items) == 2
        assert info.title == "My Awesome Playlist"


@pytest.mark.asyncio
async def test_download_spotify_track_mocked():
    with tempfile.TemporaryDirectory() as td:
        work_dir = Path(td)
        dummy_mp3 = work_dir / "audio.mp3"
        dummy_mp3.write_bytes(b"FAKE_MP3_DATA" * 50)

        with patch(
            "app.media.downloader._download_audio_sync",
            return_value=dummy_mp3,
        ):
            res_file = await download_spotify_track(
                title="Test Song",
                artist="Test Singer",
                thumbnail_url=None,
                work_dir=work_dir,
            )
            assert res_file.is_file()
            assert res_file.stat().st_size > 0


@pytest.mark.asyncio
async def test_execute_download_spotify_track(session: AsyncSession):
    token = "testspot"
    await store_download_context(
        token,
        {
            "url": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
            "platform_key": "spotify",
            "title": "Never Gonna Give You Up",
            "uploader": "Rick Astley",
            "thumbnail_url": None,
            "reply_to_id": 999,
        },
    )

    query = MagicMock(spec=CallbackQuery)
    query.from_user = User(id=777, is_bot=False, first_name="Test")
    query.message = MagicMock(spec=Message)
    query.message.chat = Chat(id=777, type="private")
    query.message.edit_text = AsyncMock()
    query.message.delete = AsyncMock()
    query.answer = AsyncMock()

    bot = MagicMock()
    bot.send_audio = AsyncMock()
    bot.username = "Mewvia_bot"

    with tempfile.TemporaryDirectory() as td:
        fake_file = Path(td) / "audio.mp3"
        fake_file.write_bytes(b"MP3_DATA_123")

        with patch(
            "app.features.downloader.router.download_spotify_track",
            new_callable=AsyncMock,
            return_value=fake_file,
        ):
            cb_data = DownloaderCB(action="spotify_track", token=token)
            settings = get_settings()

            await execute_download(
                query=query,
                callback_data=cb_data,
                bot=bot,
                session=session,
                settings=settings,
            )

            bot.send_audio.assert_awaited_once()
            call_kwargs = bot.send_audio.await_args.kwargs
            assert call_kwargs["title"] == "Never Gonna Give You Up"
            assert call_kwargs["performer"] == "Rick Astley"


@pytest.mark.asyncio
async def test_execute_download_playlist_batch(session: AsyncSession):
    token = "testpl01"
    await store_download_context(
        token,
        {
            "url": "https://open.spotify.com/playlist/test",
            "platform_key": "spotify",
            "title": "Best Hits",
            "uploader": "Curator",
            "thumbnail_url": None,
            "reply_to_id": 999,
            "is_playlist": True,
            "playlist_count": 2,
            "playlist_items": [
                {"title": "Song 1", "artist": "Artist 1", "duration_seconds": 180},
                {"title": "Song 2", "artist": "Artist 2", "duration_seconds": 200},
            ],
        },
    )

    query = MagicMock(spec=CallbackQuery)
    query.from_user = User(id=777, is_bot=False, first_name="Test")
    query.message = MagicMock(spec=Message)
    query.message.chat = Chat(id=777, type="private")
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    bot = MagicMock()
    bot.send_audio = AsyncMock()
    bot.username = "Mewvia_bot"

    with tempfile.TemporaryDirectory() as td:
        cnt = 0

        async def _mock_dl(*args, **kwargs):
            nonlocal cnt
            cnt += 1
            f = Path(td) / f"audio_{cnt}.mp3"
            f.write_bytes(b"MP3_DATA_123")
            return f

        with patch(
            "app.features.downloader.router.download_spotify_track",
            side_effect=_mock_dl,
        ):
            cb_data = DownloaderCB(action="pl_audio_all", token=token)
            settings = get_settings()

            await execute_download(
                query=query,
                callback_data=cb_data,
                bot=bot,
                session=session,
                settings=settings,
            )

            assert bot.send_audio.await_count == 2


@pytest.mark.asyncio
async def test_execute_download_playlist_zip(session):
    token = create_token()
    await store_download_context(
        token,
        {
            "url": "https://open.spotify.com/album/4cOdK2wGLETKBW3PvgPWqT",
            "platform_key": "spotify",
            "title": "Hit Album",
            "uploader": "The Band",
            "thumbnail_url": "https://example.com/cover.jpg",
            "reply_to_id": 11,
            "is_playlist": True,
            "playlist_count": 2,
            "playlist_items": [
                {"title": "Song 1", "artist": "Artist 1", "duration_seconds": 180},
                {"title": "Song 2", "artist": "Artist 2", "duration_seconds": 200},
            ],
        },
    )

    query = MagicMock(spec=CallbackQuery)
    query.from_user = User(id=777, is_bot=False, first_name="Test")
    query.message = MagicMock(spec=Message)
    query.message.chat = Chat(id=777, type="private")
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    bot = MagicMock()
    bot.send_document = AsyncMock()
    bot.username = "Mewvia_bot"

    with tempfile.TemporaryDirectory() as td:
        cnt = 0

        async def _mock_dl(*args, **kwargs):
            nonlocal cnt
            cnt += 1
            f = Path(td) / f"audio_{cnt}.mp3"
            f.write_bytes(b"MP3_DATA_123")
            return f

        with patch(
            "app.features.downloader.router.download_spotify_track",
            side_effect=_mock_dl,
        ):
            cb_data = DownloaderCB(action="pl_zip", token=token)
            settings = get_settings()

            await execute_download(
                query=query,
                callback_data=cb_data,
                bot=bot,
                session=session,
                settings=settings,
            )

            assert bot.send_document.await_count == 1
