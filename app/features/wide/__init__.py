"""Wide feature package."""

from __future__ import annotations

from app.features.registry import Feature, register
from app.features.wide.router import router

feature = register(
    Feature(
        name="wide",
        router=router,
        order=160,
    )
)
