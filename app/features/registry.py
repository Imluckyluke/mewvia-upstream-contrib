"""Feature registry — the extension seam of the project.

Each feature package exposes a `feature` object. `app.bot` walks the registry
to attach routers, and (from phase 2) to build the main menu. Adding a new
capability means adding a package plus one `register()` call — the core stays
untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aiogram import Router


@dataclass(frozen=True, slots=True)
class Feature:
    """A user-facing capability."""

    name: str
    """Stable id, used in callback data and settings keys."""

    router: Router
    """aiogram router holding every handler of this feature."""

    menu_label_key: str = ""
    """Key into app.ui.texts for the main-menu button. Empty = not in the menu."""

    menu_emoji_slot: str = ""
    """Key into the emoji registry for the button icon."""

    menu_style: str = ""
    """Semantic style name; resolved by app.ui.styles."""

    menu_admin_only: bool = False
    """Show the menu button to admins only."""

    order: int = 100
    """Sort order in the main menu."""

    settings_flag: str = ""
    """Optional settings key that can disable this feature at runtime."""

    tags: frozenset[str] = field(default_factory=frozenset)


_registry: dict[str, Feature] = {}


def register(feature: Feature) -> Feature:
    if feature.name in _registry:
        raise ValueError(f"feature {feature.name!r} is already registered")
    _registry[feature.name] = feature
    return feature


def get(name: str) -> Feature | None:
    return _registry.get(name)


def all_features() -> list[Feature]:
    """Every registered feature, ordered for display."""
    return sorted(_registry.values(), key=lambda f: (f.order, f.name))


def menu_features() -> list[Feature]:
    """Features that should appear in the main menu."""
    return [f for f in all_features() if f.menu_label_key]


def clear() -> None:
    """Test helper — drops all registrations."""
    _registry.clear()
