"""Phase-10 acceptance tests: inline mode, hardening and full system validation."""

from __future__ import annotations

from aiogram.filters import CommandObject
from aiogram.types import Message
from aiogram.types import User as TgUser
from app.features import load_features
from app.features.basic import cmd_start
from app.ui.texts import t


def test_all_features_registered() -> None:
    features = load_features()
    names = [f.name for f in features]

    assert "basic" in names
    assert "convert" in names
    assert "stickerpack" in names
    assert "videotools" in names
    assert "audio" in names
    assert "admin" in names


async def test_deeplink_start_shortcuts(monkeypatch) -> None:
    sent_messages = []

    async def fake_answer(self: Message, text: str, **kwargs: object) -> Message:
        sent_messages.append(text)
        return self

    monkeypatch.setattr(Message, "answer", fake_answer)

    user = TgUser(id=1, is_bot=False, first_name="Test")
    dummy_msg = Message(
        message_id=100, date=1234567890, chat=dict(id=1, type="private"), from_user=user
    )

    # Test /start convert
    cmd_convert = CommandObject(prefix="/", command="start", args="convert")
    await cmd_start(dummy_msg, cmd_convert, is_admin=False)
    assert sent_messages[-1] == t("start.deeplink_convert")

    # Test /start newpack
    cmd_newpack = CommandObject(prefix="/", command="start", args="newpack")
    await cmd_start(dummy_msg, cmd_newpack, is_admin=False)
    assert sent_messages[-1] == t("start.deeplink_newpack")

    # Test /start addpack_<name>
    cmd_addpack = CommandObject(prefix="/", command="start", args="addpack_mypack")
    await cmd_start(dummy_msg, cmd_addpack, is_admin=False)
    assert sent_messages[-1] == t("start.deeplink_addpack", pack_name="mypack")
