"""Admin panel statistics helper."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.ui.emoji import EmojiRegistry

from app.core.units import fa_digits, human_bytes
from app.db.models.conversion import Conversion
from app.db.models.instagram_cookie import InstagramCookie
from app.db.models.sponsor_channel import SponsorChannel
from app.db.models.sponsor_seen import SponsorSeenMember
from app.db.models.sticker_set import StickerSet
from app.db.models.user import User
from app.ui.emoji import em

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
    "dl_spotify": "دانلود از اسپاتیفای",
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

#: Metadata for supported media downloader platforms.
PLATFORM_META: dict[str, dict[str, str]] = {
    "dl_youtube": {"name": "یوتیوب (YouTube)", "icon": "🔴"},
    "dl_instagram": {"name": "اینستاگرام (Instagram)", "icon": "🟣"},
    "dl_spotify": {"name": "اسپاتیفای (Spotify)", "icon": "🟢"},
    "dl_soundcloud": {"name": "ساندکلاد (SoundCloud)", "icon": "🟠"},
    "dl_tiktok": {"name": "تیک‌تاک (TikTok)", "icon": "⚫️"},
    "dl_twitter": {"name": "توییتر (X)", "icon": "🔵"},
    "dl_pinterest": {"name": "پینترست (Pinterest)", "icon": "📌"},
    "dl_reddit": {"name": "ردیت (Reddit)", "icon": "🟧"},
    "dl_aparat": {"name": "آپارات (Aparat)", "icon": "📺"},
    "dl_facebook": {"name": "فیسبوک (Facebook)", "icon": "🔷"},
    "dl_other": {"name": "سایر پلتفرم‌ها", "icon": "🌐"},
}


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

    # --- Downloader Analytics ---
    downloader_total: int = 0
    downloader_success: int = 0
    downloader_failed: int = 0
    downloader_bytes: int = 0
    downloader_today: int = 0
    downloader_week: int = 0
    downloader_unique_users: int = 0
    downloader_avg_duration_ms: float = 0.0
    downloader_platforms: tuple[tuple[str, str, str, int, int, int], ...] = field(
        default_factory=tuple
    )
    downloader_total_cookies: int = 0
    downloader_active_cookies: int = 0

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

    @property
    def downloader_success_rate(self) -> float:
        if self.downloader_total == 0:
            return 100.0
        return (self.downloader_success / self.downloader_total) * 100.0


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

    # 7. Downloader analytics (single-pass aggregate over dl_% targets)
    dl_row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(case((Conversion.target_id.like("dl_%"), 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    Conversion.target_id.like("dl_%"),
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
                            (
                                and_(
                                    Conversion.target_id.like("dl_%"),
                                    Conversion.status != "success",
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
                            (Conversion.target_id.like("dl_%"), Conversion.out_bytes),
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
                                    Conversion.target_id.like("dl_%"),
                                    Conversion.created_at >= day_ago,
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
                            (
                                and_(
                                    Conversion.target_id.like("dl_%"),
                                    Conversion.created_at >= week_ago,
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.count(
                    func.distinct(
                        case(
                            (Conversion.target_id.like("dl_%"), Conversion.telegram_id),
                            else_=None,
                        )
                    )
                ),
                func.coalesce(
                    func.avg(
                        case(
                            (
                                and_(
                                    Conversion.target_id.like("dl_%"),
                                    Conversion.status == "success",
                                ),
                                Conversion.duration_ms,
                            ),
                            else_=None,
                        )
                    ),
                    0,
                ),
            )
        )
    ).one()

    downloader_total = int(dl_row[0])
    downloader_success = int(dl_row[1])
    downloader_failed = int(dl_row[2])
    downloader_bytes = int(dl_row[3])
    downloader_today = int(dl_row[4])
    downloader_week = int(dl_row[5])
    downloader_unique_users = int(dl_row[6])
    downloader_avg_duration_ms = float(dl_row[7])

    # Per-platform breakdown
    dl_platforms_raw = (
        await session.execute(
            select(
                Conversion.target_id,
                func.count(Conversion.id),
                func.coalesce(
                    func.sum(case((Conversion.status == "success", 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (Conversion.status == "success", Conversion.out_bytes),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
            .where(Conversion.target_id.like("dl_%"))
            .group_by(Conversion.target_id)
            .order_by(func.count(Conversion.id).desc())
        )
    ).all()

    downloader_platforms: list[tuple[str, str, str, int, int, int]] = []
    for p_row in dl_platforms_raw:
        tgt = str(p_row[0])
        tot = int(p_row[1])
        succ = int(p_row[2])
        b_count = int(p_row[3])
        meta = PLATFORM_META.get(
            tgt, {"name": TARGET_ID_LABELS.get(tgt, tgt), "icon": em("web")}
        )
        downloader_platforms.append((tgt, meta["name"], meta["icon"], tot, succ, b_count))

    # Instagram cookies status
    cookie_counts = (
        await session.execute(
            select(
                func.count(InstagramCookie.id),
                func.coalesce(
                    func.sum(case((InstagramCookie.is_active.is_(True), 1), else_=0)),
                    0,
                ),
            )
        )
    ).one()
    downloader_total_cookies = int(cookie_counts[0])
    downloader_active_cookies = int(cookie_counts[1])

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
        downloader_total=downloader_total,
        downloader_success=downloader_success,
        downloader_failed=downloader_failed,
        downloader_bytes=downloader_bytes,
        downloader_today=downloader_today,
        downloader_week=downloader_week,
        downloader_unique_users=downloader_unique_users,
        downloader_avg_duration_ms=downloader_avg_duration_ms,
        downloader_platforms=tuple(downloader_platforms),
        downloader_total_cookies=downloader_total_cookies,
        downloader_active_cookies=downloader_active_cookies,
    )


def format_stats_overview(stats: AdminStats, registry: EmojiRegistry | None = None) -> str:
    """Format overview & user segmentation tab."""
    engaged_pct = stats.engaged_user_rate
    passerby_pct = (stats.passerby_users / stats.total_users * 100) if stats.total_users else 0.0
    prem_pct = (stats.premium_users / stats.total_users * 100) if stats.total_users else 0.0
    conv_pct = stats.conversion_user_rate

    ic_stats = registry.text("stats") if registry else "📊"
    ic_users = registry.text("users") if registry else "👥"
    ic_bullet = registry.text("bullet") if registry else "•"
    ic_ban = registry.text("ban") if registry else "🚫"
    ic_idea = registry.text("idea") if registry else f"{em('target')}"

    return (
        f"<b>{ic_stats} آمار جامع ربات Mewvia | بخش کاربران و رشد</b>\n\n"
        f"<b>{ic_users} وضعیت کلی و فعالیت:</b>\n"
        f"{ic_bullet} کل کاربران: <code>{fa_digits(str(stats.total_users))}</code>\n"
        f"{ic_bullet} ورودی‌های جدید ۲۴ ساعت اخیر: <code>{fa_digits(str(stats.new_today))}</code>\n"
        f"{ic_bullet} ورودی‌های جدید ۷ روز اخیر: <code>{fa_digits(str(stats.new_week))}</code>\n"
        f"{ic_bullet} فعال امروز (۲۴ ساعت): <code>{fa_digits(str(stats.active_today))}</code>\n"
        f"{ic_bullet} فعال هفته (۷ روز): <code>{fa_digits(str(stats.active_week))}</code>\n\n"
        f"<b>{ic_idea} کیفیت و تعامل مخاطبان:</b>\n"
        f"{ic_bullet} کاربران واقعی (۱+ استفاده موفق): "
        f"<code>{fa_digits(str(stats.engaged_users))}</code> "
        f"(<code>{fa_digits(f'{engaged_pct:.1f}')}%</code>)\n"
        f"{ic_bullet} ثبت‌شده بدون استفاده: "
        f"<code>{fa_digits(str(stats.passerby_users))}</code> "
        f"(<code>{fa_digits(f'{passerby_pct:.1f}')}%</code>)\n"
        f"{ic_bullet} کاربران دارای پرمیوم تلگرام: "
        f"<code>{fa_digits(str(stats.premium_users))}</code> "
        f"(<code>{fa_digits(f'{prem_pct:.1f}')}%</code>)\n"
        f"{ic_bullet} استفاده‌کنندگان از ابزارهای مدیا: "
        f"<code>{fa_digits(str(stats.converting_users))}</code> "
        f"(<code>{fa_digits(f'{conv_pct:.1f}')}%</code>)\n\n"
        f"<b>{ic_ban} مسدودی و ریزش:</b>\n"
        f"{ic_bullet} مسدود شده توسط ادمین: <code>{fa_digits(str(stats.banned_users))}</code>\n"
        f"{ic_bullet} ربات را بلاک کرده‌اند: <code>{fa_digits(str(stats.blocked_users))}</code>"
    )


def format_stats_media(stats: AdminStats, registry: EmojiRegistry | None = None) -> str:
    """Format media processing & performance tab."""
    in_h = human_bytes(stats.total_in_bytes)
    out_h = human_bytes(stats.total_out_bytes)
    failed_pct = (
        (stats.failed_conversions / stats.total_conversions * 100)
        if stats.total_conversions
        else 0.0
    )
    avg_s = stats.avg_duration_ms / 1000.0

    ic_convert = registry.text("convert") if registry else "🎨"
    ic_reverse = registry.text("reverse") if registry else "🔄"
    ic_backup = registry.text("backup") if registry else "💾"
    ic_bullet = registry.text("bullet") if registry else "•"
    ic_download = registry.text("download") if registry else "📥"
    ic_pack = registry.text("pack") if registry else "📦"
    ic_audio = registry.text("audio") if registry else "🎵"

    lines = [
        f"<b>{ic_convert} آمار جامع مدیا، پردازش‌ها و ابزارها</b>\n",
        f"<b>{ic_reverse} عملیات‌های تبدیل:</b>",
        f"{ic_bullet} کل پردازش‌ها: <code>{fa_digits(str(stats.total_conversions))}</code>",
        f"{ic_bullet} تبدیل‌های موفق: <code>{fa_digits(str(stats.successful_conversions))}</code> "
        f"(<code>{fa_digits(f'{stats.success_rate:.1f}')}%</code>)",
        f"{ic_bullet} ناموفق / رد شده: <code>{fa_digits(str(stats.failed_conversions))}</code> "
        f"(<code>{fa_digits(f'{failed_pct:.1f}')}%</code>)",
        f"{ic_bullet} میانگین زمان تبدیل: <code>{fa_digits(f'{avg_s:.2f}')} ثانیه</code>\n",
        f"<b>{ic_backup} ترافیک سرور:</b>",
        f"{ic_bullet} کل حجم دریافتی: <code>{in_h}</code>",
        f"{ic_bullet} کل حجم ارسالی: <code>{out_h}</code>\n",
    ]

    if stats.top_sources:
        lines.append(f"<b>{ic_download} پرکاربردترین ورودی‌ها:</b>")
        for kind, count in stats.top_sources:
            label = SOURCE_KIND_LABELS.get(kind, kind)
            lines.append(f"{ic_bullet} {label}: <code>{fa_digits(str(count))}</code> بار")
        lines.append("")

    if stats.top_targets:
        rocket_icon = registry.text("rocket") if registry else em("upload")
        lines.append(f"<b>{rocket_icon} محبوبترین خروجیها / ابزارها:</b>")
        for target, count in stats.top_targets:
            label = TARGET_ID_LABELS.get(target, target)
            lines.append(f"{ic_bullet} {label}: <code>{fa_digits(str(count))}</code> بار")
        lines.append("")

    if stats.total_sticker_packs > 0 or stats.total_stickers_count > 0:
        lines.append(f"<b>{ic_pack} استیکرپک‌ها:</b>")
        lines.append(
            f"{ic_bullet} پک‌های ساخته شده: <code>{fa_digits(str(stats.total_sticker_packs))}</code>"
        )
        lines.append(
            f"{ic_bullet} مجموع استیکرها: <code>{fa_digits(str(stats.total_stickers_count))}</code>"
        )
        lines.append("")

    lines.append(f"<b>{ic_audio} ابزارهای موزیک و ویس:</b>")
    if stats.music_voice_total > 0:
        lines.append(
            f"{ic_bullet} کل عملیات صوتی: <code>{fa_digits(str(stats.music_voice_total))}</code> "
            f"(موفق: <code>{fa_digits(f'{stats.music_voice_success_rate:.1f}')}%</code>)"
        )
        lines.append(
            f"{ic_bullet} حجم خروجی صوتی: <code>{human_bytes(stats.music_voice_bytes)}</code>"
        )
    else:
        lines.append(f"{ic_bullet} <i>هنوز عملیات صوتی ثبت نشده است.</i>")
    if stats.voice_intake or stats.audio_intake:
        lines.append(
            f"{ic_bullet} ورودی ویس: <code>{fa_digits(str(stats.voice_intake))}</code> | "
            f"ورودی موزیک: <code>{fa_digits(str(stats.audio_intake))}</code>"
        )
    if stats.music_voice_tools:
        for target, count in stats.music_voice_tools:
            label = TARGET_ID_LABELS.get(target, target)
            lines.append(f"{ic_bullet} {label}: <code>{fa_digits(str(count))}</code> بار")

    return "\n".join(lines)


def format_stats_sponsors(stats: AdminStats, registry: EmojiRegistry | None = None) -> str:
    """Format sponsor & forced membership growth analytics tab."""
    ic_sponsor = registry.text("sponsor") if registry else "📢"
    ic_stats = registry.text("stats") if registry else "📊"
    ic_bullet = registry.text("bullet") if registry else "•"
    ic_idea = registry.text("idea") if registry else "💡"

    lines = [
        f"<b>{ic_sponsor} آمار جامع اسپانسر و عضویت اجباری</b>\n",
        f"<b>{ic_stats} وضعیت کلی گیت اسپانسر:</b>",
        f"{ic_bullet} کل کانال‌های ثبت‌شده: <code>{fa_digits(str(stats.total_sponsors))}</code>",
        f"{ic_bullet} کانال‌های فعال در گیت: <code>{fa_digits(str(stats.active_sponsors))}</code>",
        (
            f"{ic_bullet} کاربران تاییدشده عضو (یکتا): "
            f"<code>{fa_digits(str(stats.total_seen_members))}</code> نفر\n"
        ),
    ]

    if stats.sponsor_breakdown:
        lines.append(f"<b>{ic_bullet} اعضای تاییدشده به تفکیک کانال‌های فعال:</b>")
        for title, _channel_id, seen_count in stats.sponsor_breakdown:
            lines.append(
                f"{ic_bullet} <b>{title}</b>: "
                f"<code>{fa_digits(str(seen_count))}</code> ممبر تاییدشده"
            )
        lines.append("")
    else:
        lines.append("<i>هنوز کانال اسپانسر فعالی با ممبر تاییدشده ثبت نشده است.</i>\n")

    lines.append(
        f"{ic_idea} <b>مکانیزم گیت:</b>\n"
        "«ممبر تاییدشده» کاربری است که عضویت او حداقل یک‌بار توسط ربات استعلام و ثبت شده است. "
        "در صورت لفت دادن کاربر، ربات در همان ثانیه پیام دعوت خودکار در پی‌وی می‌فرستد."
    )
    return "\n".join(lines)


def format_stats_downloader(stats: AdminStats, registry: EmojiRegistry | None = None) -> str:
    """Format downloader metrics & per-platform breakdown tab."""
    avg_s = stats.downloader_avg_duration_ms / 1000.0 if stats.downloader_avg_duration_ms else 0.0
    bytes_h = human_bytes(stats.downloader_bytes)
    succ_rate = stats.downloader_success_rate

    ic_download = registry.text("download") if registry else "📥"
    ic_stats = registry.text("stats") if registry else "📊"
    ic_bullet = registry.text("bullet") if registry else "•"
    ic_tool = registry.text("tool") if registry else f"{em('settings')}"

    lines = [
        f"<b>{ic_download} آمار جامع دانلودر چندرسانه‌ای | Mewvia Downloader</b>\n",
        f"<b>{ic_stats} عملکرد کلی دانلودها:</b>",
        f"{ic_bullet} کل درخواست‌های دانلود: <code>{fa_digits(str(stats.downloader_total))}</code>",
        (
            f"{ic_bullet} دانلودهای موفق: <code>{fa_digits(str(stats.downloader_success))}</code> "
            f"(<code>{fa_digits(f'{succ_rate:.1f}')}%</code>)"
        ),
        f"{ic_bullet} دانلودهای ناموفق / خطا: "
        f"<code>{fa_digits(str(stats.downloader_failed))}</code>",
        (
            f"{ic_bullet} کاربران یکتا دانلودر: "
            f"<code>{fa_digits(str(stats.downloader_unique_users))}</code> کاربر"
        ),
        f"{ic_bullet} دانلودهای ۲۴ ساعت اخیر: "
        f"<code>{fa_digits(str(stats.downloader_today))}</code>",
        f"{ic_bullet} دانلودهای ۷ روز اخیر: <code>{fa_digits(str(stats.downloader_week))}</code>",
        f"{ic_bullet} میانگین زمان دانلود: <code>{fa_digits(f'{avg_s:.2f}')} ثانیه</code>",
        f"{ic_bullet} حجم کل ترافیک ارسالی: <code>{bytes_h}</code>\n",
    ]

    lines.append(f"<b>{ic_bullet} تفکیک پلتفرم‌ها و آمار زیرمجموعه:</b>")
    if stats.downloader_platforms:
        for _tgt, name, icon, total, succ, b_count in stats.downloader_platforms:
            rate = (succ / total * 100) if total else 0.0
            size_str = human_bytes(b_count)
            lines.append(
                f"{icon} <b>{name}:</b>\n"
                f"   └ تعداد: <code>{fa_digits(str(total))}</code> | "
                f"موفق: <code>{fa_digits(str(succ))}</code> "
                f"(<code>{fa_digits(f'{rate:.0f}')}%</code>) | "
                f"حجم: <code>{size_str}</code>"
            )
        lines.append("")
    else:
        lines.append(f"{ic_bullet} <i>هنوز دانلودی از پلتفرم‌ها ثبت نشده است.</i>\n")

    lines.append(f"<b>{ic_tool} وضعیت زیرساخت و سرویس‌های دانلود:</b>")
    lines.append(
        f"{ic_bullet} اکانت‌های اینستاگرام فعال: "
        f"<code>{fa_digits(str(stats.downloader_active_cookies))}</code> از "
        f"<code>{fa_digits(str(stats.downloader_total_cookies))}</code> اکانت"
    )
    lines.append(f"{ic_bullet} تونل پراکسی WARP: وضعیت بررسی نشده")
    lines.append(f"{ic_bullet} ارائه‌دهنده PO-Token یوتیوب: وضعیت بررسی نشده")

    return "\n".join(lines)


def format_stats_text(stats: AdminStats) -> str:
    """Default backward-compatible formatter (points to overview)."""
    return format_stats_overview(stats)
