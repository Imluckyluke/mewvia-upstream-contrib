"""Social media & web media downloader engine (yt-dlp + gallery-dl).

Supports over 1800 platforms:
- YouTube (video, shorts, audio/music)
- Instagram (reels, stories, posts, carousels)
- TikTok (videos without watermark, audio)
- Twitter / X (videos, GIFs, photos)
- Pinterest (video pins, high-res image pins)
- SoundCloud (audio tracks)
- Reddit, Aparat, Facebook, Vimeo and more.

All downloads are scoped to temporary directories and bounded by the
process-wide job concurrency limiter and timeouts.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yt_dlp

from app.cache.redis import get_redis
from app.core.errors import ConversionError
from app.core.logging import get_logger
from app.core.units import human_duration

log = get_logger(__name__)

_ytdlp_init_lock = threading.Lock()


def _make_ydl(opts: dict[str, Any]) -> yt_dlp.YoutubeDL:
    """Thread-safe YoutubeDL factory preventing concurrent plugin registration race."""
    with _ytdlp_init_lock:
        return yt_dlp.YoutubeDL(opts)


# Fallback in-memory token cache (token -> payload dict, capped).
_IN_MEMORY_TOKENS: dict[str, dict[str, Any]] = {}
_IN_MEMORY_TOKENS_CAP = 1024

URL_REGEX = re.compile(
    r"https?://(?:www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_+.~#?&/=]*)",
    re.IGNORECASE,
)

PLATFORM_INFO: dict[str, tuple[str, str]] = {
    "youtube": ("یوتیوب", "🎬"),
    "instagram": ("اینستاگرام", "📱"),
    "tiktok": ("تیک‌تاک", "🎵"),
    "twitter": ("توییتر (X)", "🐦"),
    "pinterest": ("پینترست", "📌"),
    "soundcloud": ("ساندکلاد", "🎧"),
    "spotify": ("اسپاتیفای", "🟢"),
    "reddit": ("ردیت", "🤖"),
    "aparat": ("آپارات", "📺"),
    "facebook": ("فیسبوک", "🌐"),
    "other": ("وب‌سایت", "🌐"),
}


def find_cookie_file(platform_key: str | None = None) -> Path | None:
    """Check for platform-specific cookie file."""
    if not platform_key or platform_key in ("spotify", "other"):
        return None
    p = Path(f"/data/cookies/{platform_key}.txt")
    try:
        if p.is_file() and p.stat().st_size > 0:
            return p
    except OSError:
        pass
    return None


@dataclass(frozen=True, slots=True)
class DownloadInfo:
    """Normalized metadata for a media URL."""

    url: str
    platform_key: str
    platform_name: str
    platform_icon: str
    title: str
    duration_seconds: int | None
    duration_str: str
    uploader: str | None
    thumbnail_url: str | None
    is_video: bool
    is_audio_only: bool
    is_gallery: bool
    is_playlist: bool = False
    playlist_count: int = 0
    playlist_items: tuple[dict[str, Any], ...] = ()


def detect_platform(url: str) -> tuple[str, str, str]:
    """Identify the platform from the URL host."""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]

    if any(k in host for k in ("youtube.com", "youtu.be")):
        key = "youtube"
    elif "instagram.com" in host or "instagr.am" in host:
        key = "instagram"
    elif "tiktok.com" in host:
        key = "tiktok"
    elif any(k in host for k in ("twitter.com", "x.com")):
        key = "twitter"
    elif any(k in host for k in ("pinterest.com", "pin.it")):
        key = "pinterest"
    elif "soundcloud.com" in host:
        key = "soundcloud"
    elif "spotify.com" in host or "spotify.link" in host:
        key = "spotify"
    elif "reddit.com" in host or "redd.it" in host:
        key = "reddit"
    elif "aparat.com" in host:
        key = "aparat"
    elif "facebook.com" in host or "fb.watch" in host:
        key = "facebook"
    else:
        key = "other"

    name, icon = PLATFORM_INFO[key]
    return key, name, icon


def extract_first_url(text: str) -> str | None:
    """Find the first HTTP(S) URL in the message text."""
    if not text:
        return None
    match = URL_REGEX.search(text.strip())
    return match.group(0) if match else None


async def store_download_context(token: str, payload: dict[str, Any]) -> None:
    """Save URL metadata under an 8-char short token with a 1-hour TTL."""
    key = f"mewvia:dl:{token}"
    try:
        redis = get_redis()
        await redis.set(key, json.dumps(payload), ex=3600)
    except Exception as exc:
        log.warning("downloader_token_redis_failed", error=str(exc))
        _IN_MEMORY_TOKENS[token] = payload
        while len(_IN_MEMORY_TOKENS) > _IN_MEMORY_TOKENS_CAP:
            _IN_MEMORY_TOKENS.pop(next(iter(_IN_MEMORY_TOKENS)))


async def get_download_context(token: str) -> dict[str, Any] | None:
    """Retrieve download context by short token."""
    key = f"mewvia:dl:{token}"
    try:
        redis = get_redis()
        raw = await redis.get(key)
        if raw:
            data: dict[str, Any] = json.loads(raw)
            return data
    except Exception as exc:
        log.warning("downloader_token_redis_get_failed", error=str(exc))

    return _IN_MEMORY_TOKENS.get(token)


def create_token() -> str:
    """Generate a collision-resistant 8-char hex token for callback_data."""
    return secrets.token_hex(4)


def _build_ytdlp_opts(
    *,
    download: bool = False,
    outtmpl: str | None = None,
    format_str: str | None = None,
    postprocessors: list[dict[str, Any]] | None = None,
    timeout: int = 25,
    max_filesize: int | None = None,
    platform_key: str | None = None,
    cookie_file: str | None = None,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": not download,
        "socket_timeout": timeout,
        "extract_flat": False,
        "nocheckcertificate": True,
        "remote_components": ["ejs:github"],
        "proxy": "socks5h://warp:1080",
        "extractor_args": {
            "youtubepot-bgutilhttp": {
                "base_url": ["http://warp:4416"],
            },
        },
    }

    # Enable browser TLS fingerprint impersonation (bypasses Instagram/Cloudflare bot gating)
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget

        opts["impersonate"] = ImpersonateTarget.from_str("chrome")
    except Exception as exc:
        log.debug("ytdlp_impersonate_setup_failed", error=str(exc))

    if cookie_file:
        opts["cookiefile"] = str(cookie_file)
    else:
        cookie = find_cookie_file(platform_key)
        if cookie:
            opts["cookiefile"] = str(cookie)

    if outtmpl:
        opts["outtmpl"] = outtmpl
    if format_str:
        opts["format"] = format_str
    if postprocessors:
        opts["postprocessors"] = postprocessors
    if max_filesize:
        opts["max_filesize"] = max_filesize

    return opts


def extract_spotify_metadata_sync(url: str) -> dict[str, Any]:
    """Fetch track or playlist/album metadata from Spotify public embeds/oembed."""
    clean_url = url.split("?")[0].rstrip("/")
    parsed = urlparse(clean_url)
    parts = [p for p in parsed.path.split("/") if p]
    kind = parts[0] if parts else "track"
    item_id = parts[1] if len(parts) > 1 else ""

    oembed_url = f"https://open.spotify.com/oembed?url={clean_url}"
    oembed_data: dict[str, Any] = {}
    try:
        import urllib.request

        req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            oembed_data = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        log.warning("spotify_oembed_failed", url=url, error=str(exc))

    title = oembed_data.get("title") or "Spotify Media"
    thumbnail = oembed_data.get("thumbnail_url")

    tracks: list[dict[str, Any]] = []
    artist: str | None = None
    duration_sec: int | None = None

    embed_url = f"https://open.spotify.com/embed/{kind}/{item_id}"
    try:
        import urllib.request

        req = urllib.request.Request(embed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            html_text = r.read().decode("utf-8")
            m = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                html_text,
                re.DOTALL,
            )
            if m:
                d = json.loads(m.group(1))
                entity = (
                    d.get("props", {})
                    .get("pageProps", {})
                    .get("state", {})
                    .get("data", {})
                    .get("entity", {})
                )
                artists_list = entity.get("artists", [])
                if artists_list:
                    artist = ", ".join(a.get("name") for a in artists_list if a.get("name"))

                if kind == "track":
                    dur_ms = entity.get("duration")
                    if dur_ms:
                        duration_sec = int(dur_ms / 1000)
                    track_name = entity.get("name") or title
                    tracks.append(
                        {
                            "title": track_name,
                            "artist": artist,
                            "duration_seconds": duration_sec,
                        }
                    )
                elif kind in ("playlist", "album"):
                    track_list = entity.get("trackList", [])
                    for t in track_list:
                        t_dur = int(t.get("duration", 0) / 1000) if t.get("duration") else None
                        tracks.append(
                            {
                                "title": t.get("title") or "Unknown",
                                "artist": t.get("subtitle") or artist or "Various Artists",
                                "duration_seconds": t_dur,
                            }
                        )
    except Exception as exc:
        log.warning("spotify_embed_parse_failed", url=url, error=str(exc))

    if not tracks and kind == "track":
        tracks.append(
            {
                "title": title,
                "artist": artist,
                "duration_seconds": None,
            }
        )

    return {
        "kind": kind,
        "title": title,
        "artist": artist,
        "thumbnail": thumbnail,
        "duration_seconds": duration_sec,
        "tracks": tracks,
    }


def _download_spotify_track_sync(
    title: str,
    artist: str | None,
    thumbnail_url: str | None,
    work_dir: Path,
    max_bytes: int | None = None,
) -> Path:
    """Download matching audio for a Spotify track in a single pass and embed ID3 cover & tags."""
    query_parts = [artist, title, "audio"] if artist else [title, "audio"]
    query = f"ytsearch1:{' '.join(p for p in query_parts if p)}"

    # Direct 1-pass search and download (cuts latency in half)
    mp3_file = _download_audio_sync(query, work_dir, max_bytes=max_bytes)

    # Embed original Spotify cover and ID3 tags using Mutagen
    try:
        from mutagen.id3 import APIC, ID3, TIT2, TPE1

        tags = ID3(mp3_file)
        tags.add(TIT2(encoding=3, text=title))
        if artist:
            tags.add(TPE1(encoding=3, text=artist))
        if thumbnail_url:
            try:
                import urllib.request

                req = urllib.request.Request(thumbnail_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    thumb_bytes = resp.read()
                tags.add(
                    APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,
                        desc="Cover",
                        data=thumb_bytes,
                    )
                )
            except Exception:
                pass
        tags.save()
    except Exception as exc:
        log.warning("spotify_tagging_failed", error=str(exc))

    return mp3_file


async def download_spotify_track(
    title: str,
    artist: str | None,
    thumbnail_url: str | None,
    work_dir: Path,
    max_bytes: int | None = None,
) -> Path:
    """Async wrapper for downloading and tagging a Spotify track."""
    return await asyncio.to_thread(
        _download_spotify_track_sync, title, artist, thumbnail_url, work_dir, max_bytes
    )


def _probe_info_sync(url: str, cookie_file: str | None = None) -> DownloadInfo:
    """Extract metadata using yt-dlp, falling back to gallery-dl if needed."""
    platform_key, platform_name, platform_icon = detect_platform(url)

    # 1. Spotify special handler
    if platform_key == "spotify":
        sp = extract_spotify_metadata_sync(url)
        kind = sp["kind"]
        title = sp["title"]
        artist = sp["artist"]
        thumb = sp["thumbnail"]
        tracks = sp["tracks"]
        is_pl = kind in ("playlist", "album")
        dur_sec = sp.get("duration_seconds")
        dur_str = human_duration(float(dur_sec)) if dur_sec else "—"

        return DownloadInfo(
            url=url,
            platform_key="spotify",
            platform_name="اسپاتیفای",
            platform_icon="🟢",
            title=title,
            duration_seconds=dur_sec,
            duration_str=dur_str,
            uploader=artist or "Spotify",
            thumbnail_url=thumb,
            is_video=False,
            is_audio_only=True,
            is_gallery=False,
            is_playlist=is_pl,
            playlist_count=len(tracks),
            playlist_items=tuple(tracks),
        )

    # 2. General yt-dlp extraction
    opts = _build_ytdlp_opts(
        download=False,
        timeout=15,
        platform_key=platform_key,
        cookie_file=cookie_file,
    )
    opts["extract_flat"] = "in_playlist"
    info: dict[str, Any] | None = None
    probe_error: str | None = None
    try:
        with _make_ydl(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        probe_error = str(exc)
        log.warning("ytdlp_probe_failed", url=url, error=probe_error[:300])
        # If Instagram photo post raised 'There is no video in this post', extract photo metadata!
        if platform_key == "instagram" and "no video in this post" in probe_error.lower():
            try:
                with _make_ydl(opts) as ydl_photo:
                    ie = ydl_photo.get_info_extractor("Instagram")
                    ie.initialize()
                    ie.raise_no_formats = lambda *a, **kw: None
                    info = ie.extract(url)
            except Exception as photo_exc:
                log.warning("instagram_photo_probe_failed", error=str(photo_exc))

    # 3. Check gallery-dl compatibility if yt-dlp failed or gave no media
    is_gallery = False
    if not info or (info.get("_type") == "playlist" and not info.get("entries")):
        try:
            from gallery_dl import extractor

            if extractor.find(url):
                is_gallery = True
        except Exception:
            pass

    if not info and not is_gallery:
        if probe_error:
            err_lower = probe_error.lower()
            if "sign in to confirm" in err_lower or "bot" in err_lower:
                raise ConversionError(
                    "⚠️ یوتیوب برای مشاهده این ویدیو درخواست تأیید هویت (Bot Verification) داده است."
                )
            if "private" in err_lower:
                raise ConversionError("🔒 این ویدیو خصوصی (Private) است.")
            if "unavailable" in err_lower:
                raise ConversionError("❌ این ویدیو در دسترس نیست یا حذف شده است.")
        raise ConversionError("امکان استخراج اطلاعات از این لینک وجود ندارد.")

    info = info or {}
    raw_desc = (info.get("description") or "").strip()
    raw_title = (info.get("title") or "").strip()

    is_dummy_title = (
        not raw_title
        or raw_title.startswith("Video by ")
        or raw_title.startswith("Post by ")
        or raw_title.startswith("رسانه ")
        or raw_title.lower()
        in ("media", "video", "post", "audio", "instagram post", "instagram video")
    )

    if raw_desc:
        first_line = raw_desc.splitlines()[0].strip()
        title = (first_line[:90] + "…") if len(first_line) > 90 else first_line
    elif not is_dummy_title:
        title = (raw_title[:90] + "…") if len(raw_title) > 90 else raw_title
    else:
        title = ""

    duration = info.get("duration")
    duration_sec = int(duration) if duration and isinstance(duration, (int, float)) else None
    duration_str = human_duration(float(duration_sec)) if duration_sec else "—"

    uploader = info.get("uploader") or info.get("channel") or info.get("creator")
    thumbnail = info.get("thumbnail") or (
        info["thumbnails"][-1]["url"] if info.get("thumbnails") else None
    )

    # Detect playlist in yt-dlp response
    is_pl = (info.get("_type") == "playlist") or bool(info.get("entries"))
    entries = info.get("entries") or []
    pl_items: list[dict[str, Any]] = []
    if is_pl and entries:
        for e in entries:
            if isinstance(e, dict):
                pl_items.append(
                    {
                        "title": (e.get("title") or "Unknown").strip(),
                        "url": e.get("url") or e.get("webpage_url") or "",
                        "duration_seconds": e.get("duration"),
                        "uploader": e.get("uploader") or uploader,
                    }
                )

    formats = info.get("formats", [])
    has_video = any(f.get("vcodec") and f.get("vcodec") != "none" for f in formats)
    is_audio_only = platform_key in ("soundcloud", "spotify") or (not has_video and bool(formats))
    if not has_video and not is_audio_only:
        is_gallery = True

    return DownloadInfo(
        url=url,
        platform_key=platform_key,
        platform_name=platform_name,
        platform_icon=platform_icon,
        title=title,
        duration_seconds=duration_sec,
        duration_str=duration_str,
        uploader=uploader,
        thumbnail_url=thumbnail,
        is_video=has_video and not is_audio_only,
        is_audio_only=is_audio_only,
        is_gallery=is_gallery,
        is_playlist=is_pl,
        playlist_count=len(pl_items) if is_pl else 0,
        playlist_items=tuple(pl_items),
    )


async def probe_media_url(url: str, cookie_file: str | None = None) -> DownloadInfo:
    """Async wrapper for metadata probe."""
    return await asyncio.to_thread(_probe_info_sync, url, cookie_file)


def _download_video_sync(
    url: str,
    work_dir: Path,
    max_height: int | None = None,
    max_bytes: int | None = None,
    cookie_file: str | None = None,
) -> Path:
    outtmpl = str(work_dir / "media.%(ext)s")

    if max_height:
        format_str = (
            f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height<={max_height}][ext=mp4]/"
            f"best[height<={max_height}]/best"
        )
    else:
        format_str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best"

    opts = _build_ytdlp_opts(
        download=True,
        outtmpl=outtmpl,
        format_str=format_str,
        timeout=45,
        max_filesize=max_bytes,
        cookie_file=cookie_file,
    )
    opts["merge_output_format"] = "mp4"

    try:
        with _make_ydl(opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        err_str = str(exc).lower()
        if cookie_file:
            log.warning(
                "video_download_failed_with_cookie_retrying_anonymous",
                url=url,
                error=str(exc),
            )
            opts_no_cookie = dict(opts)
            opts_no_cookie.pop("cookiefile", None)
            try:
                with _make_ydl(opts_no_cookie) as ydl_retry:
                    ydl_retry.download([url])
            except Exception as retry_exc:
                retry_err = str(retry_exc).lower()
                if (
                    "login" in retry_err
                    or "empty media" in retry_err
                    or "redirect to login" in retry_err
                ):
                    raise ConversionError(
                        "⚠️ اینستاگرام برای مشاهده این پست نیاز به لاگین دارد.\n\n"
                        "علت: این پست احتمالاً در یک پیج خصوصی (Private) قرار دارد."
                    ) from retry_exc
                raise ConversionError(f"خطا در دانلود ویدیو: {str(retry_exc)[:150]}") from retry_exc
        else:
            if "login" in err_str or "empty media" in err_str or "redirect to login" in err_str:
                raise ConversionError(
                    "⚠️ اینستاگرام برای مشاهده این پست نیاز به لاگین دارد.\n\n"
                    "علت: این پست احتمالاً در یک پیج خصوصی (Private) قرار دارد."
                ) from None
            if "sign in to confirm" in err_str:
                raise ConversionError(
                    "⚠️ یوتیوب برای این ویدیو درخواست تأیید هویت (Bot Verification) داده است."
                ) from None
            raise

    # Find the output file
    candidates = list(work_dir.glob("media.*"))
    if not candidates:
        # Fallback search for any non-empty file
        candidates = [p for p in work_dir.glob("*") if p.is_file() and p.stat().st_size > 0]

    if not candidates:
        raise ConversionError("دانلود ویدیو ناموفق بود (فایلی ذخیره نشد).")

    # Prefer .mp4 if multiple
    mp4s = [p for p in candidates if p.suffix.lower() == ".mp4"]
    return mp4s[0] if mp4s else candidates[0]


def _download_audio_sync(
    url: str,
    work_dir: Path,
    max_bytes: int | None = None,
    cookie_file: str | None = None,
) -> Path:
    outtmpl = str(work_dir / "audio.%(ext)s")
    postprocessors = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }
    ]

    opts = _build_ytdlp_opts(
        download=True,
        outtmpl=outtmpl,
        format_str="140/bestaudio[ext=m4a]/bestaudio/best",
        postprocessors=postprocessors,
        timeout=45,
        max_filesize=max_bytes,
        cookie_file=cookie_file,
    )

    try:
        with _make_ydl(opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        err_str = str(exc).lower()
        if "login" in err_str or "empty media" in err_str or "redirect to login" in err_str:
            raise ConversionError(
                "⚠️ اینستاگرام برای مشاهده این پست نیاز به لاگین یا کوکی دارد.\n\n"
                "علت: این پست ممکن است خصوصی باشد، یا اینستاگرام برای آی‌پی سرور محدودیت گذاشته است."
            ) from None
        if "sign in to confirm" in err_str:
            raise ConversionError(
                "⚠️ یوتیوب برای این ویدیو درخواست تأیید هویت (Bot Verification) داده است."
            ) from None
        raise

    candidates = list(work_dir.glob("audio.mp3"))
    if not candidates:
        candidates = [p for p in work_dir.glob("*") if p.is_file() and p.stat().st_size > 0]

    if not candidates:
        raise ConversionError("استخراج صوت ناموفق بود.")

    return candidates[0]


def _download_gallery_sync(
    url: str,
    work_dir: Path,
    cookie_file: str | None = None,
) -> list[Path]:
    """Download images/galleries using gallery-dl CLI."""
    dest_dir = work_dir / "gallery"
    dest_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python",
        "-m",
        "gallery_dl",
        "--proxy",
        "socks5h://warp:1080",
        "--dest",
        str(dest_dir),
        "--no-mtime",
    ]

    platform_key, _, _ = detect_platform(url)
    cookie = Path(cookie_file) if cookie_file else find_cookie_file(platform_key)
    if cookie and cookie.is_file() and cookie.stat().st_size > 0:
        cmd.extend(["--cookies", str(cookie)])

    if platform_key == "instagram":
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        cmd.extend(
            [
                "-o",
                f"extractor.headers.User-Agent={ua}",
            ]
        )

    cmd.append(url)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ConversionError("دانلود گالری به دلیل اتمام زمان متوقف شد.") from None

    # Collect downloaded image files
    results = [
        p
        for p in dest_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".mp4")
    ]

    if not results:
        err = proc.stderr.decode(errors="replace").strip()
        log.warning("gallery_dl_failed", error=err[:300])

        # Modern 2026 fallback: download Instagram photos via TLS-impersonated yt-dlp CDN thumbnails
        if platform_key == "instagram":
            log.info("trying_instagram_photo_fallback_via_ytdlp", url=url)
            fallback_photos = _download_instagram_photos_via_ytdlp(url, dest_dir)
            if fallback_photos:
                return sorted(fallback_photos)

        err_lower = err.lower()
        if "login" in err_lower or "redirect to login" in err_lower:
            raise ConversionError(
                "⚠️ اینستاگرام برای مشاهده این پست نیاز به لاگین دارد.\n\n"
                "علت: این پست ممکن است در یک پیج خصوصی باشد."
            )
        raise ConversionError("فایلی در گالری یافت نشد یا دسترسی به آن محدود است.")

    return sorted(results)


def _download_instagram_photos_via_ytdlp(url: str, dest_dir: Path) -> list[Path]:
    """Extract and download Instagram images via Chrome TLS-impersonated yt-dlp CDN URLs."""
    import requests
    from yt_dlp.networking.impersonate import ImpersonateTarget

    opts: dict[str, Any] = {
        "quiet": True,
        "proxy": "socks5h://warp:1080",
        "impersonate": ImpersonateTarget.from_str("chrome"),
    }
    try:
        with _make_ydl(opts) as ydl:
            ie = ydl.get_info_extractor("Instagram")
            ie.initialize()
            ie.raise_no_formats = lambda *args, **kwargs: None
            info = ie.extract(url)
        entries = info.get("entries") or [info]
        results: list[Path] = []
        for idx, e in enumerate(entries, 1):
            thumbs = e.get("thumbnails") or []
            if thumbs:
                img_url = thumbs[-1]["url"]
                resp = requests.get(img_url, proxies={"https": "socks5h://warp:1080"}, timeout=15)
                if resp.status_code == 200 and resp.content:
                    out = dest_dir / f"instagram_photo_{idx}.jpg"
                    out.write_bytes(resp.content)
                    results.append(out)
        return results
    except Exception as exc:
        log.warning("instagram_photo_fallback_failed", error=str(exc))
        return []


async def download_video(
    url: str,
    work_dir: Path,
    max_height: int | None = None,
    max_bytes: int | None = None,
    cookie_file: str | None = None,
) -> Path:
    """Async wrapper for video downloading."""
    return await asyncio.to_thread(
        _download_video_sync, url, work_dir, max_height, max_bytes, cookie_file
    )


async def download_audio(
    url: str,
    work_dir: Path,
    max_bytes: int | None = None,
    cookie_file: str | None = None,
) -> Path:
    """Async wrapper for audio downloading."""
    return await asyncio.to_thread(_download_audio_sync, url, work_dir, max_bytes, cookie_file)


async def download_gallery(
    url: str,
    work_dir: Path,
    cookie_file: str | None = None,
) -> list[Path]:
    """Async wrapper for gallery downloading."""
    return await asyncio.to_thread(_download_gallery_sync, url, work_dir, cookie_file)
