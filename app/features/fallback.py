"""Last-resort router.

Registered with the highest order so every other feature gets a chance first.
Its job is to make sure no tap and no message is met with silence — an inline
button that never answers leaves a spinner on the user's screen for ~30s.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.callbacks import MenuCB
from app.features.registry import Feature, register
from app.ui.texts import t

router = Router(name="fallback")


@router.callback_query(MenuCB.filter())
async def menu_target_not_ready(query: CallbackQuery, callback_data: MenuCB) -> None:
    """A menu entry whose feature has not landed yet (admin, convert)."""
    await query.answer(t("common.soon"), show_alert=True)


@router.callback_query()
async def unknown_callback(query: CallbackQuery) -> None:
    """Stale buttons from an old message — answer so the spinner stops."""
    await query.answer(t("common.stale_button"), show_alert=True)


@router.message(F.chat.type == "private")
async def unknown_message(message: Message) -> None:
    await message.answer(t("common.unknown_command"))


feature = register(
    Feature(
        name="fallback",
        router=router,
        order=9999,
    )
)
