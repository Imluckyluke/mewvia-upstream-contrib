"""ORM models.

Every model must be imported here: Alembic autogenerate only sees what is
attached to `Base.metadata` at import time.
"""

from __future__ import annotations

from app.db.models.conversion import Conversion
from app.db.models.emoji_slot import EmojiSlot
from app.db.models.game_stat import GameStat
from app.db.models.group_game_result import GroupGameResult
from app.db.models.group_game_stat import GroupGameStat
from app.db.models.instagram_cookie import InstagramCookie
from app.db.models.quota import QuotaLease
from app.db.models.setting import Setting
from app.db.models.sponsor_channel import SponsorChannel
from app.db.models.sponsor_seen import SponsorSeenMember
from app.db.models.sticker_set import StickerSet
from app.db.models.user import User

__all__ = [
    "Conversion",
    "EmojiSlot",
    "GameStat",
    "GroupGameResult",
    "GroupGameStat",
    "InstagramCookie",
    "QuotaLease",
    "Setting",
    "SponsorChannel",
    "SponsorSeenMember",
    "StickerSet",
    "User",
]
