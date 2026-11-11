"""ORM models.

Every model must be imported here: Alembic autogenerate only sees what is
attached to `Base.metadata` at import time.
"""

from __future__ import annotations

from app.db.models.conversion import Conversion
from app.db.models.emoji_slot import EmojiSlot
from app.db.models.game_stat import GameStat
from app.db.models.instagram_cookie import InstagramCookie
from app.db.models.setting import Setting
from app.db.models.sponsor_channel import SponsorChannel
from app.db.models.sponsor_seen import SponsorSeenMember
from app.db.models.sticker_set import StickerSet
from app.db.models.user import User

__all__ = [
    "Conversion",
    "EmojiSlot",
    "GameStat",
    "InstagramCookie",
    "Setting",
    "SponsorChannel",
    "SponsorSeenMember",
    "StickerSet",
    "User",
]
