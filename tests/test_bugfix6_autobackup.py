"""Bugfix 6-14 + autobackup acceptance tests."""

from __future__ import annotations

import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession


def _make_update(update_id: int, text: str, user_id: int) -> Update:
    user = TgUser(id=user_id, is_bot=False, first_name="T", username="t")
    message = Message(
        message_id=update_id,
        date=datetime.datetime.now(datetime.UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=user,
        text=text,
    )
    return Update(update_id=update_id, message=message)


async def test_maintenance_blocks_users_but_not_admins(
    app_dispatcher: Dispatcher,
) -> None:
    from app.core.settings_store import get_settings_store

    from tests.test_phase1_middleware import FakeBot

    bot = FakeBot()
    try:
        await get_settings_store().set_bool("maintenance", True)
        await get_settings_store().set("maintenance_message", "تعمیرات!")

        await app_dispatcher.feed_update(bot, _make_update(9001, "/me", 999_111_222))
        sends = [c for c in bot.calls if type(c).__name__ == "SendMessage"]
        assert len(sends) >= 1
        assert any("تعمیرات!" in (s.text or "") for s in sends)

        bot.calls.clear()
        await app_dispatcher.feed_update(bot, _make_update(9002, "/me", 1))
        # Admin goes through (a profile reply, not the maintenance notice).
        sends2 = [c for c in bot.calls if type(c).__name__ == "SendMessage"]
        assert not any("تعمیرات!" in (s.text or "") for s in sends2)
    finally:
        await get_settings_store().set_bool("maintenance", False)
        await bot.session.close()


async def test_quota_unlimited_by_default(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    from app.core.quota import remaining_quota
    from app.core.settings_store import get_settings_store

    await get_settings_store().set("daily_quota", "0")
    assert await remaining_quota(session, 12345) is None


async def test_quota_counts_only_success_today(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    from app.core.quota import remaining_quota
    from app.core.settings_store import get_settings_store
    from app.db.repo import ConversionRepo, UserRepo

    await get_settings_store().set("daily_quota", "2")
    try:
        repo = UserRepo(session)
        await repo.upsert_from_telegram(TgUser(id=666_000_111, is_bot=False, first_name="Q"))
        conv = ConversionRepo(session)
        await conv.log_conversion(
            telegram_id=666_000_111, source_kind="photo", target_id="webp", status="success"
        )
        await conv.log_conversion(
            telegram_id=666_000_111, source_kind="photo", target_id="webp", status="failed"
        )
        await session.commit()

        assert await remaining_quota(session, 666_000_111) == 1
    finally:
        await get_settings_store().set("daily_quota", "0")


async def test_quota_blocks_at_cap(app_dispatcher: Dispatcher, session: AsyncSession) -> None:
    from app.core.quota import check_daily_quota
    from app.core.settings_store import get_settings_store
    from app.db.repo import ConversionRepo, UserRepo

    await get_settings_store().set("daily_quota", "1")
    try:
        repo = UserRepo(session)
        await repo.upsert_from_telegram(TgUser(id=666_000_222, is_bot=False, first_name="Q2"))
        conv = ConversionRepo(session)
        await conv.log_conversion(
            telegram_id=666_000_222, source_kind="photo", target_id="webp", status="success"
        )
        await session.commit()

        msg = Message(
            message_id=1,
            date=datetime.datetime.now(datetime.UTC),
            chat=Chat(id=666_000_222, type="private"),
            from_user=TgUser(id=666_000_222, is_bot=False, first_name="Q2"),
            text="x",
        )
        from tests.test_phase1_middleware import FakeBot

        bot = FakeBot()
        try:
            assert await check_daily_quota(session, 666_000_222, msg.as_(bot)) is False
        finally:
            await bot.session.close()
    finally:
        await get_settings_store().set("daily_quota", "0")


def test_cron_validation() -> None:
    from app.admin.autobackup import describe_schedule

    assert describe_schedule("0 4 * * *") == "0 4 * * *"
    assert "نامعتبر" in describe_schedule("not a cron")


async def test_autobackup_sync_off_by_default(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    from app.admin.autobackup import shutdown_autobackup, sync_autobackup_job
    from app.config import get_settings
    from app.core.settings_store import get_settings_store

    await get_settings_store().set("autobackup_cron", "")
    bot = Bot(
        token="111111:TEST-TOKEN-FOR-PYTEST",
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        assert await sync_autobackup_job(get_settings(), bot) is None
    finally:
        await shutdown_autobackup()
        await bot.session.close()


def test_no_dead_service_package() -> None:
    import pathlib

    assert not pathlib.Path("app/services").exists(), "dead app/services must stay deleted"


def test_force_join_setting_removed() -> None:
    from app.db.seed import SETTINGS

    keys = {s.key for s in SETTINGS}
    assert "force_join_channel" not in keys


def test_units_has_fa_to_en() -> None:
    from app.core.units import fa_to_en_digits

    assert fa_to_en_digits("۱۲۳") == "123"
    assert fa_to_en_digits("٤٥٦") == "456"
