"""Admin backup & restore panel.

Flow:
  🗄 بکاپ و ریستور  →  take a backup (delivered as a document, then wiped from
                        disk) or restore one:
                          • upload a .dump / .dump.age document, or
                          • pick one still sitting in the backups directory.
                        The dump is validated, applied to a scratch database,
                        summarised, and only promoted on explicit confirmation.
                          • «بازگردانی نسخه قبلی» undoes the last restore.
"""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

from aiogram import Bot, F, Router, html
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.admin.backup import (
    create_db_backup,
    decrypt_backup,
    inspect_dump,
    restore_into_database,
)
from app.admin.db_ops import (
    create_empty_database,
    drop_database,
    list_previous,
    scratch_name,
    snapshot,
    swap_databases,
)
from app.config import Settings
from app.core.errors import MewviaError
from app.core.logging import get_logger
from app.core.units import fa_digits, human_bytes
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style
from app.ui.texts import t

log = get_logger(__name__)

router = Router(name="admin_backup")

_DUMP_SUFFIXES = (".dump", ".dump.age", ".age")


class RestoreSG(StatesGroup):
    waiting_for_dump = State()


async def _registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


def _format_list(entries: list[tuple[str, int]]) -> str:
    if not entries:
        return "<i>هیچ فایلی روی سرور نیست.</i>"
    lines = []
    for name, size in entries:
        lines.append(f"• <code>{html.quote(name)}</code> — {human_bytes(size)}")
    return "\n".join(lines)


async def list_backup_files(settings: Settings) -> list[tuple[str, int]]:
    """Backups currently on disk, newest first."""
    directory = settings.backup_dir
    if not directory.is_dir():
        return []
    found: list[tuple[str, int, float]] = []
    for path in directory.iterdir():
        if not path.is_file() or not path.name.endswith(_DUMP_SUFFIXES):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        found.append((path.name, stat.st_size, stat.st_mtime))
    found.sort(key=lambda row: row[2], reverse=True)
    return [(name, size) for name, size, _mtime in found]


async def render_panel(settings: Settings) -> tuple[str, KB]:
    registry = await _registry()
    files = await list_backup_files(settings)
    total = sum(size for _name, size in files)
    previous = await list_previous(settings)

    encryption = "🔐 رمزنگاری فعال (age)" if settings.backup_age_recipient else "⚠️ رمزنگاری غیرفعال"
    restore_ready = (
        "✅ کلید رمزگشایی تنظیم شده"
        if settings.backup_age_identity
        else "❌ کلید رمزگشایی تنظیم نشده (BACKUP_AGE_IDENTITY)"
    )

    text = (
        f"{registry.text('backup')} <b>مدیریت بکاپ و ریستور دیتابیس</b>\n\n"
        f"📦 <b>بکاپ‌های روی سرور:</b> {fa_digits(str(len(files)))} فایل "
        f"({human_bytes(total)})\n"
        f"{_format_list(files[:10])}\n\n"
        f"{encryption}\n"
        f"{restore_ready}\n"
        f"🗂 نسخه‌های قبلی قابل بازگشت: <b>{fa_digits(str(len(previous)))}</b>\n\n"
        f"{registry.text('info')} بکاپ بلافاصله به‌صورت فایل به شما تحویل داده "
        f"می‌شود و سپس از سرور حذف می‌گردد."
    )

    kb = KB(registry)
    kb.row(
        inline_button(
            "دریافت بکاپ جدید",
            callback_data="bk:create",
            style=Style.CONFIRM,
            emoji_slot="backup",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "ریستور از فایل آپلودی",
            callback_data="bk:restore_upload",
            style=Style.DESTRUCTIVE,
            emoji_slot="download",
            registry=registry,
        ),
    )
    if files:
        kb.row(
            inline_button(
                "ریستور از فایل‌های سرور",
                callback_data="bk:pick:0",
                style=Style.DESTRUCTIVE,
                emoji_slot="tool",
                registry=registry,
            ),
        )
    if previous:
        kb.row(
            inline_button(
                "بازگردانی نسخه قبلی",
                callback_data="bk:undo",
                style=Style.CONFIRM,
                emoji_slot="refresh",
                registry=registry,
            ),
        )
    kb.row(
        inline_button(
            t("common.back"),
            callback_data="menu:admin",
            style=Style.PRIMARY,
            emoji_slot="back",
            registry=registry,
        )
    )
    return text, kb


