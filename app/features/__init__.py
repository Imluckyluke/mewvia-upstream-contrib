"""Feature packages.

`load_features()` imports every feature module so their `register()` calls run.
This is the single place to add a new capability.
"""

from __future__ import annotations

from app.features.registry import Feature, all_features, menu_features, register

__all__ = ["Feature", "all_features", "load_features", "menu_features", "register"]

_loaded = False


def load_features() -> list[Feature]:
    """Import feature modules once, then return the registry contents."""
    global _loaded
    if not _loaded:
        # Import for side effects: each module calls registry.register().
        # `fallback` must be imported too — its high order puts it last.
        from app.admin import router as admin_router  # noqa: F401
        from app.features import (  # noqa: F401
            audio,
            basic,
            batch,
            convert,
            downloader,
            fallback,
            filters,
            group_media,
            music_search,
            sponsor_leavewatch,
            stickerpack,
            tag_editor,
            textoverlay,
            uidemo,
            videotools,
            wide,
        )

        _loaded = True
    return all_features()
