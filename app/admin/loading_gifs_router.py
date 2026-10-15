"""Loading GIFs management router for the admin panel.

Allows bot administrators to:
- View all configured loading animation GIFs
- Upload/send new GIFs, animations, short videos, or URLs
- Preview/test individual GIFs with live progress bar simulation
- Test random selection with a live progress bar demo
- Toggle GIF progress indicators on/off
- Delete unwanted GIFs from the rotation pool
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, F, Router, html
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.units import fa_digits
from app.filters import IsAdmin
from app.media.progress_gif import (
    add_loading_gif,
    delete_loading_gif,
    get_random_loading_gif,
    is_loading_gifs_enabled,
    list_loading_gifs,
    set_loading_gifs_enabled,
)
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.progress import format_progress_caption
from app.ui.styles import Style
from app.ui.texts import t

log = get_logger(__name__)

router = Router(name="admin_loading_gifs")


class LoadingGifAddSG(StatesGroup):
    waiting_for_gif = State()


async def ready_registry() -> EmojiRegistry:
    try:
        registry = get_emoji_registry()
        await registry.refresh_premium_flag()
        await registry.load()
        return registry
    except Exception:
        pass

    class _StandInRegistry:
        def custom_id(self, slot: str) -> str | None:
            return None

        def fallback(self, slot: str) -> str:
            return ""

        def slot(self, name: str) -> None:
            return None

        def text(self, slot: str) -> str:
            return "⏳" if slot == "clock" else "⚡"

        def is_premium_ready(self) -> bool:
            return False

        async def refresh_premium_flag(self) -> bool:
            return True

        async def load(self) -> dict[str, tuple[str | None, str]]:
            return {}

    return _StandInRegistry()  # type: ignore[return-value]


async def render_loading_gifs_dashboard(
    session: AsyncSession, registry: EmojiRegistry
) -> tuple[str, KB]:
    """Generate text and keyboard for the Loading GIFs management panel."""
    gifs = await list_loading_gifs(session)
    enabled = await is_loading_gifs_enabled(session)

    status_badge = "🟢 فعال" if enabled else "🔴 غیرفعال (حالت متنی)"
    count_fa = fa_digits(str(len(gifs)))

    lines = [
        f"{registry.text('video')} <b>مدیریت گیف‌های نوار پیشرفت و لودینگ</b>\n",
        f"{registry.text('bullet')} <b>وضعیت نمایش گیف:</b> {status_badge}",
        f"{registry.text('bullet')} <b>تعداد گیف‌های ثبت‌شده:</b> <code>{count_fa}</code> عدد\n",
        (
            "💡 <i>در هنگام دانلود، پردازش و تبدیل مدیا، ربات یکی از این گیف‌ها را "
            "به همراه نوار پیشرفت درصدی به‌صورت تصادفی نمایش می‌دهد.</i>"
        ),
    ]

    kb = KB(registry)

    for idx, item in enumerate(gifs, 1):
        g_id = item["id"]
        label = item.get("label", f"گیف {idx}")
        created = item.get("created_at", "—")
        btn_label = f"🎬 {label} ({fa_digits(created)})"
        kb.row(
            inline_button(
                btn_label,
                callback_data=f"lgif:view:{g_id}",
                style=Style.PRIMARY if enabled else Style.NEUTRAL,
                emoji_slot="video",
                registry=registry,
            )
        )
        kb.row(
            inline_button(
                "👁 تست و پیش‌نمایش",
                callback_data=f"lgif:test:{g_id}",
                style=Style.PRIMARY,
                emoji_slot="refresh",
                registry=registry,
            ),
            inline_button(
                "🗑 حذف",
                callback_data=f"lgif:del:{g_id}",
                style=Style.DESTRUCTIVE,
                emoji_slot="delete",
                registry=registry,
            ),
        )

    # Action buttons
    kb.row(
        inline_button(
            "➕ افزودن گیف جدید",
            callback_data="lgif:add",
            style=Style.CONFIRM,
            emoji_slot="add",
            registry=registry,
        ),
        inline_button(
            "🧪 تست تصادفی با نوار پیشرفت",
            callback_data="lgif:test_random",
            style=Style.PRIMARY,
            emoji_slot="lightning",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "⏸ توقف نمایش گیف" if enabled else "▶️ فعال‌سازی نمایش گیف",
            callback_data="lgif:toggle",
            style=Style.NEUTRAL if enabled else Style.CONFIRM,
            emoji_slot="on" if enabled else "off",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            t("common.refresh"),
            callback_data="lgif:list",
            style=Style.PRIMARY,
            emoji_slot="refresh",
            registry=registry,
        ),
        inline_button(
            t("common.back"),
            callback_data="menu:admin",
            style=Style.PRIMARY,
            emoji_slot="back",
            registry=registry,
        ),
    )

    return "\n".join(lines), kb


@router.message(F.text.regexp(r"^/(gifs|loading_gifs)(@\w+)?$"), IsAdmin())
async def cmd_loading_gifs(message: Message, session: AsyncSession) -> None:
    registry = await ready_registry()
    text, kb = await render_loading_gifs_dashboard(session, registry)
    await message.reply(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "admin_act:loading_gifs", IsAdmin())
@router.callback_query(F.data == "lgif:list", IsAdmin())
async def show_loading_gifs_panel(
    query: CallbackQuery, session: AsyncSession, state: FSMContext | None = None
) -> None:
    if state is not None:
        await state.clear()
    registry = await ready_registry()
    text, kb = await render_loading_gifs_dashboard(session, registry)
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text(text, reply_markup=kb.as_markup())
        except Exception:
            await query.message.answer(text, reply_markup=kb.as_markup())
    await query.answer()


@router.callback_query(F.data == "lgif:toggle", IsAdmin())
async def toggle_loading_gifs(query: CallbackQuery, session: AsyncSession) -> None:
    enabled = await is_loading_gifs_enabled(session)
    await set_loading_gifs_enabled(not enabled, session)
    await query.answer("نمایش گیف لودینگ فعال شد 🟢" if not enabled else "نمایش گیف غیرفعال شد 🔴")
    registry = await ready_registry()
    text, kb = await render_loading_gifs_dashboard(session, registry)
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text(text, reply_markup=kb.as_markup())
        except Exception:
            pass


@router.callback_query(F.data == "lgif:add", IsAdmin())
async def prompt_add_loading_gif(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(LoadingGifAddSG.waiting_for_gif)
    registry = await ready_registry()
    kb = KB(registry)
    kb.row(
        inline_button(
            "❌ لغو",
            callback_data="lgif:list",
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        )
    )

    prompt = (
        f"{registry.text('video')} <b>افزودن گیف جدید برای نوار پیشرفت:</b>\n\n"
        "لطفاً یک فایل <b>گیف (GIF)</b>، انیمیشن تلگرام یا ویدیوی کوتاه بفرستید.\n"
        "همچنین می‌توانید لینک اینترنتی یک فایل گیف/ویدیو (URL معتبر) را ارسال کنید.\n\n"
        "💡 <i>ربات در هنگام دانلود و پردازش مدیا، این گیف را به همراه نوار پیشرفت "
        "درصدی نمایش می‌دهد.</i>"
    )
    if isinstance(query.message, Message):
        await query.message.edit_text(prompt, reply_markup=kb.as_markup())
    await query.answer()


def extract_media_file_id(message: Message) -> tuple[str | None, str]:
    """Extract file_id and file_unique_id from any message or reply."""
    reply = getattr(message, "reply_to_message", None)
    target = reply if reply else message

    anim = getattr(target, "animation", None)
    if anim is not None:
        return anim.file_id, getattr(anim, "file_unique_id", "")
    vid = getattr(target, "video", None)
    if vid is not None:
        return vid.file_id, getattr(vid, "file_unique_id", "")
    vn = getattr(target, "video_note", None)
    if vn is not None:
        return vn.file_id, getattr(vn, "file_unique_id", "")
    doc = getattr(target, "document", None)
    if doc is not None:
        return doc.file_id, getattr(doc, "file_unique_id", "")
    st = getattr(target, "sticker", None)
    if st is not None and (getattr(st, "is_video", False) or getattr(st, "is_animated", False)):
        return st.file_id, getattr(st, "file_unique_id", "")

    text_content = getattr(target, "text", "") or getattr(target, "caption", "") or ""
    if text_content.startswith("http://") or text_content.startswith("https://"):
        return text_content.strip(), ""
    return None, ""


@router.message(
    F.text.regexp(r"^/(addgif|add_gif)(@\w+)?$") | F.caption.regexp(r"^/(addgif|add_gif)(@\w+)?$"),
    IsAdmin(),
)
async def cmd_add_gif_direct(message: Message, session: AsyncSession) -> None:
    file_id, file_unique_id = extract_media_file_id(message)
    if not file_id:
        await message.reply(
            "⚠️ لطفاً دستور /addgif را روی یک گیف ریپلای کنید\n"
            "یا همراه با فایل در کپشن بنویسید /addgif."
        )
        return

    current_gifs = await list_loading_gifs(session)
    label = f"گیف شماره {len(current_gifs) + 1}"

    await add_loading_gif(
        file_id=file_id,
        label=label,
        file_unique_id=file_unique_id,
        session=session,
    )

    registry = await ready_registry()
    _, kb = await render_loading_gifs_dashboard(session, registry)
    await message.reply(
        f"✅ <b>گیف جدید «{html.quote(label)}» با موفقیت ثبت شد!</b>",
        reply_markup=kb.as_markup(),
    )


@router.message(LoadingGifAddSG.waiting_for_gif, IsAdmin())
async def save_new_loading_gif(message: Message, state: FSMContext, session: AsyncSession) -> None:
    file_id, file_unique_id = extract_media_file_id(message)

    if not file_id:
        await message.reply(
            "⚠️ فایلی که فرستادید شناسایی نشد.\n"
            "لطفاً یک فایل انیمیشن/گیف تلگرام یا ویدیوی کوتاه بفرستید."
        )
        return

    await state.clear()
    current_gifs = await list_loading_gifs(session)
    label = f"گیف شماره {len(current_gifs) + 1}"

    await add_loading_gif(
        file_id=file_id,
        label=label,
        file_unique_id=file_unique_id,
        session=session,
    )

    registry = await ready_registry()
    _, kb = await render_loading_gifs_dashboard(session, registry)
    await message.reply(
        f"✅ <b>گیف جدید «{html.quote(label)}» با موفقیت به چرخه لودینگ اضافه شد!</b>\n\n"
        "از این به بعد ربات به صورت تصادفی از این گیف در هنگام پردازش‌ها استفاده می‌کند.",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("lgif:view:"), IsAdmin())
async def view_loading_gif(query: CallbackQuery, session: AsyncSession) -> None:
    gif_id = (query.data or "").split(":")[-1]
    gifs = await list_loading_gifs(session)
    found = next((g for g in gifs if g.get("id") == gif_id), None)
    if not found:
        await query.answer("گیف مورد نظر یافت نشد.", show_alert=True)
        return

    label = found.get("label", "گیف")
    created = found.get("created_at", "—")
    await query.answer(f"{label}\nتاریخ ثبت: {created}", show_alert=True)


@router.callback_query(F.data.startswith("lgif:test:"), IsAdmin())
async def test_loading_gif_single(query: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    gif_id = (query.data or "").split(":")[-1]
    gifs = await list_loading_gifs(session)
    found = next((g for g in gifs if g.get("id") == gif_id), None)
    if not found:
        await query.answer("گیف مورد نظر یافت نشد.", show_alert=True)
        return

    await query.answer("در حال اجرای پیش‌نمایش زنده نوار پیشرفت…")
    registry = await ready_registry()
    file_id = found["file_id"]
    chat_id = query.message.chat.id if query.message else 0

    await _run_progress_demo(bot, chat_id, file_id, registry)


@router.callback_query(F.data == "lgif:test_random", IsAdmin())
async def test_loading_gif_random(query: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    random_file_id = await get_random_loading_gif(session)
    if not random_file_id:
        await query.answer("هنوز هیچ گیفی در سیستم ثبت نشده است!", show_alert=True)
        return

    await query.answer("در حال اجرای تست تصادفی با نوار پیشرفت…")
    registry = await ready_registry()
    chat_id = query.message.chat.id if query.message else 0

    await _run_progress_demo(bot, chat_id, random_file_id, registry)


async def _run_progress_demo(bot: Bot, chat_id: int, file_id: str, registry: EmojiRegistry) -> None:
    """Run a realistic 3-step live demo of the progress bar on the GIF."""
    cap1 = format_progress_caption("در حال دانلود مدیا از سرور…", 30, registry=registry)
    try:
        sent = await bot.send_animation(chat_id=chat_id, animation=file_id, caption=cap1)
        if not sent:
            return

        await asyncio.sleep(1.4)
        cap2 = format_progress_caption("در حال پردازش و تبدیل فریم‌ها…", 70, registry=registry)
        try:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=sent.message_id,
                caption=cap2,
            )
        except Exception:
            pass

        await asyncio.sleep(1.4)
        cap3 = format_progress_caption(
            "تکمیل شد! در حال ارسال به تلگرام…", 100, registry=registry, detail="عملیات موفق"
        )
        try:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=sent.message_id,
                caption=cap3,
            )
        except Exception:
            pass

        await asyncio.sleep(2.0)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=sent.message_id)
        except Exception:
            pass
    except Exception as exc:
        log.warning("progress_demo_failed", error=str(exc))
        await bot.send_message(chat_id=chat_id, text=f"❌ خطا در ارسال گیف: {str(exc)[:100]}")


@router.callback_query(F.data.startswith("lgif:del:"), IsAdmin())
async def confirm_delete_loading_gif(query: CallbackQuery, session: AsyncSession) -> None:
    gif_id = (query.data or "").split(":")[-1]
    gifs = await list_loading_gifs(session)
    found = next((g for g in gifs if g.get("id") == gif_id), None)
    if not found:
        await query.answer("گیف مورد نظر یافت نشد.", show_alert=True)
        return

    label = found.get("label", "این گیف")
    registry = await ready_registry()
    kb = KB(registry)
    kb.row(
        inline_button(
            "بله، حذف کن",
            callback_data=f"lgif:del_yes:{gif_id}",
            style=Style.DESTRUCTIVE,
            emoji_slot="delete",
            registry=registry,
        ),
        inline_button(
            "انصراف",
            callback_data="lgif:list",
            style=Style.NEUTRAL,
            emoji_slot="error",
            registry=registry,
        ),
    )

    confirm_text = f"⚠️ <b>آیا از حذف «{html.quote(label)}» اطمینان دارید؟</b>"
    if isinstance(query.message, Message):
        await query.message.edit_text(confirm_text, reply_markup=kb.as_markup())
    await query.answer()


@router.callback_query(F.data.startswith("lgif:del_yes:"), IsAdmin())
async def process_delete_loading_gif(query: CallbackQuery, session: AsyncSession) -> None:
    gif_id = (query.data or "").split(":")[-1]
    deleted = await delete_loading_gif(gif_id, session)
    await query.answer("گیف با موفقیت حذف شد." if deleted else "یافت نشد.", show_alert=True)
    registry = await ready_registry()
    text, kb = await render_loading_gifs_dashboard(session, registry)
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text(text, reply_markup=kb.as_markup())
        except Exception:
            pass
