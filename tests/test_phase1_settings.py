"""Phase-1 acceptance tests, part 2: settings, emoji slots, seed, cache,
middlewares, admin filter.
"""

from __future__ import annotations

import pytest
from app.config import Settings
from app.db.models import Setting
from app.db.repo import EmojiRepo, SettingRepo
from app.db.seed import EMOJI_SLOTS, SETTINGS, apply_seed
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_phase1_data import TEST_ID

# ---------------------------------------------------------------------------
# SettingRepo
# ---------------------------------------------------------------------------


async def test_setting_set_then_get_roundtrip(session: AsyncSession) -> None:
    repo = SettingRepo(session)
    await repo.set("test_key", "value-1", updated_by=TEST_ID)
    await session.flush()
    assert await repo.get("test_key") == "value-1"

    await repo.set("test_key", "value-2", updated_by=TEST_ID)
    await session.flush()
    assert await repo.get("test_key") == "value-2", "second set must update, not fail"


async def test_setting_get_unknown_key_is_none(session: AsyncSession) -> None:
    assert await SettingRepo(session).get("definitely_not_a_key") is None


async def test_setting_ensure_does_not_clobber_an_existing_value(
    session: AsyncSession,
) -> None:
    """This is what makes running the seed on every boot safe."""
    repo = SettingRepo(session)
    await repo.set("ensure_probe", "admin-chose-this")
    await session.flush()

    returned = await repo.ensure("ensure_probe", "code-default", "desc")
    await session.flush()

    assert returned == "admin-chose-this"
    assert await repo.get("ensure_probe") == "admin-chose-this"


async def test_setting_ensure_inserts_when_absent(session: AsyncSession) -> None:
    repo = SettingRepo(session)
    returned = await repo.ensure("fresh_probe_key", "the-default", "desc")
    await session.flush()
    assert returned == "the-default"
    assert await repo.get("fresh_probe_key") == "the-default"


async def test_setting_set_records_description_and_author(session: AsyncSession) -> None:
    repo = SettingRepo(session)
    await repo.set("audit_probe", "v", updated_by=42, description="why")
    await session.flush()
    row = (await session.execute(select(Setting).where(Setting.key == "audit_probe"))).scalar_one()
    assert row.updated_by == 42
    assert row.description == "why"


async def test_setting_as_dict_includes_written_keys(session: AsyncSession) -> None:
    repo = SettingRepo(session)
    await repo.set("dict_probe", "yes")
    await session.flush()
    assert (await repo.as_dict())["dict_probe"] == "yes"


# ---------------------------------------------------------------------------
# EmojiRepo
# ---------------------------------------------------------------------------


async def test_emoji_slots_are_seeded_and_ordered(session: AsyncSession) -> None:
    slots = await EmojiRepo(session).all()
    assert len(slots) >= len(EMOJI_SLOTS)

    orders = [(s.sort_order, s.slot) for s in slots]
    assert orders == sorted(orders), "all() must return panel order"


async def test_every_declared_slot_exists_with_a_fallback(session: AsyncSession) -> None:
    """A slot without a fallback would break the UI when premium is off."""
    mapping = await EmojiRepo(session).as_mapping()
    for declared in EMOJI_SLOTS:
        assert declared.slot in mapping, f"slot {declared.slot} missing from database"
        _, fallback = mapping[declared.slot]
        assert fallback, f"slot {declared.slot} has no unicode fallback"


async def test_set_and_clear_custom_emoji(session: AsyncSession) -> None:
    repo = EmojiRepo(session)

    assert await repo.set_custom_emoji("ok", "5368324170671202286", updated_by=TEST_ID)
    await session.flush()
    row = await repo.get("ok")
    assert row is not None
    assert row.custom_emoji_id == "5368324170671202286"
    assert row.is_premium
    assert row.updated_by == TEST_ID

    assert await repo.set_custom_emoji("ok", None)
    await session.flush()
    row = await repo.get("ok")
    assert row is not None
    assert row.custom_emoji_id is None
    assert not row.is_premium


async def test_set_custom_emoji_on_unknown_slot_returns_false(session: AsyncSession) -> None:
    assert await EmojiRepo(session).set_custom_emoji("no_such_slot", "1") is False


async def test_clear_all_drops_ids_but_keeps_slots_and_fallbacks(
    session: AsyncSession,
) -> None:
    """The panel's "delete all emoji" button must not delete the slots."""
    repo = EmojiRepo(session)
    await repo.clear_all()
    await session.flush()
    before_count = await repo.count()

    await repo.set_custom_emoji("ok", "111")
    await repo.set_custom_emoji("error", "222")
    await session.flush()

    touched = await repo.clear_all(updated_by=TEST_ID)
    await session.flush()

    assert touched == 2, "only slots that had an id should be counted"
    assert await repo.count() == before_count, "slots must survive"
    assert all(s.custom_emoji_id is None for s in await repo.all())
    assert all(s.fallback for s in await repo.all()), "fallbacks must survive"


async def test_clear_all_is_a_noop_when_nothing_is_set(session: AsyncSession) -> None:
    repo = EmojiRepo(session)
    await repo.clear_all()
    await session.flush()
    assert await repo.clear_all() == 0


