"""Phase-6 acceptance tests: personal sticker set builder."""

from __future__ import annotations

from pathlib import Path

import pytest
from aiogram import Dispatcher
from app.db.repo import StickerSetRepo
from app.features.stickerpack.router import render_pack_list
from app.features.stickerpack.service import (
    add_sticker_to_user_set,
    create_user_sticker_set,
    make_pack_name,
    make_pack_title,
)
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_phase2_menu import RecordingBot

USER_ID = 999_000_000_150


def test_make_pack_name_formatting() -> None:
    name1 = make_pack_name(123456789, "My Cool Pack", "MewviaBot")
    assert name1.endswith("_by_MewviaBot")
    assert name1.startswith("u123456789_")

    name2 = make_pack_name(123456789, "My Cool Pack", "@MewviaBot")
    assert name2.endswith("_by_MewviaBot")
    assert name2 != name1, "Pack names must be unique due to short hex"

    assert make_pack_title("My Pack", "MewviaBot") == "My Pack | @MewviaBot"
    assert make_pack_title("پک من", "@MewviaBot") == "پک من | @MewviaBot"


async def test_sticker_set_repo_operations(session: AsyncSession) -> None:
    repo = StickerSetRepo(session)
    pack_name = make_pack_name(USER_ID, "TestPack", "MewviaBot")

    s_set = await repo.create_set(USER_ID, pack_name, "Test Pack Title")
    await session.commit()

    assert s_set.name == pack_name
    assert s_set.stickers_count == 1

    fetched = await repo.get_by_name(pack_name)
    assert fetched is not None
    assert fetched.title == "Test Pack Title"

    await repo.increment_count(pack_name)
    await session.commit()

    updated = await repo.get_by_name(pack_name)
    assert updated is not None
    assert updated.stickers_count == 2

    user_sets = await repo.get_user_sets(USER_ID)
    assert len(user_sets) >= 1
    assert any(s.name == pack_name for s in user_sets)

    # Test delete
    deleted = await repo.delete_set(pack_name)
    await session.commit()
    assert deleted is True
    assert await repo.get_by_name(pack_name) is None


async def test_create_and_add_sticker_to_set_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sticker_path = tmp_path / "st.png"
    sticker_path.write_bytes(b"fake png data")
    pack_name = make_pack_name(USER_ID, "MyStickers", "MewviaBot")

    bot = RecordingBot()

    async def fake_create_new_sticker_set(*args: object, **kwargs: object) -> bool:
        return True

    async def fake_add_sticker_to_set(*args: object, **kwargs: object) -> bool:
        return True

    monkeypatch.setattr(bot, "create_new_sticker_set", fake_create_new_sticker_set)
    monkeypatch.setattr(bot, "add_sticker_to_set", fake_add_sticker_to_set)

    try:
        created = await create_user_sticker_set(
            bot=bot,
            user_id=USER_ID,
            pack_name=pack_name,
            title="My Stickers",
            sticker_path=sticker_path,
            emoji="🎨",
            sticker_format="static",
        )
        assert created is True

        added = await add_sticker_to_user_set(
            bot=bot,
            user_id=USER_ID,
            pack_name=pack_name,
            sticker_path=sticker_path,
            emoji="🔥",
            sticker_format="static",
        )
        assert added is True
    finally:
        await bot.session.close()


async def test_sticker_pack_management_ui(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    repo = StickerSetRepo(session)
    user_id = 888_777_666

    # Create 10 sticker sets to test pagination (>8 per page)
    for i in range(10):
        pack_name = make_pack_name(user_id, f"Pack {i}", "MewviaBot")
        title = make_pack_title(f"Pack {i}", "MewviaBot")
        await repo.create_set(user_id, pack_name, title)
    await session.commit()

    # Page 0 should render first 8 items
    text, kb = await render_pack_list(session, user_id, page_index=0)
    assert "استیکرپک‌های من" in text
    markup = kb.as_markup()
    assert len(markup.inline_keyboard) == 10  # 8 pack rows + 1 nav row + 1 back row

    # Page 1 should render remaining 2 items
    _text1, kb1 = await render_pack_list(session, user_id, page_index=1)
    markup1 = kb1.as_markup()
    assert len(markup1.inline_keyboard) == 4  # 2 pack rows + 1 nav row + 1 back row
