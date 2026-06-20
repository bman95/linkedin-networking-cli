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
    check_if_connected,
    get_connection_status,
    _is_true_limit,
)
from automation import selectors as sel


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
class TestIsTrueLimit:
    """_is_true_limit sources its icon/header candidates from the central
    LIMIT_TRUE_MARKER selector; verify both branches stay wired to it."""

    @pytest.mark.asyncio
    async def test_locked_icon_anchor_means_true_limit(self):
        # The modal exposes the locked-padlock anchor -> immediate True, and the
        # header text is never consulted.
        icon_css = sel.LIMIT_TRUE_MARKER.anchor

        async def query(css):
            return _element() if css == icon_css else None

        modal = AsyncMock()
        modal.query_selector = AsyncMock(side_effect=query)
        assert await _is_true_limit(modal) is True

    @pytest.mark.asyncio
    async def test_header_text_fallback_when_no_icon(self):
        # No icon, but the header carries the real-limit wording -> True via the
        # header-text fallback candidates.
        icon_css = sel.LIMIT_TRUE_MARKER.anchor
        header_el = _element(
            inner_text="Has alcanzado el límite semanal de invitaciones"
        )

        async def query(css):
            return None if css == icon_css else header_el

        modal = AsyncMock()
        modal.query_selector = AsyncMock(side_effect=query)
        assert await _is_true_limit(modal) is True

    @pytest.mark.asyncio
    async def test_near_limit_warning_is_not_true(self):
        # No icon and an unrelated header -> not a true limit.
        icon_css = sel.LIMIT_TRUE_MARKER.anchor
        header_el = _element(inner_text="Te estás acercando al límite")

        async def query(css):
            return None if css == icon_css else header_el

        modal = AsyncMock()
        modal.query_selector = AsyncMock(side_effect=query)
        assert await _is_true_limit(modal) is False
