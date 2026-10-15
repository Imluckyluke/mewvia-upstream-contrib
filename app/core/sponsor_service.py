"""Sponsor membership checking service."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.sponsor_channel import SponsorChannel
from app.db.models.sponsor_seen import SponsorSeenMember
from app.db.repo.sponsor import SponsorRepo

log = get_logger(__name__)

# Short-lived membership cache: one update fans out to one get_chat_member
# per active sponsor channel. Without this a burst of updates (or a slow API)
# multiplies Telegram calls and invites FloodWait. Entries expire after 30s
# so leaves/joins are picked up quickly. Tests reset it via
# Tests reset it via clear_membership_cache() (autouse fixture in tests/test_sponsor.py).
_MEMBERSHIP_CACHE: dict[tuple[int, int], tuple[bool, float]] = {}
_MEMBERSHIP_TTL = 30.0
_MEMBERSHIP_CACHE_CAP = 4096

_ACTIVE_CHANNELS_CACHE: tuple[Sequence[SponsorChannel], float] | None = None
_ACTIVE_CHANNELS_TTL = 30.0


@dataclass(frozen=True, slots=True)
class LeaveEvent:
    """Snapshot of a channel the user just left (for the warm re-invite)."""

    channel_id: int
    title: str


# Leave events waiting to be claimed by the gate (user_id -> events).
# Claimed (and cleared) on read so the warm invite fires exactly once.
_RECENT_LEAVES: dict[int, list[LeaveEvent]] = {}
_RECENT_LEAVES_CAP = 1024


def clear_membership_cache() -> None:
    """Drop cached membership results, active channels cache, and pending leave events."""
    global _ACTIVE_CHANNELS_CACHE
    _ACTIVE_CHANNELS_CACHE = None
    _MEMBERSHIP_CACHE.clear()
    _RECENT_LEAVES.clear()


def claim_recent_leaves(user_id: int) -> list[LeaveEvent]:
    """Pop pending leave events for a user (exactly-once warm invites)."""
    return _RECENT_LEAVES.pop(user_id, [])


async def _track_seen_membership(
    session: AsyncSession, channel: SponsorChannel, user_id: int, in_channel: bool
) -> None:
    """Persist seen-membership so leave detection survives restarts.

    A row means "we positively saw this user inside this channel". A fresh
    False for a recorded pair is a leave event -> warm re-invite. Best-effort:
    if the table is missing (migration not applied yet) tracking is skipped
    silently and the gate falls back to the standard prompt.
    """
    try:
        if in_channel:
            stmt = (
                insert(SponsorSeenMember)
                .values(channel_id=channel.channel_id, user_id=user_id)
                .on_conflict_do_nothing(
                    index_elements=[
                        SponsorSeenMember.channel_id,
                        SponsorSeenMember.user_id,
                    ]
                )
            )
            await session.execute(stmt)
            await session.flush()
        else:
            seen = (
                await session.execute(
                    select(SponsorSeenMember).where(
                        SponsorSeenMember.channel_id == channel.channel_id,
                        SponsorSeenMember.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if seen is not None:
                await session.delete(seen)
                await session.flush()
                if len(_RECENT_LEAVES) < _RECENT_LEAVES_CAP:
                    _RECENT_LEAVES.setdefault(user_id, []).append(
                        LeaveEvent(channel_id=channel.channel_id, title=channel.title)
                    )
    except Exception as exc:
        log.warning("sponsor_seen_tracking_unavailable", error=str(exc))


JOINED_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
}


async def is_user_in_channel(bot: Bot, channel_id: int, user_id: int) -> bool:
    """Check if user is in channel using Fail-Open pattern."""
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        if member is None or getattr(member, "status", None) is None:
            log.warning(
                "sponsor_membership_none_fail_open",
                channel_id=channel_id,
                user_id=user_id,
            )
            return True
        if member.status in JOINED_STATUSES:
            return True
        if member.status == ChatMemberStatus.RESTRICTED:
            return bool(getattr(member, "is_member", False))
        return False
    except TelegramAPIError as exc:
        # Fail-Open pattern: if bot lacks admin rights or API fails, allow user through
        log.warning(
            "sponsor_membership_check_fail_open",
            channel_id=channel_id,
            user_id=user_id,
            error=str(exc),
        )
        return True
    except Exception as exc:
        log.error(
            "sponsor_membership_check_error", channel_id=channel_id, user_id=user_id, error=str(exc)
        )
        return True


async def get_missing_sponsors(
    bot: Bot, session: AsyncSession, user_id: int, *, fresh: bool = False
) -> list[SponsorChannel]:
    """Get list of active sponsor channels the user has NOT joined yet.

    Fail-Open at the database layer too: if `sponsor_channels` does not exist yet
    (migration not applied) the gate must not break every update in the bot.

    Pass fresh=True for explicit user-triggered re-checks (the «عضو شدم»
    button): the 30s membership cache is bypassed so a just-completed join is
    seen immediately. Automatic per-update gate checks stay cached (rate-limit).
    """
    global _ACTIVE_CHANNELS_CACHE
    now = time.monotonic()
    if (
        not fresh
        and _ACTIVE_CHANNELS_CACHE is not None
        and (now - _ACTIVE_CHANNELS_CACHE[1] < _ACTIVE_CHANNELS_TTL)
    ):
        active_channels = _ACTIVE_CHANNELS_CACHE[0]
    else:
        try:
            repo = SponsorRepo(session)
            active_channels = await repo.get_active_channels()
            _ACTIVE_CHANNELS_CACHE = (active_channels, now)
        except Exception as exc:
            log.warning("sponsor_table_unavailable_fail_open", error=str(exc))
            return []

    if not active_channels:
        return []
    missing = []
    for channel in active_channels:
        key = (channel.channel_id, user_id)
        cached = None if fresh else _MEMBERSHIP_CACHE.get(key)
        if cached is not None and now - cached[1] < _MEMBERSHIP_TTL:
            in_channel = cached[0]
            did_check = False
        else:
            in_channel = await is_user_in_channel(bot, channel.channel_id, user_id)
            _MEMBERSHIP_CACHE[key] = (in_channel, now)
            if len(_MEMBERSHIP_CACHE) > _MEMBERSHIP_CACHE_CAP:
                _MEMBERSHIP_CACHE.pop(next(iter(_MEMBERSHIP_CACHE)))
            did_check = True
        if did_check:
            await _track_seen_membership(session, channel, user_id, in_channel)
        if not in_channel:
            missing.append(channel)

    return missing


async def verify_bot_is_admin(bot: Bot, channel_id: int) -> tuple[bool, str]:
    """Check whether the bot itself is an administrator of `channel_id`.

    Returns (is_admin, human_readable_reason). Membership checks only work when
    the bot is an admin of the channel, so the admin panel refuses to register a
    channel until this passes.
    """
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id=channel_id, user_id=me.id)
    except TelegramAPIError as exc:
        return False, str(exc)
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)

    if member is None or getattr(member, "status", None) is None:
        return False, "member-not-found"

    if member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        return True, "ok"
    return False, f"status={member.status}"


async def resolve_channel(
    bot: Bot, target: str | int
) -> tuple[int, str, str | None, str | None] | None:
    """Resolve a channel target to (channel_id, title, username, invite_link).

    Returns None when Telegram cannot resolve the target (private link without a
    forwarded message, wrong username, bot not a member, …).
    """
    try:
        chat = await bot.get_chat(target)
    except Exception:
        return None

    if not chat:
        return None

    invite_link = getattr(chat, "invite_link", None)
    username = getattr(chat, "username", None)
    if not invite_link and username:
        invite_link = f"https://t.me/{username}"

    title = getattr(chat, "title", None) or username or str(getattr(chat, "id", 0))
    return getattr(chat, "id", 0), title, username, invite_link
