"""Admin panel statistics helper."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.units import fa_digits, human_bytes
from app.db.models.conversion import Conversion
from app.db.models.sponsor_channel import SponsorChannel
from app.db.models.sponsor_seen import SponsorSeenMember
from app.db.models.sticker_set import StickerSet
from app.db.models.user import User

SOURCE_KIND_LABELS: dict[str, str] = {
    "video": "ویدیو",
    "animation": "گیف (GIF)",
    "photo": "عکس",
    "sticker_video": "استیکر ویدیویی",
    "sticker_static": "استیکر ثابت",
    "sticker_animated": "استیکر متحرک (TGS)",
    "video_note": "ویدیومسیج گرد",
    "voice": "پیام صوتی (Voice)",
    "audio": "صوت / موزیک",
    "document_image": "سند عکس",
    "document_video": "سند ویدیو",
    "url": "لینک وب / فضای مجازی",
}

TARGET_ID_LABELS: dict[str, str] = {
    "gif": "گیف (GIF)",
    "webm_sticker": "استیکر ویدیو",
    "webp": "استیکر عکس (WEBP)",
    "vtool_note": "ویدیومسیج گرد",
    "mp4": "ویدیو (MP4)",
    "png": "عکس (PNG)",
    "vtool_mute": "بی‌صدا کردن ویدیو",
    "vtool_compress": "فشرده‌سازی ویدیو",
    "vtool_reverse": "معکوس کردن ویدیو",
    "atool_to_voice": "تبدیل به ویس",
    "atool_to_mp3": "تبدیل به MP3",
    "atool_extract": "استخراج صدا از ویدیو",
    "atool_set_speed": "تغییر سرعت صدا",
    "atool_tag_edit": "ویرایش تگ و کاور موزیک",
    "sticker_pack": "ساخت استیکرپک",
    "text_overlay": "افزودن متن به مدیا",
    "wide": "واید کردن مدیا",
    "filter_bw": "فیلتر سیاه و سفید",
    "filter_negative": "فیلتر نگاتیو",
    "filter_sepia": "فیلتر سپیا",
    "filter_bright": "فیلتر درخشان",
    "filter_blur": "فیلتر بلور",
    "dl_youtube": "دانلود از یوتیوب",
    "dl_instagram": "دانلود از اینستاگرام",
    "dl_tiktok": "دانلود از تیک‌تاک",
    "dl_twitter": "دانلود از توییتر (X)",
    "dl_pinterest": "دانلود از پینترست",
    "dl_soundcloud": "دانلود از ساندکلاد",
    "dl_reddit": "دانلود از ردیت",
    "dl_aparat": "دانلود از آپارات",
    "dl_facebook": "دانلود از فیسبوک",
    "dl_other": "دانلود از سایر وب‌سایت‌ها",
}

#: Conversion target_ids that belong to the Music & Voice toolkit.
MUSIC_VOICE_TARGETS: tuple[str, ...] = (
    "atool_to_voice",
    "atool_to_mp3",
    "atool_extract",
    "atool_set_speed",
    "atool_tag_edit",
)

#: Source kinds counted as music/voice intake.
MUSIC_VOICE_SOURCES: tuple[str, ...] = ("voice", "audio")


@dataclass(frozen=True, slots=True)
class AdminStats:
    # --- Basic User counts ---
    total_users: int
    active_today: int
    active_week: int
    banned_users: int
    blocked_users: int

    # --- Basic Conversion metrics ---
    total_conversions: int
    successful_conversions: int
    total_in_bytes: int
    total_out_bytes: int

    # --- Advanced User Segmentation ---
    new_today: int = 0
    new_week: int = 0
    engaged_users: int = 0  # >=1 successful conversion (real bot users)
    passerby_users: int = 0  # registered but never completed a conversion
    premium_users: int = 0  # is_premium is True
    converting_users: int = 0  # unique users with >=1 successful conversion

    # --- Advanced Media Metrics ---
    failed_conversions: int = 0
    avg_duration_ms: float = 0.0
    top_sources: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    top_targets: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    total_sticker_packs: int = 0
    total_stickers_count: int = 0

    # --- Music & Voice Toolkit ---
    music_voice_total: int = 0
    music_voice_success: int = 0
    music_voice_bytes: int = 0
    music_voice_tools: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    voice_intake: int = 0
    audio_intake: int = 0

    # --- Sponsor & Forced Membership Analytics ---
    total_sponsors: int = 0
    active_sponsors: int = 0
    total_seen_members: int = 0
    sponsor_breakdown: tuple[tuple[str, int, int], ...] = field(default_factory=tuple)

    @property
    def success_rate(self) -> float:
        if self.total_conversions == 0:
            return 100.0
        return (self.successful_conversions / self.total_conversions) * 100.0

    @property
    def conversion_user_rate(self) -> float:
        if self.total_users == 0:
            return 0.0
        return (self.converting_users / self.total_users) * 100.0

    @property
    def engaged_user_rate(self) -> float:
        if self.total_users == 0:
            return 0.0
        return (self.engaged_users / self.total_users) * 100.0

    @property
    def music_voice_success_rate(self) -> float:
        if self.music_voice_total == 0:
            return 100.0
        return (self.music_voice_success / self.music_voice_total) * 100.0


async def get_admin_stats(session: AsyncSession) -> AdminStats:
    """Compute comprehensive system statistics across all operational dimensions."""
    now = datetime.now(UTC)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    # 1. Single-pass User segmentation query
    user_stmt = select(
        func.count(User.telegram_id),
        func.coalesce(func.sum(case((User.is_banned.is_(True), 1), else_=0)), 0),
        func.coalesce(func.sum(case((User.has_blocked_bot.is_(True), 1), else_=0)), 0),
        func.coalesce(func.sum(case((User.last_seen_at >= day_ago, 1), else_=0)), 0),
        func.coalesce(func.sum(case((User.last_seen_at >= week_ago, 1), else_=0)), 0),
        func.coalesce(func.sum(case((User.created_at >= day_ago, 1), else_=0)), 0),
        func.coalesce(func.sum(case((User.created_at >= week_ago, 1), else_=0)), 0),
        func.coalesce(func.sum(case((User.has_used_bot.is_(True), 1), else_=0)), 0),
        func.coalesce(func.sum(case((User.is_premium.is_(True), 1), else_=0)), 0),
    )
    user_row = (await session.execute(user_stmt)).one()
    (
        total_users,
        banned_users,
        blocked_users,
        active_today,
        active_week,
        new_today,
        new_week,
        engaged_users,
        premium_users,
    ) = (
        int(user_row[0]),
        int(user_row[1]),
        int(user_row[2]),
        int(user_row[3]),
        int(user_row[4]),
        int(user_row[5]),
        int(user_row[6]),
        int(user_row[7]),
        int(user_row[8]),
    )
    passerby_users = max(0, total_users - engaged_users)

    # 2. Single-pass Conversion aggregate query
    conv_stmt = select(
        func.count(Conversion.id),
        func.coalesce(func.sum(case((Conversion.status == "success", 1), else_=0)), 0),
        func.coalesce(func.sum(case((Conversion.status != "success", 1), else_=0)), 0),
        func.count(
            func.distinct(
                case((Conversion.status == "success", Conversion.telegram_id), else_=None)
            )
        ),
        func.coalesce(func.sum(Conversion.in_bytes), 0),
        func.coalesce(func.sum(Conversion.out_bytes), 0),
        func.coalesce(
            func.avg(case((Conversion.status == "success", Conversion.duration_ms), else_=None)),
            0,
        ),
    )
    conv_row = (await session.execute(conv_stmt)).one()
    (
        total_conversions,
        successful_conversions,
        failed_conversions,
        converting_users,
        total_in_bytes,
        total_out_bytes,
        avg_duration_raw,
    ) = (
        int(conv_row[0]),
        int(conv_row[1]),
        int(conv_row[2]),
        int(conv_row[3]),
        int(conv_row[4]),
        int(conv_row[5]),
        float(conv_row[6]),
    )

    # 3. Top input media sources
    top_sources_raw = (
        await session.execute(
            select(Conversion.source_kind, func.count(Conversion.id))
            .group_by(Conversion.source_kind)
            .order_by(func.count(Conversion.id).desc())
            .limit(5)
        )
    ).all()
    top_sources = tuple((str(k), int(c)) for k, c in top_sources_raw if k)

    # 4. Top output targets / tools
    top_targets_raw = (
        await session.execute(
            select(Conversion.target_id, func.count(Conversion.id))
            .group_by(Conversion.target_id)
            .order_by(func.count(Conversion.id).desc())
            .limit(5)
        )
    ).all()
    top_targets = tuple((str(t), int(c)) for t, c in top_targets_raw if t)

    # 5. Sticker sets stats
    stickers_row = (
        await session.execute(
            select(
                func.count(StickerSet.id),
                func.coalesce(func.sum(StickerSet.stickers_count), 0),
            )
        )
    ).one()
    total_sticker_packs = int(stickers_row[0])
    total_stickers_count = int(stickers_row[1])

    # 5b. Music & Voice toolkit stats (single-pass over conversions).
    mv_row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (Conversion.target_id.in_(MUSIC_VOICE_TARGETS), 1),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    Conversion.target_id.in_(MUSIC_VOICE_TARGETS),
                                    Conversion.status == "success",
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (Conversion.target_id.in_(MUSIC_VOICE_TARGETS), Conversion.out_bytes),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(case((Conversion.source_kind == "voice", 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((Conversion.source_kind == "audio", 1), else_=0)),
                    0,
                ),
            )
        )
    ).one()
    music_voice_total = int(mv_row[0])
    music_voice_success = int(mv_row[1])
    music_voice_bytes = int(mv_row[2])
    voice_intake = int(mv_row[3])
    audio_intake = int(mv_row[4])

    # 5c. Per-tool breakdown for the music & voice section.
    mv_tools_raw = (
        await session.execute(
            select(Conversion.target_id, func.count(Conversion.id))
            .where(Conversion.target_id.in_(MUSIC_VOICE_TARGETS))
            .group_by(Conversion.target_id)
            .order_by(func.count(Conversion.id).desc())
        )
    ).all()
    music_voice_tools = tuple((str(t), int(c)) for t, c in mv_tools_raw if t)

    # 6. Sponsor channels & seen members
    sponsor_counts = (
        await session.execute(
            select(
                func.count(SponsorChannel.id),
                func.coalesce(func.sum(case((SponsorChannel.is_active.is_(True), 1), else_=0)), 0),
            )
        )
    ).one()
    total_sponsors = int(sponsor_counts[0])
    active_sponsors = int(sponsor_counts[1])

    total_seen_members = (
        await session.execute(select(func.count(func.distinct(SponsorSeenMember.user_id))))
    ).scalar_one()

    breakdown_raw = (
        await session.execute(
            select(
                SponsorChannel.title,
                SponsorChannel.channel_id,
                func.count(SponsorSeenMember.user_id).label("seen_count"),
            )
            .outerjoin(
                SponsorSeenMember,
                SponsorChannel.channel_id == SponsorSeenMember.channel_id,
            )
            .where(SponsorChannel.is_active.is_(True))
            .group_by(SponsorChannel.id, SponsorChannel.title, SponsorChannel.channel_id)
            .order_by(func.count(SponsorSeenMember.user_id).desc())
        )
    ).all()
    sponsor_breakdown = tuple((str(row[0]), int(row[1]), int(row[2])) for row in breakdown_raw)

    return AdminStats(
        total_users=total_users,
        active_today=active_today,
        active_week=active_week,
        banned_users=banned_users,
        blocked_users=blocked_users,
        total_conversions=total_conversions,
        successful_conversions=successful_conversions,
        total_in_bytes=total_in_bytes,
        total_out_bytes=total_out_bytes,
        new_today=new_today,
        new_week=new_week,
        engaged_users=engaged_users,
        passerby_users=passerby_users,
        premium_users=premium_users,
        converting_users=converting_users,
        failed_conversions=failed_conversions,
        avg_duration_ms=avg_duration_raw,
        top_sources=top_sources,
        top_targets=top_targets,
        total_sticker_packs=total_sticker_packs,
        total_stickers_count=total_stickers_count,
        music_voice_total=music_voice_total,
        music_voice_success=music_voice_success,
        music_voice_bytes=music_voice_bytes,
        music_voice_tools=music_voice_tools,
        voice_intake=voice_intake,
        audio_intake=audio_intake,
        total_sponsors=total_sponsors,
        active_sponsors=active_sponsors,
        total_seen_members=int(total_seen_members),
        sponsor_breakdown=sponsor_breakdown,
    )


def format_stats_overview(stats: AdminStats) -> str:
    """Format overview & user segmentation tab."""
    engaged_pct = stats.engaged_user_rate
    passerby_pct = (stats.passerby_users / stats.total_users * 100) if stats.total_users else 0.0
    prem_pct = (stats.premium_users / stats.total_users * 100) if stats.total_users else 0.0
    conv_pct = stats.conversion_user_rate

    return (
        "<b>📊 آمار جامع ربات Mewvia | بخش کاربران و رشد</b>\n\n"
        "<b>👥 وضعیت کلی و فعالیت:</b>\n"
        f"• کل کاربران: <code>{fa_digits(str(stats.total_users))}</code>\n"
        f"• ورودی‌های جدید ۲۴ ساعت اخیر: <code>{fa_digits(str(stats.new_today))}</code>\n"
        f"• ورودی‌های جدید ۷ روز اخیر: <code>{fa_digits(str(stats.new_week))}</code>\n"
        f"• فعال امروز (۲۴ ساعت): <code>{fa_digits(str(stats.active_today))}</code>\n"
        f"• فعال هفته (۷ روز): <code>{fa_digits(str(stats.active_week))}</code>\n\n"
        "<b>🎯 کیفیت و تعامل مخاطبان:</b>\n"
        f"• کاربران واقعی (۱+ استفاده موفق): "
        f"<code>{fa_digits(str(stats.engaged_users))}</code> "
        f"(<code>{fa_digits(f'{engaged_pct:.1f}')}%</code>)\n"
        f"• ثبت‌شده بدون استفاده: "
        f"<code>{fa_digits(str(stats.passerby_users))}</code> "
        f"(<code>{fa_digits(f'{passerby_pct:.1f}')}%</code>)\n"
        f"• کاربران دارای پرمیوم تلگرام: <code>{fa_digits(str(stats.premium_users))}</code> "
        f"(<code>{fa_digits(f'{prem_pct:.1f}')}%</code>)\n"
        f"• استفاده‌کنندگان از ابزارهای مدیا: <code>{fa_digits(str(stats.converting_users))}</code> "
        f"(<code>{fa_digits(f'{conv_pct:.1f}')}%</code>)\n\n"
        "<b>🚫 مسدودی و ریزش:</b>\n"
        f"• مسدود شده توسط ادمین: <code>{fa_digits(str(stats.banned_users))}</code>\n"
        f"• ربات را بلاک کرده‌اند: <code>{fa_digits(str(stats.blocked_users))}</code>"
    )


def format_stats_media(stats: AdminStats) -> str:
    """Format media processing & performance tab."""
    in_h = human_bytes(stats.total_in_bytes)
    out_h = human_bytes(stats.total_out_bytes)
    failed_pct = (
        (stats.failed_conversions / stats.total_conversions * 100)
        if stats.total_conversions
        else 0.0
    )
    avg_s = stats.avg_duration_ms / 1000.0

    lines = [
        "<b>🎨 آمار جامع مدیا، پردازش‌ها و ابزارها</b>\n",
        "<b>🔄 عملیات‌های تبدیل:</b>",
        f"• کل پردازش‌ها: <code>{fa_digits(str(stats.total_conversions))}</code>",
        f"• تبدیل‌های موفق: <code>{fa_digits(str(stats.successful_conversions))}</code> "
        f"(<code>{fa_digits(f'{stats.success_rate:.1f}')}%</code>)",
        f"• ناموفق / رد شده: <code>{fa_digits(str(stats.failed_conversions))}</code> "
        f"(<code>{fa_digits(f'{failed_pct:.1f}')}%</code>)",
        f"• میانگین زمان تبدیل: <code>{fa_digits(f'{avg_s:.2f}')} ثانیه</code>\n",
        "<b>💾 ترافیک سرور:</b>",
        f"• کل حجم دریافتی: <code>{in_h}</code>",
        f"• کل حجم ارسالی: <code>{out_h}</code>\n",
    ]

    if stats.top_sources:
        lines.append("<b>📥 پرکاربردترین ورودی‌ها:</b>")
        for kind, count in stats.top_sources:
            label = SOURCE_KIND_LABELS.get(kind, kind)
            lines.append(f"• {label}: <code>{fa_digits(str(count))}</code> بار")
        lines.append("")

    if stats.top_targets:
        lines.append("<b>📤 محبوب‌ترین خروجی‌ها / ابزارها:</b>")
        for target, count in stats.top_targets:
            label = TARGET_ID_LABELS.get(target, target)
            lines.append(f"• {label}: <code>{fa_digits(str(count))}</code> بار")
        lines.append("")

    if stats.total_sticker_packs > 0 or stats.total_stickers_count > 0:
        lines.append("<b>📦 استیکرپک‌ها:</b>")
        lines.append(f"• پک‌های ساخته شده: <code>{fa_digits(str(stats.total_sticker_packs))}</code>")
        lines.append(f"• مجموع استیکرها: <code>{fa_digits(str(stats.total_stickers_count))}</code>")
        lines.append("")

    lines.append("<b>🎵 ابزارهای موزیک و ویس:</b>")
    if stats.music_voice_total > 0:
        lines.append(
            f"• کل عملیات صوتی: <code>{fa_digits(str(stats.music_voice_total))}</code> "
            f"(موفق: <code>{fa_digits(f'{stats.music_voice_success_rate:.1f}')}%</code>)"
        )
        lines.append(f"• حجم خروجی صوتی: <code>{human_bytes(stats.music_voice_bytes)}</code>")
    else:
        lines.append("• <i>هنوز عملیات صوتی ثبت نشده است.</i>")
    if stats.voice_intake or stats.audio_intake:
        lines.append(
            f"• ورودی ویس: <code>{fa_digits(str(stats.voice_intake))}</code> | "
            f"ورودی موزیک: <code>{fa_digits(str(stats.audio_intake))}</code>"
        )
    if stats.music_voice_tools:
        for target, count in stats.music_voice_tools:
            label = TARGET_ID_LABELS.get(target, target)
            lines.append(f"• {label}: <code>{fa_digits(str(count))}</code> بار")

    return "\n".join(lines)


def format_stats_sponsors(stats: AdminStats) -> str:
    """Format sponsor & forced membership growth analytics tab."""
    lines = [
        "<b>📢 آمار جامع اسپانسر و عضویت اجباری</b>\n",
        "<b>📊 وضعیت کلی گیت اسپانسر:</b>",
        f"• کل کانال‌های ثبت‌شده: <code>{fa_digits(str(stats.total_sponsors))}</code>",
        f"• کانال‌های فعال در گیت: <code>{fa_digits(str(stats.active_sponsors))}</code>",
        (
            f"• کاربران تاییدشده عضو (یکتا): "
            f"<code>{fa_digits(str(stats.total_seen_members))}</code> نفر\n"
        ),
    ]

    if stats.sponsor_breakdown:
        lines.append("<b>🔹 اعضای تاییدشده به تفکیک کانال‌های فعال:</b>")
        for title, _channel_id, seen_count in stats.sponsor_breakdown:
            lines.append(
                f"• <b>{title}</b>: <code>{fa_digits(str(seen_count))}</code> ممبر تاییدشده"
            )
        lines.append("")
    else:
        lines.append("<i>هنوز کانال اسپانسر فعالی با ممبر تاییدشده ثبت نشده است.</i>\n")

    lines.append(
        "💡 <b>مکانیزم گیت:</b>\n"
        "«ممبر تاییدشده» کاربری است که عضویت او حداقل یک‌بار توسط ربات استعلام و ثبت شده است. "
        "در صورت لفت دادن کاربر، ربات در همان ثانیه پیام دعوت خودکار در پی‌وی می‌فرستد."
    )
    return "\n".join(lines)


def format_stats_text(stats: AdminStats) -> str:
    """Default backward-compatible formatter (points to overview)."""
    return format_stats_overview(stats)
