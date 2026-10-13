"""Unit & integration tests for multi-account Instagram cookies management."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Dispatcher
from aiogram.types import CallbackQuery, Message
from app.admin.cookies_router import (
    delete_cookie_execute,
    ready_registry,
    render_cookie_detail,
    render_cookies_dashboard,
    toggle_cookie,
)
from app.admin.cookies_router import (
    test_all_cookies as handle_test_all_cookies,
)
from app.admin.cookies_router import (
    test_single_cookie as handle_test_single_cookie,
)
from app.db.repo import InstagramCookieRepo
from app.media.cookie_checker import parse_and_normalize_cookies
from sqlalchemy.ext.asyncio import AsyncSession

SAMPLE_NETSCAPE_COOKIE = """# Netscape HTTP Cookie File
# https://curl.se/docs/http-cookies.html
.instagram.com	TRUE	/	TRUE	1999999999	sessionid	test_session_id_12345
.instagram.com	TRUE	/	TRUE	1999999999	ds_user_id	987654321
.instagram.com	TRUE	/	TRUE	1999999999	csrftoken	csrf_token_abc
"""

SAMPLE_RAW_COOKIE = "sessionid=test_raw_session_67890; ds_user_id=11223344; csrftoken=csrf_test"


def test_parse_and_normalize_netscape():
    netscape_text, cookie_dict = parse_and_normalize_cookies(SAMPLE_NETSCAPE_COOKIE)
    assert cookie_dict.get("sessionid") == "test_session_id_12345"
    assert cookie_dict.get("ds_user_id") == "987654321"
    assert cookie_dict.get("csrftoken") == "csrf_token_abc"
    assert "# Netscape HTTP Cookie File" in netscape_text


def test_parse_and_normalize_raw_key_values():
    netscape_text, cookie_dict = parse_and_normalize_cookies(SAMPLE_RAW_COOKIE)
    assert cookie_dict.get("sessionid") == "test_raw_session_67890"
    assert cookie_dict.get("ds_user_id") == "11223344"
    assert "# Netscape HTTP Cookie File" in netscape_text
    assert (
        ".instagram.com\tTRUE\t/\tTRUE\t1999999999\tsessionid\ttest_raw_session_67890"
        in netscape_text
    )


@pytest.mark.asyncio
async def test_instagram_cookie_repo_crud_and_rotation(session: AsyncSession):
    repo = InstagramCookieRepo(session)

    # 1. Add cookies
    c1 = await repo.add(
        label="Account Alpha",
        cookie_text=SAMPLE_NETSCAPE_COOKIE,
        account_id="987654321",
        status="valid",
    )
    c2 = await repo.add(
        label="Account Beta",
        cookie_text=SAMPLE_RAW_COOKIE,
        account_id="11223344",
        status="valid",
    )
    assert c1.id is not None
    assert c2.id is not None
    assert await repo.count() >= 2

    # 2. Test round-robin rotation (get_next_cookie)
    next_c = await repo.get_next_cookie()
    assert next_c is not None
    assert next_c.id in (c1.id, c2.id)

    # Record usage for first
    await repo.record_usage(next_c.id, success=True)
    assert next_c.use_count == 1
    assert next_c.last_used_at is not None

    # Next call should pick the other cookie
    other_c = await repo.get_next_cookie()
    assert other_c is not None
    assert other_c.id != next_c.id

    # 3. Test toggle active
    await repo.set_active(c1.id, False)
    active_cookies = await repo.get_active_cookies()
    assert all(c.id != c1.id for c in active_cookies)

    # 4. Test error recording & auto-invalid status
    await repo.set_active(c1.id, True)
    await repo.record_usage(c1.id, success=False, error="Session expired")
    await repo.record_usage(c1.id, success=False, error="Session expired")
    await repo.record_usage(c1.id, success=False, error="Session expired")
    assert c1.status == "invalid"
    assert "Session expired" in (c1.error_message or "")

    # 5. Delete
    deleted = await repo.delete(c2.id)
    assert deleted is True
    assert await repo.get_by_id(c2.id) is None


@pytest.mark.asyncio
async def test_admin_cookies_dashboard_and_detail(
    app_dispatcher: Dispatcher, session: AsyncSession
):
    repo = InstagramCookieRepo(session)
    c = await repo.add(
        label="Dashboard Test Acc",
        cookie_text=SAMPLE_NETSCAPE_COOKIE,
        account_id="123456",
        status="valid",
    )

    registry = await ready_registry()
    dashboard_text, kb_dash = await render_cookies_dashboard(session, registry)
    assert "مدیریت کوکی‌های اینستاگرام" in dashboard_text
    assert "Dashboard Test Acc" in str(kb_dash.as_markup().inline_keyboard)

    detail_text, kb_detail = await render_cookie_detail(c, registry)
    assert "Dashboard Test Acc" in detail_text
    assert "123456" in detail_text
    assert any(
        "تست آنلاین" in btn.text for row in kb_detail.as_markup().inline_keyboard for btn in row
    )


@pytest.mark.asyncio
async def test_admin_cookie_actions_e2e(app_dispatcher: Dispatcher, session: AsyncSession):
    repo = InstagramCookieRepo(session)
    c = await repo.add(
        label="Action Test Acc",
        cookie_text=SAMPLE_NETSCAPE_COOKIE,
        account_id="777",
        status="untested",
    )

    query = MagicMock(spec=CallbackQuery)
    query.data = f"igc:toggle:{c.id}"
    query.answer = AsyncMock()
    query.message = MagicMock(spec=Message)
    query.message.edit_text = AsyncMock()

    # 1. Toggle
    await toggle_cookie(query, session)
    updated = await repo.get_by_id(c.id)
    assert updated is not None
    assert updated.is_active is False

    # 2. Test single cookie with mocked HTTP check
    query.data = f"igc:test:{c.id}"
    with patch(
        "app.admin.cookies_router.check_instagram_cookie_health_sync",
        return_value=(True, "777", None),
    ):
        await handle_test_single_cookie(query, session)
        tested = await repo.get_by_id(c.id)
        assert tested is not None
        assert tested.status == "valid"

    # 3. Test all cookies
    query.data = "igc:test_all"
    with patch(
        "app.admin.cookies_router.check_instagram_cookie_health_sync",
        return_value=(True, "777", None),
    ):
        await handle_test_all_cookies(query, session)

    # 4. Delete
    query.data = f"igc:del_yes:{c.id}"
    await delete_cookie_execute(query, session)
    assert await repo.get_by_id(c.id) is None