async def test_emoji_pagination_covers_every_slot_exactly_once(
    session: AsyncSession,
) -> None:
    """Mirrors the panel's 8-per-page layout from the reference screenshot."""
    repo = EmojiRepo(session)
    total = await repo.count()
    per_page = 8

    seen: list[str] = []
    for offset in range(0, total, per_page):
        page = await repo.page(offset=offset, limit=per_page)
        assert len(page) <= per_page
        seen.extend(s.slot for s in page)

    assert len(seen) == total
    assert len(set(seen)) == total, "no slot may appear on two pages"


async def test_emoji_ensure_preserves_admin_custom_id(session: AsyncSession) -> None:
    """Re-seeding must never wipe what the admin configured."""
    repo = EmojiRepo(session)
    await repo.set_custom_emoji("ok", "keep-me")
    await session.flush()

    await repo.ensure("ok", "New Title", "🆗", 5)
    await session.flush()

    row = await repo.get("ok")
    assert row is not None
    assert row.custom_emoji_id == "keep-me", "admin's premium emoji must survive a re-seed"
    assert row.title == "New Title", "code-owned fields should update"
    assert row.fallback == "🆗"
    assert row.sort_order == 5


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


async def test_seed_is_idempotent(session: AsyncSession, settings: Settings) -> None:
    first = await apply_seed(session, settings)
    second = await apply_seed(session, settings)
    assert first == second, "running the seed twice must not change the counts"
    assert first["emoji_slots"] == len(EMOJI_SLOTS)
    assert first["settings"] >= len(SETTINGS)


async def test_seed_slot_names_are_unique() -> None:
    names = [s.slot for s in EMOJI_SLOTS]
    assert len(names) == len(set(names))


async def test_seed_setting_keys_are_unique() -> None:
    keys = [s.key for s in SETTINGS]
    assert len(keys) == len(set(keys))


async def test_seed_honours_env_premium_flag(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh install with PREMIUM_EMOJI_ENABLED=false must start disabled."""
    from app.config import Settings as S

    await session.execute(Setting.__table__.delete().where(Setting.key == "premium_emoji_enabled"))
    await session.flush()

    await apply_seed(session, S(bot_token="1:x", premium_emoji_enabled=False))
    await session.flush()
    assert await SettingRepo(session).get("premium_emoji_enabled") == "false"


# ---------------------------------------------------------------------------
# SettingsStore (typed access + Redis cache)
# ---------------------------------------------------------------------------


async def test_settings_store_typed_getters(
    db_sessionmaker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point the store's session_scope at the rolled-back test transaction.
    from contextlib import asynccontextmanager

    import app.core.settings_store as store_module
    from app.core.settings_store import SettingsStore

    @asynccontextmanager
    async def _scope():
        async with db_sessionmaker() as s:
            yield s
            await s.commit()

    monkeypatch.setattr(store_module, "session_scope", _scope)

    store = SettingsStore(redis=None)  # no cache: exercise the DB path

    await store.set("bool_probe", "true")
    assert await store.get_bool("bool_probe") is True
    for truthy in ("1", "yes", "on", "TRUE"):
        await store.set("bool_probe", truthy)
        assert await store.get_bool("bool_probe") is True, truthy
    for falsy in ("0", "no", "off", "false", ""):
        await store.set("bool_probe", falsy)
        assert await store.get_bool("bool_probe") is False, falsy

    await store.set("int_probe", "42")
    assert await store.get_int("int_probe") == 42
    await store.set("int_probe", "not-a-number")
    assert await store.get_int("int_probe", default=7) == 7, "bad int must fall back"

    assert await store.get("missing_probe", "fallback") == "fallback"
    assert await store.get_bool("missing_probe", True) is True
    assert await store.get_int("missing_probe", 5) == 5


async def test_settings_store_toggle_flips_and_returns_new_value(
    db_sessionmaker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import asynccontextmanager

    import app.core.settings_store as store_module
    from app.core.settings_store import SettingsStore

    @asynccontextmanager
    async def _scope():
        async with db_sessionmaker() as s:
            yield s
            await s.commit()

    monkeypatch.setattr(store_module, "session_scope", _scope)
    store = SettingsStore(redis=None)

    await store.set_bool("toggle_probe", False)
    assert await store.toggle("toggle_probe") is True
    assert await store.get_bool("toggle_probe") is True
    assert await store.toggle("toggle_probe") is False


async def test_settings_store_survives_a_broken_cache(
    db_sessionmaker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Redis outage must degrade to direct DB reads, not take the bot down."""
    from contextlib import asynccontextmanager

    import app.core.settings_store as store_module
    from app.core.settings_store import SettingsStore
    from redis.exceptions import ConnectionError as RedisConnectionError

    @asynccontextmanager
    async def _scope():
        async with db_sessionmaker() as s:
            yield s
            await s.commit()

    monkeypatch.setattr(store_module, "session_scope", _scope)

    class BrokenRedis:
        async def get(self, *_: object) -> None:
            raise RedisConnectionError("cache is down")

        async def set(self, *_: object, **__: object) -> None:
            raise RedisConnectionError("cache is down")

        async def delete(self, *_: object) -> None:
            raise RedisConnectionError("cache is down")

    store = SettingsStore(redis=BrokenRedis())  # type: ignore[arg-type]
    await store.set("outage_probe", "still-works")
    assert await store.get("outage_probe") == "still-works"
    await store.invalidate()  # must not raise


async def test_settings_store_requires_initialisation() -> None:
    import app.core.settings_store as store_module

    monkey = store_module._store
    store_module._store = None
    try:
        with pytest.raises(RuntimeError, match="init_settings_store"):
            store_module.get_settings_store()
    finally:
        store_module._store = monkey
