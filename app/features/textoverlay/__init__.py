"""Text overlay feature package."""

from __future__ import annotations

from app.features.registry import Feature, register
from app.features.textoverlay.router import router

feature = register(
    Feature(
        name="textoverlay",
        router=router,
        order=151,
    )
)
