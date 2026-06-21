"""Tests for the navigation landing guard (issue #16).

Cover the pure URL helpers (path/param diff, challenge detection), the
``navigate_guarded`` gate (clean landing, challenge/login bounce, param reset,
ignored LinkedIn-added params, strict_path miss, overlay sweep + evidence
capture), the DOM-backed login confirmation, and the empty-vs-not-rendered
listing disambiguation (reload-once).
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from playwright.async_api import TimeoutError as PWTimeoutError

from automation import navigation as nav
from automation import selectors as sel
from exceptions import (
    CaptchaDetectedException,
    NotAuthenticatedException,
    UnexpectedLandingException,
    SelectorNotFoundException,
)


def _page(url="https://www.linkedin.com/feed/", *, overlay_count=0):
    """A guard-friendly mock page.

    ``goto`` lands on the requested URL (no redirect), ``page.url`` reports the
    landed URL, and ``page.locator(css).count()`` reports ``overlay_count`` for
    the blocking-overlay selector (0 = no overlay), 0 otherwise.
    """
    page = AsyncMock()
    page.url = url

    async def _goto(target, *_a, **_k):
        page.url = target

    page.goto = AsyncMock(side_effect=_goto)
    page.wait_for_load_state = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.reload = AsyncMock()

    overlay_css = sel.BLOCKING_OVERLAY.css

    def _locator(css="", *_a, **_k):
        loc = MagicMock()
        loc.count = AsyncMock(return_value=overlay_count if css == overlay_css else 0)
        return loc

    page.locator = MagicMock(side_effect=_locator)
    return page


@pytest.mark.unit
class TestUnexpectedLandingException:
    def test_carries_structured_attributes(self):
        exc = UnexpectedLandingException(
            "bounced",
            requested_url="https://x/search",
            landed_url="https://x/feed",
            reason="path_changed",
        )
        assert exc.requested_url == "https://x/search"
        assert exc.landed_url == "https://x/feed"
        assert exc.reason == "path_changed"

    def test_str_includes_details(self):
        exc = UnexpectedLandingException(
            "bounced",
            requested_url="https://x/search",
            landed_url="https://x/feed",
            reason="param_reset",
        )
        rendered = str(exc)
        assert "param_reset" in rendered
        assert "https://x/search" in rendered
        assert "https://x/feed" in rendered

    def test_subclass_of_automation_error(self):
        from exceptions import LinkedInAutomationError

        assert issubclass(UnexpectedLandingException, LinkedInAutomationError)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSplit:
    def test_normalizes_trailing_slash_and_case(self):
        path, _ = nav._split("https://www.linkedin.com/Feed/")
        assert path == "/feed"

    def test_parses_query(self):
        _, query = nav._split("https://x/search?start=1000&keywords=eng")
        assert query == {"start": "1000", "keywords": "eng"}

    def test_empty_url(self):
        assert nav._split("") == ("", {})


@pytest.mark.unit
class TestLandedOnChallenge:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.linkedin.com/login", "login"),
            ("https://www.linkedin.com/uas/login", "login"),
            ("https://www.linkedin.com/checkpoint/challenge/", "challenge"),
            ("https://www.linkedin.com/authwall", "challenge"),
            ("https://www.linkedin.com/feed/", None),
            ("https://www.linkedin.com/in/john-smith", None),
        ],
    )
    def test_classification(self, url, expected):
        assert nav.landed_on_challenge(url) == expected

    def test_login_in_query_does_not_trip(self):
        # The marker is matched against the PATH only, so a /login in the query
        # string (a redirect target) is not a wall.
        assert nav.landed_on_challenge("https://x/feed?redirect=/login") is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.linkedin.com/company/loginworks",  # contains "login"
            "https://www.linkedin.com/in/authwall-fan",     # contains "authwall"
            "https://www.linkedin.com/company/uasolutions",  # contains "uas"
        ],
    )
    def test_segment_boundary_avoids_substring_false_positive(self, url):
        # Markers match whole path SEGMENTS, so a legitimate path that merely
        # contains the marker text is not misread as a wall.
        assert nav.landed_on_challenge(url) is None

    def test_uas_login_segment_matches(self):
        assert nav.landed_on_challenge("https://www.linkedin.com/uas/login") == "login"


@pytest.mark.unit
class TestLandedOnCheckpoint:
    """The finer checkpoint-vs-authwall distinction the login probe relies on."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            # A /checkpoint is a routine login verification step (login probe
            # defers it instead of aborting as a CAPTCHA).
            ("https://www.linkedin.com/checkpoint/challenge/", True),
            ("https://www.linkedin.com/checkpoint/lg/login-submit", True),
            # /authwall is a genuine block, not a checkpoint.
            ("https://www.linkedin.com/authwall", False),
            # Non-challenge paths.
            ("https://www.linkedin.com/feed/", False),
            ("https://www.linkedin.com/login", False),
        ],
    )
    def test_classification(self, url, expected):
        assert nav.landed_on_checkpoint(url) is expected

    def test_segment_boundary_avoids_substring_false_positive(self):
        # Whole-segment match: a path that merely contains "checkpoint" is not one.
        assert (
            nav.landed_on_checkpoint("https://www.linkedin.com/in/checkpointer")
            is False
        )

    def test_checkpoint_in_query_does_not_trip(self):
        # Path-only match: a /checkpoint in the query string is not a checkpoint.
        assert (
            nav.landed_on_checkpoint("https://x/feed?next=/checkpoint/x") is False
        )


