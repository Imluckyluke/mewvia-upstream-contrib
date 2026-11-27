"""A durable receipt for a settled group match."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GroupGameResult(Base):
    __tablename__ = "group_game_results"

    match_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    rating_changes: Mapped[dict[str, list[int]]] = mapped_column(JSONB, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
