"""Tests for the lyrics engine, expandable blockquote formatting, and MP3 embedding."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from mutagen.id3 import ID3

from app.media.lyrics import (
    build_full_lyrics_message,
    build_lyrics_caption_block,
    clean_artist_name,
    clean_music_title,
    embed_lyrics_to_mp3,
    fetch_lyrics,
)


class DummyRegistry:
    def text(self, slot: str) -> str:
        return f"[{slot}]"


def test_clean_music_title_and_artist() -> None:
    raw_title = "In The End (Official Music Video) [4K Remastered]"
    assert clean_music_title(raw_title) == "In The End"

    raw_artist = "Linkin Park feat. Jay-Z & Friends"
    assert clean_artist_name(raw_artist) == "Linkin Park"


def test_build_lyrics_caption_block_short() -> None:
    registry = DummyRegistry()
    lyrics = "First line of the song\nSecond line of the song"
    block, is_truncated = build_lyrics_caption_block(lyrics, registry, max_len=200)

    assert not is_truncated
    assert "<blockquote expandable>" in block
    assert "First line of the song" in block
    assert "Second line of the song" in block
    assert "</blockquote>" in block


def test_build_lyrics_caption_block_truncated() -> None:
    registry = DummyRegistry()
    lines = [f"Line number {i} of the long lyrics" for i in range(30)]
    lyrics = "\n".join(lines)
    block, is_truncated = build_lyrics_caption_block(lyrics, registry, max_len=200)

    assert is_truncated
    assert "<blockquote expandable>" in block
    assert "… (متن کامل در پیام ریپلای)" in block
    assert "</blockquote>" in block
    assert len(block) < 350


def test_build_full_lyrics_message() -> None:
    registry = DummyRegistry()
    msg = build_full_lyrics_message("Behet Ghol Midam", "Mohsen Yeganeh", "خودت میخای بری...", registry)

    assert "Behet Ghol Midam" in msg
    assert "Mohsen Yeganeh" in msg
    assert "<blockquote expandable>" in msg
    assert "خودت میخای بری..." in msg
    assert "</blockquote>" in msg


def test_embed_lyrics_to_mp3() -> None:
    with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp:
        path = Path(tmp.name)
        path.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 200)
        lyrics_text = "Hello darkness my old friend\nI've come to talk with you again"
        res = embed_lyrics_to_mp3(path, lyrics_text)
        assert res is True

        tags = ID3(path)
        uslt = tags.getall("USLT")
        assert len(uslt) == 1
        assert uslt[0].text == lyrics_text


@pytest.mark.asyncio
async def test_fetch_lyrics_mocked() -> None:
    mock_data = {"plainLyrics": "Test lyrics from mock API\nSecond line"}
    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_data)
        mock_get.return_value.__aenter__.return_value = mock_resp

        lyrics = await fetch_lyrics("Song", "Artist")
        assert lyrics == "Test lyrics from mock API\nSecond line"
