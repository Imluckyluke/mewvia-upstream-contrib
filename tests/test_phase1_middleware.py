"""Phase-1 acceptance tests, part 3: middlewares and the admin filter.

These feed synthetic Updates through a real Dispatcher, so the middleware chain,
the context keys it publishes, and the admin gate are exercised the same way a
real Telegram update would exercise them. No network is involved.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser
from app.config import Settings
from app.db.models import User
from app.middlewares import setup_middlewares
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_phase1_data import TEST_ID, TEST_ID_2

ADMIN_ID = 1  # matches ADMIN_IDS in tests/conftest.py
NON_ADMIN_ID = TEST_ID


class FakeBot(Bot):
    """Bot that records calls instead of hitting the network."""

    def __init__(self) -> None:
        super().__init__(
            token="111111:TEST-TOKEN-FOR-PYTEST",
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.calls: list[TelegramMethod[Any]] = []

    async def __call__(self, method: TelegramMethod[Any], request_timeout: int | None = None):
        self.calls.append(method)
        return None


def make_update(
    update_id: int,
    text: str,
    user_id: int,
    *,
    is_premium: bool = False,
    username: str | None = "tester",
) -> Update:
    user = TgUser(
        id=user_id,
        is_bot=False,
        first_name="Test",
        username=username,
        is_premium=is_premium,
    )
    message = Message(
        message_id=update_id,
        date=__import__("datetime").datetime.now(__import__("datetime").UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=user,
        text=text,
    )
    return Update(update_id=update_id, message=message)


@pytest.fixture
def captured() -> dict[str, Any]:
    return {}


@pytest.fixture
def dispatcher(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    captured: dict[str, Any],
) -> Dispatcher:
    """Dispatcher with the real middleware chain and a probe handler."""
    from app.filters import IsAdmin

    router = Router(name="probe")

    @router.message(Command("probe"))
    async def probe(
        message: Message,
        db_user: User | None,
        is_admin: bool,
        session: AsyncSession,
    ) -> None:
        captured["handled"] = True
        captured["db_user"] = db_user
        captured["is_admin"] = is_admin
        captured["session"] = session

    @router.message(Command("adminonly"), IsAdmin())
    async def admin_only(message: Message) -> None:
        captured["admin_handled"] = True

    @router.message(Command("boom"))
    async def boom(message: Message, session: AsyncSession, db_user: User) -> None:
        # Write something, then fail: the session must be rolled back.
        db_user.first_name = "SHOULD-NOT-PERSIST"
        await session.flush()
        raise RuntimeError("handler exploded")

    dp = Dispatcher(storage=MemoryStorage(), settings=settings)
    setup_middlewares(dp, settings, db_sessionmaker)
    dp.include_router(router)
    return dp


# ---------------------------------------------------------------------------
# UserTrackingMiddleware
# ---------------------------------------------------------------------------


async def test_middleware_creates_user_and_publishes_context(
    dispatcher: Dispatcher, session: AsyncSession, captured: dict[str, Any]
) -> None:
    bot = FakeBot()
    try:
        await dispatcher.feed_update(bot, make_update(1, "/probe", NON_ADMIN_ID))
    finally:
        await bot.session.close()

    assert captured["handled"] is True
    db_user = captured["db_user"]
    assert db_user is not None
    assert db_user.telegram_id == NON_ADMIN_ID
    assert db_user.updates_seen == 1
    assert captured["is_admin"] is False
    assert isinstance(captured["session"], AsyncSession)

    row = (
        await session.execute(select(User).where(User.telegram_id == NON_ADMIN_ID))
    ).scalar_one_or_none()
    assert row is not None, "the middleware must have committed the user"


async def test_two_updates_produce_one_row_with_incremented_counter(
    dispatcher: Dispatcher, session: AsyncSession, captured: dict[str, Any]
) -> None:
    """The core phase-1 acceptance check."""
    bot = FakeBot()
    try:
        await dispatcher.feed_update(bot, make_update(1, "/probe", NON_ADMIN_ID))
        first_seen = captured["db_user"].last_seen_at
        await dispatcher.feed_update(bot, make_update(2, "/probe", NON_ADMIN_ID))
    finally:
        await bot.session.close()

    second = captured["db_user"]
    assert second.updates_seen == 2
    assert second.last_seen_at >= first_seen

    rows = (
        (await session.execute(select(User).where(User.telegram_id == NON_ADMIN_ID)))
        .scalars()
        .all()
    )
    assert len(rows) == 1, "the same user must never be inserted twice"


async def test_middleware_marks_configured_admin(
    dispatcher: Dispatcher, captured: dict[str, Any]
) -> None:
    bot = FakeBot()
    try:
        await dispatcher.feed_update(bot, make_update(1, "/probe", ADMIN_ID))
    finally:
        await bot.session.close()
    assert captured["is_admin"] is True


async def test_banned_user_is_dropped_before_the_handler(
    dispatcher: Dispatcher, session: AsyncSession, captured: dict[str, Any]
) -> None:
    from app.db.repo import UserRepo

    bot = FakeBot()
    try:
        await dispatcher.feed_update(bot, make_update(1, "/probe", NON_ADMIN_ID))
        assert captured["handled"] is True

        await UserRepo(session).set_banned(NON_ADMIN_ID, True)
        await session.commit()

        captured.clear()
        await dispatcher.feed_update(bot, make_update(2, "/probe", NON_ADMIN_ID))
    finally:
        await bot.session.close()

    assert "handled" not in captured, "a banned user's update must not reach handlers"


async def test_banned_admin_is_still_served(
    dispatcher: Dispatcher, session: AsyncSession, captured: dict[str, Any]
) -> None:
    """Locking yourself out of your own bot should not be possible."""
    from app.db.repo import UserRepo

    bot = FakeBot()
    try:
        await dispatcher.feed_update(bot, make_update(1, "/probe", ADMIN_ID))
        await UserRepo(session).set_banned(ADMIN_ID, True)
        await session.commit()

        captured.clear()
        await dispatcher.feed_update(bot, make_update(2, "/probe", ADMIN_ID))
    finally:
        await bot.session.close()

    assert captured.get("handled") is True


async def test_premium_flag_is_recorded(dispatcher: Dispatcher, captured: dict[str, Any]) -> None:
    bot = FakeBot()
    try:
        await dispatcher.feed_update(bot, make_update(1, "/probe", TEST_ID_2, is_premium=True))
    finally:
        await bot.session.close()
    assert captured["db_user"].is_premium is True


def _make_group_chatter_update(
    update_id: int, text: str, user_id: int, chat_type: str = "supergroup"
) -> Update:
    user = TgUser(id=user_id, is_bot=False, first_name="Chatter", username="chatter")
    message = Message(
        message_id=update_id,
        date=__import__("datetime").datetime.now(__import__("datetime").UTC),
        chat=Chat(id=-100123456789, type=chat_type),
        from_user=user,
        text=text,
    )
    return Update(update_id=update_id, message=message)


async def test_group_chatter_does_not_create_user_row(
    dispatcher: Dispatcher, session: AsyncSession, captured: dict[str, Any]
) -> None:
    """Plain group messages must not register the sender as a bot user."""
    bot = FakeBot()
    try:
        await dispatcher.feed_update(bot, _make_group_chatter_update(1, "سلام بچه‌ها", 777_000_111))
    finally:
        await bot.session.close()

    row = (
        await session.execute(select(User).where(User.telegram_id == 777_000_111))
    ).scalar_one_or_none()
    assert row is None, "group chatter must not create a users row"


async def test_group_chatter_does_not_touch_existing_user(
    dispatcher: Dispatcher, session: AsyncSession, captured: dict[str, Any]
) -> None:
    """Group noise must not bump updates_seen / last_seen_at of a known user."""
    bot = FakeBot()
    try:
        await dispatcher.feed_update(bot, make_update(1, "/probe", NON_ADMIN_ID))
        before = captured["db_user"]
        assert before is not None
        seen_before = before.updates_seen
        last_before = before.last_seen_at

        captured.clear()
        await dispatcher.feed_update(
            bot, _make_group_chatter_update(2, "چت عادی گروه", NON_ADMIN_ID)
        )
    finally:
        await bot.session.close()

    session.expire_all()
    row = (await session.execute(select(User).where(User.telegram_id == NON_ADMIN_ID))).scalar_one()
    assert row.updates_seen == seen_before, "group chatter must not bump updates_seen"
    assert row.last_seen_at == last_before, "group chatter must not refresh last_seen_at"


async def test_group_command_still_tracks_user(
    dispatcher: Dispatcher, session: AsyncSession, captured: dict[str, Any]
) -> None:
    """/commands in groups are deliberate — they must still upsert."""
    bot = FakeBot()
    try:
        await dispatcher.feed_update(bot, _make_group_chatter_update(1, "/media", 777_000_222))
    finally:
        await bot.session.close()

    row = (
        await session.execute(select(User).where(User.telegram_id == 777_000_222))
    ).scalar_one_or_none()
    assert row is not None, "a group /command must register the user"


# ---------------------------------------------------------------------------
# DbSessionMiddleware
# ---------------------------------------------------------------------------


async def test_handler_failure_rolls_the_session_back(
    dispatcher: Dispatcher, session: AsyncSession
) -> None:
    """A crashing handler must not leave half-written rows behind."""
    bot = FakeBot()
    try:
        await dispatcher.feed_update(bot, make_update(1, "/probe", NON_ADMIN_ID))
        with pytest.raises(RuntimeError, match="handler exploded"):
            await dispatcher.feed_update(bot, make_update(2, "/boom", NON_ADMIN_ID))
    finally:
        await bot.session.close()

    row = (await session.execute(select(User).where(User.telegram_id == NON_ADMIN_ID))).scalar_one()
    assert row.first_name != "SHOULD-NOT-PERSIST", "the failed write must be rolled back"


# ---------------------------------------------------------------------------
# IsAdmin filter
# ---------------------------------------------------------------------------


async def test_admin_filter_allows_admin_and_blocks_others(
    dispatcher: Dispatcher, captured: dict[str, Any]
) -> None:
    bot = FakeBot()
    try:
        await dispatcher.feed_update(bot, make_update(1, "/adminonly", ADMIN_ID))
        assert captured.get("admin_handled") is True

        captured.clear()
        await dispatcher.feed_update(bot, make_update(2, "/adminonly", NON_ADMIN_ID))
    finally:
        await bot.session.close()

    assert "admin_handled" not in captured


async def test_admin_filter_returns_false_without_context() -> None:
    """Defensive: no user or no settings must not raise."""
    from app.filters import IsAdmin

    flt = IsAdmin()
    message = make_update(1, "/x", ADMIN_ID).message
    assert message is not None
    assert await flt(message, event_from_user=None, settings=None) is False
    assert await flt(message, event_from_user=message.from_user, settings=None) is False
