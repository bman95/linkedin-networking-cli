"""Tests for the central selector registry with fallback candidates.

These verify the three behaviors that let the automation survive LinkedIn's
SDUI churn instead of misreading a DOM change as "no results":

1. **Ordered fallback** — candidates are tried most-stable-first and the first
   hit wins.
2. **Self-degrade** — falling back to a non-primary candidate logs a WARNING so
   DOM drift is visible while the run continues.
3. **Fail loud** — a *required* selector matching nothing raises
   ``SelectorNotFoundException`` AND captures a diagnostics evidence bundle
   recording the selector name, the full candidate list, and the URL.
"""

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation.selectors import Selector
import automation.selectors as selectors
from exceptions import SelectorNotFoundException


def _page(matches=None, url="https://www.linkedin.com/search"):
    """A mock async Playwright page.

    ``matches`` maps a candidate CSS string to the handle ``query_selector``
    should return for it; any candidate not in the map resolves to ``None``.
    A ``locator(css).count()`` chain is also stubbed for ``.count`` tests.
    """
    matches = matches or {}
    page = AsyncMock()
    page.url = url
    page.title = AsyncMock(return_value="Search")
    page.content = AsyncMock(return_value="<html></html>")
    page.screenshot = AsyncMock()

    async def query_selector(css):
        return matches.get(css)

    page.query_selector = AsyncMock(side_effect=query_selector)
    return page


@pytest.mark.unit
class TestSelectorConstruction:
    def test_strips_blank_candidates(self):
        s = Selector("x", ["  a ", "", "  ", "b"])
        assert s.candidates == ["a", "b"]

    def test_rejects_empty_candidate_list(self):
        with pytest.raises(ValueError):
            Selector("x", ["", "   "])

    def test_css_is_comma_joined(self):
        s = Selector("x", ["a", "b", "c"])
        assert s.css == "a, b, c"


@pytest.mark.unit
class TestCount:
    @pytest.mark.asyncio
    async def test_count_uses_combined_css(self):
        s = Selector("x", ["a", "b"])
        page = AsyncMock()
        locator = AsyncMock()
        locator.count = AsyncMock(return_value=3)
        page.locator = lambda css: locator if css == "a, b" else AsyncMock()
        assert await s.count(page) == 3