@pytest.mark.unit
class TestDiffRedirect:
    def test_clean_landing(self):
        url = "https://x/search?start=0&keywords=eng"
        assert nav.diff_redirect(url, url) is None

    def test_path_changed(self):
        drift = nav.diff_redirect("https://x/search", "https://x/feed")
        assert drift == ("path_changed", "/feed")

    def test_requested_param_reset(self):
        # ?start=1000 capped back to ?start=0 is a reset of a param WE requested.
        drift = nav.diff_redirect(
            "https://x/search?start=1000", "https://x/search?start=0"
        )
        assert drift[0] == "param_reset"
        assert "start=1000->0" in drift[1]

    def test_requested_param_dropped(self):
        drift = nav.diff_redirect("https://x/s?start=1000", "https://x/s")
        assert drift[0] == "param_reset"
        assert "start=1000->None" in drift[1]

    def test_linkedin_added_params_ignored(self):
        # trk/sessionId added by LinkedIn (not requested) must NOT false-flag.
        drift = nav.diff_redirect(
            "https://x/search?keywords=eng",
            "https://x/search?keywords=eng&trk=public&sessionId=abc",
        )
        assert drift is None

    def test_non_semantic_requested_param_dropped_ignored(self):
        # origin=FACETED_SEARCH is a navigation hint we always send; LinkedIn
        # dropping it on a correct landing is normalization, not a wrong landing.
        drift = nav.diff_redirect(
            "https://x/search/results/people?keywords=eng&origin=FACETED_SEARCH",
            "https://x/search/results/people?keywords=eng",
        )
        assert drift is None

    def test_non_semantic_requested_param_rewritten_ignored(self):
        # A rewritten non-semantic hint is likewise ignored, while a real filter
        # change on the same URL would still be caught (covered elsewhere).
        drift = nav.diff_redirect(
            "https://x/search?keywords=eng&origin=FACETED_SEARCH",
            "https://x/search?keywords=eng&origin=CLUSTER_EXPANSION",
        )
        assert drift is None

    def test_semantic_param_still_flagged_with_non_semantic_present(self):
        # The non-semantic ignore must not mask a real load-bearing param reset.
        drift = nav.diff_redirect(
            "https://x/search?keywords=eng&origin=FACETED_SEARCH",
            "https://x/search?keywords=other&origin=FACETED_SEARCH",
        )
        assert drift is not None
        assert drift[0] == "param_reset"
        assert "keywords=eng->other" in drift[1]


