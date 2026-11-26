"""Group game lobby state.

Lobbies live in Redis (with a process-local fallback) and expire after an
hour. A lobby holds the host, the players who joined, and the live board once
the host starts the match. ``game_type`` makes room for future games.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.cache.fallback import FallbackCache
from app.cache.redis import get_redis
from app.core.logging import get_logger

log = get_logger(__name__)

_TTL = 3600
_CAP = 512
_fallback = FallbackCache(_CAP, _TTL)


def key_for(chat_id: int) -> str:
    return f"mewvia:ggame:{chat_id}"


async def load(chat_id: int) -> dict[str, Any] | None:
    key = key_for(chat_id)
    try:
        raw = await get_redis().get(key)
        if raw:
            data: dict[str, Any] = json.loads(raw)
            return data
    except Exception as exc:
        log.warning("group_game_session_redis_get_failed", error=str(exc))
    return _fallback.get(key)


async def save(chat_id: int, state: dict[str, Any]) -> None:
    key = key_for(chat_id)
    state["updated_at"] = time.time()
    try:
        await get_redis().set(key, json.dumps(state), ex=_TTL)
        _fallback.discard(key)
    except Exception as exc:
        log.warning("group_game_session_redis_set_failed", error=str(exc))
        _fallback.put(key, state)


async def drop(chat_id: int) -> None:
    key = key_for(chat_id)
    _fallback.discard(key)
    try:
        await get_redis().delete(key)
    except Exception as exc:
        log.warning("group_game_session_redis_delete_failed", error=str(exc))
