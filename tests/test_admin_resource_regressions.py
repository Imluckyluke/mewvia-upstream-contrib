from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.admin.users_export import export_users_json, export_users_txt
from app.db.models import User
from app.db.repo.user import UserRepo


@pytest.mark.parametrize("query", ["--12", "²", "9" * 19, "1" * 4000])
def test_invalid_ids_still_allow_name_search(query: str) -> None:
    stmt = UserRepo(MagicMock())._search_stmt(query)
    assert "users.telegram_id =" not in str(stmt)
    assert f"%{query}%" in stmt.compile().params.values()


@pytest.mark.parametrize("count", [0, 258])
async def test_streamed_user_exports(tmp_path: Path, count: int) -> None:
    now = datetime.now(UTC)
    rows = [
        User(telegram_id=i, first_name="نام\nجدید", created_at=now, last_seen_at=now)
        for i in range(count)
    ]

    async def partitions(size: int):
        for i in range(0, len(rows), size):
            yield rows[i : i + size]

    stream = MagicMock(partitions=partitions, close=AsyncMock())
    session = AsyncMock()
    session.stream_scalars.return_value = stream
    exported = await export_users_json(session, tmp_path / "users.json")
    data = json.loads(exported.read_bytes())
    assert [u["telegram_id"] for u in data] == list(range(count))
    assert all(u["first_name"] == "نام\nجدید" for u in data)
    txt = await export_users_txt(session, tmp_path / "users.txt")
    lines = txt.read_text().splitlines()[2:]
    assert len(lines) == count
    assert all(line.split("\t")[2] == "نام جدید" for line in lines)
    assert stream.close.await_count == 2
