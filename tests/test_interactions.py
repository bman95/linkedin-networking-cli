"""
Tests for the async page-interaction helpers in src/automation/interactions.py.

These verify the functions are correctly awaitable and behave as expected
against a mocked async Playwright page.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation.interactions import (
    random_wait,
    detect_captcha,
    detect_invitation_limit,
    check_connection_email_required,
    check_if_connected,
    get_connection_status,
    send_connection_request,
)
from exceptions import CaptchaDetectedException


def _element(visible=True, **attrs):
    el = AsyncMock()
    el.is_visible = AsyncMock(return_value=visible)
    el.click = AsyncMock()
    el.fill = AsyncMock()
    el.inner_text = AsyncMock(return_value=attrs.get("inner_text", ""))
    el.get_attribute = AsyncMock(return_value=attrs.get("href"))
    return el


def _page(query_result=None, content=""):
    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=query_result)
    page.content = AsyncMock(return_value=content)
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock()
    return page


@pytest.mark.unit
class TestRandomWait:
    @pytest.mark.asyncio
    async def test_random_wait_awaits_timeout(self):
        page = _page()
        await random_wait(page, min_ms=10, max_ms=10, verbose=False)
        page.wait_for_timeout.assert_awaited_once_with(10)


@pytest.mark.unit
class TestDetectCaptcha:
    @pytest.mark.asyncio
    async def test_no_captcha_returns_false(self):
        page = _page(query_result=None, content="welcome to linkedin")
        assert await detect_captcha(page) is False

    @pytest.mark.asyncio
    async def test_visible_captcha_element_returns_true(self):
        page = _page(query_result=_element(visible=True))
        assert await detect_captcha(page) is True

    @pytest.mark.asyncio
    async def test_captcha_text_returns_true(self):
        page = _page(query_result=None, content="Please verify you're not a robot")
        assert await detect_captcha(page) is True


@pytest.mark.unit
class TestDetectInvitationLimit:
    @pytest.mark.asyncio
    async def test_no_limit_returns_false(self):
        page = _page(query_result=None, content="all good")
        assert await detect_invitation_limit(page) is False

    @pytest.mark.asyncio
    async def test_limit_text_returns_true(self):
        page = _page(query_result=None, content="You've reached the weekly invitation limit")
        assert await detect_invitation_limit(page) is True


@pytest.mark.unit
class TestEmailRequired:
    @pytest.mark.asyncio
    async def test_email_required_true_and_dismisses(self):
        label = _element()
        dismiss = _element()
        page = AsyncMock()
        page.query_selector = AsyncMock(side_effect=[label, dismiss])
        assert await check_connection_email_required(page) is True
        dismiss.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_email_not_required(self):
        page = _page(query_result=None)
        assert await check_connection_email_required(page) is False


@pytest.mark.unit
class TestConnectionStatus:
    @pytest.mark.asyncio
    async def test_check_if_connected_true(self):
        page = _page(query_result=_element(visible=True))
        assert await check_if_connected(page) is True

    @pytest.mark.asyncio
    async def test_check_if_connected_false(self):
        page = _page(query_result=None)
        assert await check_if_connected(page) is False

    @pytest.mark.asyncio
    async def test_get_connection_status_connected(self):
        page = _page(query_result=_element(visible=True))
        assert await get_connection_status(page) == "connected"


@pytest.mark.unit
class TestSendConnectionRequest:
    @pytest.mark.asyncio
    async def test_no_connect_button(self):
        # query_selector always returns None: no captcha, no email modal, no button.
        page = _page(query_result=None, content="")
        result = await send_connection_request(page, "Jane Doe")
        assert result["success"] is False
        assert result["status"] == "no_connect_button"

    @pytest.mark.asyncio
    async def test_captcha_raises(self, monkeypatch):
        page = _page(query_result=None, content="security verification")
        with pytest.raises(CaptchaDetectedException):
            await send_connection_request(page, "Jane Doe")
