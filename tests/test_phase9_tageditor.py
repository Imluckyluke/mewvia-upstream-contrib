"""Phase-9 acceptance tests: Music Tag & Cover Editor.

Unit level: mutagen read/write round-trip on real ffmpeg fixtures.
E2E level: full FSM flow (open editor -> stage title -> stage cover ->
save -> retagged audio delivered) through the live dispatcher.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from aiogram import Dispatcher
from aiogram.types import Audio, CallbackQuery, Chat, Message, PhotoSize, Update
from aiogram.types import User as TgUser
from app.callbacks import AudioToolCB
from app.db.repo import ConversionRepo
from app.features.tag_editor.router import TagFieldCB, _prepare_cover, fa_to_en_digits
from app.media.tag_editor import (
    MAX_TITLE_LEN,
    AudioTags,
    apply_tags,
    format_tags_card,
    read_tags,
)
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.gen import generate_audio_fixture, generate_png_fixture
from tests.test_phase2_menu import RecordingBot

pytestmark = pytest.mark.media

USER_ID = 999_000_100_001


# ---------------------------------------------------------------------------
# Unit: tag engine
# ---------------------------------------------------------------------------


async def test_read_empty_then_write_roundtrip(tmp_path: Path) -> None:
    src = generate_audio_fixture(tmp_path / "song.mp3", duration=2.0)

    before = read_tags(src)
    assert before.title is None
    assert before.has_cover is False
    assert before.format_label == "MP3"

    after = apply_tags(
        src,
        title="Test Song",
        artist="Test Artist",
        album="Test Album",
        track=3,
        genre="Pop",
    )
    assert after.title == "Test Song"
    assert after.artist == "Test Artist"
    assert after.album == "Test Album"
    assert after.track == 3
    assert after.genre == "Pop"

    # Persistence: re-read from disk.
    reread = read_tags(src)
    assert reread.title == "Test Song"
    assert reread.track == 3


async def test_cover_write_and_detect(tmp_path: Path) -> None:
    src = generate_audio_fixture(tmp_path / "song.mp3", duration=2.0)
    png = generate_png_fixture(tmp_path / "cover.png", 800, 800)

    cover_bytes, mime = _prepare_cover(png)
    assert mime == "image/jpeg"
    assert len(cover_bytes) > 0

    after = apply_tags(src, cover_bytes=cover_bytes, cover_mime=mime)
    assert after.has_cover is True
    assert read_tags(src).has_cover is True


async def test_write_limits_and_track_range(tmp_path: Path) -> None:
    from app.core.errors import ConversionError

    src = generate_audio_fixture(tmp_path / "song.mp3", duration=1.0)

    long_title = "x" * (MAX_TITLE_LEN + 50)
    capped = apply_tags(src, title=long_title)
    assert capped.title is not None
    assert len(capped.title) <= MAX_TITLE_LEN

    with pytest.raises(ConversionError):
        apply_tags(src, track=10000)

    with pytest.raises(ConversionError):
        apply_tags(src.with_suffix(".wav"), title="nope")


async def test_clear_field_really_deletes_frame(tmp_path: Path) -> None:
    """Regression: حذف must delete the frame, not silently keep the old value."""
    src = generate_audio_fixture(tmp_path / "song.mp3", duration=2.0)

    first = apply_tags(src, title="First Title", artist="First Artist")
    assert first.title == "First Title"
    assert first.artist == "First Artist"

    # Untouched fields survive a partial edit.
    second = apply_tags(src, title="Second Title")
    assert second.title == "Second Title"
    assert second.artist == "First Artist"

    # Empty string deletes the frame — the old value must NOT come back.
    third = apply_tags(src, artist="")
    assert third.title == "Second Title"
    assert third.artist is None
    assert read_tags(src).artist is None

    fourth = apply_tags(src, title="", track=5)
    assert fourth.title is None
    assert fourth.track == 5


async def test_read_tags_without_extension(tmp_path: Path) -> None:
    """Regression: extension-less downloads (Telegram) still read MP3 tags."""
    import shutil

    real = generate_audio_fixture(tmp_path / "song.mp3", duration=2.0)
    apply_tags(real, title="Hello", artist="World")

    noext = tmp_path / "tag_src"
    shutil.copy(real, noext)
    tags = read_tags(noext)
    assert tags.title == "Hello"
    assert tags.artist == "World"
    assert tags.format_label == "MP3"


async def test_format_tags_card_renders(tmp_path: Path) -> None:
    tags = AudioTags(title="Song", artist="Artist", track=7, has_cover=True)
    card = format_tags_card(tags, "song.mp3")
    assert "ویرایشگر تگ و کاور" in card
    assert "Song" in card
    assert "Artist" in card
    assert "دارد" in card


def test_fa_to_en_digits() -> None:
    assert fa_to_en_digits("۱۲۳") == "123"
    assert fa_to_en_digits("٤٥٦") == "456"
    assert fa_to_en_digits("track ۷") == "track 7"


# ---------------------------------------------------------------------------
# E2E: full FSM flow through the dispatcher
# ---------------------------------------------------------------------------


def _audio_message(msg_id: int, audio_file_id: str = "AUDIO_FILE_ID") -> Message:
    return Message(
        message_id=msg_id,
        date=datetime.datetime.now(datetime.UTC),
        chat=Chat(id=USER_ID, type="private"),
        from_user=TgUser(id=USER_ID, is_bot=False, first_name="TagUser"),
        audio=Audio(
            file_id=audio_file_id,
            file_unique_id="uniq-audio-1",
            duration=120,
            title="Old Title",
            performer="Old Artist",
        ),
    )


async def test_tag_editor_e2e_full_flow(
    app_dispatcher: Dispatcher,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Open editor -> stage title -> stage cover -> save -> audio delivered."""
    fixture_mp3 = generate_audio_fixture(tmp_path / "real_song.mp3", duration=2.0)
    fixture_cover = generate_png_fixture(tmp_path / "real_cover.png", 400, 400)

    async def fake_download(
        self: RecordingBot, file_id: str, destination: Path, **_: object
    ) -> Path:
        import shutil

        if file_id == "COVER_FILE_ID":
            shutil.copy(fixture_cover, destination)
        else:
            shutil.copy(fixture_mp3, destination)
        return destination

    monkeypatch.setattr(RecordingBot, "download", fake_download)

    bot = RecordingBot()
    try:
        # Step 1: send audio -> panel with tag button.
        await app_dispatcher.feed_update(bot, Update(update_id=1, message=_audio_message(1)))
        panels = [m for m in bot.sent if type(m).__name__ == "SendMessage"]
        assert len(panels) >= 1
        assert "ابزارهای صدا" in str(panels[-1].text)

        # The tag button lives on the panel markup — open the editor directly
        # via callback on the panel message.
        panel_msg = Message(
            message_id=2,
            date=datetime.datetime.now(datetime.UTC),
            chat=Chat(id=USER_ID, type="private"),
            from_user=TgUser(id=999, is_bot=True, first_name="Bot"),
            text="panel",
            reply_to_message=_audio_message(1),
        )
        cb_open = CallbackQuery(
            id="cb_tag_open",
            from_user=TgUser(id=USER_ID, is_bot=False, first_name="TagUser"),
            message=panel_msg,
            chat_instance="ci_tag",
            data=AudioToolCB(action="tag_menu").pack(),
        )
        await app_dispatcher.feed_update(bot, Update(update_id=2, callback_query=cb_open))
        # RecordingBot returns None for EditMessageText, so the handler falls
        # back to msg.answer (SendMessage) for the tag card.
        cards = [
            m
            for m in bot.sent
            if type(m).__name__ in ("EditMessageText", "SendMessage")
            and "ویرایشگر تگ و کاور" in str(getattr(m, "text", ""))
        ]
        assert len(cards) >= 1, "tag card was never rendered"

        # Step 2: tap the title field -> prompt.
        cb_field = CallbackQuery(
            id="cb_tag_field",
            from_user=TgUser(id=USER_ID, is_bot=False, first_name="TagUser"),
            message=panel_msg,
            chat_instance="ci_tag",
            data=TagFieldCB(action="field", field="title").pack(),
        )
        await app_dispatcher.feed_update(bot, Update(update_id=3, callback_query=cb_field))
        prompts = [
            m
            for m in bot.sent
            if type(m).__name__ in ("EditMessageText", "SendMessage")
            and "ویرایش" in str(getattr(m, "text", ""))
            and "نام آهنگ" in str(getattr(m, "text", ""))
        ]
        assert len(prompts) >= 1, "field prompt was never rendered"

        # Step 3: reply with the new title -> staged.
        title_msg = Message(
            message_id=3,
            date=datetime.datetime.now(datetime.UTC),
            chat=Chat(id=USER_ID, type="private"),
            from_user=TgUser(id=USER_ID, is_bot=False, first_name="TagUser"),
            text="آهنگ جدید من",
        )
        await app_dispatcher.feed_update(bot, Update(update_id=4, message=title_msg))
        sends = [m for m in bot.sent if type(m).__name__ == "SendMessage"]
        assert "ثبت شد" in str(sends[-1].text)
        assert "آهنگ جدید من" in str(sends[-1].text)

        # Step 4: tap cover -> prompt, then send a photo.
        cb_cover = CallbackQuery(
            id="cb_tag_cover",
            from_user=TgUser(id=USER_ID, is_bot=False, first_name="TagUser"),
            message=panel_msg,
            chat_instance="ci_tag",
            data=TagFieldCB(action="cover").pack(),
        )
        await app_dispatcher.feed_update(bot, Update(update_id=5, callback_query=cb_cover))
        cover_prompts = [
            m
            for m in bot.sent
            if type(m).__name__ in ("EditMessageText", "SendMessage")
            and "کاور جدید" in str(getattr(m, "text", ""))
        ]
        assert len(cover_prompts) >= 1, "cover prompt was never rendered"

        cover_msg = Message(
            message_id=4,
            date=datetime.datetime.now(datetime.UTC),
            chat=Chat(id=USER_ID, type="private"),
            from_user=TgUser(id=USER_ID, is_bot=False, first_name="TagUser"),
            photo=[
                PhotoSize(
                    file_id="COVER_FILE_ID",
                    file_unique_id="uniq-cover-1",
                    width=400,
                    height=400,
                )
            ],
        )
        await app_dispatcher.feed_update(bot, Update(update_id=6, message=cover_msg))
        sends = [m for m in bot.sent if type(m).__name__ == "SendMessage"]
        assert "کاور جدید" in str(sends[-1].text)

        # Step 5: save -> retagged audio delivered.
        cb_save = CallbackQuery(
            id="cb_tag_save",
            from_user=TgUser(id=USER_ID, is_bot=False, first_name="TagUser"),
            message=panel_msg,
            chat_instance="ci_tag",
            data=TagFieldCB(action="save").pack(),
        )
        await app_dispatcher.feed_update(bot, Update(update_id=7, callback_query=cb_save))
        methods = [type(m).__name__ for m in bot.sent]
        assert "SendAudio" in methods
    finally:
        await bot.session.close()

    # Conversion logged as atool_tag_edit.
    repo = ConversionRepo(session)
    assert await repo.count_total() >= 1


