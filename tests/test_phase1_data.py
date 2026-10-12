"""Phase-1 acceptance tests: models, repositories, seed, settings store,
middlewares, admin filter.

Anything touching PostgreSQL uses the `session` fixture, which rolls back, so the
suite can run repeatedly against a live database without accumulating rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram.types import User as TgUser
from app.db.models import EmojiSlot, User
from app.db.repo import UserRepo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# A test id far outside the range Telegram actually issues, so a stray row can
# never collide with a real user.
TEST_ID = 999_000_000_001
TEST_ID_2 = 999_000_000_002


def make_tg_user(
    user_id: int = TEST_ID,
    *,
    username: str | None = "tester",
    first_name: str = "Test",
    last_name: str | None = "User",
    is_premium: bool = False,
    language_code: str | None = "fa",
) -> TgUser:
    return TgUser(
        id=user_id,
        is_bot=False,
        first_name=first_name,
        last_name=last_name,
        username=username,
        language_code=language_code,
        is_premium=is_premium,
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_display_name_prefers_username() -> None:
    u = User(telegram_id=1, username="ali", first_name="Ali", last_name="Alavi")
    assert u.display_name == "@ali"


def test_display_name_falls_back_to_names_then_id() -> None:
    assert User(telegram_id=1, first_name="Ali", last_name="Alavi").display_name == "Ali Alavi"
    assert User(telegram_id=1, first_name="Ali").display_name == "Ali"
    assert User(telegram_id=7).display_name == "7"


def test_emoji_slot_is_premium_reflects_custom_id() -> None:
    assert not EmojiSlot(slot="ok", title="t", fallback="✅").is_premium
    assert EmojiSlot(slot="ok", title="t", fallback="✅", custom_emoji_id="123").is_premium


# ---------------------------------------------------------------------------
# UserRepo
# ---------------------------------------------------------------------------


async def test_upsert_inserts_then_updates_without_duplicating(session: AsyncSession) -> None:
    repo = UserRepo(session)

    first = await repo.upsert_from_telegram(make_tg_user())
    await session.flush()
    assert first.telegram_id == TEST_ID
    assert first.updates_seen == 1
    first_seen = first.created_at

    second = await repo.upsert_from_telegram(make_tg_user(username="renamed"))
    await session.flush()

    assert second.telegram_id == TEST_ID
    assert second.username == "renamed"
    assert second.updates_seen == 2, "second update must increment, not reset"
    assert second.created_at == first_seen, "created_at must never move"
    assert second.last_seen_at >= first_seen

    count = await session.scalar(
        select(User.telegram_id).where(User.telegram_id == TEST_ID).exists().select()
    )
    assert count
    rows = (await session.execute(select(User).where(User.telegram_id == TEST_ID))).scalars().all()
    assert len(rows) == 1, "upsert must not create a second row"


async def test_upsert_refreshes_profile_fields(session: AsyncSession) -> None:
    repo = UserRepo(session)
    await repo.upsert_from_telegram(make_tg_user(is_premium=False, language_code="fa"))
    await session.flush()

    updated = await repo.upsert_from_telegram(
        make_tg_user(first_name="Changed", last_name=None, is_premium=True, language_code="en")
    )
    await session.flush()

    assert updated.first_name == "Changed"
    assert updated.last_name is None
    assert updated.is_premium is True
    assert updated.language_code == "en"


async def test_upsert_preserves_ban_but_clears_blocked_flag(session: AsyncSession) -> None:
    """Our own state survives a refresh; blocked-ness is evidently stale."""
    repo = UserRepo(session)
    await repo.upsert_from_telegram(make_tg_user())
    await session.flush()

    await repo.set_banned(TEST_ID, True)
    await repo.mark_blocked(TEST_ID)
    await session.flush()

    refreshed = await repo.upsert_from_telegram(make_tg_user())
    await session.flush()

    assert refreshed.is_banned is True, "a ban must not be lifted by a new message"
    assert refreshed.has_blocked_bot is False, "messaging us proves they unblocked"


async def test_set_banned_on_unknown_user_returns_false(session: AsyncSession) -> None:
    assert await UserRepo(session).set_banned(123_456_789_012, True) is False


async def test_search_matches_username_names_and_numeric_id(session: AsyncSession) -> None:
    repo = UserRepo(session)
    await repo.upsert_from_telegram(make_tg_user(username="uniquealpha", first_name="Zahra"))
    await repo.upsert_from_telegram(make_tg_user(TEST_ID_2, username=None, first_name="Uniquebeta"))
    await session.flush()

    by_username = await repo.list_page(query="uniquealpha")
    assert [u.telegram_id for u in by_username] == [TEST_ID]

    by_first_name = await repo.list_page(query="Uniquebeta")
    assert [u.telegram_id for u in by_first_name] == [TEST_ID_2]

    by_id = await repo.list_page(query=str(TEST_ID))
    assert [u.telegram_id for u in by_id] == [TEST_ID]

    assert await repo.count(query="uniquealpha") == 1
    assert await repo.count(query="no-such-user-anywhere") == 0


async def test_search_is_case_insensitive(session: AsyncSession) -> None:
    repo = UserRepo(session)
    await repo.upsert_from_telegram(make_tg_user(username="MixedCaseName"))
    await session.flush()
    assert await repo.count(query="mixedcasename") == 1


async def test_list_page_orders_by_last_seen_desc_and_paginates(session: AsyncSession) -> None:
    repo = UserRepo(session)
    await repo.upsert_from_telegram(make_tg_user(TEST_ID, username="zzolder"))
    await session.flush()
    await repo.upsert_from_telegram(make_tg_user(TEST_ID_2, username="zznewer"))
    await session.flush()

    page = await repo.list_page(limit=2, query="zz")
    assert [u.username for u in page] == ["zznewer", "zzolder"]

    first_only = await repo.list_page(limit=1, query="zz")
    second_only = await repo.list_page(offset=1, limit=1, query="zz")
    assert first_only[0].username == "zznewer"
    assert second_only[0].username == "zzolder"


async def test_broadcast_targets_exclude_banned_and_blocked(session: AsyncSession) -> None:
    repo = UserRepo(session)
    await repo.upsert_from_telegram(make_tg_user(TEST_ID))
    await repo.upsert_from_telegram(make_tg_user(TEST_ID_2))
    await session.flush()

    targets = await repo.iter_broadcast_targets()
    assert TEST_ID in targets and TEST_ID_2 in targets

    await repo.set_banned(TEST_ID, True)
    await repo.mark_blocked(TEST_ID_2)
    await session.flush()

    targets = await repo.iter_broadcast_targets()
    assert TEST_ID not in targets
    assert TEST_ID_2 not in targets


async def test_stats_returns_every_expected_counter(session: AsyncSession) -> None:
    repo = UserRepo(session)
    await repo.upsert_from_telegram(make_tg_user(is_premium=True))
    await session.flush()

    stats = await repo.stats()
    expected = {
        "total",
        "active_24h",
        "active_7d",
        "new_24h",
        "new_7d",
        "banned",
        "blocked_bot",
        "premium",
    }
    assert set(stats) == expected
    assert all(isinstance(v, int) for v in stats.values())
    assert stats["total"] >= 1
    assert stats["premium"] >= 1
    assert stats["new_24h"] >= 1


async def test_count_active_and_new_since(session: AsyncSession) -> None:
    repo = UserRepo(session)
    await repo.upsert_from_telegram(make_tg_user())
    await session.flush()

    now = datetime.now(UTC)
    assert await repo.count_active_since(now - timedelta(minutes=1)) >= 1
    assert await repo.count_new_since(now - timedelta(minutes=1)) >= 1
    assert await repo.count_active_since(now + timedelta(minutes=1)) == 0
