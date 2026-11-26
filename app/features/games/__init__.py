"""Games and entertainment feature package."""

from __future__ import annotations

from app.features.games.group_router import router as group_router
from app.features.games.router import feature, router

# Group multiplayer ships as part of the games feature.
router.include_router(group_router)

__all__ = ["feature", "router"]
