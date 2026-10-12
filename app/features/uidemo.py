"""`/ui` — showcase and live emoji slot manager.

Allows admins to inspect UI elements and edit emoji for any slot live.
Changes take effect immediately across the entire bot by invalidating
the EmojiRegistry and SettingsStore caches.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import DemoCB, MenuCB, PageCB
from app.core.settings_store import get_settings_store
from app.core.units import fa_digits
from app.db.repo import EmojiRepo
from app.features.registry import Feature, register
from app.filters import IsAdmin
from app.ui.emoji import PREMIUM_FLAG, EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.labels import looks_like_unicode_emoji
from app.ui.paginate import DEFAULT_PER_PAGE, Page, clamp_page, nav_row
from app.ui.styles import Style
from app.ui.texts import t

router = Router(name="uidemo")

SCOPE = "uislots"


class EmojiEditSG(StatesGroup):
    waiting_for_emoji = State()


def _page_cb(index: int) -> str:
    return PageCB(scope=SCOPE, index=index).pack()


def _demo(
    registry: EmojiRegistry,
    action: str,
    label: str,
    style: Style,
    slot: str,
    *,
    target: str = "",
) -> InlineKeyboardButton:
    """A showcase or action button."""
    return inline_button(
        label,
        callback_data=DemoCB(action=action, slot=slot, target=target).pack(),
        style=style,
        emoji_slot=slot,
        registry=registry,
    )


async def render(session: AsyncSession, page_index: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build the showcase & slot management page."""
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()

    repo = EmojiRepo(session)
    total = await repo.count()
    index = clamp_page(page_index, total, DEFAULT_PER_PAGE)
    rows = await repo.page(offset=index * DEFAULT_PER_PAGE, limit=DEFAULT_PER_PAGE)
    premium_count = sum(1 for s in await repo.all() if s.is_premium)

    if registry.premium_enabled:
        premium_line = t(
            "ui.premium_on",
            count=fa_digits(str(premium_count)),
            total=fa_digits(str(total)),
        )
    else:
        premium_line = t("ui.premium_off")

    body = "\n\n".join(
        [
            t("ui.title"),
            t("ui.styles_note"),
            premium_line,
            registry.label("ok", t("ui.inline_emoji_note")),
        ]
    )

    kb = KB(registry)

    # All three colours plus the unstyled default
    kb.row(
        _demo(registry, "primary", t("ui.primary"), Style.PRIMARY, "on"),
        _demo(registry, "confirm", t("ui.confirm"), Style.CONFIRM, "ok"),
        _demo(registry, "danger", t("ui.destructive"), Style.DESTRUCTIVE, "delete"),
    )
    kb.row(_demo(registry, "neutral", t("ui.neutral"), Style.NEUTRAL, "file"))

    # The slot management rows: [preview/title] [edit ✏️] [delete 🗑]
    for slot_obj in rows:
        kb.row(
            _demo(
                registry,
                "peek",
                slot_obj.title,
                Style.PRIMARY,
                slot_obj.slot,
                target=slot_obj.slot,
            ),
            _demo(
                registry,
                "edit",
                t("ui.edit"),
                Style.CONFIRM,
                "edit",
                target=slot_obj.slot,
            ),
            _demo(
                registry,
                "del",
                t("ui.delete"),
                Style.DESTRUCTIVE,
                "delete",
                target=slot_obj.slot,
            ),
        )

    pager = Page[object](items=[], index=index, total_items=total, per_page=DEFAULT_PER_PAGE)
    kb.row(*nav_row(pager, callback_for_page=_page_cb, registry=registry))

    kb.row(_demo(registry, "toggle", t("ui.toggle_premium"), Style.DESTRUCTIVE, "emoji"))
    kb.row(
        _demo(registry, "refresh", t("common.refresh"), Style.CONFIRM, "refresh"),
        inline_button(
            t("common.back"),
            callback_data=MenuCB(target="root").pack(),
            style=Style.PRIMARY,
            emoji_slot="back",
            registry=registry,
        ),
    )

    return body, kb.as_markup()


async def _show(query: CallbackQuery, session: AsyncSession, index: int) -> None:
    body, kb = await render(session, index)
    if isinstance(query.message, Message):
        await query.message.edit_text(body, reply_markup=kb)


