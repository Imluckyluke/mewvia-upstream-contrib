"""Repository layer.

Handlers talk to repositories, never to raw SQL. Keeps queries testable and
keeps the ORM out of feature code.
"""

from __future__ import annotations

from app.db.repo.conversion import ConversionRepo
from app.db.repo.emoji import EmojiRepo
from app.db.repo.setting import SettingRepo
from app.db.repo.sponsor import SponsorRepo
from app.db.repo.sticker_set import StickerSetRepo
from app.db.repo.user import UserRepo

__all__ = [
    "ConversionRepo",
    "EmojiRepo",
    "SettingRepo",
    "SponsorRepo",
    "StickerSetRepo",
    "UserRepo",
]
