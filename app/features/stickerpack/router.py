"""Sticker pack builder feature router."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from aiogram import Bot, F, Router, html
from aiogram.enums import ChatType
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import MenuCB, PageCB
from app.config import Settings
from app.core.logging import get_logger
from app.core.quota import check_daily_quota
from app.db.repo import ConversionRepo, StickerSetRepo
from app.features.registry import Feature, register
from app.features.stickerpack.service import (
    add_sticker_to_user_set,
    create_user_sticker_set,
    make_pack_name,
    make_pack_title,
)
from app.media.fit import fit_static_webp, fit_video_sticker
from app.media.kinds import MediaKind, resolve_panel_media
from app.media.probe import probe
from app.ui.emoji import EmojiRegistry, em, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.paginate import DEFAULT_PER_PAGE, Page, clamp_page, nav_row
from app.ui.styles import Style
from app.ui.texts import t

log = get_logger(__name__)

router = Router(name="stickerpack")

EMOJI_PATTERN = re.compile(
    r"(?:"
    r"[\U0001F1E6-\U0001F1FF]{2}|"
    r"[\U0001F600-\U0001F64F]|"
    r"[\U0001F300-\U0001F5FF]|"
    r"[\U0001F680-\U0001F6FF]|"
    r"[\U0001F700-\U0001F77F]|"
    r"[\U0001F780-\U0001F7FF]|"
    r"[\U0001F800-\U0001F8FF]|"
    r"[\U0001F900-\U0001F9FF]|"
    r"[\U0001FA00-\U0001FA6F]|"
    r"[\U0001FA70-\U0001FAFF]|"
    r"[\u2600-\u26FF]|"
    r"[\u2700-\u27BF]"
    r")(?:[\uFE0E\uFE0F])?"
    r"(?:[\U0001F3FB-\U0001F3FF])?"
    r"(?:(?:\u200D)(?:"
    r"[\U0001F600-\U0001F64F]|[\U0001F300-\U0001F5FF]|[\U0001F680-\U0001F6FF]|"
    r"[\U0001F900-\U0001F9FF]|[\U0001FA70-\U0001FAFF]|[\u2600-\u26FF]|[\u2700-\u27BF]"
    r")(?:[\uFE0E\uFE0F])?(?:[\U0001F3FB-\U0001F3FF])?)*",
    re.UNICODE,
)

SCOPE = "spack_list"


class StickerPackCB(CallbackData, prefix="spack"):
    action: str  # choose, create, select, view, delete, confirm_delete
    pack_name: str = ""
    page: int = 0


class PackFSM(StatesGroup):
    waiting_for_title = State()
    waiting_for_emoji = State()


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


@router.callback_query(F.data == "spack_action:start")
async def start_pack_flow(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    if query.message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        await query.answer(
            f"{em('warning')} مدیریت و ساخت استیکرپک نیازمند ورود متن و ایموجی است؛ "
            "لطفاً در پی‌وی ربات استفاده کنید.",
            show_alert=True,
        )
        return

    resolved = await resolve_panel_media(query)

    if not resolved:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    kind, file_id, file_size, _reply_to_id = resolved
    user_id = query.from_user.id

    await state.update_data(
        file_id=file_id,
        kind=kind.value,
        file_size=file_size,
    )

    repo = StickerSetRepo(session)
    user_sets = await repo.get_user_sets(user_id)

    registry = await ready_registry()
    kb = KB(registry)

    for s_set in user_sets:
        kb.row(
            inline_button(
                f"{s_set.title} ({s_set.stickers_count})",
                callback_data=StickerPackCB(action="select", pack_name=s_set.name).pack(),
                style=Style.PRIMARY,
                emoji_slot="pack",
                registry=registry,
            )
        )

    kb.row(
        inline_button(
            "ساخت پک جدید",
            callback_data=StickerPackCB(action="create").pack(),
            style=Style.CONFIRM,
            emoji_slot="add",
            registry=registry,
        )
    )
    kb.row(
        inline_button(
            t("common.cancel"),
            callback_data="menu:root",
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        )
    )

    prompt_sp_text = (
        f"{registry.text('pack')} <b>اضافه کردن به استیکرپک شخصی</b>\n\n"
        "یک پک از لیست زیر انتخاب کن یا یک پک جدید بساز:"
    )
    await query.message.edit_text(
        prompt_sp_text,
        reply_markup=kb.as_markup(),
    )
    await query.answer()


@router.callback_query(StickerPackCB.filter(F.action == "create"))
async def prompt_create_pack(query: CallbackQuery, state: FSMContext) -> None:
    if query.message and query.message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        await query.answer(
            f"{em('warning')} ساخت استیکرپک در گروه پشتیبانی نمی‌شود؛ "
            "لطفاً در پیوی ربات استفاده کنید.",
            show_alert=True,
        )
        return

    registry = await ready_registry()
    await state.set_state(PackFSM.waiting_for_title)

    kb = (
        KB(registry)
        .button(
            t("common.cancel"),
            callback_data="menu:root",
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
        )
        .as_markup()
    )

    if isinstance(query.message, Message):
        await query.message.edit_text(
            "<b>نام/عنوان استیکرپک جدیدت رو بفرست:</b>\nمثال: <code>استیکرهای من</code>",
            reply_markup=kb,
        )
    await query.answer()


@router.message(F.chat.type == ChatType.PRIVATE, PackFSM.waiting_for_title)
async def process_pack_title(message: Message, state: FSMContext, settings: Settings) -> None:
    title = (message.text or "").strip()
    if title.startswith("/"):
        return
    if not title or len(title) > 64:
        await message.reply("لطفاً یک عنوان معتبر (حداکثر ۶۴ کاراکتر) بفرست.")
        return

    user_id = message.from_user.id if message.from_user else 0
    # Generate legal unique pack name u{user_id}_{short_hex}_by_{bot_username}
    pack_name = make_pack_name(user_id, title, settings.bot_username)
    # Brand the title
    branded_title = make_pack_title(title, settings.bot_username)
    await state.update_data(pack_name=pack_name, pack_title=branded_title, is_new_pack=True)

    await state.set_state(PackFSM.waiting_for_emoji)
    await message.reply(
        f"عالی! عنوان پک <code>{html.quote(branded_title)}</code> ثبت شد.\n\n"
        f"حالا یک <b>ایموجی مرتبط</b> برای این استیکر بفرست "
        f"(مثلاً {em('emoji')} یا {em('sticker')}):"
    )


@router.callback_query(StickerPackCB.filter(F.action == "select"))
async def select_existing_pack(
    query: CallbackQuery, callback_data: StickerPackCB, state: FSMContext
) -> None:
    await state.update_data(pack_name=callback_data.pack_name, is_new_pack=False)
    await state.set_state(PackFSM.waiting_for_emoji)

    if isinstance(query.message, Message):
        await query.message.edit_text(
            f"حالا یک <b>ایموجی مرتبط</b> برای این استیکر بفرست "
            f"(مثلاً {em('emoji')} یا {em('sticker')}):"
        )
    await query.answer()


@router.message(F.chat.type == ChatType.PRIVATE, PackFSM.waiting_for_emoji)
async def process_pack_emoji_and_build(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    raw_text = (message.text or "").strip()
    if raw_text.startswith("/"):
        return
    match = EMOJI_PATTERN.search(raw_text)
    emoji = match.group(0) if match else "🎨"
    data = await state.get_data()
    await state.clear()

    file_id = data.get("file_id")
    kind_val = data.get("kind", "photo")
    pack_name = data.get("pack_name")
    pack_title = data.get("pack_title", "My Sticker Pack")
    is_new_pack = bool(data.get("is_new_pack", False))
    user_id = message.from_user.id if message.from_user else 0

    if not file_id or not pack_name:
        await message.reply(t("common.stale_button"))
        return

    if not await check_daily_quota(session, user_id, message):
        return

    status_msg = await message.reply("<b>⏳ در حال افزودن استیکر به پک شما…</b>")

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir:
        work_dir = Path(tmp_dir)
        src_file = work_dir / "src_media"
        out_file = work_dir / "sticker_output"

        try:
            await bot.download(file_id, destination=src_file)
            info = await probe(src_file)

            sticker_format = "static"
            if kind_val in (
                MediaKind.VIDEO.value,
                MediaKind.ANIMATION.value,
                MediaKind.STICKER_VIDEO.value,
            ):
                out_file = work_dir / "sticker_output.webm"
                await fit_video_sticker(src_file, out_file, info)
                sticker_format = "video"
            else:
                out_file = work_dir / "sticker_output.webp"
                await fit_static_webp(src_file, out_file, info)
                sticker_format = "static"

            repo = StickerSetRepo(session)

            if is_new_pack:
                await create_user_sticker_set(
                    bot=bot,
                    user_id=user_id,
                    pack_name=pack_name,
                    title=pack_title,
                    sticker_path=out_file,
                    emoji=emoji,
                    sticker_format=sticker_format,
                )
                await repo.create_set(
                    telegram_id=user_id,
                    name=pack_name,
                    title=pack_title,
                )
            else:
                await add_sticker_to_user_set(
                    bot=bot,
                    user_id=user_id,
                    pack_name=pack_name,
                    sticker_path=out_file,
                    emoji=emoji,
                    sticker_format=sticker_format,
                )
                await repo.increment_count(pack_name)

            await session.commit()

            try:
                out_bytes = out_file.stat().st_size
            except OSError:
                out_bytes = 0
            await ConversionRepo(session).log_conversion(
                telegram_id=user_id,
                source_kind=kind_val if isinstance(kind_val, str) else str(kind_val),
                target_id="sticker_pack",
                in_bytes=src_file.stat().st_size if src_file.exists() else 0,
                out_bytes=out_bytes,
                duration_ms=0,
                status="success",
            )

            try:
                await status_msg.delete()
            except Exception:
                pass

            registry = await ready_registry()
            pack_url = f"https://t.me/addstickers/{pack_name}"
            kb = (
                KB(registry)
                .button(
                    "📦 مشاهده و افزودن استیکرپک",
                    url=pack_url,
                    style=Style.CONFIRM,
                    emoji_slot="sticker",
                )
                .as_markup()
            )

            await message.reply(
                f"🎉 <b>استیکر با موفقیت به پک اضافه شد!</b>\n\n"
                f"نام پک: <code>{html.quote(pack_title)}</code>\n"
                f"لینک مستقیم: {pack_url}",
                reply_markup=kb,
            )

        except Exception as exc:
            log.error("sticker_pack_flow_error", error=str(exc))
            await status_msg.edit_text(f"❌ خطا در ساخت استیکرپک: {exc}")


# ---------------------------------------------------------------------------
# Sticker Pack Management UI
# ---------------------------------------------------------------------------


def _page_cb(index: int) -> str:
    return PageCB(scope=SCOPE, index=index).pack()


async def render_pack_list(session: AsyncSession, user_id: int, page_index: int) -> tuple[str, KB]:
    repo = StickerSetRepo(session)
    user_sets = await repo.get_user_sets(user_id)
    total = len(user_sets)
    index = clamp_page(page_index, total, DEFAULT_PER_PAGE)

    registry = await ready_registry()
    kb = KB(registry)

    if not user_sets:
        text = f"{t('spack.title')}\n\n{t('spack.empty')}"
        kb.row(
            inline_button(
                t("common.back"),
                callback_data=MenuCB(target="root").pack(),
                style=Style.PRIMARY,
                emoji_slot="back",
                registry=registry,
            )
        )
        return text, kb

    start = index * DEFAULT_PER_PAGE
    page_items = user_sets[start : start + DEFAULT_PER_PAGE]

    for s_set in page_items:
        label = t("spack.view", title=s_set.title, count=s_set.stickers_count)
        kb.row(
            inline_button(
                label,
                callback_data=StickerPackCB(action="view", pack_name=s_set.name, page=index).pack(),
                style=Style.PRIMARY,
                registry=registry,
            )
        )

    pager = Page[object](
        items=list(page_items), index=index, total_items=total, per_page=DEFAULT_PER_PAGE
    )
    kb.row(*nav_row(pager, callback_for_page=_page_cb, registry=registry))

    kb.row(
        inline_button(
            t("common.back"),
            callback_data=MenuCB(target="root").pack(),
            style=Style.PRIMARY,
            emoji_slot="back",
            registry=registry,
        )
    )

    text = f"{t('spack.title')}\n\nیک استیکرپک برای مشاهده یا مدیریت انتخاب کنید:"
    return text, kb


@router.message(F.text.regexp(r"^/packs(@\w+)?$"))
async def cmd_packs(message: Message, session: AsyncSession) -> None:
    user_id = message.from_user.id if message.from_user else 0
    text, kb = await render_pack_list(session, user_id, 0)
    await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(MenuCB.filter(F.target == "stickerpack"))
async def menu_stickerpack(query: CallbackQuery, session: AsyncSession) -> None:
    user_id = query.from_user.id
    text, kb = await render_pack_list(session, user_id, 0)
    if isinstance(query.message, Message):
        await query.message.edit_text(text, reply_markup=kb.as_markup())
    await query.answer()


@router.callback_query(PageCB.filter(F.scope == SCOPE))
async def turn_pack_page(
    query: CallbackQuery, callback_data: PageCB, session: AsyncSession
) -> None:
    user_id = query.from_user.id
    text, kb = await render_pack_list(session, user_id, callback_data.index)
    if isinstance(query.message, Message):
        await query.message.edit_text(text, reply_markup=kb.as_markup())
    await query.answer()


@router.callback_query(StickerPackCB.filter(F.action == "view"))
async def view_pack_detail(
    query: CallbackQuery, callback_data: StickerPackCB, session: AsyncSession
) -> None:
    repo = StickerSetRepo(session)
    s_set = await repo.get_by_name(callback_data.pack_name)
    if not s_set:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    registry = await ready_registry()
    kb = KB(registry)

    pack_url = f"https://t.me/addstickers/{s_set.name}"
    kb.row(
        inline_button(
            "افزودن به تلگرام",
            url=pack_url,
            style=Style.CONFIRM,
            emoji_slot="pack",
            registry=registry,
        )
    )
    kb.row(
        inline_button(
            t("spack.delete"),
            callback_data=StickerPackCB(
                action="confirm_delete", pack_name=s_set.name, page=callback_data.page
            ).pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="delete",
            registry=registry,
        )
    )
    kb.row(
        inline_button(
            t("common.back"),
            callback_data=PageCB(scope=SCOPE, index=callback_data.page).pack(),
            style=Style.PRIMARY,
            emoji_slot="back",
            registry=registry,
        )
    )

    text = (
        f"{registry.text('pack')} <b>{s_set.title}</b>\n\n"
        f"{registry.text('bullet')} تعداد استیکر: <code>{s_set.stickers_count}</code>\n"
        f"{registry.text('bullet')} نام فنی: <code>{s_set.name}</code>\n"
        f"{registry.text('bullet')} لینک: {pack_url}"
    )

    if isinstance(query.message, Message):
        await query.message.edit_text(text, reply_markup=kb.as_markup())
    await query.answer()


@router.callback_query(StickerPackCB.filter(F.action == "confirm_delete"))
async def confirm_delete_pack(
    query: CallbackQuery, callback_data: StickerPackCB, session: AsyncSession
) -> None:
    repo = StickerSetRepo(session)
    s_set = await repo.get_by_name(callback_data.pack_name)
    if not s_set:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    registry = await ready_registry()
    kb = KB(registry)

    kb.row(
        inline_button(
            "بله، حذف شود",
            callback_data=StickerPackCB(
                action="delete", pack_name=s_set.name, page=callback_data.page
            ).pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="ok",
            registry=registry,
        ),
        inline_button(
            "انصراف",
            callback_data=StickerPackCB(
                action="view", pack_name=s_set.name, page=callback_data.page
            ).pack(),
            style=Style.PRIMARY,
            emoji_slot="error",
            registry=registry,
        ),
    )

    text = t("spack.delete_confirm", title=s_set.title)
    if isinstance(query.message, Message):
        await query.message.edit_text(text, reply_markup=kb.as_markup())
    await query.answer()


@router.callback_query(StickerPackCB.filter(F.action == "delete"))
async def delete_pack(
    query: CallbackQuery, callback_data: StickerPackCB, session: AsyncSession
) -> None:
    user_id = query.from_user.id
    repo = StickerSetRepo(session)
    await repo.delete_set(callback_data.pack_name)
    await session.commit()

    await query.answer(t("spack.deleted"), show_alert=True)

    text, kb = await render_pack_list(session, user_id, callback_data.page)
    if isinstance(query.message, Message):
        await query.message.edit_text(text, reply_markup=kb.as_markup())


feature = register(
    Feature(
        name="stickerpack",
        router=router,
        menu_label_key="menu.stickerpack",
        menu_emoji_slot="sticker",
        menu_style=Style.PRIMARY,
        order=150,
    )
)
