"""Admin user list export helpers (JSON and TXT/CSV)."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User


async def export_users_json(session: AsyncSession, out_path: Path) -> Path:
    """Export all users as a JSON file."""
    stmt = select(User).order_by(User.created_at.asc())
    users = (await session.execute(stmt)).scalars().all()

    data = [
        {
            "telegram_id": u.telegram_id,
            "username": u.username,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "language_code": u.language_code,
            "is_premium": u.is_premium,
            "is_banned": u.is_banned,
            "has_blocked_bot": u.has_blocked_bot,
            "has_used_bot": u.has_used_bot,
            "updates_seen": u.updates_seen,
            "created_at": u.created_at.isoformat(),
            "last_seen_at": u.last_seen_at.isoformat(),
        }
        for u in users
    ]

    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")  # noqa: ASYNC240
    return out_path


async def export_users_txt(session: AsyncSession, out_path: Path) -> Path:
    """Export all users as a tab-separated TXT file."""
    stmt = select(User).order_by(User.created_at.asc())
    users = (await session.execute(stmt)).scalars().all()

    lines = [
        "ID\tUsername\tFirst Name\tLast Name\tPremium\tBanned\tJoined",
        "--------------------------------------------------------------------------------",
    ]

    for u in users:
        uname = f"@{u.username}" if u.username else "-"
        fname = u.first_name or "-"
        lname = u.last_name or "-"
        prem = "Yes" if u.is_premium else "No"
        banned = "Yes" if u.is_banned else "No"
        joined = u.created_at.strftime("%Y-%m-%d")
        lines.append(f"{u.telegram_id}\t{uname}\t{fname}\t{lname}\t{prem}\t{banned}\t{joined}")

    out_path.write_text("\n".join(lines), encoding="utf-8")  # noqa: ASYNC240
    return out_path
