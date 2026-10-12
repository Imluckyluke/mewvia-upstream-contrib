"""aiogram middlewares.

Order matters. `setup_middlewares` is the single place that defines it.
"""

from __future__ import annotations

from app.middlewares.setup import setup_middlewares

__all__ = ["setup_middlewares"]
