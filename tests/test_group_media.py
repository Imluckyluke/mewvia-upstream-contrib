"""Tests for group media assistant feature and panel ownership middleware."""

from __future__ import annotations

import pytest
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, Message, User
from app.media.kinds import MediaKind, resolve_panel_media
from app.middlewares.group_guard import (
    GroupPanelGuardMiddleware,
    get_panel_opts,
    store_panel,
    store_panel_owner,
    update_panel_opts,
)


@pytest.mark.asyncio
async def test_resolve_panel_media_from_store() -> None:
    await store_panel(
        chat_id=-100123456789,
        message_id=999,
        owner_id=111222,
        owner_name="Ali",
        reply_to_message_id=444,
        kind="photo",
        file_id="test_file_id_abc",
        file_size=2048,
    )

    opts = await update_panel_opts(-100123456789, 999, wide=True, filter_id="bw")
    assert opts["wide"] is True

    opts = await get_panel_opts(-100123456789, 999)
    assert opts["filter_id"] == "bw"

    chat = Chat(id=-100123456789, type=ChatType.SUPERGROUP)
    msg = Message(message_id=999, date=100000, chat=chat)
    user = User(id=111222, is_bot=False, first_name="Ali")

    cb = CallbackQuery(
        id="cb3",
        from_user=user,
        chat_instance="inst1",
        message=msg,
        data="conv:photo:gif",
    )

    resolved = await resolve_panel_media(cb)
    assert resolved is not None
    kind, file_id, file_size, reply_to_id = resolved
    assert kind == MediaKind.PHOTO
    assert file_id == "test_file_id_abc"
    assert file_size == 2048
    assert reply_to_id == 444


@pytest.mark.asyncio
async def test_group_guard_middleware_allows_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = GroupPanelGuardMiddleware()

    await store_panel_owner(
        chat_id=-100123456789,
        message_id=777,
        owner_id=1000,
        owner_name="Reza",
    )

    user = User(id=1000, is_bot=False, first_name="Reza")
    chat = Chat(id=-100123456789, type=ChatType.SUPERGROUP)
    msg = Message(message_id=777, date=100000, chat=chat)

    answered = []

    async def dummy_answer(self: object, text: str = "", show_alert: bool = False) -> None:
        answered.append((text, show_alert))

    monkeypatch.setattr(CallbackQuery, "answer", dummy_answer)

    cb = CallbackQuery(
        id="cb1",
        from_user=user,
        chat_instance="inst1",
        message=msg,
        data="conv:photo:gif",
    )

    handler_called = False

    async def dummy_handler(event: object, data: dict) -> str:
        nonlocal handler_called
        handler_called = True
        return "OK"

    res = await middleware(dummy_handler, cb, {})
    assert handler_called is True
    assert res == "OK"
    assert len(answered) == 0


@pytest.mark.asyncio
async def test_group_guard_middleware_blocks_non_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = GroupPanelGuardMiddleware()

    await store_panel_owner(
        chat_id=-100123456789,
        message_id=888,
        owner_id=1000,
        owner_name="Reza",
    )

    other_user = User(id=2000, is_bot=False, first_name="Mehdi")
    chat = Chat(id=-100123456789, type=ChatType.SUPERGROUP)
    msg = Message(message_id=888, date=100000, chat=chat)

    alerts = []

    async def dummy_answer(self: object, text: str = "", show_alert: bool = False) -> None:
        alerts.append((text, show_alert))

    monkeypatch.setattr(CallbackQuery, "answer", dummy_answer)

    cb = CallbackQuery(
        id="cb2",
        from_user=other_user,
        chat_instance="inst1",
        message=msg,
        data="conv:photo:gif",
    )

    handler_called = False

    async def dummy_handler(event: object, data: dict) -> str:
        nonlocal handler_called
        handler_called = True
        return "OK"

    res = await middleware(dummy_handler, cb, {})
    assert handler_called is False
    assert res is None
    assert len(alerts) == 1
    assert "Reza" in alerts[0][0]
    assert alerts[0][1] is True