# ---------------------------------------------------------------------------
# navigate_guarded
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNavigateGuarded:
    @pytest.mark.asyncio
    async def test_clean_landing_returns(self):
        page = _page("https://www.linkedin.com/search/results/people/")
        await nav.navigate_guarded(
            page,
            "https://www.linkedin.com/search/results/people/?keywords=eng",
            strict_path="/search/results/people",
            settle_timeout_ms=0,
        )
        page.goto.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_login_bounce_raises_not_authenticated_with_evidence(self):
        page = _page("https://www.linkedin.com/feed/")

        # goto bounces to a login wall.
        async def _bounce(target, *_a, **_k):
            page.url = "https://www.linkedin.com/login"

        page.goto = AsyncMock(side_effect=_bounce)

        with patch(
            "automation.navigation.capture_error_context", new=AsyncMock()
        ) as cap:
            with pytest.raises(NotAuthenticatedException):
                await nav.navigate_guarded(
                    page, "https://www.linkedin.com/feed", settle_timeout_ms=0
                )
        cap.assert_awaited_once()
        assert cap.await_args.args[1] == "navigation_login_wall"

    @pytest.mark.asyncio
    async def test_checkpoint_bounce_raises_captcha_with_evidence(self):
        page = _page()

        async def _bounce(target, *_a, **_k):
            page.url = "https://www.linkedin.com/checkpoint/challenge/"

        page.goto = AsyncMock(side_effect=_bounce)

        with patch(
            "automation.navigation.capture_error_context", new=AsyncMock()
        ) as cap:
            with pytest.raises(CaptchaDetectedException):
                await nav.navigate_guarded(
                    page, "https://www.linkedin.com/feed", settle_timeout_ms=0
                )
        assert cap.await_args.args[1] == "navigation_challenge"

    @pytest.mark.asyncio
    async def test_param_reset_raises_unexpected_landing(self):
        page = _page()

        async def _cap_start(target, *_a, **_k):
            # Requested start=1000, LinkedIn caps it back to start=0.
            page.url = "https://www.linkedin.com/search/results/people/?start=0"

        page.goto = AsyncMock(side_effect=_cap_start)

        with patch("automation.navigation.capture_error_context", new=AsyncMock()):
            with pytest.raises(UnexpectedLandingException) as excinfo:
                await nav.navigate_guarded(
                    page,
                    "https://www.linkedin.com/search/results/people/?start=1000",
                    settle_timeout_ms=0,
                )
        assert excinfo.value.reason == "param_reset"

    @pytest.mark.asyncio
    async def test_strict_path_miss_raises(self):
        page = _page()

        async def _wrong(target, *_a, **_k):
            page.url = "https://www.linkedin.com/feed/"

        page.goto = AsyncMock(side_effect=_wrong)

        with patch("automation.navigation.capture_error_context", new=AsyncMock()):
            with pytest.raises(UnexpectedLandingException) as excinfo:
                await nav.navigate_guarded(
                    page,
                    "https://www.linkedin.com/search/results/people/",
                    strict_path="/search/results/people",
                    settle_timeout_ms=0,
                )
        assert excinfo.value.reason == "strict_path_miss"

    @pytest.mark.asyncio
    async def test_check_path_off_ignores_redirect(self):
        # The per-profile nav turns path-diff OFF: vanity-URL canonicalization is
        # a normal redirect, not a wrong landing.
        page = _page()

        async def _canonicalize(target, *_a, **_k):
            page.url = "https://www.linkedin.com/in/john-smith"

        page.goto = AsyncMock(side_effect=_canonicalize)

        # No raise even though the landed path differs from the requested one.
        await nav.navigate_guarded(
            page,
            "https://www.linkedin.com/in/john-123abc",
            check_path=False,
            settle_timeout_ms=0,
        )

    @pytest.mark.asyncio
    async def test_check_path_off_still_catches_challenge(self):
        # Challenge detection always runs, even with check_path off.
        page = _page()

        async def _bounce(target, *_a, **_k):
            page.url = "https://www.linkedin.com/authwall"

        page.goto = AsyncMock(side_effect=_bounce)

        with patch("automation.navigation.capture_error_context", new=AsyncMock()):
            with pytest.raises(CaptchaDetectedException):
                await nav.navigate_guarded(
                    page,
                    "https://www.linkedin.com/in/john",
                    check_path=False,
                    settle_timeout_ms=0,
                )

    @pytest.mark.asyncio
    async def test_unexpected_overlay_captured_not_fatal(self):
        # A blocking overlay is an anomaly (captured), not a stop: the call
        # returns normally.
        page = _page("https://www.linkedin.com/feed/", overlay_count=1)
        with patch(
            "automation.navigation.capture_anomaly_context", new=AsyncMock()
        ) as anomaly:
            await nav.navigate_guarded(
                page, "https://www.linkedin.com/feed", settle_timeout_ms=0
            )
        anomaly.assert_awaited_once()
        assert anomaly.await_args.args[1] == "unexpected_overlay"