async def _show(query: CallbackQuery, settings: Settings) -> None:
    text, kb = await render_panel(settings)
    if isinstance(query.message, Message):
        with contextlib.suppress(Exception):
            await query.message.edit_text(text, reply_markup=kb.as_markup())
            return
        await query.message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "bk:panel")
async def open_panel(query: CallbackQuery, settings: Settings) -> None:
    await query.answer()
    await _show(query, settings)


@router.callback_query(F.data == "bk:create")
async def create_backup_now(query: CallbackQuery, settings: Settings, bot: Bot) -> None:
    if query.from_user is None:
        await query.answer()
        return
    await query.answer("در حال ساخت بکاپ…")
    try:
        path = await create_db_backup(settings, settings.backup_dir)
    except MewviaError as exc:
        await bot.send_message(query.from_user.id, f"❌ بکاپ ناموفق بود: {html.quote(str(exc))}")
        return

    size = path.stat().st_size
    caption = (
        f"{get_emoji_registry().text('backup')} <b>بکاپ دیتابیس Mewvia</b>\n"
        f"🗓 {html.quote(path.name)}\n"
        f"📦 {human_bytes(size)}\n"
    )
    if path.name.endswith(".age"):
        caption += "\n🔐 این فایل با کلید عمومی رمز شده است."

    delivered = False
    try:
        await bot.send_document(query.from_user.id, FSInputFile(path), caption=caption)
        delivered = True
    except Exception as exc:
        log.error("backup_delivery_failed", error=str(exc))
        await bot.send_message(
            query.from_user.id,
            f"⚠️ بکاپ ساخته شد ({human_bytes(size)}) اما ارسال فایل ناموفق بود.\n"
            f"<code>{html.quote(path.name)}</code>",
        )

    if delivered and settings.backup_deliver_to_admin:
        # Disk is not trusted storage for user data: the delivered copy is the
        # only one that should survive.
        path.unlink(missing_ok=True)
        with contextlib.suppress(Exception):
            await bot.send_message(
                query.from_user.id,
                "🧹 نسخه روی سرور پاک شد؛ فقط فایلی که دریافت کردید معتبر است.",
            )
    with contextlib.suppress(Exception):
        await _show(query, settings)


@router.callback_query(F.data == "bk:restore_upload")
async def ask_for_dump(query: CallbackQuery, state: FSMContext) -> None:
    registry = await _registry()
    await query.answer()
    await state.set_state(RestoreSG.waiting_for_dump)
    if isinstance(query.message, Message):
        await query.message.answer(
            "📤 فایل بکاپ را بفرستید.\n\n"
            "پسوند مجاز: <code>.dump</code>، <code>.dump.age</code> یا <code>.gz</code>\n"
            "⚠️ فایل روی یک دیتابیس موقت ریستور می‌شود و تا تأیید شما جایگزین "
            "دیتابیس اصلی <b>نمی‌شود</b>.\n\n"
            "برای انصراف /cancel را بفرستید.",
            reply_markup=KB(registry)
            .row(
                inline_button(
                    "انصراف",
                    callback_data="bk:panel",
                    style=Style.NEUTRAL,
                    registry=registry,
                )
            )
            .as_markup(),
        )


@router.message(StateFilter(RestoreSG.waiting_for_dump), F.document)
async def receive_dump(message: Message, state: FSMContext, bot: Bot, settings: Settings) -> None:
    document = message.document
    if document is None:
        return
    name = document.file_name or "backup.dump"
    if not name.endswith(_DUMP_SUFFIXES):
        await message.answer(
            "❌ فقط فایل‌های <code>.dump</code> / <code>.dump.age</code> پذیرفته می‌شوند."
        )
        return
    if document.file_size and document.file_size > settings.backup_max_bytes:
        await message.answer("❌ فایل بزرگ‌تر از حد مجاز است.")
        return

    await state.clear()
    status = await message.answer("⏳ دریافت و بررسی فایل…")
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp:
        work = Path(tmp)
        local = work / name
        try:
            await bot.download(document, destination=local)
            if name.endswith((".age",)):
                plain = await decrypt_backup(settings, local, work)
            else:
                plain = local
            ok, reason = inspect_dump(plain)
            if not ok:
                await status.edit_text(f"❌ {reason}")
                return
            await _stage_restore(status, plain, settings)
        except MewviaError as exc:
            await status.edit_text(f"❌ {html.quote(str(exc))}")
        except Exception as exc:
            log.error("restore_upload_failed", error=str(exc))
            await status.edit_text("❌ دریافت یا پردازش فایل ناموفق بود.")


