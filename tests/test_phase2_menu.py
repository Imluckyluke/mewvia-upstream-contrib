"""Phase-2 acceptance tests, part 3: the menu built from the registry, the
premium-emoji guard on the live request chain, and the /start screen end to end.

Updates are fed through a real Dispatcher with a Bot whose transport is replaced,
so the payload asserted on is exactly what would have gone to Telegram.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import (
    Chat,
    InlineKeyboardMarkup,
    Message,
    MessageId,
    ReplyKeyboardMarkup,
    Update,
)
from aiogram.types import User as TgUser
from app.core.settings_store import SettingsStore
from app.db.repo import EmojiRepo
from app.ui.emoji import PREMIUM_FLAG
from app.ui.premium_guard import PremiumEmojiGuard
from app.ui.texts import t
from sqlalchemy.ext.asyncio import AsyncSession

ADMIN_ID = 1
USER_ID = 999_000_000_055
PREMIUM_ID = "5368324170671202286"


class RecordingBot(Bot):
    """Bot whose transport is replaced; the middleware chain still runs."""

    def __init__(self, *, fail_on_custom_emoji: bool = False) -> None:
        super().__init__(
            token="111111:TEST-TOKEN-FOR-PYTEST",
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.session.middleware(PremiumEmojiGuard())
        self.sent: list[TelegramMethod[Any]] = []
        self.attempts: list[TelegramMethod[Any]] = []
        self.fail_on_custom_emoji = fail_on_custom_emoji

    async def __call__(self, method: TelegramMethod[Any], request_timeout: int | None = None):
        # Route through the session middlewares so PremiumEmojiGuard is exercised.
        return await self.session.middleware.wrap_middlewares(self._transport, timeout=None)(
            bot=self, method=method
        )

    async def _transport(self, bot: Bot, method: TelegramMethod[Any], **_: Any) -> Any:
        self.attempts.append(method)
        if self.fail_on_custom_emoji and _carries_premium(method):
            raise TelegramBadRequest(method=method, message="Bad Request: CUSTOM_EMOJI_INVALID")
        method_name = type(method).__name__
        if method_name == "GetChatMember":
            # Membership probe, not a user-visible message: visible in attempts
            # only, so `sent` keeps counting exactly what the user received.
            # Fail-open as a member so the sponsor gate passes through.
            from aiogram.enums import ChatMemberStatus
            from aiogram.types import ChatMemberMember

            user_id = getattr(method, "user_id", 0)
            return ChatMemberMember(
                status=ChatMemberStatus.MEMBER,
                user=TgUser(id=user_id, is_bot=False, first_name="Test"),
            )
        if method_name in ("GetChat", "GetMe"):
            return None
        self.sent.append(method)
        if method_name == "CopyMessage":
            return MessageId(message_id=999)
        if method_name == "SendMessage":
            # message.reply()/answer() must resolve to a Message (production
            # always returns one); the id feeds store_panel in convert flow.
            chat_id = getattr(method, "chat_id", 0)
            return Message(
                message_id=999,
                date=datetime.datetime.now(datetime.UTC),
                chat=Chat(id=chat_id, type="private"),
                text=getattr(method, "text", ""),
            )
        return None


def _carries_premium(method: TelegramMethod[Any]) -> bool:
    text = getattr(method, "text", None) or getattr(method, "caption", None) or ""
    if "<tg-emoji" in text:
        return True
    markup = getattr(method, "reply_markup", None)
    rows: list[list[Any]] = []
    if isinstance(markup, InlineKeyboardMarkup):
        rows = list(markup.inline_keyboard)
    elif isinstance(markup, ReplyKeyboardMarkup):
        rows = list(markup.keyboard)
    return any(getattr(b, "icon_custom_emoji_id", None) for row in rows for b in row)


def make_update(update_id: int, text: str, user_id: int) -> Update:
    user = TgUser(id=user_id, is_bot=False, first_name="Test", username="tester")
    message = Message(
        message_id=update_id,
        date=datetime.datetime.now(datetime.UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=user,
        text=text,
    )
    return Update(update_id=update_id, message=message)


# The dispatcher itself lives in conftest (`app_dispatcher`): the feature routers
# are module-level singletons, so only one Dispatcher may ever hold them.


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------


async def test_start_sends_a_reply_keyboard_and_an_inline_menu(app_dispatcher: Dispatcher) -> None:
    """The phase-2 acceptance check: /start must produce buttons."""
    bot = RecordingBot()
    try:
        await app_dispatcher.feed_update(bot, make_update(1, "/start", USER_ID))
    finally:
        await bot.session.close()

    assert len(bot.sent) == 2, "one message for the reply keyboard, one for the menu"

    reply_kb = bot.sent[0].reply_markup
    assert isinstance(reply_kb, ReplyKeyboardMarkup)
    assert reply_kb.keyboard, "persistent keyboard must not be empty"

    inline_kb = bot.sent[1].reply_markup
    assert isinstance(inline_kb, InlineKeyboardMarkup)
    assert inline_kb.inline_keyboard, "inline menu must not be empty"


async def test_start_menu_carries_styles(app_dispatcher: Dispatcher) -> None:
    bot = RecordingBot()
    try:
        await app_dispatcher.feed_update(bot, make_update(1, "/start", USER_ID))
    finally:
        await bot.session.close()

    markup = bot.sent[1].reply_markup
    assert isinstance(markup, InlineKeyboardMarkup)
    styles = {b.style for row in markup.inline_keyboard for b in row}
    assert styles - {None}, "at least one button must be styled"


async def test_admin_sees_an_extra_menu_entry(app_dispatcher: Dispatcher) -> None:
    bot = RecordingBot()
    try:
        await app_dispatcher.feed_update(bot, make_update(1, "/start", USER_ID))
        user_buttons = _count_buttons(bot.sent[1].reply_markup)
        bot.sent.clear()
        await app_dispatcher.feed_update(bot, make_update(2, "/start", ADMIN_ID))
        admin_buttons = _count_buttons(bot.sent[1].reply_markup)
    finally:
        await bot.session.close()

    assert admin_buttons > user_buttons


def _count_buttons(markup: Any) -> int:
    assert isinstance(markup, InlineKeyboardMarkup)
    return sum(len(row) for row in markup.inline_keyboard)


async def test_every_menu_button_has_callback_data(app_dispatcher: Dispatcher) -> None:
    """A button with no callback data would leave a spinner on screen."""
    bot = RecordingBot()
    try:
        await app_dispatcher.feed_update(bot, make_update(1, "/start", ADMIN_ID))
    finally:
        await bot.session.close()

    markup = bot.sent[1].reply_markup
    assert isinstance(markup, InlineKeyboardMarkup)
    for row in markup.inline_keyboard:
        for button in row:
            assert button.callback_data or button.url


async def test_help_and_me_offer_a_way_back(app_dispatcher: Dispatcher) -> None:
    bot = RecordingBot()
    try:
        await app_dispatcher.feed_update(bot, make_update(1, "/help", USER_ID))
        await app_dispatcher.feed_update(bot, make_update(2, "/me", USER_ID))
    finally:
        await bot.session.close()

    for method in bot.sent:
        markup = method.reply_markup
        assert isinstance(markup, InlineKeyboardMarkup)
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert any(d and d.startswith("menu") for d in datas)


async def test_unknown_text_gets_an_answer(app_dispatcher: Dispatcher) -> None:
    """The fallback router must leave nothing unanswered."""
    bot = RecordingBot()
    try:
        await app_dispatcher.feed_update(bot, make_update(1, "چیز نامفهوم", USER_ID))
    finally:
        await bot.session.close()

    assert len(bot.sent) == 1
    assert isinstance(bot.sent[0], SendMessage)
    assert bot.sent[0].text == t("common.unknown_command")


# ---------------------------------------------------------------------------
# Premium emoji: end-to-end degradation on the request chain
# ---------------------------------------------------------------------------


async def test_menu_uses_premium_icons_when_configured(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    await EmojiRepo(session).set_custom_emoji("file", PREMIUM_ID)
    await session.commit()

    bot = RecordingBot()
    try:
        await app_dispatcher.feed_update(bot, make_update(1, "/start", USER_ID))
    finally:
        await bot.session.close()

    markup = bot.sent[1].reply_markup
    assert isinstance(markup, InlineKeyboardMarkup)
    icons = [b.icon_custom_emoji_id for row in markup.inline_keyboard for b in row]
    assert PREMIUM_ID in icons


async def test_rejected_custom_emoji_is_retried_without_it(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    """A lapsed Premium subscription must degrade, not break every message."""
    await EmojiRepo(session).set_custom_emoji("file", PREMIUM_ID)
    await session.commit()

    bot = RecordingBot(fail_on_custom_emoji=True)
    try:
        await app_dispatcher.feed_update(bot, make_update(1, "/start", USER_ID))
    finally:
        await bot.session.close()

    # Two messages, and the one carrying premium icons was attempted twice.
    assert len(bot.sent) == 2, "the user still received both messages"
    assert len(bot.attempts) > len(bot.sent), "the failed attempt was retried"

    delivered = bot.sent[1].reply_markup
    assert isinstance(delivered, InlineKeyboardMarkup)
    icons = [b.icon_custom_emoji_id for row in delivered.inline_keyboard for b in row]
    assert not any(icons), "the retry must carry no premium icons"

    labels = [b.text for row in delivered.inline_keyboard for b in row]
    assert any("📎" in label for label in labels), "the unicode fallback replaces the icon"


async def test_rejection_turns_the_global_switch_off(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    """So the round trip happens once, not on every later message."""
    await EmojiRepo(session).set_custom_emoji("file", PREMIUM_ID)
    await session.commit()

    store = SettingsStore(redis=None)
    assert await store.get_bool(PREMIUM_FLAG) is True

    bot = RecordingBot(fail_on_custom_emoji=True)
    try:
        await app_dispatcher.feed_update(bot, make_update(1, "/start", USER_ID))
    finally:
        await bot.session.close()

    assert await store.get_bool(PREMIUM_FLAG) is False


async def test_unrelated_bad_request_is_not_swallowed(app_dispatcher: Dispatcher) -> None:
    """Only custom-emoji failures may be retried; everything else must surface."""

    class AlwaysFails(RecordingBot):
        async def _transport(self, bot: Bot, method: TelegramMethod[Any], **_: Any):
            raise TelegramBadRequest(method=method, message="Bad Request: chat not found")

    bot = AlwaysFails()
    try:
        with pytest.raises(TelegramBadRequest, match="chat not found"):
            await app_dispatcher.feed_update(bot, make_update(1, "/start", USER_ID))
    finally:
        await bot.session.close()


async def test_check_sponsors_button_sees_fresh_join(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    """E2E: block caches False, user joins, «عضو شدم» must pass fresh."""
    from aiogram.enums import ChatMemberStatus
    from aiogram.types import CallbackQuery, ChatMemberLeft, ChatMemberMember, Update
    from app.db.repo.sponsor import SponsorRepo

    await SponsorRepo(session).add_or_update(channel_id=-1001999999009, title="Fresh Check Chan")
    await session.commit()

    left = ChatMemberLeft(
        status=ChatMemberStatus.LEFT,
        user=TgUser(id=USER_ID, is_bot=False, first_name="Test"),
    )
    member = ChatMemberMember(
        status=ChatMemberStatus.MEMBER,
        user=TgUser(id=USER_ID, is_bot=False, first_name="Test"),
    )
    states: list[bool] = [False]  # start outside the channel

    real_bot = RecordingBot()
    orig_transport = real_bot._transport

    async def flipping_transport(bot: Bot, method: Any, **kwargs: Any) -> Any:
        if type(method).__name__ == "GetChatMember":
            real_bot.attempts.append(method)
            return member if states[0] else left
        return await orig_transport(bot, method, **kwargs)

    real_bot._transport = flipping_transport  # type: ignore[method-assign]

    async def feed_callback(cb_id: str) -> RecordingBot:
        msg = Message(
            message_id=77,
            date=datetime.datetime.now(datetime.UTC),
            chat=Chat(id=USER_ID, type="private"),
            from_user=TgUser(id=USER_ID, is_bot=False, first_name="Test"),
            text="prompt",
        )
        cb = CallbackQuery(
            id=cb_id,
            from_user=TgUser(id=USER_ID, is_bot=False, first_name="Test"),
            chat_instance="ci",
            data="check_sponsors",
            message=msg,
        )
        try:
            await app_dispatcher.feed_update(real_bot, Update(update_id=77, callback_query=cb))
        except Exception:
            pass
        return real_bot

    try:
        # 1. Blocked (caches False): the button answers with the alert.
        await feed_callback("cb-block")
        alerts = [
            a
            for a in real_bot.attempts
            if type(a).__name__ == "AnswerCallbackQuery"
            and "هنوز در این کانال‌ها عضو نشده‌اید" in str(getattr(a, "text", ""))
        ]
        assert alerts, "blocked tap must answer with the still-missing alert"
        # 2. User joins, taps again -> must confirm, not repeat the stale verdict.
        states[0] = True
        n_before = len(real_bot.sent)
        await feed_callback("cb-confirm")
        new_methods = [type(m).__name__ for m in real_bot.sent[n_before:]]
        assert "SendMessage" in new_methods or "EditMessageText" in new_methods, (
            f"expected a confirmation screen, got {new_methods}"
        )
    finally:
        await real_bot.session.close()
        for row in await SponsorRepo(session).all():
            if row.channel_id == -1001999999009:
                await session.delete(row)
        await session.flush()

    # Cleanup: membership cache entry must not leak into other tests.
    from app.core.sponsor_service import _MEMBERSHIP_CACHE

    _MEMBERSHIP_CACHE.pop((-1001999999009, USER_ID), None)
