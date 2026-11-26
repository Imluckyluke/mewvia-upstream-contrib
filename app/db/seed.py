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
    SlotDef("trash", "سطل زباله / حذف", "🗑️", 135),
    SlotDef("page", "شماره صفحه", "📄", 140),
    # Media / conversion
    SlotDef("sticker", "استیکر", "🎨", 200),
    SlotDef("gif", "گیف", "🎞", 210),
    SlotDef("photo", "عکس", "🖼", 220),
    SlotDef("video", "ویدیو", "🎬", 230),
    SlotDef("video_note", "ویدیومسیج گرد", "🔴", 235),
    SlotDef("compress", "فشرده‌سازی", "📉", 236),
    SlotDef("mute", "بی‌صدا", "🔇", 237),
    SlotDef("audio", "صدا / ویس", "🎵", 240),
    SlotDef("music", "موزیک / آهنگ", "🎶", 245),
    SlotDef("file", "فایل", "📎", 250),
    SlotDef("processing", "در حال پردازش", "⏳", 260),
    SlotDef("download", "دانلود", "📥", 270),
    SlotDef("text", "متن / استیکرمانی", "📝", 280),
    SlotDef("font", "فونت متن", "🔤", 281),
    SlotDef("size", "اندازه متن", "📐", 282),
    SlotDef("color", "رنگ متن", "🎨", 283),
    SlotDef("top", "موقعیت بالا", "⬆️", 284),
    SlotDef("center", "موقعیت وسط", "↔️", 285),
    SlotDef("bottom", "موقعیت پایین", "⬇️", 286),
    SlotDef("white_circle", "دایره سفید", "⚪", 287),
    SlotDef("yellow_circle", "دایره زرد", "🟡", 288),
    SlotDef("red_circle", "دایره قرمز", "🔴", 289),
    SlotDef("green_circle", "دایره سبز", "🟢", 290),
    SlotDef("blue_circle", "دایره آبی", "🔵", 291),
    SlotDef("black_square", "مربع مشکی", "⬛", 292),
    SlotDef("pack", "استیکرپک", "📦", 293),
    SlotDef("pin", "پین / سنجاق", "📌", 294),
    SlotDef("filter", "فیلتر رنگی", "🎨", 295),
    SlotDef("wide", "واید / پهن کردن", "↔️", 296),
    SlotDef("flt_bw", "فیلتر سیاه و سفید", "🖤", 297),
    SlotDef("flt_negative", "فیلتر نگاتیو", "🔄", 298),
    SlotDef("flt_sepia", "فیلتر سپیا", "🤎", 299),
    SlotDef("flt_bright", "فیلتر درخشان", "☀️", 301),
    SlotDef("flt_blur", "فیلتر بلور", "🌫", 302),
    # Music & Tag Editor
    SlotDef("artist", "خواننده / هنرمند", "🎤", 303),
    SlotDef("album", "آلبوم موسیقی", "💿", 304),
    SlotDef("genre", "ژانر موسیقی", "🎼", 305),
    SlotDef("track_num", "شماره ترک", "🔢", 306),
    SlotDef("cover", "کاور موزیک", "🖼️", 307),
    SlotDef("lyrics", "متن ترانه", "📜", 308),
    SlotDef("shazam", "تشخیص آهنگ (شزم)", "🔍", 309),
    # Downloader & Platforms
    SlotDef("zip", "فایل فشرده (ZIP)", "📦", 310),
    SlotDef("quality_hd", "کیفیت HD (720p)", "📱", 311),
    SlotDef("quality_sd", "کیفیت کم‌حجم (480p)", "📉", 312),
    SlotDef("youtube", "یوتیوب", "🔴", 315),
    SlotDef("instagram", "اینستاگرام", "🟣", 316),
    SlotDef("spotify", "اسپاتیفای", "🟢", 317),
    SlotDef("soundcloud", "ساندکلاد", "🟠", 318),
    SlotDef("tiktok", "تیک‌تاک", "⚫️", 319),
    SlotDef("twitter", "توییتر (X)", "🔵", 321),
    SlotDef("pinterest", "پینترست", "📌", 322),
    SlotDef("cookie", "کوکی اکانت", "🍪", 325),
    # Admin panel
    SlotDef("admin", "پنل ادمین", "🛠", 330),
    SlotDef("tool", "ابزارها", "🔧", 332),
    SlotDef("stats", "آمار", "📊", 340),
    SlotDef("users", "کاربران", "👥", 350),
    SlotDef("user", "کاربر", "👤", 355),
    SlotDef("broadcast", "پیام همگانی", "📣", 360),
    SlotDef("sponsor", "کانال‌های اسپانسر", "📢", 365),
    SlotDef("backup", "بکاپ", "💾", 370),
    SlotDef("emoji", "ایموجی‌ها", "😀", 375),
    SlotDef("convert", "تبدیل مدیا", "⚡", 380),
    SlotDef("search", "جستجو", "🔍", 385),
    SlotDef("ban", "مسدود / بن", "🚫", 390),
    SlotDef("unban", "آزادسازی / آن‌بن", "🔓", 395),
    # Games & XO (Dynamic configurable slots)
    SlotDef("game", "آیکون بازی و سرگرمی", "🎮", 400),
    SlotDef("xo_x", "علامت ضربدر دوز (X)", "❌", 401),
    SlotDef("xo_o", "علامت دایره دوز (O)", "⭕", 402),
    SlotDef("xo_empty", "خانه خالی دوز", "⬜", 403),
    SlotDef("trophy", "جام قهرمانی / رتبه‌بندی", "🏆", 404),
    SlotDef("streak_fire", "آتش زنجیره برد", "🔥", 405),
    SlotDef("medal", "مدال و رتبه", "🎖", 406),
    SlotDef("league_bronze", "نشان لیگ برنز", "🥉", 407),
    SlotDef("league_silver", "نشان لیگ نقره", "🥈", 408),
    SlotDef("league_gold", "نشان لیگ طلا", "🥇", 409),
    SlotDef("league_diamond", "نشان لیگ الماس", "💎", 410),
    SlotDef("league_master", "نشان لیگ مستر", "👑", 411),
    # UI icons that used to be hard-coded in feature strings
    SlotDef("handshake", "دست دادن / مساوی", "🤝", 420),
    SlotDef("bot_robot", "ربات / هوش مصنوعی", "🤖", 421),
    SlotDef("target", "هدف / قوانین امتیاز", "🎯", 422),
    SlotDef("calendar", "تاریخ / زمان", "🗓", 423),
    SlotDef("upload", "آپلود / ارسال فایل", "📤", 424),
    SlotDef("star", "ستاره / پریمیوم", "⭐", 425),
    SlotDef("alert", "هشدار فوری", "🚨", 426),
    SlotDef("folder", "پوشه / دستهبندی", "🗂", 427),
    SlotDef("settings", "تنظیمات", "⚙️", 428),
    SlotDef("web", "وب / اینترنت", "🌐", 429),
    SlotDef("tv", "آپارات / ویدیو", "📺", 430),
    SlotDef("lab", "تست / آزمایش", "🧪", 431),
    SlotDef("wave", "سلام / خداحافظی", "👋", 432),
    SlotDef("finish", "پایان / تمام شد", "🏁", 433),
    SlotDef("down", "کاهش / پایین", "🔻", 434),
    SlotDef("label", "برچسب / لقب", "🏷", 435),
    SlotDef("shield", "سپر / حاشیه امن", "🛡", 436),
    SlotDef("eye", "چشم / پیشنمایش", "👁", 437),
    SlotDef("check", "تیک", "✓", 438),
    SlotDef("question", "علامت سوال", "❓", 439),
    SlotDef("arrow_right", "پیکان راست", "➔", 440),
    SlotDef("disc", "دیسک / فرمت", "💽", 441),
    SlotDef("headphone", "هدفون / ساندکلاد", "🎧", 442),
    SlotDef("bird", "پرنده / توییتر", "🐦", 443),
    SlotDef("gamepad", "دست بازی قدیمی", "🕹", 444),
    SlotDef("broom", "پاکسازی", "🧹", 445),
    SlotDef("archive", "بایگانی / آرشیو", "🗄", 446),
    SlotDef("repeat", "تکرار", "🔁", 447),
    SlotDef("dna", "ریویژن / نسخه", "🧬", 448),
    SlotDef("infinity", "نامحدود", "♾️", 449),
    SlotDef("clipboard", "لیست / کلیپبورد", "📋", 450),
    SlotDef("check_mark", "تیک سبز", "✔", 451),
    SlotDef("reddit", "ردیت", "🟧", 452),
    SlotDef("facebook", "فیسبوک", "🔷", 453),
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
    SettingDef("loading_gifs", "[]", "لیست گیف‌های لودینگ برای نوار پیشرفت"),
    SettingDef("loading_gifs_enabled", "true", "فعال بودن گیف‌های متحرک لودینگ"),
    SettingDef("games_group_enabled", "false", "فعال بودن بازیها در گروهها (چندنفره)"),
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