async def test_tag_editor_tick_toggle(app_dispatcher: Dispatcher, session: AsyncSession) -> None:
    """Staged field switches its icon slot to ok; tapping again unstages."""
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from app.features.tag_editor.router import tag_editor_kb

    class SlotRegistry:
        """Per-slot premium ids so slot switches are observable."""

        def custom_id(self, slot: str) -> str | None:
            return f"premium-{slot}"

        def fallback(self, slot: str) -> str:
            return "✅" if slot == "ok" else "🔹"

        def text(self, slot: str) -> str:
            return self.fallback(slot)

        def label(self, slot: str, text: str) -> str:
            return f"{self.fallback(slot)} {text}"

    def icons(kb: object) -> dict[str, str | None]:
        markup = kb.as_markup()  # type: ignore[attr-defined]
        out: dict[str, str | None] = {}
        for row in markup.inline_keyboard:
            for btn in row:
                out[btn.callback_data or ""] = btn.icon_custom_emoji_id
        return out

    registry = SlotRegistry()

    # Unstaged: every field keeps its own slot.
    clean = icons(tag_editor_kb(registry, staged={}))  # type: ignore[arg-type]
    assert clean["tagf:field:title"] == "premium-edit"
    assert clean["tagf:cover:"] == "premium-photo"

    # Staged title + cover switch to the ok slot (owner's premium tick).
    staged = icons(tag_editor_kb(registry, staged={"tag_title": "X", "cover_file_id": "C"}))  # type: ignore[arg-type]
    assert staged["tagf:field:title"] == "premium-ok"
    assert staged["tagf:cover:"] == "premium-ok"
    assert staged["tagf:field:artist"] == "premium-user"

    # E2E: tap staged field -> unstage message.
    tick_user = USER_ID + 5
    bot = RecordingBot()
    key = StorageKey(bot_id=bot.id, chat_id=tick_user, user_id=tick_user)
    state = FSMContext(app_dispatcher.storage, key)
    await state.update_data(file_id="SOME_FILE", kind="audio", tag_title="Old")

    panel_msg = Message(
        message_id=60,
        date=datetime.datetime.now(datetime.UTC),
        chat=Chat(id=tick_user, type="private"),
        from_user=TgUser(id=999, is_bot=True, first_name="Bot"),
        text="panel",
    )
    try:
        cb = CallbackQuery(
            id="cb_tick_toggle",
            from_user=TgUser(id=tick_user, is_bot=False, first_name="TickUser"),
            message=panel_msg,
            chat_instance="ci_tick",
            data=TagFieldCB(action="field", field="title").pack(),
        )
        await app_dispatcher.feed_update(bot, Update(update_id=60, callback_query=cb))
        texts = [
            str(getattr(m, "text", ""))
            for m in bot.sent
            if type(m).__name__ in ("EditMessageText", "SendMessage")
        ]
        assert any("حذف شد" in t for t in texts), f"no unstage message; texts={texts}"
        # State unstaged.
        fresh = await FSMContext(app_dispatcher.storage, key).get_data()
        assert fresh.get("tag_title") is None
    finally:
        await bot.session.close()