@router.callback_query(F.data.startswith("bk:pick:"))
async def pick_server_backup(query: CallbackQuery, settings: Settings) -> None:
    files = await list_backup_files(settings)
    if not files:
        await query.answer("فایلی روی سرور نیست.", show_alert=True)
        return
    try:
        index = int((query.data or "bk:pick:0").split(":")[-1])
    except ValueError:
        index = 0
    index = max(0, min(index, len(files) - 1))
    name, size = files[index]
    registry = await _registry()
    kb = KB(registry)
    kb.row(
        inline_button(
            f"✅ ریستور {name[:24]}",
            callback_data=f"bk:apply:{index}",
            style=Style.DESTRUCTIVE,
            emoji_slot="confirm",
            registry=registry,
        )
    )
    kb.row(
        inline_button(
            "فایل بعدی",
            callback_data=f"bk:pick:{(index + 1) % len(files)}",
            style=Style.PRIMARY,
            emoji_slot="refresh",
            registry=registry,
        ),
        inline_button(
            "انصراف",
            callback_data="bk:panel",
            style=Style.NEUTRAL,
            emoji_slot="back",
            registry=registry,
        ),
    )
    text = (
        "🗂 <b>انتخاب بکاپ برای ریستور</b>\n\n"
        f"فایل: <code>{html.quote(name)}</code>\n"
        f"حجم: {human_bytes(size)}\n"
        f"({fa_digits(str(index + 1))} از {fa_digits(str(len(files)))})\n\n"
        "⚠️ ابتدا روی دیتابیس موقت ریستور می‌شود و نتیجه به شما نشان داده می‌شود."
    )
    if isinstance(query.message, Message):
        with contextlib.suppress(Exception):
            await query.message.edit_text(text, reply_markup=kb.as_markup())
            await query.answer()
            return
        await query.message.answer(text, reply_markup=kb.as_markup())
    await query.answer()


@router.callback_query(F.data.startswith("bk:apply:"))
async def apply_server_backup(query: CallbackQuery, settings: Settings) -> None:
    files = await list_backup_files(settings)
    try:
        index = int((query.data or "bk:apply:0").split(":")[-1])
    except ValueError:
        index = 0
    if index >= len(files):
        await query.answer("فایل موجود نیست.", show_alert=True)
        return
    name, _size = files[index]
    if isinstance(query.message, Message):
        await query.message.edit_text("⏳ در حال ریستور روی دیتابیس موقت…")
    await query.answer()
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp:
        work = Path(tmp)
        source = settings.backup_dir / name
        if not source.is_file():
            await query.answer("فایل یافت نشد.", show_alert=True)
            return
        try:
            plain = (
                await decrypt_backup(settings, source, work) if name.endswith(".age") else source
            )
            ok, reason = inspect_dump(plain)
            if not ok:
                if isinstance(query.message, Message):
                    await query.message.edit_text(f"❌ {reason}")
                return
            if isinstance(query.message, Message):
                await _stage_restore(query.message, plain, settings)
        except MewviaError as exc:
            if isinstance(query.message, Message):
                await query.message.edit_text(f"❌ {html.quote(str(exc))}")