@router.message(F.text.regexp(r"^/ui(@\w+)?$"), IsAdmin())
async def cmd_ui(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    body, kb = await render(session, 0)
    await message.answer(body, reply_markup=kb)


@router.callback_query(MenuCB.filter(F.target == "uidemo"), IsAdmin())
async def menu_entry(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await _show(query, session, 0)
    await query.answer()


@router.callback_query(PageCB.filter(F.scope == SCOPE))
async def turn_page(query: CallbackQuery, callback_data: PageCB, session: AsyncSession) -> None:
    await _show(query, session, callback_data.index)
    await query.answer()


@router.callback_query(DemoCB.filter(F.action == "toggle"))
async def toggle_premium(query: CallbackQuery, session: AsyncSession) -> None:
    new_value = await get_settings_store().toggle(
        PREMIUM_FLAG, updated_by=query.from_user.id if query.from_user else None
    )
    await get_emoji_registry().invalidate()
    await _show(query, session, 0)
    await query.answer(t("ui.premium_now_on") if new_value else t("ui.premium_now_off"))


@router.callback_query(DemoCB.filter(F.action == "refresh"))
async def refresh(query: CallbackQuery, session: AsyncSession) -> None:
    await get_emoji_registry().invalidate()
    await _show(query, session, 0)
    await query.answer(t("common.done"))


# ---------------------------------------------------------------------------
# Emoji Slot Editing
# ---------------------------------------------------------------------------


@router.callback_query(DemoCB.filter(F.action == "edit"), IsAdmin())
async def start_edit_slot(
    query: CallbackQuery, callback_data: DemoCB, session: AsyncSession, state: FSMContext
) -> None:
    slot_name = callback_data.target
    repo = EmojiRepo(session)
    slot_obj = await repo.get(slot_name)
    if slot_obj is None:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    registry = get_emoji_registry()
    current_display = registry.text(slot_name)

    await state.set_state(EmojiEditSG.waiting_for_emoji)
    await state.update_data(editing_slot=slot_name)

    cancel_kb = (
        KB(registry)
        .button(
            t("common.cancel"),
            callback_data=DemoCB(action="cancel_edit", slot="error").pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
        )
        .as_markup()
    )

    msg_text = t(
        "ui.prompt_edit",
        slot=slot_name,
        title=slot_obj.title,
        current=current_display,
    )
    if isinstance(query.message, Message):
        await query.message.edit_text(msg_text, reply_markup=cancel_kb)
    await query.answer()


@router.callback_query(DemoCB.filter(F.action == "cancel_edit"))
async def cancel_edit_slot(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await _show(query, session, 0)
    await query.answer(t("ui.edit_cancelled"))


@router.message(EmojiEditSG.waiting_for_emoji, IsAdmin())
async def process_new_emoji(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    slot_name = data.get("editing_slot")
    if not slot_name:
        await state.clear()
        return

    repo = EmojiRepo(session)
    slot_obj = await repo.get(slot_name)
    if slot_obj is None:
        await state.clear()
        await message.answer(t("common.stale_button"))
        return

    admin_id = message.from_user.id if message.from_user else None
    custom_emoji_id: str | None = None

    # Check for custom emoji entity first
    if message.entities:
        for entity in message.entities:
            if entity.type == "custom_emoji" and entity.custom_emoji_id:
                custom_emoji_id = entity.custom_emoji_id
                break

    if custom_emoji_id:
        # Set premium custom emoji ID
        await repo.set_custom_emoji(slot_name, custom_emoji_id, updated_by=admin_id)
        await get_emoji_registry().invalidate()
        await state.clear()

        body, kb = await render(session, 0)
        await message.answer(f"{t('ui.edit_success', slot=slot_name)}\n\n{body}", reply_markup=kb)
        return

    # If no custom emoji, check if text is a valid unicode emoji
    text = message.text or message.caption or ""
    if looks_like_unicode_emoji(text):
        new_fallback = text.strip()
        slot_obj.fallback = new_fallback
        slot_obj.custom_emoji_id = None
        slot_obj.updated_by = admin_id
        await get_emoji_registry().invalidate()
        await state.clear()

        body, kb = await render(session, 0)
        await message.answer(f"{t('ui.edit_success', slot=slot_name)}\n\n{body}", reply_markup=kb)
        return

    # Invalid input
    await message.answer(t("ui.edit_invalid"))


@router.callback_query(DemoCB.filter(F.action == "del"), IsAdmin())
async def delete_custom_emoji_slot(
    query: CallbackQuery, callback_data: DemoCB, session: AsyncSession
) -> None:
    slot_name = callback_data.target
    repo = EmojiRepo(session)
    admin_id = query.from_user.id if query.from_user else None

    if await repo.set_custom_emoji(slot_name, None, updated_by=admin_id):
        await get_emoji_registry().invalidate()
        await _show(query, session, 0)
        await query.answer(t("ui.delete_success", slot=slot_name))
    else:
        await query.answer(t("common.stale_button"), show_alert=True)


@router.callback_query(DemoCB.filter())
async def demo_tapped(query: CallbackQuery, callback_data: DemoCB) -> None:
    """Every other showcase button reports its own label back."""
    await query.answer(t("ui.tapped", label=_label_of(query, callback_data.pack())))


def _label_of(query: CallbackQuery, data: str) -> str:
    if not isinstance(query.message, Message) or query.message.reply_markup is None:
        return data
    for row in query.message.reply_markup.inline_keyboard:
        for button in row:
            if button.callback_data == data:
                return button.text
    return data


feature = register(
    Feature(
        name="uidemo",
        router=router,
        menu_label_key="ui.menu_entry",
        menu_emoji_slot="emoji",
        menu_style=Style.CONFIRM,
        menu_admin_only=True,
        order=900,
    )
)
