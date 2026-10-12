"""Tests for reply-keyboard label stripping, decoration, and slot emoji live editing."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from app.callbacks import DemoCB
from app.db.repo import EmojiRepo
from app.ui.labels import decorate, looks_like_unicode_emoji, matches_label, strip_decoration
from sqlalchemy.ext.asyncio import AsyncSession

ADMIN_ID = 1


def test_strip_decoration_drops_leading_unicode_emoji() -> None:
    assert strip_decoration("🔄 شروع مجدد") == "شروع مجدد"
    assert strip_decoration("📎 راهنما") == "راهنما"
    assert strip_decoration("شروع مجدد") == "شروع مجدد"


def test_matches_label_handles_both_decorated_and_plain() -> None:
    assert matches_label("🔄 شروع مجدد", "شروع مجدد")
    assert matches_label("شروع مجدد", "شروع مجدد")
    assert matches_label("🔄 شروع مجدد", "🔄 شروع مجدد")
    assert not matches_label("راهنما", "شروع مجدد")


def test_decorate_prefixes_icon_idempotently() -> None:
    assert decorate("🔄", "شروع مجدد") == "🔄 شروع مجدد"
    assert decorate("🔄", "🔄 شروع مجدد") == "🔄 شروع مجدد"
    assert decorate("", "شروع مجدد") == "شروع مجدد"


def test_looks_like_unicode_emoji_validation() -> None:
    assert looks_like_unicode_emoji("✅")
    assert looks_like_unicode_emoji("🎉")
    assert not looks_like_unicode_emoji("متن عادی")
    assert not looks_like_unicode_emoji("123")
    assert not looks_like_unicode_emoji("")


async def test_reply_keyboard_buttons_respond_when_decorated(app_dispatcher) -> None:
    from tests.test_phase2_menu import RecordingBot, make_update

    bot = RecordingBot()
    try:
        # User sends the text as Telegram delivers it (with inlined fallback icon)
        await app_dispatcher.feed_update(bot, make_update(1, "🔄 شروع مجدد", 1001))
    finally:
        await bot.session.close()

    assert len(bot.sent) == 2, "reply '🔄 شروع مجدد' must trigger send_start"


async def test_emoji_slot_delete_action(app_dispatcher, session: AsyncSession) -> None:
    from tests.test_phase2_menu import RecordingBot

    repo = EmojiRepo(session)
    await repo.set_custom_emoji("ok", "5368324170671202286")
    await session.commit()

    bot = RecordingBot()
    try:
        # Trigger edit/del action
        import datetime

        from aiogram.types import CallbackQuery, Chat, Message
        from aiogram.types import User as TgUser

        cb = CallbackQuery(
            id="1",
            from_user=TgUser(id=ADMIN_ID, is_bot=False, first_name="Admin"),
            message=Message(
                message_id=10,
                date=datetime.datetime.now(datetime.UTC),
                chat=Chat(id=ADMIN_ID, type="private"),
                text="UI Showcase",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            ),
            chat_instance="1",
            data=DemoCB(action="del", slot="delete", target="ok").pack(),
        )
        from aiogram.types import Update

        update = Update(update_id=1, callback_query=cb)
        await app_dispatcher.feed_update(bot, update)
    finally:
        await bot.session.close()

    slot = await repo.get("ok")
    assert slot is not None
    assert slot.custom_emoji_id is None, "custom emoji must be cleared on delete"
