"""League tiers, rankings, and score math."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class League:
    key: str
    name: str
    badge: str
    title: str
    min_score: int
    max_score: int | None
    next_league_name: str | None


LEAGUES: list[League] = [
    League(
        key="bronze",
        name="لیگ برنز",
        badge="🥉",
        title="تازه‌نفس",
        min_score=1000,
        max_score=1149,
        next_league_name="لیگ نقره",
    ),
    League(
        key="silver",
        name="لیگ نقره",
        badge="🥈",
        title="شکارچی",
        min_score=1150,
        max_score=1349,
        next_league_name="لیگ طلا",
    ),
    League(
        key="gold",
        name="لیگ طلا",
        badge="🥇",
        title="استاد استراتژی",
        min_score=1350,
        max_score=1649,
        next_league_name="لیگ الماس",
    ),
    League(
        key="diamond",
        name="لیگ الماس",
        badge="💎",
        title="قهرمان بورد",
        min_score=1650,
        max_score=1999,
        next_league_name="لیگ افسانه‌ای مستر",
    ),
    League(
        key="master",
        name="لیگ افسانه‌ای مستر",
        badge="👑",
        title="اسطوره میوویا",
        min_score=2000,
        max_score=None,
        next_league_name=None,
    ),
]


def get_league(score: int) -> League:
    """Resolve league by score."""
    for league in reversed(LEAGUES):
        if score >= league.min_score:
            return league
    return LEAGUES[0]


def format_league_progress_bar(score: int, length: int = 10) -> str:
    """Render a text progress bar to the next league."""
    league = get_league(score)
    if league.max_score is None:
        # Max league reached
        return "▰" * length + " (بالاترین لیگ)"

    span = league.max_score - league.min_score + 1
    current_in_span = max(0, score - league.min_score)
    ratio = min(1.0, current_in_span / span)
    filled = round(ratio * length)
    empty = length - filled
    pct = round(ratio * 100)
    bar = "▰" * filled + "▱" * empty
    pts_left = (league.max_score + 1) - score
    return f"[{bar}] {pct}% ({pts_left} امتیاز تا {league.next_league_name})"
