"""Unit and integration tests for loading GIFs and visual progress indicators."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Animation, CallbackQuery, Message
from app.admin.loading_gifs_router import (
    LoadingGifAddSG,
    confirm_delete_loading_gif,
    process_delete_loading_gif,
    prompt_add_loading_gif,
    ready_registry,
    render_loading_gifs_dashboard,
    save_new_loading_gif,
    toggle_loading_gifs,
)
from app.admin.loading_gifs_router import (
    test_loading_gif_random as admin_test_random,
)
from app.admin.loading_gifs_router import (
    test_loading_gif_single as admin_test_single,
)
from app.db.repo import SettingRepo
from app.media.progress_gif import (
    add_loading_gif,
    delete_loading_gif,
    get_random_loading_gif,
    is_loading_gifs_enabled,
    list_loading_gifs,
    set_loading_gifs_enabled,
)
from app.ui.progress import (
    ProgressController,
    format_progress_caption,
    render_progress_bar,
)
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_progress_bar_rendering() -> None:
    """Test visual progress bar generation with various percentages."""
    bar0 = render_progress_bar(0)
    assert "[▱▱▱▱▱▱▱▱▱▱]" in bar0
    assert "۰%" in bar0

    bar50 = render_progress_bar(50)
    assert "▰▰▰▰▰" in bar50
    assert "۵۰%" in bar50

    bar100 = render_progress_bar(100)
    assert "[▰▰▰▰▰▰▰▰▰▰]" in bar100
    assert "۱۰۰%" in bar100

    # Test clamping
    bar_neg = render_progress_bar(-20)
    assert "۰%" in bar_neg
    bar_overflow = render_progress_bar(150)
    assert "۱۰۰%" in bar_overflow


@pytest.mark.asyncio
async def test_format_progress_caption() -> None:
    """Test formatting progress caption for messages and animations."""
    caption = format_progress_caption("در حال دانلود…", 45)
    assert "در حال دانلود…" in caption
    assert "۴۵%" in caption
    # Nothing is rendered below the progress bar any more (no filler one-liner).
    assert caption.strip().endswith("</code>")

    # `detail` is accepted for backwards compatibility but never rendered.
    caption_detail = format_progress_caption("در حال فشرده‌سازی…", 80, detail="سایز: 5MB")
    assert "سایز: 5MB" not in caption_detail
    assert "شکیبا باشید" not in caption_detail
    assert caption_detail.strip().endswith("</code>")


@pytest.mark.asyncio
async def test_loading_gifs_crud(session: AsyncSession) -> None:
    """Test adding, listing, toggling, and deleting loading GIFs in the repository."""
    # Initially empty or default
    await SettingRepo(session).set("loading_gifs", "[]")
    await SettingRepo(session).set("loading_gifs_enabled", "true")

    gifs = await list_loading_gifs(session)
    assert isinstance(gifs, list)

    # Add GIF 1
    item1 = await add_loading_gif(
        file_id="CgACAgQAAxkBAAE1",
        label="گیف آزمایشی ۱",
        file_unique_id="AQAD1",
        session=session,
    )
    assert item1["file_id"] == "CgACAgQAAxkBAAE1"
    assert item1["label"] == "گیف آزمایشی ۱"

    # Add GIF 2
    item2 = await add_loading_gif(
        file_id="CgACAgQAAxkBAAE2",
        label="گیف آزمایشی ۲",
        file_unique_id="AQAD2",
        session=session,
    )

    gifs = await list_loading_gifs(session)
    assert len(gifs) == 2
    assert any(g["id"] == item1["id"] for g in gifs)
    assert any(g["id"] == item2["id"] for g in gifs)

    # Random selection
    random_id = await get_random_loading_gif(session)
    assert random_id in ("CgACAgQAAxkBAAE1", "CgACAgQAAxkBAAE2")

    # Toggle enabled
    assert await is_loading_gifs_enabled(session) is True
    await set_loading_gifs_enabled(False, session)
    assert await is_loading_gifs_enabled(session) is False

    # When disabled, random selection returns None
    assert await get_random_loading_gif(session) is None

    # Re-enable
    await set_loading_gifs_enabled(True, session)
    assert await is_loading_gifs_enabled(session) is True

    # Delete GIF 1
    deleted = await delete_loading_gif(item1["id"], session)
    assert deleted is True

    gifs = await list_loading_gifs(session)
    assert len(gifs) == 1
    assert gifs[0]["id"] == item2["id"]


@pytest.mark.asyncio
async def test_progress_controller_lifecycle(session: AsyncSession) -> None:
    """Test ProgressController starting, ticker loop, updating stage, and clean completion."""
    bot = AsyncMock(spec=Bot)
    mock_anim_msg = AsyncMock(spec=Message)
    mock_anim_msg.message_id = 999
    bot.send_animation.return_value = mock_anim_msg
    bot.edit_message_caption.return_value = True
    bot.delete_message.return_value = True

    # Configure a GIF so it uses animation
    await SettingRepo(session).set(
        "loading_gifs",
        json.dumps([{"id": "g1", "file_id": "test_file_id", "label": "test"}]),
    )
    await SettingRepo(session).set("loading_gifs_enabled", "true")

    # Start progress controller with animation
    controller = await ProgressController.start(
        bot=bot,
        chat_id=12345,
        reply_to_id=6789,
        initial_stage="در حال شروع پردازش…",
        session=session,
        auto_tick=False,  # manual testing
    )
    assert controller.is_animation is True
    assert controller.message_id == 999
    bot.send_animation.assert_called_once()

    # Update stage manually
    await controller.update_stage("مرحله دو: فشرده‌سازی", percent=60, force=True)
    bot.edit_message_caption.assert_called_once()
    assert controller.percent == 60

    # Duck-typing edit_text
    await controller.edit_text("متن تستی جدید", force=True)
    assert bot.edit_message_caption.call_count == 2

    # Complete (delete)
    await controller.complete(delete=True)
    bot.delete_message.assert_called_once_with(12345, 999)


@pytest.mark.asyncio
async def test_progress_controller_text_fallback(session: AsyncSession) -> None:
    """Test ProgressController falls back cleanly to text message when no GIF is configured."""
    bot = AsyncMock(spec=Bot)
    mock_text_msg = AsyncMock(spec=Message)
    mock_text_msg.message_id = 888
    bot.send_message.return_value = mock_text_msg
    bot.edit_message_text.return_value = True
    bot.delete_message.return_value = True

    # Empty pool
    await SettingRepo(session).set("loading_gifs", "[]")

    controller = await ProgressController.start(
        bot=bot,
        chat_id=12345,
        initial_stage="در حال پردازش تستی…",
        session=session,
        auto_tick=False,
    )
    assert controller.is_animation is False
    assert controller.message_id == 888
    bot.send_message.assert_called_once()

    await controller.update_stage("پیشرفت متنی", percent=75, force=True)
    bot.edit_message_text.assert_called_once()

    await controller.fail("خطای تستی")
    bot.edit_message_text.assert_called_with(
        chat_id=12345,
        message_id=888,
        text="خطای تستی",
    )


@pytest.mark.asyncio
async def test_admin_loading_gifs_dashboard(session: AsyncSession) -> None:
    """Test admin dashboard rendering with registered GIFs."""
    registry = await ready_registry()
    await SettingRepo(session).set(
        "loading_gifs",
        json.dumps(
            [{"id": "g_test", "file_id": "fid_123", "label": "گیف اول", "created_at": "2026/10/15"}]
        ),
    )
    await SettingRepo(session).set("loading_gifs_enabled", "true")

    text, kb = await render_loading_gifs_dashboard(session, registry)
    assert "مدیریت گیف‌های نوار پیشرفت و لودینگ" in text
    assert "🟢 فعال" in text
    assert "عدد" in text
    markup = kb.as_markup()
    assert any("گیف اول" in btn.text for row in markup.inline_keyboard for btn in row)


@pytest.mark.asyncio
async def test_admin_loading_gifs_actions(session: AsyncSession) -> None:
    """Test admin callbacks: toggle, add prompt, save from message, and delete confirmation."""
    # Test toggle
    query = MagicMock(spec=CallbackQuery)
    query.answer = AsyncMock()
    query.data = "lgif:toggle"
    query.message = AsyncMock(spec=Message)
    await toggle_loading_gifs(query, session)
    query.answer.assert_called_once()

    # Test prompt add
    state = AsyncMock()
    query_add = MagicMock(spec=CallbackQuery)
    query_add.answer = AsyncMock()
    mock_msg = MagicMock(spec=Message)
    mock_msg.edit_text = AsyncMock()
    query_add.message = mock_msg
    await prompt_add_loading_gif(query_add, state)
    state.set_state.assert_called_once_with(LoadingGifAddSG.waiting_for_gif)

    # Test save animation
    msg = MagicMock(spec=Message)
    msg.reply = AsyncMock()
    msg.animation = MagicMock(spec=Animation)
    msg.animation.file_id = "new_anim_file_id"
    msg.animation.file_unique_id = "uniq_1"
    msg.video = None
    msg.document = None
    msg.text = None

    await save_new_loading_gif(msg, state, session)
    gifs = await list_loading_gifs(session)
    assert any(g["file_id"] == "new_anim_file_id" for g in gifs)
    saved_item = next(g for g in gifs if g["file_id"] == "new_anim_file_id")

    # Test delete confirmation and execution
    query_del = MagicMock(spec=CallbackQuery)
    query_del.answer = AsyncMock()
    query_del.data = f"lgif:del:{saved_item['id']}"
    mock_del_msg = MagicMock(spec=Message)
    mock_del_msg.edit_text = AsyncMock()
    query_del.message = mock_del_msg
    await confirm_delete_loading_gif(query_del, session)
    query_del.message.edit_text.assert_called_once()

    query_del_yes = MagicMock(spec=CallbackQuery)
    query_del_yes.answer = AsyncMock()
    query_del_yes.data = f"lgif:del_yes:{saved_item['id']}"
    mock_del_yes_msg = MagicMock(spec=Message)
    mock_del_yes_msg.edit_text = AsyncMock()
    query_del_yes.message = mock_del_yes_msg
    await process_delete_loading_gif(query_del_yes, session)

    gifs_after = await list_loading_gifs(session)
    assert not any(g["file_id"] == "new_anim_file_id" for g in gifs_after)


@pytest.mark.asyncio
async def test_admin_loading_gifs_demo_runs(session: AsyncSession) -> None:
    """Test live demo simulation on single and random GIFs."""
    bot = AsyncMock(spec=Bot)
    mock_msg = AsyncMock(spec=Message)
    mock_msg.message_id = 777
    bot.send_animation.return_value = mock_msg

    await SettingRepo(session).set(
        "loading_gifs",
        json.dumps([{"id": "g_demo", "file_id": "demo_fid", "label": "گیف دمو"}]),
    )
    await SettingRepo(session).set("loading_gifs_enabled", "true")

    query = MagicMock(spec=CallbackQuery)
    query.answer = AsyncMock()
    query.data = "lgif:test:g_demo"
    mock_chat = MagicMock()
    mock_chat.id = 555
    query.message = MagicMock(spec=Message)
    query.message.chat = mock_chat

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await admin_test_single(query, session, bot)
        bot.send_animation.assert_called_once()
        assert bot.edit_message_caption.call_count >= 2
        bot.delete_message.assert_called_once_with(chat_id=555, message_id=777)

    # Test random
    query_rnd = MagicMock(spec=CallbackQuery)
    query_rnd.answer = AsyncMock()
    query_rnd.data = "lgif:test_random"
    query_rnd.message = MagicMock(spec=Message)
    query_rnd.message.chat = mock_chat
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await admin_test_random(query_rnd, session, bot)
        assert bot.send_animation.call_count == 2
