"""Song lyrics fetching and embedding engine.

Provides:
- Asynchronous lyrics retrieval via LRCLIB (direct get + search) with lyrics.ovh fallback
- USLT frame embedding into MP3 files via Mutagen
- Modern Telegram expandable blockquote formatting (<blockquote expandable>)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

import aiohttp
from aiogram import html
from mutagen.id3 import ID3, USLT, ID3NoHeaderError

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.ui.emoji import EmojiRegistry

log = get_logger(__name__)

_USER_AGENT = "MewviaBot/1.0 (https://t.me/Mewvia_bot)"
_TIMEOUT_SECONDS = 4.0


def clean_music_title(title: str) -> str:
    """Clean common metadata noise like (Official Video), [4K], etc."""
    pattern = (
        r"[\(\[\{].*?"
        r"(?:official|video|music video|audio|remix|feat|ft\.|4k|hd|clip|lyric|lyrics)"
        r".*?[\)\]\}]"
    )
    cleaned = re.sub(pattern, "", title, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or title


def clean_artist_name(artist: str) -> str:
    """Clean artist string, stripping 'feat.' or 'ft.' secondary credits."""
    cleaned = re.split(r"\s+(?:feat\.|ft\.|&|,)\s+", artist, flags=re.IGNORECASE)[0]
    return cleaned.strip() or artist


async def fetch_lyrics(
    title: str,
    artist: str = "",
    duration_seconds: int | None = None,
) -> str | None:
    """Fetch plain song lyrics asynchronously with multi-provider fallbacks.

    Tries:
    1. LRCLIB /api/get with exact/cleaned title + artist + duration
    2. LRCLIB /api/search with query string
    3. lyrics.ovh /v1/{artist}/{title}
    """
    if not title:
        return None

    c_title = clean_music_title(title)
    c_artist = clean_artist_name(artist) if artist else ""

    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        # Step 1: LRCLIB /api/get (fastest, most accurate)
        params: dict[str, str | int] = {"track_name": c_title}
        if c_artist:
            params["artist_name"] = c_artist
        if duration_seconds and duration_seconds > 0:
            params["duration"] = duration_seconds

        get_url = f"https://lrclib.net/api/get?{urlencode(params)}"
        try:
            async with session.get(get_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    plain = data.get("plainLyrics")
                    if plain and isinstance(plain, str) and plain.strip():
                        log.info("lyrics_found_lrclib_get", title=title, artist=artist)
                        return plain.strip()
        except Exception as exc:
            log.debug("lyrics_lrclib_get_error", error=str(exc))

        # Step 2: LRCLIB /api/search
        q_str = f"{c_title} {c_artist}".strip()
        search_url = f"https://lrclib.net/api/search?{urlencode({'q': q_str})}"
        try:
            async with session.get(search_url) as resp:
                if resp.status == 200:
                    results = await resp.json()
                    if isinstance(results, list) and results:
                        for item in results:
                            plain = item.get("plainLyrics")
                            if plain and isinstance(plain, str) and plain.strip():
                                log.info("lyrics_found_lrclib_search", title=title, artist=artist)
                                return plain.strip()
        except Exception as exc:
            log.debug("lyrics_lrclib_search_error", error=str(exc))

        # Step 3: lyrics.ovh fallback (English & Western tracks)
        if c_artist and c_title:
            ovh_url = f"https://api.lyrics.ovh/v1/{quote(c_artist)}/{quote(c_title)}"
            try:
                async with session.get(ovh_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        plain = data.get("lyrics")
                        if plain and isinstance(plain, str) and plain.strip():
                            log.info("lyrics_found_lyricsovh", title=title, artist=artist)
                            return plain.strip()
            except Exception as exc:
                log.debug("lyrics_ovh_error", error=str(exc))

    return None


def embed_lyrics_to_mp3(file_path: Path, lyrics: str) -> bool:
    """Embed plain lyrics into an MP3 file's ID3 USLT frame."""
    if not file_path.is_file() or not lyrics:
        return False
    try:
        try:
            tags = ID3(file_path)
        except ID3NoHeaderError:
            tags = ID3()

        tags.setall("USLT", [USLT(encoding=3, lang="eng", desc="", text=lyrics)])
        tags.save(file_path)
        return True
    except Exception as exc:
        log.warning("embed_lyrics_failed", path=str(file_path), error=str(exc))
        return False


def build_lyrics_caption_block(
    lyrics: str,
    registry: EmojiRegistry,
    max_len: int = 550,
) -> tuple[str, bool]:
    """Format an expandable blockquote for the audio caption.

    Returns:
        tuple of (formatted_block, is_truncated)
    """
    clean = lyrics.strip()
    if not clean:
        return "", False

    ic_lyrics = registry.text("lyrics")

    if len(clean) <= max_len:
        escaped = html.quote(clean)
        block = f"\n\n{ic_lyrics} <b>متن ترانه:</b>\n<blockquote expandable>{escaped}</blockquote>"
        return block, False

    # Truncate neatly at last newline before max_len
    cut = clean[:max_len]
    last_nl = cut.rfind("\n")
    if last_nl > 100:
        cut = cut[:last_nl]

    escaped = html.quote(cut.strip())
    block = (
        f"\n\n{ic_lyrics} <b>متن ترانه:</b>\n"
        f"<blockquote expandable>{escaped}\n… (متن کامل در پیام ریپلای)</blockquote>"
    )
    return block, True


def build_full_lyrics_message(
    title: str,
    artist: str,
    lyrics: str,
    registry: EmojiRegistry,
) -> str:
    """Format full lyrics message with expandable blockquote up to Telegram's 4096 cap."""
    ic_lyrics = registry.text("lyrics")
    ic_user = registry.text("user")

    # Leave room for header
    max_body_len = 3800
    clean = lyrics.strip()
    if len(clean) > max_body_len:
        cut = clean[:max_body_len]
        last_nl = cut.rfind("\n")
        if last_nl > 200:
            cut = cut[:last_nl]
        clean = cut + "\n…"

    escaped = html.quote(clean)
    header_artist = f"\n{ic_user} <b>خواننده:</b> {html.quote(artist)}" if artist else ""

    return (
        f"{ic_lyrics} <b>متن کامل ترانه: {html.quote(title)}</b>"
        f"{header_artist}\n\n"
        f"<blockquote expandable>{escaped}</blockquote>"
    )
