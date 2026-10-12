"""Runtime settings stored in the database.

Distinct from `app.config.Settings` (environment, read-only, needs a restart):
these are switches an admin flips from the panel while the bot runs. Values are
stored as text and coerced by `app.core.settings_store`.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Setting(TimestampMixin, Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    #: Shown in the admin panel so keys are self-documenting.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Admin who last changed it, for an audit trail.
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    def __repr__(self) -> str:
        return f"<Setting {self.key}={self.value!r}>"
