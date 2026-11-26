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
from app.ui.emoji import PREMIUM_FLAG, EmojiRegistry, em, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.labels import looks_like_unicode_emoji
from app.ui.paginate import DEFAULT_PER_PAGE, Page, clamp_page, nav_row
from app.ui.styles import Style
from app.ui.texts import t

router = Router(name="uidemo")

SCOPE = "uislots"

# Slots are seeded in functional blocks. Filtering by sort_order keeps the
# categories in sync with the seed file without duplicating 101 slot names.
# (key, label, first_order, last_order_inclusive)
CATEGORIES: tuple[tuple[str, str, int, int], ...] = (
    ("all", "همه", 0, 10**9),
    ("base", "عمومی", 0, 199),
    ("media", "مدیا و ویرایش", 200, 302),
    ("meta", "اطلاعات آهنگ", 303, 310),
    ("platform", "پلتفرمها", 311, 329),
    ("admin", "پنل ادمین", 330, 399),
    ("games", "بازیها", 400, 10**9),
)
_CATEGORY_KEYS = {row[0] for row in CATEGORIES}
DEFAULT_CATEGORY = "all"


def _category_slots(slots: list, category: str) -> list:
    """Filter live slot rows into a category, preserving display order."""
    row = next((c for c in CATEGORIES if c[0] == category), CATEGORIES[0])
    _key, _label, low, high = row
    return [slot for slot in slots if low <= slot.sort_order <= high]


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


async def render(
    session: AsyncSession, page_index: int, category: str = DEFAULT_CATEGORY
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the showcase & slot management page, optionally filtered."""
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()

    repo = EmojiRepo(session)
    all_slots = list(await repo.all())
    if category not in _CATEGORY_KEYS:
        category = DEFAULT_CATEGORY
    slots = _category_slots(all_slots, category)
    total = len(slots)
    index = clamp_page(page_index, total, DEFAULT_PER_PAGE)
    rows = slots[index * DEFAULT_PER_PAGE : (index + 1) * DEFAULT_PER_PAGE]
    premium_count = sum(1 for s in slots if s.is_premium)

    if registry.premium_enabled:
        premium_line = t(
            "ui.premium_on",
            count=fa_digits(str(premium_count)),
            total=fa_digits(str(total)),
        )
    else:
        premium_line = t("ui.premium_off")

    active_label = next(c[1] for c in CATEGORIES if c[0] == category)
    body = "\n\n".join(
        [
            t("ui.title"),
            f"{em('folder')} دسته: <b>{active_label}</b> — {fa_digits(str(total))} اسلات",
            t("ui.styles_note"),
            premium_line,
            registry.label("ok", t("ui.inline_emoji_note")),
        ]
    )

    kb = KB(registry)

    # Category filter — one tap to reach any section instead of paging
    # through 101 slots one screen at a time.
    for start in range(0, len(CATEGORIES), 3):
        kb.row(
            *[
                inline_button(
                    f"{label} ({fa_digits(str(len(_category_slots(all_slots, key))))})",
                    callback_data=DemoCB(action="cat", target=key).pack(),
                    style=Style.CONFIRM if key == category else Style.NEUTRAL,
                    registry=registry,
                )
                for key, label, _lo, _hi in CATEGORIES[start : start + 3]
            ]
        )

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


async def _show(
    query: CallbackQuery, session: AsyncSession, index: int, category: str = DEFAULT_CATEGORY
) -> None:
    body, kb = await render(session, index, category)
    if isinstance(query.message, Message):
        await query.message.edit_text(body, reply_markup=kb)


async def _saved_category(state: FSMContext) -> str:
    data = await state.get_data()
    category = data.get("ui_category", DEFAULT_CATEGORY)
    return category if category in _CATEGORY_KEYS else DEFAULT_CATEGORY


@router.message(F.text.regexp(r"^/ui(@\w+)?$"), IsAdmin())
async def cmd_ui(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(ui_category=DEFAULT_CATEGORY)
    body, kb = await render(session, 0, DEFAULT_CATEGORY)
    await message.answer(body, reply_markup=kb)


@router.callback_query(MenuCB.filter(F.target == "uidemo"), IsAdmin())
async def menu_entry(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(ui_category=DEFAULT_CATEGORY)
    await _show(query, session, 0, DEFAULT_CATEGORY)
    await query.answer()


@router.callback_query(DemoCB.filter(F.action == "cat"), IsAdmin())
async def pick_category(
    query: CallbackQuery, callback_data: DemoCB, session: AsyncSession, state: FSMContext
) -> None:
    category = callback_data.target if callback_data.target in _CATEGORY_KEYS else DEFAULT_CATEGORY
    await state.update_data(ui_category=category)
    await state.set_state(None)
    await _show(query, session, 0, category)
    await query.answer(f"دسته {category} انتخاب شد")


@router.callback_query(PageCB.filter(F.scope == SCOPE))
async def turn_page(
    query: CallbackQuery, callback_data: PageCB, session: AsyncSession, state: FSMContext
) -> None:
    await _show(query, session, callback_data.index, await _saved_category(state))
    await query.answer()


@router.callback_query(DemoCB.filter(F.action == "toggle"))
async def toggle_premium(
    query: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    new_value = await get_settings_store().toggle(
        PREMIUM_FLAG, updated_by=query.from_user.id if query.from_user else None
    )
    await get_emoji_registry().invalidate()
    await _show(query, session, 0, await _saved_category(state))
    await query.answer(t("ui.premium_now_on") if new_value else t("ui.premium_now_off"))


@router.callback_query(DemoCB.filter(F.action == "refresh"))
async def refresh(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await get_emoji_registry().invalidate()
    await _show(query, session, 0, await _saved_category(state))
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
    category = await _saved_category(state)
    await state.clear()
    await state.update_data(ui_category=category)
    await _show(query, session, 0, category)
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
    category = await _saved_category(state)
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
        await state.update_data(ui_category=category)

        body, kb = await render(session, 0, category)
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
        await state.update_data(ui_category=category)

        body, kb = await render(session, 0, category)
        await message.answer(f"{t('ui.edit_success', slot=slot_name)}\n\n{body}", reply_markup=kb)
        return

    # Invalid input
    await message.answer(t("ui.edit_invalid"))


@router.callback_query(DemoCB.filter(F.action == "del"), IsAdmin())
async def delete_custom_emoji_slot(
    query: CallbackQuery,
    callback_data: DemoCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    slot_name = callback_data.target
    repo = EmojiRepo(session)
    admin_id = query.from_user.id if query.from_user else None

    if await repo.set_custom_emoji(slot_name, None, updated_by=admin_id):
        await get_emoji_registry().invalidate()
        await _show(query, session, 0, await _saved_category(state))
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
