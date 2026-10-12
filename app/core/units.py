"""Human-readable formatting helpers shared across features."""

from __future__ import annotations

_UNITS = ("B", "KB", "MB", "GB", "TB")

# Persian-Indic digits, used when echoing numbers inside Persian sentences.
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def human_bytes(size: float, /, precision: int = 1) -> str:
    """1536 -> '1.5 KB'. Uses 1024 steps."""
    value = float(size)
    for unit in _UNITS:
        if abs(value) < 1024.0 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.{precision}f} {unit}"
        value /= 1024.0
    return f"{value:.{precision}f} {_UNITS[-1]}"  # unreachable, keeps mypy happy


def human_duration(seconds: float, /) -> str:
    """95.4 -> '1:35'.  3725 -> '1:02:05'."""
    total = round(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def fa_digits(text: str, /) -> str:
    """Convert ASCII digits to Persian-Indic digits for display."""
    return text.translate(_FA_DIGITS)


def fa_to_en_digits(text: str, /) -> str:
    """Convert Persian/Arabic-Indic digits to ASCII (for numeric parsing)."""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0x06F0 <= code <= 0x06F9:  # Persian digits
            out.append(str(code - 0x06F0))
        elif 0x0660 <= code <= 0x0669:  # Arabic digits
            out.append(str(code - 0x0660))
        else:
            out.append(ch)
    return "".join(out)