@pytest.mark.unit
class TestLocateOrdering:
    @pytest.mark.asyncio
    async def test_primary_match_returns_without_warning(self, caplog):
        handle = object()
        s = Selector("x", ["primary", "fallback"])
        page = _page({"primary": handle})
        with caplog.at_level(logging.WARNING):
            result = await s.locate(page)
        assert result is handle
        assert not caplog.records

    @pytest.mark.asyncio
    async def test_falls_back_to_next_candidate_and_warns(self, caplog):
        # Primary no longer matches; the second candidate does. This is the
        # self-degrade path: the run continues but logs a drift WARNING.
        handle = object()
        s = Selector("connect_control", ["a[data-test-x]", "button.legacy"])
        page = _page({"button.legacy": handle})
        with caplog.at_level(logging.WARNING):
            result = await s.locate(page)
        assert result is handle
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        msg = warnings[0].getMessage()
        assert "connect_control" in msg
        assert "fallback candidate #1" in msg
        assert "button.legacy" in msg

    @pytest.mark.asyncio
    async def test_no_match_not_required_returns_none(self, caplog):
        s = Selector("x", ["a", "b"])
        page = _page({})
        with caplog.at_level(logging.WARNING):
            result = await s.locate(page)
        assert result is None
        # No match at all is not a fallback, so no drift warning is emitted.
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.unit
class TestFailLoud:
    @pytest.mark.asyncio
    async def test_required_missing_raises_and_captures_bundle(self):
        s = Selector("limit_modal", ["[data-test-x]", "div.legacy"])
        page = _page({})
        with patch(
            "automation.selectors.capture_error_context", new=AsyncMock()
        ) as mock_capture:
            with pytest.raises(SelectorNotFoundException) as excinfo:
                await s.locate(page, required=True, context={"campaign": "C"})

        # The exception carries the combined candidate list as its selector.
        assert excinfo.value.selector == "[data-test-x], div.legacy"

        # An evidence bundle was captured before the raise, naming the selector,
        # the full candidate list tried, and merging caller context.
        mock_capture.assert_awaited_once()
        call = mock_capture.await_args
        assert call.args[1] == "selector_not_found_limit_modal"
        ctx = call.kwargs["context"]
        assert ctx["selector"] == "limit_modal"
        assert ctx["candidates"] == ["[data-test-x]", "div.legacy"]
        assert ctx["campaign"] == "C"
        assert type(call.kwargs["exc"]).__name__ == "SelectorNotFoundException"

    @pytest.mark.asyncio
    async def test_fail_loud_helper_records_timeout(self):
        # The wait_for_selector-timeout path calls fail_loud directly so the
        # capture+raise behavior is identical to a required locate.
        s = Selector("search_results_ready", ["a", "b"])
        page = _page({})
        with patch(
            "automation.selectors.capture_error_context", new=AsyncMock()
        ) as mock_capture:
            with pytest.raises(SelectorNotFoundException) as excinfo:
                await s.fail_loud(page, context={"campaign": "C"}, timeout=15000)
        assert excinfo.value.timeout == 15000
        mock_capture.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_capture_failure_does_not_mask_selector_exception(self):
        # capture_error_context is best-effort and itself never raises, but even
        # if it somehow did, fail-loud must still surface the real failure.
        s = Selector("x", ["a"])
        page = _page({})
        with patch(
            "automation.selectors.capture_error_context",
            new=AsyncMock(side_effect=RuntimeError("capture exploded")),
        ):
            with pytest.raises(RuntimeError):
                await s.locate(page, required=True)


@pytest.mark.unit
class TestRegistryShape:
    """The registry holds the load-bearing nav selectors with stable-first
    ordering and ES/EN variants."""

    def test_login_selectors_present(self):
        assert selectors.LOGIN_USERNAME.css == "input#username"
        assert selectors.LOGIN_PASSWORD.css == "input#password"
        assert selectors.LOGIN_SUBMIT.css == "button[type=submit]"

    def test_search_readiness_keeps_legacy_and_sdui_variants(self):
        assert selectors.SEARCH_RESULTS_READY.candidates == [
            ".search-results-container",
            "main a[href*='/in/']",
        ]

    def test_result_cards_anchor_first(self):
        # Candidate #0 is the stable data-* anchor for legacy structured cards.
        assert selectors.SEARCH_RESULT_CARDS.candidates[0] == (
            "[data-chameleon-result-urn]"
        )

    def test_pagination_has_en_es_and_text_fallback(self):
        cands = selectors.PAGINATION_NEXT.candidates
        assert "button[aria-label='Next']" in cands
        assert "button[aria-label='Siguiente']" in cands
        # Text fallback for the SDUI layout.
        assert any("has-text('Next')" in c for c in cands)

    def test_invitation_modal_buttons_es_en(self):
        assert selectors.INVITE_SEND_NO_NOTE.candidates == [
            "button:has-text('Enviar sin nota')",
            "button:has-text('Send without a note')",
        ]
        assert selectors.INVITE_SEND.candidates == [
            "button:text-is('Enviar')",
            "button:text-is('Send')",
        ]

    def test_limit_modal_anchor_first(self):
        # The data-test anchor leads; component-class and dialog-text follow.
        assert selectors.LIMIT_MODAL.candidates[0] == (
            "[data-test-modal-id='ip-fuse-limit-alert']"
        )

    def test_limit_true_marker_locked_icon_first(self):
        assert selectors.LIMIT_TRUE_MARKER.candidates[0] == (
            "svg[data-test-icon='locked']"
        )