async def test_tag_editor_save_empty_rejected(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    """Save with editor open but nothing staged shows a guiding alert."""
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey

    bot = RecordingBot()
    try:
        # Simulate a user who opened the editor (file_id staged) but changed nothing.
        # NOTE: aiogram keys FSM by (bot, chat, user) — chat comes from the message.
        fresh_id = USER_ID + 1
        panel_msg_fresh = Message(
            message_id=51,
            date=datetime.datetime.now(datetime.UTC),
            chat=Chat(id=fresh_id, type="private"),
            from_user=TgUser(id=999, is_bot=True, first_name="Bot"),
            text="panel",
        )
        key = StorageKey(bot_id=bot.id, chat_id=fresh_id, user_id=fresh_id)
        state = FSMContext(app_dispatcher.storage, key)
        await state.update_data(file_id="SOME_FILE_ID", kind="audio")

        cb_save = CallbackQuery(
            id="cb_tag_save_empty",
            from_user=TgUser(id=fresh_id, is_bot=False, first_name="FreshUser"),
            message=panel_msg_fresh,
            chat_instance="ci_tag2",
            data=TagFieldCB(action="save").pack(),
        )
        await app_dispatcher.feed_update(bot, Update(update_id=50, callback_query=cb_save))
        methods = [type(m).__name__ for m in bot.sent]
        answers = [m for m in bot.sent if type(m).__name__ == "AnswerCallbackQuery"]
        assert any("ویرایش نکردی" in str(m.text) for m in answers), (
            f"no alert; texts={[m.text for m in answers]}"
        )
        assert "SendAudio" not in methods
    finally:
        await bot.session.close()
