"""Main menu, built from the feature registry.

Nothing here names a specific feature. Adding a capability in a later phase means
registering it with `menu_label_key` set — the menu picks it up on the next
render. Features can be hidden behind a runtime settings flag or marked
admin-only, and both are handled here rather than in each feature.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from app.callbacks import MenuCB
from app.core.settings_store import get_settings_store
from app.features.registry import Feature, menu_features
from app.ui.emoji import EmojiRegistry
from app.ui.kb import KB, ReplyKB
from app.ui.styles import Style
from app.ui.texts import t


async def enabled_menu_features(*, is_admin: bool = False) -> list[Feature]:
    """Menu features visible to this user with their runtime switch on."""
    store = get_settings_store()
    result: list[Feature] = []
    for feature in menu_features():
        if feature.menu_admin_only and not is_admin:
            continue
        if feature.settings_flag and not await store.get_bool(feature.settings_flag, True):
            continue
        result.append(feature)
    return result


async def main_menu(registry: EmojiRegistry, *, is_admin: bool = False) -> InlineKeyboardMarkup:
    """The inline main menu."""
    kb = KB(registry)
    features = await enabled_menu_features(is_admin=is_admin)

    for feature in features:
        kb.button(
            t(feature.menu_label_key),
            callback_data=MenuCB(target=feature.name).pack(),
            style=feature.menu_style or Style.PRIMARY,
            emoji_slot=feature.menu_emoji_slot,
        )

    # Two per row reads better than one long column on a phone.
    widths = [2] * (len(features) // 2) + ([1] if len(features) % 2 else [])

    kb.button(
        t("menu.help"),
        callback_data=MenuCB(target="help").pack(),
        style=Style.PRIMARY,
        emoji_slot="file",
    )
    widths.append(1)

    kb.adjust(*widths)
    return kb.as_markup()


def persistent_keyboard(registry: EmojiRegistry) -> ReplyKeyboardMarkup:
    """The always-visible reply keyboard.

    Deliberately short: the inline menu carries the features, this is only the two
    shortcuts worth having one tap away at all times.
    """
    return (
        ReplyKB(registry)
        .button(t("menu.start_over"), style=Style.PRIMARY, emoji_slot="refresh")
        .button(t("menu.help"), style=Style.CONFIRM, emoji_slot="file")
        .adjust(2)
        .as_markup(placeholder=t("menu.placeholder"))
    )
