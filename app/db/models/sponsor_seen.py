"""Seen-membership records for sponsor leave detection.

A row means "we positively saw this user inside this channel". When a fresh
check later reports them missing, the gate knows it is a *leave* (warm
re-invite) rather than a never-joined user (standard prompt). Rows survive
restarts — unlike the in-memory set they replace.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SponsorSeenMember(Base):
    __tablename__ = "sponsor_seen_members"

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<SponsorSeenMember channel={self.channel_id} user={self.user_id}>"