@pytest.mark.unit
class TestSettle:
    @pytest.mark.asyncio
    async def test_load_state_resolves_skips_fixed_sleep(self):
        # When wait_for_load_state confirms the settle, no fixed sleep is added
        # (the hot path must not tax every guarded navigation with 2s).
        page = _page()
        page.wait_for_load_state = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        await nav._settle(page, 2000)
        page.wait_for_load_state.assert_awaited_once()
        page.wait_for_timeout.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fallback_sleep_when_load_state_times_out(self):
        # When the load-state wait cannot confirm (timeout / mocked page), the
        # fixed wait is used as a fallback so a slow page still settles.
        page = _page()
        page.wait_for_load_state = AsyncMock(side_effect=PWTimeoutError("slow"))
        page.wait_for_timeout = AsyncMock()
        await nav._settle(page, 2000)
        page.wait_for_timeout.assert_awaited_once_with(2000)

    @pytest.mark.asyncio
    async def test_never_raises(self):
        page = _page()
        page.wait_for_load_state = AsyncMock(side_effect=RuntimeError("x"))
        page.wait_for_timeout = AsyncMock(side_effect=RuntimeError("y"))
        await nav._settle(page, 2000)  # must not raise


@pytest.mark.unit
class TestSweepUnexpectedOverlay:
    @pytest.mark.asyncio
    async def test_no_overlay_returns_false(self):
        page = _page(overlay_count=0)
        with patch(
            "automation.navigation.capture_anomaly_context", new=AsyncMock()
        ) as anomaly:
            assert await nav.sweep_unexpected_overlay(page) is False
        anomaly.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_count_error_is_swallowed(self):
        page = _page()
        page.locator = MagicMock(side_effect=RuntimeError("boom"))
        # Never raises: a broken sweep must not derail navigation.
        assert await nav.sweep_unexpected_overlay(page) is False


# ---------------------------------------------------------------------------
# confirm_logged_in_dom
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfirmLoggedInDom:
    @pytest.mark.asyncio
    async def test_url_left_login_and_landmark_present(self):
        page = _page("https://www.linkedin.com/feed/")
        page.wait_for_url = AsyncMock()  # URL predicate satisfied
        page.wait_for_selector = AsyncMock()  # landmark renders
        await nav.confirm_logged_in_dom(page, timeout=1000)
        page.wait_for_selector.assert_awaited()

    @pytest.mark.asyncio
    async def test_landmark_missing_raises_unexpected_landing(self):
        page = _page("https://www.linkedin.com/feed/")
        page.wait_for_url = AsyncMock()
        page.wait_for_selector = AsyncMock(side_effect=PWTimeoutError("no landmark"))
        with patch("automation.navigation.capture_error_context", new=AsyncMock()):
            with pytest.raises(UnexpectedLandingException) as excinfo:
                await nav.confirm_logged_in_dom(page, timeout=10)
        assert excinfo.value.reason == "login_landmark_missing"

    @pytest.mark.asyncio
    async def test_still_on_login_wall_raises_not_authenticated(self):
        page = _page("https://www.linkedin.com/login")
        page.wait_for_url = AsyncMock(side_effect=PWTimeoutError("stuck on login"))
        with patch("automation.navigation.capture_error_context", new=AsyncMock()):
            with pytest.raises(NotAuthenticatedException):
                await nav.confirm_logged_in_dom(page, timeout=10)

    @pytest.mark.asyncio
    async def test_landmark_wait_shares_deadline_with_url_wait(self):
        # The two waits share ONE budget: if the URL wait consumed most of the
        # timeout, the landmark wait gets only the remainder (floored at 1000ms),
        # so a stuck login cannot hang for 2x the budget.
        page = _page("https://www.linkedin.com/feed/")
        page.wait_for_url = AsyncMock()

        # Simulate 8s already elapsed against a 10s budget by advancing the
        # monotonic clock between the deadline capture and the landmark wait.
        clock = iter([100.0, 108.0])  # deadline base, then "now" for remaining

        def _mono():
            try:
                return next(clock)
            except StopIteration:
                return 108.0

        with patch("automation.navigation.time.monotonic", side_effect=_mono):
            await nav.confirm_logged_in_dom(page, timeout=10_000)

        # 10s budget - 8s elapsed = 2s remaining for the landmark wait.
        assert page.wait_for_selector.await_args.kwargs["timeout"] == 2_000

    @pytest.mark.asyncio
    async def test_landmark_wait_floored_when_budget_nearly_gone(self):
        # When the URL wait ate essentially the whole budget, the landmark wait
        # is floored to 1000ms (a real, if small, chance to render) rather than 0.
        page = _page("https://www.linkedin.com/feed/")
        page.wait_for_url = AsyncMock()
        clock = iter([100.0, 109.9])  # 9.9s elapsed of a 10s budget

        def _mono():
            try:
                return next(clock)
            except StopIteration:
                return 109.9

        with patch("automation.navigation.time.monotonic", side_effect=_mono):
            await nav.confirm_logged_in_dom(page, timeout=10_000)

        assert page.wait_for_selector.await_args.kwargs["timeout"] == 1_000

    @pytest.mark.asyncio
    async def test_challenge_replaced_page_after_url_settled(self):
        # URL left the login flow, but a challenge then replaced the page before
        # the landmark rendered -> raise CAPTCHA, not the generic landing error.
        page = _page("https://www.linkedin.com/checkpoint/challenge/")
        page.wait_for_url = AsyncMock()
        page.wait_for_selector = AsyncMock(side_effect=PWTimeoutError("no landmark"))
        with patch("automation.navigation.capture_error_context", new=AsyncMock()):
            with pytest.raises(CaptchaDetectedException):
                await nav.confirm_logged_in_dom(page, timeout=10)


