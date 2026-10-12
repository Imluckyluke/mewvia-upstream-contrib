"""Middleware wiring — the one place update processing order is defined.

Order for messages/callbacks:
  1. DbSessionMiddleware    opens the session
  2. UserTrackingMiddleware upserts the sender, drops banned users

Registered on `dp.update` so it applies once per update rather than once per
event type, which keeps a single session for the whole update.
"""

from __future__ import annotations

from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.middlewares.db import DbSessionMiddleware
from app.middlewares.group_guard import GroupPanelGuardMiddleware
from app.middlewares.maintenance import MaintenanceMiddleware
from app.middlewares.sponsor_gate import SponsorGateMiddleware
from app.middlewares.user_tracking import UserTrackingMiddleware


def setup_middlewares(
    dp: Dispatcher,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))
    dp.update.outer_middleware(UserTrackingMiddleware(settings))
    dp.update.outer_middleware(MaintenanceMiddleware())
    dp.update.outer_middleware(GroupPanelGuardMiddleware())
    dp.update.outer_middleware(SponsorGateMiddleware())
