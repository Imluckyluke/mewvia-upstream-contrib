"""Text overlay feature router and FSM state machine."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from aiogram import Bot, F, Router, html
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import TextOverlayCB
from app.config import Settings
from app.core.logging import get_logger
from app.core.quota import check_daily_quota
from app.db.repo import ConversionRepo
from app.media.kinds import MediaKind, resolve_panel_media
from app.media.text_overlay import COLOR_MAP, add_text_to_image, add_text_to_video
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style
from app.ui.texts import t

log = get_logger(__name__)

router = Router(name="text_overlay")


class TextOverlayFSM(StatesGroup):
    waiting_for_text = State()
    options_menu = State()


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


def build_options_keyboard(
    registry: EmojiRegistry,
    current_font: str = "vazir",
    current_color: str = "white",
    current_pos: str = "bottom",
    current_size: str = "medium",
) -> KB:
    kb = KB(registry)

    # Font row
    font_labels = {
        "vazir": "Vazir" if current_font != "vazir" else "✓ Vazir",
        "lalezar": "Lalezar" if current_font != "lalezar" else "✓ Lalezar",
        "samim": "Samim" if current_font != "samim" else "✓ Samim",
        "sahel": "Sahel" if current_font != "sahel" else "✓ Sahel",
    }

    kb.row(
        inline_button(
            font_labels["vazir"],
            callback_data=TextOverlayCB(
                action="set_font",
                font="vazir",
                color=current_color,
                pos=current_pos,
                size=current_size,
            ).pack(),
            style=Style.PRIMARY if current_font == "vazir" else Style.NEUTRAL,
        ),
        inline_button(
            font_labels["lalezar"],
            callback_data=TextOverlayCB(
                action="set_font",
                font="lalezar",
                color=current_color,
                pos=current_pos,
                size=current_size,
            ).pack(),
            style=Style.PRIMARY if current_font == "lalezar" else Style.NEUTRAL,
        ),
        inline_button(
            font_labels["samim"],
            callback_data=TextOverlayCB(
                action="set_font",
                font="samim",
                color=current_color,
                pos=current_pos,
                size=current_size,
            ).pack(),
            style=Style.PRIMARY if current_font == "samim" else Style.NEUTRAL,
        ),
        inline_button(
            font_labels["sahel"],
            callback_data=TextOverlayCB(
                action="set_font",
                font="sahel",
                color=current_color,
                pos=current_pos,
                size=current_size,
            ).pack(),
            style=Style.PRIMARY if current_font == "sahel" else Style.NEUTRAL,
        ),
    )

    # Size row (Small, Medium, Large, XLarge)
    size_labels = {
        "small": "کوچک" if current_size != "small" else "✓ کوچک",
        "medium": "متوسط" if current_size != "medium" else "✓ متوسط",
        "large": "بزرگ" if current_size != "large" else "✓ بزرگ",
        "xlarge": "خیلی بزرگ" if current_size != "xlarge" else "✓ خیلی بزرگ",
    }
    kb.row(
        inline_button(
            size_labels["small"],
            callback_data=TextOverlayCB(
                action="set_size",
                font=current_font,
                color=current_color,
                pos=current_pos,
                size="small",
            ).pack(),
            style=Style.PRIMARY if current_size == "small" else Style.NEUTRAL,
        ),
        inline_button(
            size_labels["medium"],
            callback_data=TextOverlayCB(
                action="set_size",
                font=current_font,
                color=current_color,
                pos=current_pos,
                size="medium",
            ).pack(),
            style=Style.PRIMARY if current_size == "medium" else Style.NEUTRAL,
        ),
        inline_button(
            size_labels["large"],
            callback_data=TextOverlayCB(
                action="set_size",
                font=current_font,
                color=current_color,
                pos=current_pos,
                size="large",
            ).pack(),
            style=Style.PRIMARY if current_size == "large" else Style.NEUTRAL,
        ),
        inline_button(
            size_labels["xlarge"],
            callback_data=TextOverlayCB(
                action="set_size",
                font=current_font,
                color=current_color,
                pos=current_pos,
                size="xlarge",
            ).pack(),
            style=Style.PRIMARY if current_size == "xlarge" else Style.NEUTRAL,
        ),
    )

    # Color row (White, Yellow, Red, Green, Blue, Black)
    color_buttons = []
    for c_key, c_spec in COLOR_MAP.items():
        lbl = c_spec.name if current_color != c_key else f"✓ {c_spec.name}"
        color_buttons.append(
            inline_button(
                lbl,
                callback_data=TextOverlayCB(
                    action="set_color",
                    font=current_font,
                    color=c_key,
                    pos=current_pos,
                    size=current_size,
                ).pack(),
                style=Style.CONFIRM if current_color == c_key else Style.NEUTRAL,
                emoji_slot=c_spec.emoji_slot,
                registry=registry,
            )
        )
    kb.row(*color_buttons[:3])
    kb.row(*color_buttons[3:])

    # Position row (Top, Center, Bottom)
    pos_labels = {
        "top": "بالا" if current_pos != "top" else "✓ بالا",
        "center": "وسط" if current_pos != "center" else "✓ وسط",
        "bottom": "پایین" if current_pos != "bottom" else "✓ پایین",
    }
    kb.row(
        inline_button(
            pos_labels["top"],
            callback_data=TextOverlayCB(
                action="set_pos",
                font=current_font,
                color=current_color,
                pos="top",
                size=current_size,
            ).pack(),
            style=Style.PRIMARY if current_pos == "top" else Style.NEUTRAL,
            emoji_slot="top",
            registry=registry,
        ),
        inline_button(
            pos_labels["center"],
            callback_data=TextOverlayCB(
                action="set_pos",
                font=current_font,
                color=current_color,
                pos="center",
                size=current_size,
            ).pack(),
            style=Style.PRIMARY if current_pos == "center" else Style.NEUTRAL,
            emoji_slot="center",
            registry=registry,
        ),
        inline_button(
            pos_labels["bottom"],
            callback_data=TextOverlayCB(
                action="set_pos",
                font=current_font,
                color=current_color,
                pos="bottom",
                size=current_size,
            ).pack(),
            style=Style.PRIMARY if current_pos == "bottom" else Style.NEUTRAL,
            emoji_slot="bottom",
            registry=registry,
        ),
    )

    # Action row (Render, Cancel)
    kb.row(
        inline_button(
            t("text_overlay.render_btn"),
            callback_data=TextOverlayCB(
                action="render",
                font=current_font,
                color=current_color,
                pos=current_pos,
                size=current_size,
            ).pack(),
            style=Style.CONFIRM,
            emoji_slot="confirm",
            registry=registry,
        ),
        inline_button(
            t("common.cancel"),
            callback_data=TextOverlayCB(action="cancel").pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        ),
    )

    return kb


@router.callback_query(TextOverlayCB.filter(F.action == "start"))
async def start_text_overlay(
    query: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    if query.message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        await query.answer(
            "⚠️ افزودن متن نیازمند ورود متن است؛ لطفاً این کار را در پی‌وی ربات انجام دهید.",
            show_alert=True,
        )
        return

    msg = query.message
    resolved = await resolve_panel_media(query)

    if not resolved:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    kind, file_id, file_size, _reply_to_id = resolved

    # Fresh flow entry: drop any leftover batch-context keys from a previous flow.
    await state.clear()
    await state.update_data(
        file_id=file_id,
        kind=kind.value,
        file_size=file_size,
        font="vazir",
        color="white",
        pos="bottom",
        size="medium",
    )
    await state.set_state(TextOverlayFSM.waiting_for_text)

    await query.answer()
    await msg.edit_text(t("text_overlay.prompt_text"))


@router.message(F.chat.type == ChatType.PRIVATE, TextOverlayFSM.waiting_for_text, F.text)
async def process_user_text_input(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    user_text = (message.text or "").strip()
    if user_text.startswith("/"):
        return
    if not user_text:
        await message.reply("لطفاً یک متن معتبر وارد کنید.")
        return

    await state.update_data(overlay_text=user_text)
    await state.set_state(TextOverlayFSM.options_menu)

    data = await state.get_data()
    registry = await ready_registry()

    font_name = t(f"text_overlay.font_{data.get('font', 'vazir')}")
    size_label = t(f"text_overlay.size_{data.get('size', 'medium')}")
    color_spec = COLOR_MAP.get(data.get("color", "white"), COLOR_MAP["white"])
    color_label = registry.label(color_spec.emoji_slot, color_spec.name)
    pos_label = registry.label(
        data.get("pos", "bottom"), t(f"text_overlay.pos_{data.get('pos', 'bottom')}")
    )

    menu_text = t(
        "text_overlay.menu_title",
        text=html.quote(user_text),
        font_name=font_name,
        size_label=size_label,
        color_label=color_label,
        pos_label=pos_label,
    )

    # Batch mode: keep every interaction on the panel message itself, because only the
    # panel message has a record in the panel store (resolve_panel_media / opts).
    if data.get("batch_panel_chat_id") and data.get("batch_panel_msg_id"):
        kb = build_options_keyboard(
            registry,
            current_font=data.get("font", "vazir"),
            current_color=data.get("color", "white"),
            current_pos=data.get("pos", "bottom"),
            current_size=data.get("size", "medium"),
        )
        from app.callbacks import BatchCB

        kb.row(
            inline_button(
                "بازگشت",
                callback_data=BatchCB(action="menu").pack(),
                style=Style.DESTRUCTIVE,
                emoji_slot="back",
                registry=registry,
            )
        )
        await bot.edit_message_text(
            menu_text,
            chat_id=data["batch_panel_chat_id"],
            message_id=data["batch_panel_msg_id"],
            reply_markup=kb.as_markup(),
        )
        return

    kb = build_options_keyboard(
        registry,
        current_font=data.get("font", "vazir"),
        current_color=data.get("color", "white"),
        current_pos=data.get("pos", "bottom"),
        current_size=data.get("size", "medium"),
    )

    await message.answer(menu_text, reply_markup=kb.as_markup())


@router.callback_query(
    TextOverlayFSM.options_menu,
    TextOverlayCB.filter(F.action.in_({"set_font", "set_color", "set_pos", "set_size"})),
)
async def update_overlay_options(
    query: CallbackQuery,
    callback_data: TextOverlayCB,
    state: FSMContext,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer()
        return

    await state.update_data(
        font=callback_data.font,
        color=callback_data.color,
        pos=callback_data.pos,
        size=callback_data.size,
    )

    data = await state.get_data()
    user_text = data.get("overlay_text", "")
    registry = await ready_registry()

    font_name = t(f"text_overlay.font_{callback_data.font}")
    size_label = t(f"text_overlay.size_{callback_data.size}")
    color_spec = COLOR_MAP.get(callback_data.color, COLOR_MAP["white"])
    color_label = registry.label(color_spec.emoji_slot, color_spec.name)
    pos_label = registry.label(callback_data.pos, t(f"text_overlay.pos_{callback_data.pos}"))

    menu_text = t(
        "text_overlay.menu_title",
        text=html.quote(user_text),
        font_name=font_name,
        size_label=size_label,
        color_label=color_label,
        pos_label=pos_label,
    )

    kb = build_options_keyboard(
        registry,
        current_font=callback_data.font,
        current_color=callback_data.color,
        current_pos=callback_data.pos,
        current_size=callback_data.size,
    )

    # In batch mode the panel must always offer a way back to the batch menu.
    if data.get("batch_panel_chat_id") and data.get("batch_panel_msg_id"):
        from app.callbacks import BatchCB

        kb.row(
            inline_button(
                "بازگشت",
                callback_data=BatchCB(action="menu").pack(),
                style=Style.DESTRUCTIVE,
                emoji_slot="back",
                registry=registry,
            )
        )

    await query.message.edit_text(menu_text, reply_markup=kb.as_markup())
    await query.answer()


@router.callback_query(TextOverlayFSM.options_menu, TextOverlayCB.filter(F.action == "cancel"))
async def cancel_text_overlay(
    query: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()
    batch_chat_id = data.get("batch_panel_chat_id")
    batch_msg_id = data.get("batch_panel_msg_id")
    await state.clear()
    await query.answer("لغو شد.")

    if batch_chat_id and batch_msg_id:
        # Return to the batch menu on the panel instead of leaving a dead end.
        from app.features.batch.router import render_batch_menu

        await render_batch_menu(bot, batch_chat_id, batch_msg_id)
        return

    if isinstance(query.message, Message):
        await query.message.edit_text("عملیات افزودن متن لغو شد.")


@router.callback_query(TextOverlayFSM.options_menu, TextOverlayCB.filter(F.action == "render"))
async def render_and_send_overlay(
    query: CallbackQuery,
    callback_data: TextOverlayCB,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer()
        return

    data = await state.get_data()

    # Check if we are in batch mode
    batch_chat_id = data.get("batch_panel_chat_id")
    batch_msg_id = data.get("batch_panel_msg_id")

    if batch_chat_id and batch_msg_id:
        from app.features.batch.router import render_batch_menu
        from app.middlewares.group_guard import update_panel_opts

        await update_panel_opts(
            batch_chat_id,
            batch_msg_id,
            text={
                "text": data.get("overlay_text", ""),
                "font": callback_data.font,
                "color": callback_data.color,
                "pos": callback_data.pos,
                "size": callback_data.size,
            },
        )
        await state.clear()

        # The flow lives on the panel message itself; only delete a stray prompt
        # message if for some reason the interaction happened on a different one.
        if isinstance(query.message, Message) and query.message.message_id != batch_msg_id:
            try:
                await query.message.delete()
            except Exception:
                pass

        await render_batch_menu(bot, batch_chat_id, batch_msg_id)
        await query.answer("متن با موفقیت ثبت شد.")
        return

    file_id = data.get("file_id")
    kind_str = data.get("kind")
    user_text = data.get("overlay_text")

    if not file_id or not kind_str or not user_text:
        await query.answer(t("common.stale_button"), show_alert=True)
        await state.clear()
        return

    user_id = query.from_user.id if query.from_user else 0
    if not await check_daily_quota(session, user_id, query):
        await state.clear()
        return

    await query.answer()
    status_msg = await query.message.edit_text(t("common.processing"))
    await state.clear()

    font_key = callback_data.font
    color_key = callback_data.color
    pos_key = callback_data.pos
    size_key = callback_data.size

    kind = MediaKind(kind_str)

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir_str:
        work_dir = Path(tmp_dir_str)
        is_video_media = kind in (
            MediaKind.VIDEO,
            MediaKind.VIDEO_NOTE,
            MediaKind.ANIMATION,
            MediaKind.DOCUMENT_VIDEO,
            MediaKind.STICKER_VIDEO,
        )

        in_ext = ".bin"
        if kind in (MediaKind.PHOTO, MediaKind.DOCUMENT_IMAGE):
            in_ext = ".png"
        elif kind in (
            MediaKind.VIDEO,
            MediaKind.ANIMATION,
            MediaKind.VIDEO_NOTE,
            MediaKind.DOCUMENT_VIDEO,
        ):
            in_ext = ".mp4"
        elif kind == MediaKind.STICKER_ANIMATED:
            in_ext = ".tgs"
        elif kind == MediaKind.STICKER_VIDEO:
            in_ext = ".webm"
        elif kind == MediaKind.STICKER_STATIC:
            in_ext = ".webp"

        out_ext = ".mp4" if is_video_media else ".png"
        if kind == MediaKind.STICKER_STATIC:
            out_ext = ".webp"
        elif kind == MediaKind.STICKER_VIDEO:
            out_ext = ".webm"

        src_file = work_dir / f"input{in_ext}"
        out_file = work_dir / f"output{out_ext}"

        try:
            await bot.download(file_id, destination=src_file)

            start_time = time.perf_counter()
            if is_video_media:
                await add_text_to_video(
                    src_file, out_file, user_text, font_key, color_key, pos_key, size_key
                )
            else:
                await add_text_to_image(
                    src_file, out_file, user_text, font_key, color_key, pos_key, size_key
                )
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)

            input_file_obj = FSInputFile(out_file)
            chat_id = query.message.chat.id

            if out_ext in (".webp", ".webm"):
                await bot.send_sticker(chat_id=chat_id, sticker=input_file_obj)
            elif out_ext == ".png":
                await bot.send_photo(chat_id=chat_id, photo=input_file_obj)
            else:
                await bot.send_document(chat_id=chat_id, document=input_file_obj)

            try:
                out_bytes = out_file.stat().st_size
            except OSError:
                out_bytes = 0
            await ConversionRepo(session).log_conversion(
                telegram_id=query.from_user.id if query.from_user else 0,
                source_kind=kind.value,
                target_id="text_overlay",
                in_bytes=src_file.stat().st_size if src_file.exists() else 0,
                out_bytes=out_bytes,
                duration_ms=elapsed_ms,
                status="success",
            )

            if isinstance(status_msg, Message):
                try:
                    await status_msg.delete()
                except Exception:
                    pass

        except Exception as exc:
            log.error("text_overlay_error", error=str(exc))
            if isinstance(status_msg, Message):
                await status_msg.edit_text("خطا در افزودن متن به فایل.")
