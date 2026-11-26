"""Premium emoji rendering, with automatic degradation.

Two ways a premium emoji reaches the user:

* in text — an HTML ``<tg-emoji emoji-id="...">fallback</tg-emoji>`` tag;
* on a button — the ``icon_custom_emoji_id`` field.

Both are only allowed when the bot owner holds Telegram Premium (or the bot has a
Fragment username), and neither works in channels. So every call goes through
this registry, which:

1. reads the slot table (cached in Redis, ~60s);
2. returns the unicode fallback when premium is off, unset, or unavailable;
3. flips the global switch off and warns the admin the first time Telegram
   rejects a custom emoji, so a lapsed subscription degrades the UI instead of
   breaking every message.
"""

from __future__ import annotations

import json

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.db.repo import EmojiRepo
from app.db.session import session_scope

log = get_logger(__name__)

CACHE_KEY = "mewvia:emoji_slots"
CACHE_TTL = 60

PREMIUM_FLAG = "premium_emoji_enabled"

#: Shown when a slot name does not exist at all — visible, not silent.
MISSING = "❓"

#: Substrings Telegram uses when it refuses a custom emoji. Matched case-insensitively.
#:
#: `DOCUMENT_INVALID` is in the list because that is what the live API actually
#: answers for an unusable custom_emoji_id — verified against api.telegram.org,
#: not guessed. It is safe to treat as a premium-emoji failure because the guard
#: only retries when the request in fact carried premium emoji; a genuinely
#: invalid document produces no demoted payload and the error surfaces unchanged.
REJECTION_MARKERS = (
    "custom emoji",
    "custom_emoji",
    "CUSTOM_EMOJI_INVALID",
    "DOCUMENT_INVALID",
    "not enough rights to send custom emoji",
)


def looks_like_custom_emoji_rejection(message: str) -> bool:
    lowered = message.lower()
    return any(marker.lower() in lowered for marker in REJECTION_MARKERS)


class EmojiRegistry:
    """slot name -> premium id + unicode fallback."""

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis
        self._local: dict[str, tuple[str | None, str]] = {}
        self._premium_enabled = True

    # ---- loading ----

    async def _load_from_db(self) -> dict[str, tuple[str | None, str]]:
        async with session_scope() as session:
            return await EmojiRepo(session).as_mapping()

    async def load(self) -> dict[str, tuple[str | None, str]]:
        """Slot mapping, from cache when possible."""
        if self._redis is not None:
            try:
                cached = await self._redis.get(CACHE_KEY)
                if cached:
                    raw: dict[str, list[str | None]] = json.loads(cached)
                    self._local = {k: (v[0], str(v[1])) for k, v in raw.items()}
                    return self._local
            except (RedisError, ValueError, IndexError, TypeError) as exc:
                log.warning("emoji_cache_read_failed", error=str(exc))

        mapping = await self._load_from_db()
        self._local = mapping

        if self._redis is not None:
            try:
                payload = {k: [cid, fb] for k, (cid, fb) in mapping.items()}
                await self._redis.set(CACHE_KEY, json.dumps(payload), ex=CACHE_TTL)
            except RedisError as exc:
                log.warning("emoji_cache_write_failed", error=str(exc))

        return mapping

    async def invalidate(self) -> None:
        self._local = {}
        if self._redis is None:
            return
        try:
            await self._redis.delete(CACHE_KEY)
        except RedisError as exc:
            log.warning("emoji_cache_invalidate_failed", error=str(exc))

    # ---- premium switch ----

    async def refresh_premium_flag(self) -> bool:
        """Re-read the runtime switch. Called once per screen render, not per button."""
        from app.core.settings_store import get_settings_store

        self._premium_enabled = await get_settings_store().get_bool(PREMIUM_FLAG, True)
        return self._premium_enabled

    @property
    def premium_enabled(self) -> bool:
        return self._premium_enabled

    async def disable_premium(self, reason: str) -> None:
        """Turn premium emoji off globally after Telegram refused one."""
        from app.core.settings_store import get_settings_store

        self._premium_enabled = False
        await get_settings_store().set_bool(PREMIUM_FLAG, False)
        log.warning("premium_emoji_disabled", reason=reason)

    # ---- rendering ----

    def fallback(self, slot: str) -> str:
        """Unicode emoji for a slot. Never raises."""
        entry = self._local.get(slot)
        return entry[1] if entry else MISSING

    def fallback_for_id(self, custom_emoji_id: str) -> str | None:
        """Reverse lookup: premium id -> unicode fallback.

        The premium-emoji guard needs this. When Telegram refuses a request it
        only sees the id on the button, not the slot name, and an icon that
        vanishes on the retry looks like a bug — with this it degrades to the same
        unicode character every other screen would have shown.
        """
        for cid, fallback in self._local.values():
            if cid == custom_emoji_id:
                return fallback
        return None

    def custom_id(self, slot: str) -> str | None:
        """Premium id for a slot, or None when premium is unavailable/unset."""
        if not self._premium_enabled:
            return None
        entry = self._local.get(slot)
        return entry[0] if entry else None

    def text(self, slot: str) -> str:
        """Emoji for use inside a message body.

        Returns a ``<tg-emoji>`` tag when a premium id is available, otherwise the
        plain unicode character. The fallback is kept *inside* the tag so clients
        that cannot render the custom emoji still show something sensible.
        """
        fallback = self.fallback(slot)
        custom = self.custom_id(slot)
        if custom is None:
            return fallback
        return f'<tg-emoji emoji-id="{custom}">{fallback}</tg-emoji>'

    def label(self, slot: str, text: str) -> str:
        """`emoji text` for a message line."""
        return f"{self.text(slot)} {text}"


_registry: EmojiRegistry | None = None


def init_emoji_registry(redis: Redis | None) -> EmojiRegistry:
    global _registry
    _registry = EmojiRegistry(redis)
    return _registry


def get_emoji_registry() -> EmojiRegistry:
    if _registry is None:
        raise RuntimeError("init_emoji_registry() must be called during startup")
    return _registry


def em(slot: str) -> str:
    """Shortcut for ``get_emoji_registry().text(slot)`` in UI strings.

    The registry is loaded at bot startup, so this stays safe in handlers and
    renderers alike. Admins can change any slot live from ``/ui``.
    """
    return get_emoji_registry().text(slot)
