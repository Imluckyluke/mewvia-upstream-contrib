"""Typed error hierarchy.

Every error carries a stable `code`. Handlers map the code to a user-facing
Persian message via app.ui.texts, so wording changes never touch logic.
"""

from __future__ import annotations


class MewviaError(Exception):
    """Base class for everything this application raises on purpose."""

    code = "unknown"

    def __init__(self, message: str = "", **context: object) -> None:
        super().__init__(message or self.code)
        self.context = context


# ---- media engine ----


class MediaError(MewviaError):
    code = "media_error"


class UnsupportedMediaError(MediaError):
    code = "unsupported_media"


class ProbeError(MediaError):
    code = "probe_failed"


class ConversionError(MediaError):
    code = "conversion_failed"


class ConversionTimeoutError(MediaError):
    code = "conversion_timeout"


class SizeLimitError(MediaError):
    """Output could not be squeezed under the Telegram limit."""

    code = "size_limit"


# ---- input validation ----


class FileTooLargeError(MewviaError):
    code = "file_too_large"


class DurationTooLongError(MewviaError):
    code = "duration_too_long"


# ---- flow control ----


class ThrottledError(MewviaError):
    code = "throttled"


class MaintenanceError(MewviaError):
    code = "maintenance"
