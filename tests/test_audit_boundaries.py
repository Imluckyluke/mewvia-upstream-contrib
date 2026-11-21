from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User
from app.admin.router import router
from app.core.errors import ConversionError
from app.media.downloader import _save_response
from app.media.worker_service import handle


@pytest.mark.parametrize("kind", ["group", "supergroup", "channel"])
async def test_admin_router_rejects_nonprivate_exports(kind: str) -> None:
    message = Message(message_id=3, chat=Chat(id=-99, type=kind), date=datetime.now(UTC))
    user = User(id=1, is_bot=False, first_name="Admin")
    callback = CallbackQuery(
        id="c", from_user=user, chat_instance="c", message=message, data="admin_act:backup"
    )
    assert not (await router.callback_query.check_root_filters(callback))[0]
    assert not (await router.message.check_root_filters(message))[0]


def test_over_budget_response_is_closed_and_removed(tmp_path: Path) -> None:
    resp = MagicMock(status_code=200)
    resp.iter_content.return_value = [b"123", b"456"]
    target = tmp_path / "photo.jpg"
    with pytest.raises(ConversionError):
        _save_response(resp, target, 4)
    assert not target.exists()
    resp.__exit__.assert_called_once()


async def test_client_disconnect_cancels_worker(tmp_path: Path) -> None:
    started, stopped = asyncio.Event(), asyncio.Event()

    async def job(*args):
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    path = tmp_path / "socket"
    with patch("app.media.worker_service.run_worker", new=job):
        async with await asyncio.start_unix_server(handle, path):
            _, w = await asyncio.open_unix_connection(path)
            w.write(json.dumps(["probe", []]).encode() + b"\n")
            await w.drain()
            await asyncio.wait_for(started.wait(), 3)
            w.close()
            await w.wait_closed()
            await asyncio.wait_for(stopped.wait(), 3)
