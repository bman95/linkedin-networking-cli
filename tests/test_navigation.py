"""Tests for the navigation landing guard (issue #16) and the resilience layer
around it (issue #17).

Cover the pure URL helpers (path/param diff, challenge detection), the
``navigate_guarded`` gate (clean landing, challenge/login bounce, param reset,
ignored LinkedIn-added params, strict_path miss, overlay sweep + evidence
capture), the DOM-backed login confirmation, and the empty-vs-not-rendered
listing disambiguation (reload-once).

The #17 layer adds: transient ``net::ERR_*`` goto retry with backoff and a
bounded count, the renderer-crash watchdog (outer ``asyncio.wait_for`` so a
wedged renderer can't deadlock the goto), crash-shaped error detection + one
context-refresh recovery, the benign-interstitial surf (cookie banner / email
modal only — never CAPTCHA), and the per-item ``run_bounded`` watchdog.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from playwright.async_api import Error as PWError
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


# ---------------------------------------------------------------------------
# Issue #17 — resilience layer
# ---------------------------------------------------------------------------


def _quiet_page(url="https://www.linkedin.com/search/results/people/"):
    """A guard-friendly page that also surfs cleanly (no interstitials present).

    Extends ``_page`` so the benign-interstitial surf finds nothing: every
    ``query_selector`` returns ``None`` and the overlay count is 0, so the
    landing guard and surf are both no-ops and the test can focus on the
    retry/crash behaviour.
    """
    page = _page(url)
    page.query_selector = AsyncMock(return_value=None)
    return page


@pytest.mark.unit
class TestIsCrashError:
    @pytest.mark.parametrize(
        "msg",
        [
            "Page crashed",
            "Target page, context or browser has been closed",
            "Navigation hung past 45s — renderer unresponsive",
            "TARGET CLOSED",  # case-insensitive
        ],
    )
    def test_crash_shaped_messages(self, msg):
        assert nav._is_crash_error(Exception(msg)) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "net::ERR_NAME_NOT_RESOLVED",
            "Timeout 30000ms exceeded",
            "some unrelated failure",
        ],
    )
    def test_non_crash_messages(self, msg):
        assert nav._is_crash_error(Exception(msg)) is False


@pytest.mark.unit
class TestGotoRetry:
    @pytest.mark.asyncio
    async def test_transient_net_error_retried_then_succeeds(self):
        """A net::ERR_* goto is retried (with backoff) and then lands cleanly."""
        page = _quiet_page()
        calls = {"n": 0}

        async def _goto(target, *_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PWError("net::ERR_NAME_NOT_RESOLVED at " + target)
            page.url = target

        page.goto = AsyncMock(side_effect=_goto)

        with patch("automation.navigation.asyncio.sleep", new=AsyncMock()) as slept:
            await nav.navigate_guarded(
                page,
                "https://www.linkedin.com/search/results/people/",
                strict_path="/search/results/people",
                settle_timeout_ms=0,
                max_retries=2,
                retry_backoff_base_s=3,
            )
        assert calls["n"] == 2
        # First retry backs off base*(0+1) = 3s.
        slept.assert_awaited_once_with(3)

    @pytest.mark.asyncio
    async def test_transient_net_error_gives_up_after_bounded_count(self):
        """After max_retries+1 transient failures the error propagates."""
        page = _quiet_page()
        page.goto = AsyncMock(side_effect=PWError("net::ERR_CONNECTION_CLOSED"))

        with patch("automation.navigation.asyncio.sleep", new=AsyncMock()) as slept:
            with pytest.raises(PWError):
                await nav.navigate_guarded(
                    page,
                    "https://www.linkedin.com/feed/",
                    check_path=False,
                    settle_timeout_ms=0,
                    max_retries=2,
                )
        # 1 initial + 2 retries = 3 attempts; 2 backoff sleeps between them.
        assert page.goto.await_count == 3
        assert slept.await_count == 2

    @pytest.mark.asyncio
    async def test_non_transient_playwright_error_not_retried(self):
        """A non-``net::ERR_*`` Playwright error is raised on the first attempt."""
        page = _quiet_page()
        # A clearly non-net error: no retry, no recovery.
        page.goto = AsyncMock(side_effect=PWError("Unsupported scheme"))
        with patch("automation.navigation.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(PWError):
                await nav.navigate_guarded(
                    page,
                    "ftp://nope",
                    check_path=False,
                    settle_timeout_ms=0,
                    max_retries=2,
                )
        page.goto.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_goto_hang_becomes_crash_shaped_error(self):
        """The outer watchdog converts a deadlocked goto into a crash error.

        A renderer crash detaches the CDP session goto's own timer is bound to,
        so the driver timeout never fires. Modeled here as the outer
        ``asyncio.wait_for`` raising ``asyncio.TimeoutError``; the helper must
        re-raise it as a crash-shaped (``unresponsive``) ``PlaywrightError`` so
        recovery — not transient-retry — engages.
        """
        page = _quiet_page()
        # No recover callback: the crash-shaped error must propagate.
        with patch(
            "automation.navigation.asyncio.wait_for",
            new=AsyncMock(side_effect=asyncio.TimeoutError()),
        ):
            with pytest.raises(PWError) as excinfo:
                await nav.navigate_guarded(
                    page,
                    "https://www.linkedin.com/feed/",
                    check_path=False,
                    settle_timeout_ms=0,
                )
        assert nav._is_crash_error(excinfo.value)
        assert "unresponsive" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_driver_goto_timeout_propagates_plain_not_crash(self):
        """A driver PlaywrightTimeoutError (slow page) is NOT crash-shaped.

        It must propagate unchanged so the caller treats it as an ordinary
        navigation timeout — never folded into the wedge path that would force a
        full browser refresh on every slow load.
        """
        page = _quiet_page()
        page.goto = AsyncMock(side_effect=PWTimeoutError("Timeout 30000ms exceeded"))
        recover = AsyncMock()
        with pytest.raises(PWTimeoutError) as excinfo:
            await nav.navigate_guarded(
                page,
                "https://www.linkedin.com/feed/",
                check_path=False,
                settle_timeout_ms=0,
                recover=recover,
            )
        # The error stays a plain timeout (not re-wrapped "unresponsive") and no
        # context refresh is triggered.
        assert not nav._is_crash_error(excinfo.value)
        recover.assert_not_awaited()


@pytest.mark.unit
class TestCrashRecovery:
    @pytest.mark.asyncio
    async def test_crash_triggers_recover_and_one_retry(self):
        """A crash-shaped goto failure refreshes the context and retries once."""
        crashed = _quiet_page("https://www.linkedin.com/feed/")
        crashed.goto = AsyncMock(side_effect=PWError("Page crashed"))

        fresh = _quiet_page("https://www.linkedin.com/in/jane/")

        async def _fresh_goto(target, *_a, **_k):
            fresh.url = target

        fresh.goto = AsyncMock(side_effect=_fresh_goto)
        recover = AsyncMock(return_value=fresh)

        result = await nav.navigate_guarded(
            crashed,
            "https://www.linkedin.com/in/jane/",
            check_path=False,
            settle_timeout_ms=0,
            recover=recover,
        )
        recover.assert_awaited_once()
        # The retry ran on the FRESH page, and that page is returned for rebind.
        fresh.goto.assert_awaited_once()
        assert result is fresh

    @pytest.mark.asyncio
    async def test_second_crash_after_refresh_propagates(self):
        """If the fresh page also crashes, the error propagates (no infinite loop)."""
        crashed = _quiet_page("https://www.linkedin.com/feed/")
        crashed.goto = AsyncMock(side_effect=PWError("Page crashed"))
        still_dead = _quiet_page()
        still_dead.goto = AsyncMock(side_effect=PWError("Target closed"))
        recover = AsyncMock(return_value=still_dead)

        with pytest.raises(PWError):
            await nav.navigate_guarded(
                crashed,
                "https://www.linkedin.com/feed/",
                check_path=False,
                settle_timeout_ms=0,
                recover=recover,
            )
        recover.assert_awaited_once()  # only ONE refresh attempt

    @pytest.mark.asyncio
    async def test_transient_error_does_not_trigger_recover(self):
        """A plain net error is handled by retry, never by a context refresh."""
        page = _quiet_page()
        page.goto = AsyncMock(side_effect=PWError("net::ERR_NAME_NOT_RESOLVED"))
        recover = AsyncMock()
        with patch("automation.navigation.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(PWError):
                await nav.navigate_guarded(
                    page,
                    "https://www.linkedin.com/feed/",
                    check_path=False,
                    settle_timeout_ms=0,
                    max_retries=1,
                    recover=recover,
                )
        recover.assert_not_awaited()


@pytest.mark.unit
class TestSurfBenignInterstitials:
    @pytest.mark.asyncio
    async def test_dismisses_cookie_banner(self):
        page = _page()
        cookie_anchor = sel.COOKIE_BANNER_DISMISS.anchor
        btn = AsyncMock()

        async def _qs(candidate):
            return btn if candidate == cookie_anchor else None

        page.query_selector = AsyncMock(side_effect=_qs)
        dismissed = await nav.surf_benign_interstitials(page)
        assert dismissed is True
        btn.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dismisses_email_required_modal(self):
        page = _page()
        email_anchor = sel.EMAIL_REQUIRED_DISMISS.anchor
        btn = AsyncMock()

        async def _qs(candidate):
            return btn if candidate == email_anchor else None

        page.query_selector = AsyncMock(side_effect=_qs)
        dismissed = await nav.surf_benign_interstitials(page)
        assert dismissed is True
        btn.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_nothing_to_surf_returns_false(self):
        page = _page()
        page.query_selector = AsyncMock(return_value=None)
        assert await nav.surf_benign_interstitials(page) is False

    @pytest.mark.asyncio
    async def test_click_failure_is_non_fatal(self):
        page = _page()
        btn = AsyncMock()
        btn.click = AsyncMock(side_effect=RuntimeError("detached"))
        page.query_selector = AsyncMock(return_value=btn)
        # Must not raise even though every dismiss click fails.
        assert await nav.surf_benign_interstitials(page) is False

    @pytest.mark.asyncio
    async def test_surf_runs_during_navigate_guarded(self):
        """navigate_guarded surfs after settle and before the landing guard."""
        page = _quiet_page()
        with patch(
            "automation.navigation.surf_benign_interstitials", new=AsyncMock()
        ) as surf:
            await nav.navigate_guarded(
                page,
                "https://www.linkedin.com/search/results/people/",
                strict_path="/search/results/people",
                settle_timeout_ms=0,
            )
        surf.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_surf_can_be_disabled(self):
        page = _quiet_page()
        with patch(
            "automation.navigation.surf_benign_interstitials", new=AsyncMock()
        ) as surf:
            await nav.navigate_guarded(
                page,
                "https://www.linkedin.com/search/results/people/",
                strict_path="/search/results/people",
                settle_timeout_ms=0,
                surf=False,
            )
        surf.assert_not_awaited()


@pytest.mark.unit
class TestRunBounded:
    @pytest.mark.asyncio
    async def test_returns_result_within_budget(self):
        async def _work():
            return "done"

        result = await nav.run_bounded(_work(), timeout_s=5)
        assert result == "done"

    @pytest.mark.asyncio
    async def test_timeout_refreshes_and_reraises(self):
        async def _wedged():
            await asyncio.sleep(10)

        recover = AsyncMock()
        with pytest.raises(asyncio.TimeoutError):
            await nav.run_bounded(_wedged(), timeout_s=0.01, recover=recover)
        recover.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_without_recover_still_reraises(self):
        async def _wedged():
            await asyncio.sleep(10)

        with pytest.raises(asyncio.TimeoutError):
            await nav.run_bounded(_wedged(), timeout_s=0.01)

    @pytest.mark.asyncio
    async def test_recover_failure_does_not_mask_timeout(self):
        async def _wedged():
            await asyncio.sleep(10)

        recover = AsyncMock(side_effect=RuntimeError("relaunch failed"))
        # The TimeoutError (the real failure) must still surface, not the
        # refresh's own error.
        with pytest.raises(asyncio.TimeoutError):
            await nav.run_bounded(_wedged(), timeout_s=0.01, recover=recover)

    @pytest.mark.asyncio
    async def test_slow_but_within_budget_warns(self, caplog):
        """A unit that runs past half the budget (but finishes) is flagged slow.

        Real clock with wide margins (sleep 60ms vs a 1s budget — half is 500ms):
        the sleep comfortably exceeds half the budget yet stays far under it, so
        the slow-warning branch fires without a tight real-clock race. Patching
        ``time.monotonic`` is avoided here because ``asyncio.wait_for`` reads the
        same stdlib clock and would consume the stub.
        """
        import logging

        async def _slowish():
            await asyncio.sleep(0.06)
            return "ok"

        with caplog.at_level(logging.WARNING, logger="automation.navigation"):
            result = await nav.run_bounded(_slowish(), timeout_s=0.1, label="probe")
        assert result == "ok"
        assert any("Slow probe" in r.message for r in caplog.records)
