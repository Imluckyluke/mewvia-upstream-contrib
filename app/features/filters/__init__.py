"""Visual filters feature package."""

from __future__ import annotations

from app.features.filters.router import router
from app.features.registry import Feature, register

feature = register(
    Feature(
        name="filters",
        router=router,
        order=170,
    )
)
