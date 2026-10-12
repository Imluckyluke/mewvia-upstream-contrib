"""All user-facing strings, in one place.

Persian is the only locale for now, but the lookup goes through `t()` so adding
a second language later means adding a dict, not touching handlers.
"""

from __future__ import annotations

from app.ui.emoji import EmojiRegistry

DEFAULT_LOCALE = "fa"

_FA: dict[str, str] = {
    # ---- generic ----
    "start.greeting": (
        "سلام {name} جان! 👋\n\n"
        "من <b>Mewvia</b> هستم — جعبه‌ابزار مدیای تلگرام.\n"
        "هر استیکر، گیف، عکس یا ویدیویی بفرست تا برات تبدیلش کنم."
    ),
    "start.pick_action": "یکی از گزینه‌ها رو انتخاب کن:",
    "start.deeplink_convert": (
        "⚡ <b>تبدیل سریع مدیا</b>\n\n"
        "لطفاً عکس، ویدیو، گیف، استیکر یا ویدیومسیج خود را برای ربات بفرستید تا بلافاصله تبدیل شود."
    ),
    "start.deeplink_newpack": (
        "📦 <b>ساخت استیکرپک جدید</b>\n\n"
        "لطفاً عکس، استیکر یا ویدیویی که می‌خواهید اولین استیکر پک جدیدتان باشد را ارسال کنید."
    ),
    "start.deeplink_addpack": (
        "📌 <b>افزودن استیکر به پک {pack_name}</b>\n\n"
        "لطفاً عکس، استیکر یا ویدیوی مورد نظر خود را برای افزودن به این پک ارسال کنید."
    ),
    "common.back": "بازگشت",
    "common.refresh": "رفرش",
    "common.cancel": "لغو",
    "common.next": "بعد »",
    "common.prev": "« قبل",
    "common.done": "انجام شد",
    "common.processing": "در حال پردازش…",
    "common.unknown_command": "این دستور رو نمی‌شناسم. /start رو بزن.",
    "common.page": "{current} / {total}",
    "common.yes": "بله",
    "common.no": "خیر",
    "common.soon": "این بخش در فاز بعدی فعال می‌شود.",
    "common.stale_button": "این دکمه منقضی شده. /start رو بزن.",
    # ---- main menu ----
    "menu.title": "منوی اصلی",
    "menu.admin": "پنل ادمین",
    "menu.help": "راهنما",
    "menu.start_over": "شروع مجدد",
    "menu.placeholder": "یک فایل بفرست یا از منو انتخاب کن…",
    "menu.empty": "هنوز قابلیتی فعال نشده — فاز بعدی مبدل مدیا را اضافه می‌کند.",
    "menu.convert": "تبدیل مدیا",
    "menu.stickerpack": "استیکرپک‌های من",
    "menu.videotools": "ابزارهای ویدیو",
    "menu.audio": "ابزارهای صدا",
    "menu.imagetools": "ابزارهای عکس",
    # ---- stickerpack ----
    "spack.title": "<b>استیکرپک‌های من</b>",
    "spack.empty": "شما هنوز هیچ استیکرپکی نساخته‌اید.",
    "spack.delete": "حذف از ربات",
    "spack.deleted": "استیکرپک با موفقیت حذف شد.",
    "spack.delete_confirm": "آیا از حذف استیکرپک <b>{title}</b> اطمینان دارید؟",
    "spack.view": "{title} ({count})",
    # ---- UI showcase (/ui) ----
    "ui.menu_entry": "مدیریت ایموجی‌ها",
    "ui.title": "<b>مدیریت و پیش‌نمایش ایموجی‌های ربات</b>",
    "ui.styles_note": (
        "از این بخش می‌توانید ایموجی پریمیوم یا یونیکد هر اسلات را تغییر دهید.\n"
        "<i>تغییرات بلافاصله در کل ربات اعمال می‌شوند.</i>"
    ),
    "ui.inline_emoji_note": "این خط پیش‌نمایش ایموجی درون متن است.",
    "ui.premium_on": "ایموجی پریمیوم: <b>روشن</b> — {count} از {total} اسلات تنظیم شده.",
    "ui.premium_off": "ایموجی پریمیوم: <b>خاموش</b> — همه‌جا از ایموجی یونیکد استفاده می‌شود.",
    "ui.premium_now_on": "ایموجی پریمیوم روشن شد.",
    "ui.premium_now_off": "ایموجی پریمیوم خاموش شد.",
    "ui.primary": "اصلی",
    "ui.confirm": "تایید",
    "ui.destructive": "حذف",
    "ui.neutral": "بی‌رنگ",
    "ui.edit": "ویرایش",
    "ui.delete": "حذف",
    "ui.toggle_premium": "تغییر وضعیت ایموجی پریمیوم",
    "ui.tapped": "دکمه «{label}» زده شد.",
    "ui.prompt_edit": (
        "ویرایش ایموجی اسلات <code>{slot}</code> ({title})\n\n"
        "مقدار فعلی: {current}\n\n"
        "لطفاً ایموجی جدیدی که می‌خواهی جایگزین شود را بفرست.\n"
        "می‌توانی یک <b>ایموجی پریمیوم</b> یا یک <b>ایموجی عادی یونیکد</b> بفرستی.\n\n"
        "برای انصراف دکمه زیر را بزن."
    ),
    "ui.edit_success": "ایموجی اسلات <code>{slot}</code> با موفقیت تغییر کرد.",
    "ui.edit_cancelled": "ویرایش انصراف داده شد.",
    "ui.edit_invalid": "لطفاً فقط یک ایموجی (پریمیوم یا یونیکد) بفرست.",
    "ui.delete_success": "ایموجی پریمیوم اسلات <code>{slot}</code> حذف شد و به یونیکد افت کرد.",
    # ---- group media assistant ----
    "group.no_reply": (
        "<b>⚠️ راهنمای استفاده در گروه</b>\n\n"
        "برای استفاده از ابزارهای مدیا در گروه، لطفاً این دستور را در <b>پاسخ (ریپلای)</b> "
        "به یک عکس، ویدیو، گیف، استیکر، ویدیومسیج یا ویس ارسال کن."
    ),
    "group.unsupported_reply": (
        "<b>❌ مدیا پشتیبانی نمی‌شود</b>\n\n"
        "پیامی که به آن ریپلای کرده‌ای حاوی مدیای قابل پردازش نیست.\n"
        "لطفاً روی عکس، ویدیو، گیف، استیکر، ویدیومسیج، ویس یا موزیک ریپلای کن."
    ),
    "group.panel_title": (
        "<b>🎬 دستیار مدیای Mewvia برای {name}</b>\n\nعملیات مورد نظرت را از دکمه‌های زیر انتخاب کن:"
    ),
    # ---- conversion flow ----
    "convert.choose_target": "فرمت خروجی مورد نظر را انتخاب کن:",
    "convert.guide_body": (
        "<b>تبدیل مدیا در Mewvia</b>\n\n"
        "برای تبدیل هر مدیایی، کافیه فایل خود را "
        "(عکس، ویدیو، گیف، استیکر، ویدیومسیج یا ویس و موزیک) "
        "مستقیماً برای پیشی بفرستی.\n\n"
        "پیشی بلافاصله فرمت‌های خروجی ممکن را با دکمه‌های شیشه‌ای برایت ارسال می‌کنه:)"
    ),
    "target.webp": "استیکر استاتیک (WEBP)",
    "target.webm_sticker": "استیکر ویدیویی (WEBM)",
    "target.png": "عکس (PNG)",
    "target.gif": "گیف (GIF)",
    "target.mp4": "ویدیو (MP4)",
    # ---- text overlay ----
    "text_overlay.prompt_text": (
        "<b>افزودن متن به مدیا</b>\n\nلطفاً متنی که می‌خواهید روی فایل قرار بگیرد را بفرستید:"
    ),
    "text_overlay.menu_title": (
        "<b>تنظیمات متن و استیکرمانی</b>\n\n"
        "• متن: <code>{text}</code>\n"
        "• فونت: <b>{font_name}</b>\n"
        "• سایز: <b>{size_label}</b>\n"
        "• رنگ متن: <b>{color_label}</b>\n"
        "• موقعیت: <b>{pos_label}</b>\n\n"
        "با دکمه‌های زیر می‌توانید فونت، سایز، رنگ و محل متن را تغییر دهید:"
    ),
    "text_overlay.font_vazir": "وزیرمتن (دیفالت)",
    "text_overlay.font_lalezar": "لاله‌زار",
    "text_overlay.font_samim": "صمیم",
    "text_overlay.font_sahel": "ساحل",
    "text_overlay.size_small": "کوچک",
    "text_overlay.size_medium": "متوسط",
    "text_overlay.size_large": "بزرگ",
    "text_overlay.size_xlarge": "خیلی بزرگ",
    "text_overlay.pos_top": "⬆️ بالا",
    "text_overlay.pos_center": "↔️ وسط",
    "text_overlay.pos_bottom": "⬇️ پایین",
    "text_overlay.render_btn": "ساخت و ارسال",
    # ---- errors (keyed by MewviaError.code) ----
    "error.unknown": "یک خطای پیش‌بینی‌نشده رخ داد. لطفاً بعداً دوباره تلاش کن.",
    "error.media_error": "پردازش این فایل ممکن نشد.",
    "error.unsupported_media": "این نوع فایل پشتیبانی نمی‌شود.",
    "error.probe_failed": "نتوانستم مشخصات این فایل را بخوانم. احتمالاً فایل سالم نیست.",
    "error.conversion_failed": "تبدیل ناموفق بود. یک فایل دیگر امتحان کن.",
    "error.conversion_timeout": "تبدیل بیش از حد طول کشید و متوقف شد.",
    "error.size_limit": "خروجی در محدودیت حجم تلگرام جا نشد.",
    "error.file_too_large": "حجم فایل بیش از حد مجاز است (حداکثر {limit}).",
    "error.duration_too_long": "مدت این فایل بیش از حد مجاز است (حداکثر {limit} ثانیه).",
    "error.throttled": "کمی آرام‌تر! {seconds} ثانیه صبر کن.",
    "error.maintenance": "ربات موقتاً در حال تعمیر است. کمی بعد دوباره سر بزن.",
    # ---- phase 0 diagnostics ----
    "ping.pong": "زنده‌ام!\nتأخیر: <code>{latency_ms} ms</code>",
    "health.title": "<b>وضعیت سیستم</b>",
    "health.section_api": "<b>ارتباط با تلگرام</b>",
    "health.section_runtime": "<b>اجرا</b>",
    "health.section_data": "<b>لایه داده</b>",
}