# ---------------------------------------------------------------------------
# verify_listing_rendered
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifyListingRendered:
    @pytest.mark.asyncio
    async def test_ready_selector_present_returns_true(self):
        page = _page("https://www.linkedin.com/search/results/people/")
        page.wait_for_selector = AsyncMock()  # race resolves
        with patch.object(
            sel.SEARCH_RESULTS_READY, "count", new=AsyncMock(return_value=3)
        ):
            assert (
                await nav.verify_listing_rendered(page, sel.SEARCH_RESULTS_READY)
                is True
            )
        page.reload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_marker_wins_returns_false_no_reload(self):
        page = _page("https://www.linkedin.com/search/results/people/")
        page.wait_for_selector = AsyncMock()

        empty_loc = MagicMock()
        empty_loc.count = AsyncMock(return_value=1)
        page.locator = MagicMock(return_value=empty_loc)

        with patch.object(
            sel.SEARCH_RESULTS_READY, "count", new=AsyncMock(return_value=0)
        ):
            result = await nav.verify_listing_rendered(
                page, sel.SEARCH_RESULTS_READY, empty_selector=".no-results"
            )
        assert result is False
        page.reload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_rendered_reloads_once_then_succeeds(self):
        page = _page("https://www.linkedin.com/search/results/people/")
        # First race times out, second (after reload) resolves.
        page.wait_for_selector = AsyncMock(
            side_effect=[PWTimeoutError("not yet"), None]
        )
        with patch.object(
            sel.SEARCH_RESULTS_READY, "count", new=AsyncMock(return_value=2)
        ):
            result = await nav.verify_listing_rendered(
                page, sel.SEARCH_RESULTS_READY, ready_timeout_ms=5
            )
        assert result is True
        page.reload.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_challenge_replaced_listing_raises(self):
        page = _page("https://www.linkedin.com/checkpoint/challenge/")
        page.wait_for_selector = AsyncMock(side_effect=PWTimeoutError("gone"))
        with patch("automation.navigation.capture_error_context", new=AsyncMock()):
            with pytest.raises(CaptchaDetectedException):
                await nav.verify_listing_rendered(
                    page, sel.SEARCH_RESULTS_READY, ready_timeout_ms=5
                )
        # Detected before any reload.
        page.reload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_never_renders_fails_loud(self):
        page = _page("https://www.linkedin.com/search/results/people/")
        page.wait_for_selector = AsyncMock(side_effect=PWTimeoutError("never"))
        with patch.object(
            sel.SEARCH_RESULTS_READY, "fail_loud", new=AsyncMock(
                side_effect=SelectorNotFoundException("gone")
            )
        ) as fail_loud:
            with pytest.raises(SelectorNotFoundException):
                await nav.verify_listing_rendered(
                    page, sel.SEARCH_RESULTS_READY, ready_timeout_ms=5
                )
        fail_loud.assert_awaited_once()
        page.reload.assert_awaited_once()
