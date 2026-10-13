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
    "reddit": ("ردیت", "🤖"),
    "aparat": ("آپارات", "📺"),
    "facebook": ("فیسبوک", "🌐"),
    "other": ("وب‌سایت", "🌐"),
}


def find_cookie_file(platform_key: str | None = None) -> Path | None:
    """Check for platform-specific or global cookie file."""
    candidates = []
    if platform_key:
        candidates.append(Path(f"/data/cookies/{platform_key}.txt"))
    candidates.extend(
        [
            Path("/data/cookies.txt"),
            Path("/data/cookie.txt"),
            Path("/data/cookies/cookies.txt"),
        ]
    )
    for p in candidates:
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p
        except OSError:
            continue
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
                "base_url": ["http://bgutil-provider:4416"],
            },
        },
    }

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


def _probe_info_sync(url: str, cookie_file: str | None = None) -> DownloadInfo:
    """Extract metadata using yt-dlp, falling back to gallery-dl if needed."""
    platform_key, platform_name, platform_icon = detect_platform(url)

    # 1. Try yt-dlp first
    opts = _build_ytdlp_opts(
        download=False,
        timeout=15,
        platform_key=platform_key,
        cookie_file=cookie_file,
    )
    info: dict[str, Any] | None = None
    probe_error: str | None = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        probe_error = str(exc)
        log.warning("ytdlp_probe_failed", url=url, error=probe_error[:300])

    # 2. Check gallery-dl compatibility if yt-dlp failed or gave no media
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
    title = (info.get("title") or info.get("description") or f"رسانه {platform_name}").strip()
    # Clean up very long titles
    if len(title) > 90:
        title = title[:87] + "…"

    duration = info.get("duration")
    duration_sec = int(duration) if duration and isinstance(duration, (int, float)) else None
    duration_str = human_duration(float(duration_sec)) if duration_sec else "—"

    uploader = info.get("uploader") or info.get("channel") or info.get("creator")
    thumbnail = info.get("thumbnail")

    formats = info.get("formats", [])
    has_video = any(f.get("vcodec") and f.get("vcodec") != "none" for f in formats)
    is_audio_only = platform_key == "soundcloud" or (not has_video and bool(formats))

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
        with yt_dlp.YoutubeDL(opts) as ydl:
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
        format_str="bestaudio/best",
        postprocessors=postprocessors,
        timeout=45,
        max_filesize=max_bytes,
        cookie_file=cookie_file,
    )

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
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
        err_lower = err.lower()
        if "login" in err_lower or "redirect to login" in err_lower:
            raise ConversionError(
                "⚠️ اینستاگرام برای مشاهده این پست نیاز به لاگین یا کوکی دارد.\n\n"
                "علت: این پست ممکن است خصوصی باشد، یا اینستاگرام برای سرور دسترسی را بسته است."
            )
        raise ConversionError("فایلی در گالری یافت نشد یا دسترسی به آن محدود است.")

    return sorted(results)


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
