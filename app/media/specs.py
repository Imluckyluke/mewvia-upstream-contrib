"""Target specifications, format limits, and conversion matrix.

Single source of truth for output formats, allowed input-output pairs,
and Telegram's exact sticker format limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.media.kinds import MediaKind
from app.ui.styles import Style


@dataclass(frozen=True, slots=True)
class TargetLimits:
    max_bytes: int | None = None
    max_duration: float | None = None
    max_fps: float | None = None
    exact_dimension: int | None = None  # e.g. 512 for standard sticker, 100 for custom emoji
    max_dimension: int | None = None
    no_audio: bool = False
    format_type: Literal["static", "video", "gif", "image", "audio"] = "static"


@dataclass(frozen=True, slots=True)
class TargetSpec:
    id: str
    label_key: str
    emoji_slot: str
    style: Style
    extension: str
    mime_type: str
    accepts: frozenset[MediaKind]
    limits: TargetLimits | None = None


# Telegram official limits:
# Static Sticker: WEBP or PNG, exactly 512px on one side, other <= 512px, <= 512KB
LIMITS_STATIC_STICKER = TargetLimits(
    max_bytes=512 * 1024,
    exact_dimension=512,
    format_type="static",
)

# Video Sticker: WEBM VP9, 512px box, <= 256KB, <= 3.0s, <= 30 FPS, no audio
LIMITS_VIDEO_STICKER = TargetLimits(
    max_bytes=256 * 1024,
    max_duration=3.0,
    max_fps=30.0,
    exact_dimension=512,
    no_audio=True,
    format_type="video",
)

# Custom Emoji Sticker: WEBM or WEBP, exactly 100x100, <= 64KB
LIMITS_CUSTOM_EMOJI = TargetLimits(
    max_bytes=64 * 1024,
    exact_dimension=100,
    format_type="video",
)

NON_AUDIO_KINDS = frozenset(k for k in MediaKind if k not in (MediaKind.AUDIO, MediaKind.VOICE))

# Defined targets
TARGET_PNG = TargetSpec(
    id="png",
    label_key="target.png",
    emoji_slot="photo",
    style=Style.PRIMARY,
    extension="png",
    mime_type="image/png",
    accepts=NON_AUDIO_KINDS,
    limits=None,
)

TARGET_WEBP = TargetSpec(
    id="webp",
    label_key="target.webp",
    emoji_slot="sticker",
    style=Style.PRIMARY,
    extension="webp",
    mime_type="image/webp",
    accepts=NON_AUDIO_KINDS,
    limits=LIMITS_STATIC_STICKER,
)

TARGET_GIF = TargetSpec(
    id="gif",
    label_key="target.gif",
    emoji_slot="gif",
    style=Style.CONFIRM,
    extension="mp4",  # Telegram treats looping silent MP4 as animation/GIF
    mime_type="video/mp4",
    accepts=frozenset(
        {
            MediaKind.PHOTO,
            MediaKind.DOCUMENT_IMAGE,
            MediaKind.STICKER_STATIC,
            MediaKind.STICKER_ANIMATED,
            MediaKind.STICKER_VIDEO,
            MediaKind.ANIMATION,
            MediaKind.VIDEO,
            MediaKind.VIDEO_NOTE,
            MediaKind.DOCUMENT_VIDEO,
        }
    ),
    limits=None,
)

TARGET_WEBM_STICKER = TargetSpec(
    id="webm_sticker",
    label_key="target.webm_sticker",
    emoji_slot="sticker",
    style=Style.CONFIRM,
    extension="webm",
    mime_type="video/webm",
    accepts=frozenset(
        {
            MediaKind.STICKER_ANIMATED,
            MediaKind.STICKER_VIDEO,
            MediaKind.ANIMATION,
            MediaKind.VIDEO,
            MediaKind.VIDEO_NOTE,
            MediaKind.PHOTO,
            MediaKind.DOCUMENT_IMAGE,
            MediaKind.DOCUMENT_VIDEO,
        }
    ),
    limits=LIMITS_VIDEO_STICKER,
)

TARGET_MP4 = TargetSpec(
    id="mp4",
    label_key="target.mp4",
    emoji_slot="video",
    style=Style.PRIMARY,
    extension="mp4",
    mime_type="video/mp4",
    accepts=frozenset(
        {
            MediaKind.STICKER_ANIMATED,
            MediaKind.STICKER_VIDEO,
            MediaKind.ANIMATION,
            MediaKind.VIDEO,
            MediaKind.VIDEO_NOTE,
            MediaKind.DOCUMENT_VIDEO,
        }
    ),
    limits=None,
)

TARGETS: list[TargetSpec] = [
    TARGET_WEBP,
    TARGET_WEBM_STICKER,
    TARGET_PNG,
    TARGET_GIF,
    TARGET_MP4,
]

_TARGET_MAP = {t.id: t for t in TARGETS}


def get_target(target_id: str) -> TargetSpec | None:
    return _TARGET_MAP.get(target_id)


def allowed_targets_for(kind: MediaKind) -> list[TargetSpec]:
    """Return all target formats compatible with the given input media kind."""
    return [t for t in TARGETS if kind in t.accepts]
