"""Typed, cached access to the runtime settings table.

Handlers read switches on nearly every update, so values are cached in Redis
with a short TTL. Writes invalidate the cache immediately, which makes an admin
toggle feel instant while keeping DB reads off the hot path.

Falls back to reading the database directly if Redis is unavailable — a cache
outage must not take the bot down.
"""

from __future__ import annotations

import json

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.db.repo import SettingRepo
from app.db.session import session_scope

log = get_logger(__name__)

CACHE_KEY = "mewvia:settings"
CACHE_TTL = 60

_TRUE = frozenset({"1", "true", "yes", "on"})


class SettingsStore:
    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    async def _load_from_db(self) -> dict[str, str]:
        async with session_scope() as session:
            return await SettingRepo(session).as_dict()

    async def all(self) -> dict[str, str]:
        if self._redis is not None:
            try:
                cached = await self._redis.get(CACHE_KEY)
                if cached:
                    parsed: dict[str, str] = json.loads(cached)
                    return parsed
            except (RedisError, ValueError) as exc:
                log.warning("settings_cache_read_failed", error=str(exc))

        values = await self._load_from_db()

        if self._redis is not None:
            try:
                await self._redis.set(CACHE_KEY, json.dumps(values), ex=CACHE_TTL)
            except RedisError as exc:
                log.warning("settings_cache_write_failed", error=str(exc))

        return values

    async def invalidate(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.delete(CACHE_KEY)
        except RedisError as exc:
            log.warning("settings_cache_invalidate_failed", error=str(exc))

    # ---- typed getters ----

    async def get(self, key: str, default: str = "") -> str:
        return (await self.all()).get(key, default)

    async def get_bool(self, key: str, default: bool = False) -> bool:
        raw = (await self.all()).get(key)
        if raw is None or raw == "":
            return default
        return raw.strip().lower() in _TRUE

    async def get_int(self, key: str, default: int = 0) -> int:
        raw = (await self.all()).get(key)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            log.warning("setting_not_an_int", key=key, value=raw)
            return default

    # ---- writes ----

    async def set(self, key: str, value: str, *, updated_by: int | None = None) -> None:
        async with session_scope() as session:
            await SettingRepo(session).set(key, value, updated_by=updated_by)
        await self.invalidate()

    async def set_bool(self, key: str, value: bool, *, updated_by: int | None = None) -> None:
        await self.set(key, "true" if value else "false", updated_by=updated_by)

    async def toggle(self, key: str, *, updated_by: int | None = None) -> bool:
        """Flip a boolean switch and return the new value."""
        new_value = not await self.get_bool(key)
        await self.set_bool(key, new_value, updated_by=updated_by)
        return new_value


_store: SettingsStore | None = None


def init_settings_store(redis: Redis | None) -> SettingsStore:
    global _store
    _store = SettingsStore(redis)
    return _store


def get_settings_store() -> SettingsStore:
    if _store is None:
        raise RuntimeError("init_settings_store() must be called during startup")
    return _store