async def _stage_restore(target: Message, dump: Path, settings: Settings) -> None:
    """Restore into a scratch database and ask for confirmation before swapping."""
    name = scratch_name()
    await create_empty_database(settings, name)
    try:
        await restore_into_database(settings, dump, name)
        incoming = await snapshot(settings, name)
        current = await snapshot(settings, settings.postgres_db)
    except Exception:
        with contextlib.suppress(Exception):
            await drop_database(settings, name)
        raise

    registry = await _registry()
    kb = KB(registry)
    kb.row(
        inline_button(
            "تأیید و جایگزینی دیتابیس «زنده»",
            callback_data=f"bk:swap:{name}",
            style=Style.DESTRUCTIVE,
            emoji_slot="confirm",
            registry=registry,
        )
    )
    kb.row(
        inline_button(
            "لغو و حذف نسخه موقت",
            callback_data=f"bk:discard:{name}",
            style=Style.NEUTRAL,
            emoji_slot="delete",
            registry=registry,
        )
    )
    text = (
        "🧪 <b>ریستور آزمایشی انجام شد</b>\n\n"
        f"📥 <b>دیتابیس فعلی:</b>\n{current.summary}\n\n"
        f"📤 <b>بکاپ بازیابی‌شده:</b>\n{incoming.summary}\n\n"
        f"⚠️ با تأیید، دیتابیس فعلی به‌عنوان نسخه قبلی نگه داشته می‌شود و "
        f"«{html.quote(name)}» جای آن می‌نشیند. برای اعمال، ربات یک‌بار "
        f"ری‌استارت می‌شود.\n\n"
        f"{registry.text('info')} اگر آمار بکاپ غیرعادی است، لغو کنید."
    )
    with contextlib.suppress(Exception):
        await target.edit_text(text, reply_markup=kb.as_markup())
        return
    await target.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("bk:discard:"))
async def discard_restore(query: CallbackQuery, settings: Settings) -> None:
    name = (query.data or "").split(":", 2)[-1]
    await drop_database(settings, name)
    await query.answer("نسخه موقت حذف شد.", show_alert=True)
    await _show(query, settings)


@router.callback_query(F.data.startswith("bk:swap:"))
async def confirm_swap(query: CallbackQuery, settings: Settings) -> None:
    name = (query.data or "").split(":", 2)[-1]
    if not name.startswith("mewvia_restore_"):
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return
    await query.answer("در حال جابه‌جایی دیتابیس…")
    try:
        previous = await swap_databases(settings, name)
    except Exception as exc:
        log.error("restore_swap_failed", error=str(exc))
        if isinstance(query.message, Message):
            await query.message.edit_text(
                f"❌ جایگزینی ناموفق بود: {html.quote(str(exc)[:300])}\nدیتابیس قبلی دست‌نخورده است."
            )
        return

    # The engine's pool still holds connections to the OLD (renamed) database.
    # Re-create it in place so new sessions immediately use the promoted DB —
    # no process restart required.
    try:
        from app.db.session import dispose_engine, init_engine

        await dispose_engine()
        init_engine(settings)
        log.info("restore_engine_recycled")
        recycled = True
    except Exception as exc:
        log.error("restore_engine_recycle_failed", error=str(exc))
        recycled = False

    suffix = (
        "♻️ اتصالات دیتابیس تازه شد — ربات بلافاصله از نسخه جدید استفاده می\u200cکند."
        if recycled
        else "⚠️ اتصالات تازه نشد؛ لطفاً ربات را ری\u200cاستارت کنید."
    )
    if isinstance(query.message, Message):
        await query.message.edit_text(
            "✅ <b>دیتابیس جایگزین شد</b>\n\n"
            f"نسخه قبلی با نام <code>{html.quote(previous)}</code> نگه داشته شد.\n"
            f"{suffix}"
        )


@router.callback_query(F.data == "bk:undo")
async def undo_restore(query: CallbackQuery, settings: Settings) -> None:
    previous = await list_previous(settings)
    if not previous:
        await query.answer("نسخه قبلی موجود نیست.", show_alert=True)
        return
    latest = previous[0]
    await query.answer("در حال بازگردانی…")
    try:
        parked = await swap_databases(settings, latest)
    except Exception as exc:
        log.error("restore_undo_failed", error=str(exc))
        if isinstance(query.message, Message):
            await query.message.edit_text(f"❌ بازگردانی ناموفق بود: {html.quote(str(exc)[:300])}")
        return
    try:
        from app.db.session import dispose_engine, init_engine

        await dispose_engine()
        init_engine(settings)
    except Exception as exc:
        log.error("restore_undo_engine_recycle_failed", error=str(exc))
    if isinstance(query.message, Message):
        await query.message.edit_text(
            "✅ <b>نسخه قبلی بازگردانده شد</b>\n\n"
            f"نسخهای که کنار گذاشته شد: <code>{html.quote(parked)}</code>\n"
            f"♻️ اتصالات دیتابیس تازه شد — نیازی به ریاستارت نیست."
        )
