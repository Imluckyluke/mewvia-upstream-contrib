"""Loading GIFs repository and pool manager.

Stores a configurable list of Telegram animation file_ids or URLs in the runtime
settings store. Used by ProgressController to display visual animated progress
bars during media download and processing operations.
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings_store import get_settings_store
from app.db.repo import SettingRepo

log = get_logger(__name__)

SETTING_KEY_GIFS = "loading_gifs"
SETTING_KEY_ENABLED = "loading_gifs_enabled"


async def list_loading_gifs(session: AsyncSession | None = None) -> list[dict[str, Any]]:
    """Return all configured loading GIF items."""
    try:
        raw: str | None = None
        if session is not None:
            raw = await SettingRepo(session).get(SETTING_KEY_GIFS)
        if raw is None:
            try:
                raw = await get_settings_store().get(SETTING_KEY_GIFS, "[]")
            except RuntimeError:
                raw = "[]"
        if not raw:
            return []
        items = json.loads(raw)
        if isinstance(items, list):
            return items
    except Exception as exc:
        log.warning("list_loading_gifs_failed", error=str(exc))
    return []


async def is_loading_gifs_enabled(session: AsyncSession | None = None) -> bool:
    """Return whether loading GIFs should be displayed."""
    try:
        if session is not None:
            raw = await SettingRepo(session).get(SETTING_KEY_ENABLED)
            return raw != "false" if raw is not None else True
        try:
            return await get_settings_store().get_bool(SETTING_KEY_ENABLED, default=True)
        except RuntimeError:
            return True
    except Exception:
        return True


async def set_loading_gifs_enabled(enabled: bool, session: AsyncSession | None = None) -> None:
    """Toggle loading GIFs display globally."""
    val = "true" if enabled else "false"
    if session is not None:
        await SettingRepo(session).set(SETTING_KEY_ENABLED, val)
        try:
            await get_settings_store().invalidate()
        except RuntimeError:
            pass
    else:
        try:
            await get_settings_store().set(SETTING_KEY_ENABLED, val)
        except RuntimeError:
            pass


async def add_loading_gif(
    file_id: str,
    label: str = "",
    file_unique_id: str = "",
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """Add a new animation file_id or URL to the loading GIF pool."""
    items = await list_loading_gifs(session)
    gif_id = f"gif_{uuid.uuid4().hex[:6]}"
    if not label:
        label = f"گیف شماره {len(items) + 1}"

    now_str = datetime.now(UTC).strftime("%Y/%m/%d %H:%M")
    new_item = {
        "id": gif_id,
        "file_id": file_id.strip(),
        "file_unique_id": file_unique_id.strip(),
        "label": label.strip(),
        "created_at": now_str,
    }
    items.append(new_item)
    payload = json.dumps(items, ensure_ascii=False)

    if session is not None:
        await SettingRepo(session).set(SETTING_KEY_GIFS, payload)
        try:
            await get_settings_store().invalidate()
        except RuntimeError:
            pass
    else:
        try:
            await get_settings_store().set(SETTING_KEY_GIFS, payload)
        except RuntimeError:
            pass

    log.info("loading_gif_added", gif_id=gif_id, label=label)
    return new_item


async def delete_loading_gif(gif_id: str, session: AsyncSession | None = None) -> bool:
    """Delete a loading GIF item by ID."""
    items = await list_loading_gifs(session)
    initial_len = len(items)
    items = [item for item in items if item.get("id") != gif_id]
    if len(items) == initial_len:
        return False

    payload = json.dumps(items, ensure_ascii=False)
    if session is not None:
        await SettingRepo(session).set(SETTING_KEY_GIFS, payload)
        try:
            await get_settings_store().invalidate()
        except RuntimeError:
            pass
    else:
        try:
            await get_settings_store().set(SETTING_KEY_GIFS, payload)
        except RuntimeError:
            pass

    log.info("loading_gif_deleted", gif_id=gif_id)
    return True


async def get_random_loading_gif(session: AsyncSession | None = None) -> str | None:
    """Pick one random loading GIF file_id from the active pool if enabled."""
    enabled = await is_loading_gifs_enabled(session)
    if not enabled:
        return None

    items = await list_loading_gifs(session)
    if not items:
        return None

    choice = random.choice(items)
    return choice.get("file_id")
