"""Music tag & cover editor: read and write audio metadata without re-encoding.

Uses mutagen for pure metadata operations (no ffmpeg, no quality loss):
- ID3 (MP3): TIT2/TPE1/TALB/TRCK/TCON + APIC cover art
- MP4/M4A: \\xa9nam/\\xa9ART/\\xa9alb/trkn/\\xa9gen + covr cover art
- OGG/Opus/FLAC: Vorbis TITLE/ARTIST/ALBUM/TRACKNUMBER/GENRE + cover block

Only MP3/M4A/OGG/OPUS/FLAC support embedded tags — WAV/AAC-raw do not, and
the router rejects them before reaching here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TCON, TIT2, TPE1, TRCK
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from app.core.errors import ConversionError
from app.core.logging import get_logger

log = get_logger(__name__)

# Extensions we can read tags from (superset) vs write tags to (subset).
READABLE_SUFFIXES = frozenset({".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav"})
WRITABLE_SUFFIXES = frozenset({".mp3", ".m4a", ".ogg", ".opus", ".flac"})

# Accepted cover-art uploads.
COVER_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})

# Hard caps: tags are tiny, covers are not.
MAX_TITLE_LEN = 120
MAX_ARTIST_LEN = 120
MAX_ALBUM_LEN = 120
MAX_GENRE_LEN = 60
MAX_TRACK = 9999
MAX_COVER_BYTES = 5 * 1024 * 1024  # 5 MiB


@dataclass(frozen=True, slots=True)
class AudioTags:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    track: int | None = None
    genre: str | None = None
    has_cover: bool = False
    format_label: str = ""


def _load(path: Path) -> Any:
    """Open with mutagen, raising a user-facing error for unreadable files."""
    try:
        handle = MutagenFile(path, easy=False)
    except Exception as exc:
        raise ConversionError(f"cannot read audio tags: {exc}") from exc
    if handle is None:
        raise ConversionError("unsupported audio format for tag editing")
    return handle


def _first_text(values: Any) -> str | None:
    if isinstance(values, list) and values:
        text = str(values[0]).strip()
        return text or None
    if isinstance(values, str) and values.strip():
        return values.strip()
    return None


def read_tags(path: Path) -> AudioTags:
    """Read display tags + cover presence from an audio file (never mutates)."""
    handle = _load(path)
    suffix = path.suffix.lower()
    fmt = suffix.lstrip(".").upper() or "AUDIO"

    title = artist = album = genre = None
    track: int | None = None
    has_cover = False

    if isinstance(handle, (ID3, MP3)):
        # MP3 files arrive as MP3 (has .tags) or bare ID3; either way the
        # frames live in .tags. Never gate on the file suffix here — Telegram
        # downloads often lose their extension before reaching us.
        # (MP3 checked before MP4 below: both expose .tags, exact type wins.)
        tags = handle.tags if hasattr(handle, "tags") else handle
        fmt = "MP3"
        if tags is not None:
            title = _first_text(tags.get("TIT2").text if "TIT2" in tags else None)
            artist = _first_text(tags.get("TPE1").text if "TPE1" in tags else None)
            album = _first_text(tags.get("TALB").text if "TALB" in tags else None)
            genre = _first_text(tags.get("TCON").text if "TCON" in tags else None)
            has_cover = any(k.startswith("APIC") for k in tags.keys())
            if "TRCK" in tags:
                raw = _first_text(tags["TRCK"].text)
                if raw:
                    try:
                        track = int(raw.split("/")[0])
                    except ValueError:
                        track = None
    elif isinstance(handle, MP4):
        fmt = "M4A"
        title = _first_text(handle.tags.get("\xa9nam") if handle.tags else None)
        artist = _first_text(handle.tags.get("\xa9ART") if handle.tags else None)
        album = _first_text(handle.tags.get("\xa9alb") if handle.tags else None)
        genre = _first_text(handle.tags.get("\xa9gen") if handle.tags else None)
        covr = handle.tags.get("covr") if handle.tags else None
        has_cover = bool(covr)
        if handle.tags and "trkn" in handle.tags:
            try:
                track = int(handle.tags["trkn"][0][0])
            except (ValueError, IndexError, TypeError):
                track = None
    elif isinstance(handle, (OggVorbis, OggOpus, FLAC)):
        fmt = "OGG/OPUS" if suffix in (".ogg", ".opus") else "FLAC"
        title = _first_text(handle.get("title"))
        artist = _first_text(handle.get("artist"))
        album = _first_text(handle.get("album"))
        genre = _first_text(handle.get("genre"))
        raw_track = _first_text(handle.get("tracknumber"))
        if raw_track:
            try:
                track = int(raw_track.split("/")[0])
            except ValueError:
                track = None
        if isinstance(handle, FLAC):
            has_cover = bool(handle.pictures)
        else:
            pics = handle.get("metadata_block_picture")
            has_cover = bool(pics)
    else:
        # WAV etc: readable for format label, no embedded tags.
        title = artist = album = genre = None

    return AudioTags(
        title=title,
        artist=artist,
        album=album,
        track=track,
        genre=genre,
        has_cover=has_cover,
        format_label=fmt,
    )


def _clean(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text[:limit]


def apply_tags(
    path: Path,
    *,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    track: int | None = None,
    genre: str | None = None,
    cover_bytes: bytes | None = None,
    cover_mime: str | None = None,
) -> AudioTags:
    """Write tags + optional cover art in place. No re-encoding, no quality loss."""
    suffix = path.suffix.lower()
    if suffix not in WRITABLE_SUFFIXES:
        raise ConversionError("this format does not support embedded tags")

    # An explicitly empty string means "delete this frame" (the router sends
    # "" for the حذف command). _clean() would collapse "" to None (untouched),
    # so capture the intent first.
    clear_title = title == ""
    clear_artist = artist == ""
    clear_album = album == ""
    clear_genre = genre == ""

    title = _clean(title, MAX_TITLE_LEN)
    artist = _clean(artist, MAX_ARTIST_LEN)
    album = _clean(album, MAX_ALBUM_LEN)
    genre = _clean(genre, MAX_GENRE_LEN)
    if track is not None and not (1 <= track <= MAX_TRACK):
        raise ConversionError("track number out of range")
    if cover_bytes is not None and len(cover_bytes) > MAX_COVER_BYTES:
        raise ConversionError("cover art too large (max 5 MB)")

    handle = _load(path)

    if suffix == ".mp3":
        mp3: Any = handle if isinstance(handle, MP3) else MP3(path)
        if mp3.tags is None:
            mp3.add_tags()
        assert mp3.tags is not None
        tags = mp3.tags
        if clear_title:
            tags.delall("TIT2")
        elif title is not None:
            tags.delall("TIT2")
            tags.add(TIT2(encoding=3, text=title))
        if clear_artist:
            tags.delall("TPE1")
        elif artist is not None:
            tags.delall("TPE1")
            tags.add(TPE1(encoding=3, text=artist))
        if clear_album:
            tags.delall("TALB")
        elif album is not None:
            tags.delall("TALB")
            tags.add(TALB(encoding=3, text=album))
        if clear_genre:
            tags.delall("TCON")
        elif genre is not None:
            tags.delall("TCON")
            tags.add(TCON(encoding=3, text=genre))
        if track is not None:
            tags.delall("TRCK")
            tags.add(TRCK(encoding=3, text=str(track)))
        if cover_bytes is not None:
            tags.delall("APIC")
            tags.add(
                APIC(
                    encoding=3,
                    mime=cover_mime or "image/jpeg",
                    type=3,
                    desc="cover",
                    data=cover_bytes,
                )
            )
        mp3.save()
    elif suffix == ".m4a":
        m4a: Any = handle if isinstance(handle, MP4) else MP4(path)
        if m4a.tags is None:
            m4a.add_tags()
        assert m4a.tags is not None
        if clear_title:
            m4a.tags.pop("\xa9nam", None)
        elif title is not None:
            m4a.tags["\xa9nam"] = [title]
        if clear_artist:
            m4a.tags.pop("\xa9ART", None)
        elif artist is not None:
            m4a.tags["\xa9ART"] = [artist]
        if clear_album:
            m4a.tags.pop("\xa9alb", None)
        elif album is not None:
            m4a.tags["\xa9alb"] = [album]
        if clear_genre:
            m4a.tags.pop("\xa9gen", None)
        elif genre is not None:
            m4a.tags["\xa9gen"] = [genre]
        if track is not None:
            m4a.tags["trkn"] = [(track, 0)]
        if cover_bytes is not None:
            mime = cover_mime or "image/jpeg"
            fmt = MP4Cover.FORMAT_JPEG if "png" not in mime else MP4Cover.FORMAT_PNG
            m4a.tags["covr"] = [MP4Cover(cover_bytes, imageformat=fmt)]
        m4a.save()
    elif suffix in (".ogg", ".opus"):
        cls = OggVorbis if suffix == ".ogg" else OggOpus
        ogg: Any = handle if isinstance(handle, cls) else cls(path)
        if clear_title:
            ogg.pop("title", None)
        elif title is not None:
            ogg["title"] = title
        if clear_artist:
            ogg.pop("artist", None)
        elif artist is not None:
            ogg["artist"] = artist
        if clear_album:
            ogg.pop("album", None)
        elif album is not None:
            ogg["album"] = album
        if clear_genre:
            ogg.pop("genre", None)
        elif genre is not None:
            ogg["genre"] = genre
        if track is not None:
            ogg["tracknumber"] = str(track)
        if cover_bytes is not None:
            import base64

            pic = Picture()
            pic.type = 3
            pic.mime = cover_mime or "image/jpeg"
            pic.desc = "cover"
            pic.data = cover_bytes
            raw = pic.write()
            ogg["metadata_block_picture"] = [base64.b64encode(raw).decode("ascii")]
        ogg.save()
    elif suffix == ".flac":
        flac: Any = handle if isinstance(handle, FLAC) else FLAC(path)
        if clear_title:
            flac.pop("title", None)
        elif title is not None:
            flac["title"] = title
        if clear_artist:
            flac.pop("artist", None)
        elif artist is not None:
            flac["artist"] = artist
        if clear_album:
            flac.pop("album", None)
        elif album is not None:
            flac["album"] = album
        if clear_genre:
            flac.pop("genre", None)
        elif genre is not None:
            flac["genre"] = genre
        if track is not None:
            flac["tracknumber"] = str(track)
        if cover_bytes is not None:
            pic = Picture()
            pic.type = 3
            pic.mime = cover_mime or "image/jpeg"
            pic.desc = "cover"
            pic.data = cover_bytes
            flac.clear_pictures()
            flac.add_picture(pic)
        flac.save()

    close = getattr(handle, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass

    return read_tags(path)


def format_tags_card(tags: AudioTags, file_name: str = "") -> str:
    """Persian tag summary card for the editor panel."""
    from aiogram import html

    def row(label: str, value: str | None) -> str:
        shown = html.quote(value) if value else "<i>ثبت نشده</i>"
        return f"• <b>{label}:</b> {shown}"

    track_txt = str(tags.track) if tags.track else None
    cover_txt = "دارد 🖼️" if tags.has_cover else "ندارد"
    lines = [
        "<b>🎵 ویرایشگر تگ و کاور موزیک</b>",
    ]
    if file_name:
        lines.append(f"📁 <code>{html.quote(file_name)}</code>")
    if tags.format_label:
        lines.append(f"💽 فرمت: <code>{tags.format_label}</code>")
    lines += [
        "",
        row("نام آهنگ", tags.title),
        row("خواننده", tags.artist),
        row("آلبوم", tags.album),
        row("شماره ترک", track_txt),
        row("ژانر", tags.genre),
        f"• <b>کاور:</b> {cover_txt}",
        "",
        "یکی از موارد بالا رو انتخاب کن تا ویرایشش کنی:",
    ]
    return "\n".join(lines)
