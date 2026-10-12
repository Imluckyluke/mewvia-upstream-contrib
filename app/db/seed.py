"""Seed data for the runtime tables.

Applied by `python -m app.seed` (and by `dev.sh migrate`) — idempotent, so it is
safe on every deploy. Slots and settings are declared here rather than in a
migration so that adding one later is a code change, not a schema change.
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.repo import EmojiRepo, SettingRepo


class SlotDef(NamedTuple):
    slot: str
    title: str
    fallback: str
    sort_order: int


# The first eight mirror the reference admin panel; the rest cover what the
# conversion flow and admin panel need. `slot` names are permanent — code refers
# to them — while titles and fallbacks may be edited freely.
EMOJI_SLOTS: tuple[SlotDef, ...] = (
    # General / Status
    SlotDef("ok", "تایید / موفق", "✅", 10),
    SlotDef("error", "خطا / انصراف", "❌", 20),
    SlotDef("warning", "هشدار", "⚠️", 30),
    SlotDef("on", "روشن / فعال", "🟢", 40),
    SlotDef("off", "خاموش / غیرفعال", "🔴", 50),
    SlotDef("celebrate", "تبریک", "🎉", 60),
    SlotDef("lock", "قفل / عضویت اجباری", "🔒", 70),
    SlotDef("clock", "ساعت / هشدار زمان", "⏰", 80),
    SlotDef("confirm", "تایید نهایی", "✨", 85),
    SlotDef("idea", "ایده / راهنما", "💡", 86),
    SlotDef("paw", "ردپای Mewvia", "🐾", 87),
    SlotDef("bullet", "گلوله / آیتم", "🔹", 88),
    SlotDef("lightning", "سرعت / اینلاین", "⚡", 89),
    SlotDef("add", "افزودن / اضافه", "➕", 91),
    SlotDef("rocket", "موشک / ورود سریع", "🚀", 92),
    SlotDef("mic", "میکروفون / ویس", "🎤", 93),
    SlotDef("reverse", "معکوس‌سازی", "🔄", 94),
    # Navigation
    SlotDef("back", "بازگشت", "◀️", 95),
    SlotDef("next", "صفحه بعد", "▶️", 100),
    SlotDef("refresh", "رفرش", "🔄", 110),
    SlotDef("edit", "ویرایش", "✏️", 120),
    SlotDef("delete", "حذف", "🗑", 130),
    SlotDef("page", "شماره صفحه", "📄", 140),
    # Media / conversion
    SlotDef("sticker", "استیکر", "🎨", 200),
    SlotDef("gif", "گیف", "🎞", 210),
    SlotDef("photo", "عکس", "🖼", 220),
    SlotDef("video", "ویدیو", "🎬", 230),
    SlotDef("compress", "فشرده‌سازی", "📉", 231),
    SlotDef("mute", "بی‌صدا", "🔇", 232),
    SlotDef("audio", "صدا / ویس", "🎵", 240),
    SlotDef("file", "فایل", "📎", 250),
    SlotDef("processing", "در حال پردازش", "⏳", 260),
    SlotDef("download", "دانلود", "📥", 270),
    SlotDef("text", "متن / استیکرمانی", "📝", 280),
    SlotDef("top", "موقعیت بالا", "⬆️", 281),
    SlotDef("center", "موقعیت وسط", "↔️", 282),
    SlotDef("bottom", "موقعیت پایین", "⬇️", 283),
    SlotDef("white_circle", "دایره سفید", "⚪", 284),
    SlotDef("yellow_circle", "دایره زرد", "🟡", 285),
    SlotDef("red_circle", "دایره قرمز", "🔴", 286),
    SlotDef("green_circle", "دایره سبز", "🟢", 287),
    SlotDef("blue_circle", "دایره آبی", "🔵", 288),
    SlotDef("black_square", "مربع مشکی", "⬛", 289),
    SlotDef("pack", "استیکرپک", "📦", 290),
    SlotDef("pin", "پین / سنجاق", "📌", 291),
    SlotDef("filter", "فیلتر رنگی", "🎨", 295),
    SlotDef("wide", "واید / پهن کردن", "↔️", 296),
    SlotDef("flt_bw", "فیلتر سیاه و سفید", "🖤", 297),
    SlotDef("flt_negative", "فیلتر نگاتیو", "🔄", 298),
    SlotDef("flt_sepia", "فیلتر سپیا", "🤎", 299),
    SlotDef("flt_bright", "فیلتر درخشان", "☀️", 301),
    SlotDef("flt_blur", "فیلتر بلور", "🌫", 302),
    # Admin panel
    SlotDef("admin", "پنل ادمین", "🛠", 300),
    SlotDef("stats", "آمار", "📊", 310),
    SlotDef("users", "کاربران", "👥", 320),
    SlotDef("broadcast", "پیام همگانی", "📣", 330),
    SlotDef("sponsor", "کانال‌های اسپانسر", "📢", 335),
    SlotDef("backup", "بکاپ", "💾", 340),
    SlotDef("emoji", "ایموجی‌ها", "😀", 350),
    SlotDef("convert", "تبدیل مدیا", "⚡", 360),
    SlotDef("search", "جستجو", "🔍", 370),
    SlotDef("user", "کاربر", "👤", 380),
)


class SettingDef(NamedTuple):
    key: str
    default: str
    description: str


# Runtime switches. `app.config` holds the boot-time values; these are the ones
# an admin can change without a restart.
SETTINGS: tuple[SettingDef, ...] = (
    SettingDef("premium_emoji_enabled", "true", "استفاده از ایموجی پریمیوم در دکمه‌ها و متن‌ها"),
    SettingDef("maintenance", "false", "حالت تعمیر — ربات به کاربران عادی پاسخ نمی‌دهد"),
    SettingDef("maintenance_message", "", "پیام حالت تعمیر (خالی = پیام پیش‌فرض)"),
    SettingDef("autobackup_cron", "", "کرون بکاپ خودکار، مثلاً '0 4 * * *' (خالی = خاموش)"),
    SettingDef("daily_quota", "0", "سهمیه تبدیل روزانه هر کاربر (۰ = بی‌نهایت)"),
)

#: Keys whose runtime value must stay in sync with the env-provided default on
#: first boot, so PREMIUM_EMOJI_ENABLED in .env is honoured for a fresh install.
ENV_SEEDED_SETTINGS = {"premium_emoji_enabled"}


async def apply_seed(session: AsyncSession, settings: Settings) -> dict[str, int]:
    """Insert missing slots and settings. Idempotent.

    Existing premium emoji ids and admin-changed setting values are preserved;
    only titles, fallbacks and ordering are refreshed from code.
    """
    emoji_repo = EmojiRepo(session)
    setting_repo = SettingRepo(session)

    for slot in EMOJI_SLOTS:
        await emoji_repo.ensure(slot.slot, slot.title, slot.fallback, slot.sort_order)

    for setting in SETTINGS:
        default = setting.default
        if setting.key in ENV_SEEDED_SETTINGS and setting.key == "premium_emoji_enabled":
            default = "true" if settings.premium_emoji_enabled else "false"
        await setting_repo.ensure(setting.key, default, setting.description)

    await session.flush()
    return {
        "emoji_slots": await emoji_repo.count(),
        "settings": len(await setting_repo.all()),
    }


async def _main() -> None:
    from app.core.logging import get_logger, setup_logging
    from app.db.session import dispose_engine, init_engine, session_scope

    app_settings = get_settings()
    setup_logging(app_settings.log_level, app_settings.log_json)
    log = get_logger(__name__)

    init_engine(app_settings)
    try:
        async with session_scope() as session:
            counts = await apply_seed(session, app_settings)
        log.info("seed_applied", **counts)
        print(f"seeded: {counts['emoji_slots']} emoji slots, {counts['settings']} settings")
    finally:
        await dispose_engine()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
