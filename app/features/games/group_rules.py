"""Rules and callback identity shared by group game handlers and panels."""

from __future__ import annotations

from typing import Any

from app.callbacks import GroupGameCB

GAME_LABELS = {"xo": "دوز", "gol": "گل یا پوچ", "rps": "سنگ، کاغذ، قیچی"}
GAME_RULES = {
    "xo": "نوبتی بازی کنید؛ سه مهرهٔ هم‌شکل در یک ردیف، ستون یا قطر برنده است.",
    "gol": "۴ دست؛ هر نفر دو بار گل را مخفی می‌کند. حدس درست ۲ امتیاز برای حدس‌زننده،"
    " حدس اشتباه ۱ امتیاز برای مخفی‌کننده.",
    "rps": "هر نفر مخفیانه یک گزینه می‌زند. سنگ قیچی، قیچی کاغذ و کاغذ سنگ را می‌برد.",
}
GOL_ROUNDS = 4
GOL_LABELS = {"left": "دست چپ", "right": "دست راست", "empty": "هر دو دست خالی"}
RPS_LABELS = {"rock": "🪨 سنگ", "paper": "📄 کاغذ", "scissors": "✂️ قیچی"}
_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def pack_action(state: dict[str, Any], action: str, *, cell: int = -1, side: str = "") -> str:
    return GroupGameCB(
        action=action,
        game=state["game"],
        cell=cell,
        side=side,
        match_id=state["match_id"],
        revision=state["revision"],
    ).pack()


def rps_winner(first: str, second: str) -> int | None:
    return None if first == second else (0 if _BEATS[first] == second else 1)
