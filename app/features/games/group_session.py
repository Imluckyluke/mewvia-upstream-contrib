"""Group game lobby state.

Matches live in Redis (with a process-local fallback) and expire after an hour
of inactivity. Each group has one active match.
"""

from __future__ import annotations

import asyncio
import json
import time
from copy import deepcopy
from typing import Any
from weakref import WeakValueDictionary

from app.cache.fallback import FallbackCache
from app.cache.redis import get_redis
from app.core.logging import get_logger

log = get_logger(__name__)

_TTL = 3600
_CAP = 512
_fallback = FallbackCache(_CAP, _TTL)
_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


def lock(chat_id: int) -> asyncio.Lock:
    """Serialize a group's updates in the single polling process."""
    return _locks.setdefault(chat_id, asyncio.Lock())


def key_for(chat_id: int) -> str:
    return f"mewvia:ggame:{chat_id}"


async def load(chat_id: int) -> dict[str, Any] | None:
    key = key_for(chat_id)
    try:
        raw = await get_redis().get(key)
        if raw:
            data: dict[str, Any] = json.loads(raw)
            cached = _fallback.get(key)
            if cached and cached["updated_at"] > data.get("updated_at", 0):
                return deepcopy(cached)
            return data
    except Exception as exc:
        log.warning("group_game_session_redis_get_failed", error=str(exc))
    return deepcopy(_fallback.get(key))


async def save(chat_id: int, state: dict[str, Any]) -> None:
    key = key_for(chat_id)
    state["updated_at"] = time.time()
    _fallback.put(key, deepcopy(state))
    try:
        await get_redis().set(key, json.dumps(state), ex=_TTL)
    except Exception as exc:
        log.warning("group_game_session_redis_set_failed", error=str(exc))


async def drop(chat_id: int) -> None:
    key = key_for(chat_id)
    _fallback.discard(key)
    try:
        await get_redis().delete(key)
    except Exception as exc:
        log.warning("group_game_session_redis_delete_failed", error=str(exc))
