"""
Tests for the async page-interaction helpers in src/automation/interactions.py.

These verify the functions are correctly awaitable and behave as expected
against a mocked async Playwright page.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation.interactions import (
    random_wait,
    detect_captcha,
    check_if_connected,
    get_connection_status,
    _is_true_limit,
    human_type,
    move_to_element,
    move_to_and_click,
    scroll_down,
    dwell,
    RateLimiter,
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


@pytest.mark.unit
class TestHumanType:
    @pytest.mark.asyncio
    async def test_types_one_key_per_character(self):
        box = AsyncMock()
        box.click = AsyncMock()
        box.clear = AsyncMock()
        box.press_sequentially = AsyncMock()

        # Skip the real focus-pause sleep to keep the test fast.
        with patch("automation.interactions.asyncio.sleep", new=AsyncMock()):
            await human_type(box, "abc", delay_min=10, delay_max=10)

        # Field is focused once, then one keystroke call per character.
        box.click.assert_awaited_once()
        assert box.press_sequentially.await_count == 3
        typed = "".join(call.args[0] for call in box.press_sequentially.await_args_list)
        assert typed == "abc"
        # Per-key delay is passed through within the configured range.
        for call in box.press_sequentially.await_args_list:
            assert call.kwargs["delay"] == 10

    @pytest.mark.asyncio
    async def test_clears_field_before_typing(self):
        """Pre-existing content (autofill/remembered) is overwritten, not appended."""
        box = AsyncMock()
        box.click = AsyncMock()
        box.clear = AsyncMock()
        box.press_sequentially = AsyncMock()

        with patch("automation.interactions.asyncio.sleep", new=AsyncMock()):
            await human_type(box, "x", delay_min=10, delay_max=10)

        box.clear.assert_awaited_once()


@pytest.mark.unit
class TestMouseMoveAndClick:
    def _page_with_mouse(self):
        page = AsyncMock()
        page.mouse = AsyncMock()
        page.mouse.move = AsyncMock()
        return page

    @pytest.mark.asyncio
    async def test_move_to_element_moves_mouse_toward_target(self):
        page = self._page_with_mouse()
        element = AsyncMock()
        element.bounding_box = AsyncMock(
            return_value={"x": 100, "y": 200, "width": 40, "height": 20}
        )

        with patch("automation.interactions.asyncio.sleep", new=AsyncMock()):
            await move_to_element(page, element)

        # Several jittered steps toward the element's center (5-10 per spec).
        assert 5 <= page.mouse.move.await_count <= 10
        # The final move (no jitter) lands on the element's center.
        center_x = 100 + 40 / 2
        center_y = 200 + 20 / 2
        final_x, final_y = page.mouse.move.await_args.args
        assert final_x == pytest.approx(center_x)
        assert final_y == pytest.approx(center_y)

    @pytest.mark.asyncio
    async def test_move_to_and_click_clicks_after_moving(self):
        page = self._page_with_mouse()
        element = AsyncMock()
        element.bounding_box = AsyncMock(
            return_value={"x": 0, "y": 0, "width": 10, "height": 10}
        )
        element.click = AsyncMock()

        with patch("automation.interactions.asyncio.sleep", new=AsyncMock()):
            await move_to_and_click(page, element)

        page.mouse.move.assert_awaited()
        element.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_move_to_and_click_falls_back_to_js_click(self):
        page = self._page_with_mouse()
        element = AsyncMock()
        element.bounding_box = AsyncMock(return_value=None)
        element.click = AsyncMock(side_effect=Exception("intercepted"))
        element.evaluate = AsyncMock()

        with patch("automation.interactions.asyncio.sleep", new=AsyncMock()):
            await move_to_and_click(page, element)

        # A intercepted real click falls back to a JS click.
        element.evaluate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_move_to_element_noop_when_no_bounding_box(self):
        page = self._page_with_mouse()
        element = AsyncMock()
        element.bounding_box = AsyncMock(return_value=None)

        await move_to_element(page, element)

        page.mouse.move.assert_not_awaited()


@pytest.mark.unit
class TestScrollDown:
    @pytest.mark.asyncio
    async def test_scrolls_with_wheel_until_bottom(self):
        page = AsyncMock()
        page.mouse = AsyncMock()
        page.mouse.wheel = AsyncMock()
        page.wait_for_timeout = AsyncMock()

        # scrollY starts at 0, viewport 800, total 1000 -> one step, then the
        # second evaluate batch reports we've reached the bottom.
        scroll_values = iter([0, 800, 1000, 1000, 800, 1000])

        async def _evaluate(expr):
            return next(scroll_values)

        page.evaluate = AsyncMock(side_effect=_evaluate)

        await scroll_down(page)

        # At least one wheel scroll happened, and the loop terminated.
        page.mouse.wheel.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_scroll_when_already_at_bottom(self):
        page = AsyncMock()
        page.mouse = AsyncMock()
        page.mouse.wheel = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        # current(0) + viewport(0) < total(0) is False -> zero iterations.
        page.evaluate = AsyncMock(return_value=0)

        await scroll_down(page)

        page.mouse.wheel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_terminates_on_infinite_lazy_loading_page(self):
        """A page whose scrollHeight grows on every scroll must still finish.

        Models LinkedIn's infinite results list: scrollHeight always stays
        ahead of (scrollY + viewport), so a naive 'scroll to the end' loop
        never ends. The hard step cap must bound it.
        """
        page = AsyncMock()
        page.mouse = AsyncMock()
        page.mouse.wheel = AsyncMock()
        page.wait_for_timeout = AsyncMock()

        state = {"scroll_y": 0.0}

        async def _evaluate(expr):
            if "scrollY" in expr:
                # The position advances a little each step (never clamped).
                state["scroll_y"] += 300
                return state["scroll_y"]
            if "innerHeight" in expr:
                return 800
            # scrollHeight always grows faster than we scroll -> never "done".
            return state["scroll_y"] + 5000

        page.evaluate = AsyncMock(side_effect=_evaluate)

        # Must return (not hang) and be bounded by the hard step cap (200).
        await scroll_down(page)
        assert page.mouse.wheel.await_count <= 200
        assert page.mouse.wheel.await_count > 0

    @pytest.mark.asyncio
    async def test_finite_tall_page_reaches_bottom_not_cut_by_cap(self):
        """A tall but finite page is fully scrolled to the bottom, not cut short.

        The bottom is reached via the stall guard (scrollY clamps), well before
        the hard step cap — so search harvesting sees the whole result list.
        """
        page = AsyncMock()
        page.mouse = AsyncMock()
        page.mouse.wheel = AsyncMock()
        page.wait_for_timeout = AsyncMock()

        viewport = 800
        total = 12_000  # tall finite page
        state = {"scroll_y": 0.0}

        async def _evaluate(expr):
            if "scrollY" in expr:
                # Advance toward the bottom, then clamp at (total - viewport).
                state["scroll_y"] = min(state["scroll_y"] + 250, total - viewport)
                return state["scroll_y"]
            if "innerHeight" in expr:
                return viewport
            return total

        page.evaluate = AsyncMock(side_effect=_evaluate)

        await scroll_down(page)

        # Reached the bottom (scrollY clamped at total - viewport) and stopped
        # via the stall guard, not the hard cap of 200.
        assert state["scroll_y"] == total - viewport
        assert page.mouse.wheel.await_count < 200

    @pytest.mark.asyncio
    async def test_stalls_out_when_scroll_position_does_not_advance(self):
        """If scrollY stops advancing (clamped at bottom), bail early."""
        page = AsyncMock()
        page.mouse = AsyncMock()
        page.mouse.wheel = AsyncMock()
        page.wait_for_timeout = AsyncMock()

        async def _evaluate(expr):
            if "scrollY" in expr:
                return 100  # never advances
            if "innerHeight" in expr:
                return 800
            return 5000  # bottom never "reached" by the >= check

        page.evaluate = AsyncMock(side_effect=_evaluate)

        await scroll_down(page)
        # Stall guard breaks after MAX_STALLED_STEPS (3) iterations, well
        # under the hard cap of 200.
        assert page.mouse.wheel.await_count <= 4


@pytest.mark.unit
class TestDwell:
    @pytest.mark.asyncio
    async def test_dwell_waits_within_window(self):
        page = AsyncMock()
        page.wait_for_timeout = AsyncMock()

        await dwell(page, min_s=1.0, max_s=4.0)

        page.wait_for_timeout.assert_awaited_once()
        waited_ms = page.wait_for_timeout.await_args.args[0]
        # The pause stays inside the configured window (in milliseconds).
        assert 1_000 <= waited_ms <= 4_000


@pytest.mark.unit
class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_under_cap_does_not_sleep(self):
        limiter = RateLimiter(max_per_minute=20)
        with patch("automation.interactions.asyncio.sleep", new=AsyncMock()) as sleep:
            for _ in range(5):
                await limiter.acquire()
            sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sleeps_when_window_is_full(self):
        limiter = RateLimiter(max_per_minute=3)
        with patch("automation.interactions.asyncio.sleep", new=AsyncMock()) as sleep:
            # Fill the window, then the 4th action must wait for the oldest to
            # age out of the 60s window.
            for _ in range(3):
                await limiter.acquire()
            sleep.assert_not_awaited()
            await limiter.acquire()
            sleep.assert_awaited_once()
            assert sleep.await_args.args[0] > 0

    @pytest.mark.asyncio
    async def test_zero_cap_disables_throttling(self):
        limiter = RateLimiter(max_per_minute=0)
        with patch("automation.interactions.asyncio.sleep", new=AsyncMock()) as sleep:
            for _ in range(50):
                await limiter.acquire()
            sleep.assert_not_awaited()