_CATALOG: dict[str, dict[str, str]] = {"fa": _FA}


def t(key: str, locale: str = DEFAULT_LOCALE, /, **kwargs: object) -> str:
    """Look up `key`; formats with kwargs. Missing keys surface loudly."""
    table = _CATALOG.get(locale) or _CATALOG[DEFAULT_LOCALE]
    template = table.get(key)
    if template is None:
        template = _CATALOG[DEFAULT_LOCALE].get(key)
    if template is None:
        return f"⟨{key}⟩"
    return template.format(**kwargs) if kwargs else template


def get_help_body(registry: EmojiRegistry) -> str:
    """Format help text with dynamic EmojiRegistry icons."""
    idea = registry.text("idea")
    paw = registry.text("paw")
    bullet = registry.text("bullet")
    return (
        f"{idea} <b>چجوری با Mewvia کار کنم؟ (خیلی ساده!)</b> {paw}\n\n"
        "برای استفاده از ربات نیازی به زدن هیچ دستور خاصی "
        "نداری!\n"
        "فقط <b>هر عکس، استیکر، ویدیو، گیف یا موزیکی که داری رو بفرست</b> "
        "تا منوی دکمه‌ها برات باز بشه:\n\n"
        f"{bullet} <b>تبدیل فرمت:</b> فایل رو بفرست و هر فرمتی خواستی "
        "(استیکر، عکس، گیف، ویدیو) تحویل بگیر.\n"
        f"{bullet} <b>تغییرات دسته‌ای (چند کار همزمان):</b> با انتخاب این گزینه "
        "می‌تونی همزمان فیلتر رنگی، متن و واید کردن رو روی یه فایل اعمال کنی!\n"
        f"{bullet} <b>افزودن متن (استیکرمانی):</b> روی عکس/استیکر متن فارسی بنویس، "
        "فونت، رنگ و اندازه‌شو انتخاب کن!\n"
        f"{bullet} <b>واید کردن (Wide):</b> فایل رو پهن و خنده‌دار (میم) کن!\n"
        f"{bullet} <b>فیلترهای رنگی:</b> افکت‌های جذاب مثل سیاه و سفید، نگاتیو، "
        "سپیا و بلور رو اعمال کن.\n"
        f"{bullet} <b>استیکرپک شخصی:</b> با ارسال عکس/استیکر، پک استیکر با نام خودت بساز.\n"
        f"{bullet} <b>ابزارهای ویدیو & ویس:</b> فشرده‌سازی ویدیو، تبدیل به ویدیومسیج گرد، "
        "تغییر سرعت موزیک و ویس.\n\n"
        f"{bullet} <b>دستیار مدیا در گروه‌ها:</b> پیشی رو به گروهت اضافه کن، "
        "روی هر مدیایی ریپلای بزن و کامند <code>/media</code> رو بفرست!"
    )


def has(key: str, locale: str = DEFAULT_LOCALE) -> bool:
    return key in _CATALOG.get(locale, {}) or key in _FA


def error_text(code: str, locale: str = DEFAULT_LOCALE, /, **kwargs: object) -> str:
    """Message for a MewviaError code, falling back to the generic one."""
    key = f"error.{code}"
    if not has(key, locale):
        key = "error.unknown"
        kwargs = {}
    return t(key, locale, **kwargs)
