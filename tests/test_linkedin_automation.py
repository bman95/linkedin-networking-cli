"""
Unit tests for LinkedIn automation module.

Tests LinkedInAutomation class with mocked Playwright interactions.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, Mock, MagicMock, patch
from datetime import datetime, timezone, date

from automation.linkedin import LinkedInAutomation, LinkedInProfile, ConnectResult
from automation import selectors as sel
from database.models import Campaign, Contact


# ============================================================================
# LinkedInAutomation Initialization Tests
# ============================================================================

@pytest.mark.unit
class TestLinkedInAutomationInit:
    """Test LinkedInAutomation initialization."""

    def test_init_with_db_manager_and_settings(self, db_manager, app_settings):
        """Test initialization with database manager and settings."""
        automation = LinkedInAutomation(db_manager, app_settings)

        assert automation.db_manager == db_manager
        assert automation.settings == app_settings
        assert automation.BASE_URL == "https://www.linkedin.com"
        assert automation.is_authenticated is False

    def test_search_url_correct(self, db_manager, app_settings):
        """Test that SEARCH_URL is constructed correctly."""
        automation = LinkedInAutomation(db_manager, app_settings)
        expected_url = "https://www.linkedin.com/search/results/people/"

        assert automation.SEARCH_URL == expected_url


# ============================================================================
# URL Building Tests
# ============================================================================

@pytest.mark.unit
class TestSearchParamsBuilding:
    """Test search parameter building."""

    def test_build_search_params_with_keywords(self, mock_linkedin_automation):
        """Test building search params with keywords."""
        campaign = Campaign(
            name="Test",
            keywords="software engineer"
        )

        params = mock_linkedin_automation._build_search_params(campaign)

        assert "keywords=software%20engineer" in params
        assert "origin=FACETED_SEARCH" in params

    def test_build_search_params_with_location(self, mock_linkedin_automation):
        """Test building search params with location."""
        campaign = Campaign(
            name="Test",
            geo_urn="90000084"
        )

        params = mock_linkedin_automation._build_search_params(campaign)

        assert 'geoUrn=["90000084"]' in params
        assert "origin=FACETED_SEARCH" in params

    def test_build_search_params_with_industries(self, mock_linkedin_automation):
        """Test building search params with industries."""
        campaign = Campaign(
            name="Test",
            industry_ids="4,6,96"
        )

        params = mock_linkedin_automation._build_search_params(campaign)

        assert 'industry=["4","6","96"]' in params

    def test_build_search_params_with_network(self, mock_linkedin_automation):
        """Test building search params with network filter."""
        campaign = Campaign(
            name="Test",
            network='["F"]'
        )

        params = mock_linkedin_automation._build_search_params(campaign)

        assert 'network=["F"]' in params

    def test_build_search_params_with_all_filters(self, mock_linkedin_automation):
        """Test building search params with all filters."""
        campaign = Campaign(
            name="Test",
            keywords="software engineer",
            geo_urn="90000084",
            industry_ids="4,6",
            network='["F","S"]'
        )

        params = mock_linkedin_automation._build_search_params(campaign)

        assert "keywords=" in params
        assert "geoUrn=" in params
        assert "industry=" in params
        assert "network=" in params
        assert "origin=FACETED_SEARCH" in params

    def test_build_search_params_url_encodes_keywords(self, mock_linkedin_automation):
        """Test that keywords are URL encoded."""
        campaign = Campaign(
            name="Test",
            keywords="software & data engineer"
        )

        params = mock_linkedin_automation._build_search_params(campaign)

        # '&' should be encoded as %26
        assert "software%20%26%20data" in params


# ============================================================================
# Profile Extraction Tests
# ============================================================================

@pytest.mark.unit
class TestProfileExtraction:
    """Test profile information extraction."""

    @pytest.mark.asyncio
    async def test_extract_profile_info_with_valid_data(self, mock_linkedin_automation):
        """Test extracting profile info from valid element."""
        mock_element = AsyncMock()

        # Mock link element
        mock_link = AsyncMock()
        mock_link.get_attribute = AsyncMock(return_value="https://linkedin.com/in/johndoe")
        mock_link.inner_text = AsyncMock(return_value="John Doe")

        mock_element.query_selector = AsyncMock(return_value=mock_link)

        profile = await mock_linkedin_automation._extract_profile_info(mock_element)

        assert profile is not None
        assert isinstance(profile, LinkedInProfile)
        assert profile.name == "John Doe"
        assert "johndoe" in profile.profile_url

    @pytest.mark.asyncio
    async def test_extract_profile_info_no_link(self, mock_linkedin_automation):
        """Test extracting profile info when no link is found."""
        mock_element = AsyncMock()
        mock_element.query_selector = AsyncMock(return_value=None)

        profile = await mock_linkedin_automation._extract_profile_info(mock_element)

        assert profile is None

    @pytest.mark.asyncio
    async def test_extract_profile_info_handles_exceptions(self, mock_linkedin_automation):
        """Test that profile extraction handles exceptions gracefully."""
        mock_element = AsyncMock()
        mock_element.query_selector = AsyncMock(side_effect=Exception("Test error"))

        profile = await mock_linkedin_automation._extract_profile_info(mock_element)

        assert profile is None


# ============================================================================
# Login Tests
# ============================================================================

@pytest.mark.unit
class TestLogin:
    """Test login functionality."""

    @pytest.mark.asyncio
    async def test_login_with_existing_session(self, mock_linkedin_automation):
        """Test login when session already exists (feed loads without redirect)."""
        # "Already logged in?" is URL-only: an unauthenticated session is
        # redirected away from /feed to a login wall, so staying on the feed URL
        # is itself proof of an active session (no nav DOM landmark required).
        mock_page = mock_linkedin_automation.page
        mock_page.goto = AsyncMock()
        mock_page.url = "https://www.linkedin.com/feed/"

        result = await mock_linkedin_automation.login()

        assert result is True
        assert mock_linkedin_automation.is_authenticated is True
        # Already authenticated: no credentials should be entered.
        assert mock_page.fill.call_count == 0

    @pytest.mark.asyncio
    async def test_existing_session_url_only_no_landmark_needed(
        self, mock_linkedin_automation
    ):
        """A non-login feed URL is 'already logged in' WITHOUT a nav landmark.

        Login detection is URL-only: an unauthenticated session is always
        redirected away from /feed to a /login or /authwall, so a feed URL is a
        live session even when no logged-in nav DOM landmark renders (LinkedIn's
        SDUI rewrites those hooks). Even if a brittle landmark wait would time
        out, the probe must NOT fall through to /login and re-enter credentials.
        """
        from playwright.async_api import TimeoutError as PWTimeoutError

        mock_linkedin_automation.is_authenticated = False
        mock_page = mock_linkedin_automation.page
        mock_page.goto = AsyncMock()
        mock_page.url = "https://www.linkedin.com/feed/"
        # A landmark wait, were it still attempted, would time out — prove the
        # URL-only shortcut fires regardless of the (brittle) nav DOM.
        mock_page.wait_for_selector = AsyncMock(side_effect=PWTimeoutError("no landmark"))

        result = await mock_linkedin_automation.login()

        assert result is True
        assert mock_linkedin_automation.is_authenticated is True
        # Never fell through to /login for a session that was actually live.
        assert all(
            "/login" not in str(c.args[0]) for c in mock_page.goto.await_args_list
        )
        # No credentials entered.
        assert mock_page.fill.call_count == 0

    @pytest.mark.asyncio
    async def test_feed_probe_authwall_raises_captcha(self, mock_linkedin_automation):
        """A stored session blocked by /authwall on the feed probe is surfaced.

        A non-checkpoint challenge (/authwall) is a genuine block: the login flow
        must raise CaptchaDetectedException (with evidence) instead of quietly
        routing to /login and pushing through the wall. A /checkpoint landing is
        different (a routine verification step) and is covered separately.
        """
        from exceptions import CaptchaDetectedException

        mock_linkedin_automation.is_authenticated = False
        mock_page = mock_linkedin_automation.page
        mock_page.goto = AsyncMock()
        mock_page.url = "https://www.linkedin.com/authwall"

        with patch(
            "automation.linkedin.capture_error_context", new=AsyncMock()
        ) as cap:
            with pytest.raises(CaptchaDetectedException):
                await mock_linkedin_automation.login()
        cap.assert_awaited()
        assert cap.await_args.args[1] == "login_feed_probe_challenge"
        # Never routed to the login page after a challenge.
        assert all(
            "/login" not in str(c.args[0]) for c in mock_page.goto.await_args_list
        )

    @pytest.mark.asyncio
    async def test_feed_probe_checkpoint_defers_to_login_redirect(
        self, mock_linkedin_automation
    ):
        """A /checkpoint landing is login-in-progress, not a CAPTCHA abort.

        Regression for issue #16 P1: LinkedIn's routine login verification/2FA
        uses /checkpoint, and the existing _wait_for_login_redirect flow EXPECTS
        a checkpoint step during a SUCCESSFUL login. The feed probe must hand the
        checkpoint off to that logic (NOT raise CaptchaDetectedException, NOT
        re-route to /login, which would discard the verification step) so a
        legitimate 2FA login completes.
        """
        mock_linkedin_automation.is_authenticated = False
        mock_page = mock_linkedin_automation.page
        mock_page.goto = AsyncMock()
        mock_page.url = "https://www.linkedin.com/checkpoint/challenge/"

        with patch.object(
            mock_linkedin_automation.settings,
            "get_browser_settings",
            return_value={"headless": False},
        ), patch.object(
            mock_linkedin_automation,
            "_wait_for_login_redirect",
            new=AsyncMock(),
        ) as redirect, patch(
            "automation.linkedin.capture_error_context", new=AsyncMock()
        ) as cap:
            result = await mock_linkedin_automation.login()

        assert result is True
        assert mock_linkedin_automation.is_authenticated is True
        # Handed off to the redirect-confirmation logic instead of aborting.
        redirect.assert_awaited_once()
        # No CAPTCHA evidence bundle was captured: this is not treated as a block.
        assert all(
            c.args[1] != "login_feed_probe_challenge" for c in cap.await_args_list
        )
        # The checkpoint step was never discarded by a re-route to /login.
        assert all(
            "/login" not in str(c.args[0]) for c in mock_page.goto.await_args_list
        )

    @pytest.mark.asyncio
    async def test_feed_probe_checkpoint_headless_fails_fast(
        self, mock_linkedin_automation
    ):
        """A /checkpoint under headless fails fast instead of hanging 10 minutes.

        A checkpoint needs a human to complete the verification in a visible
        browser; headless has no window for that. The checkpoint deferral must
        therefore raise an actionable LoginFailedException immediately rather than
        blocking a CI/background run on the full manual-login timeout.
        """
        from exceptions import LoginFailedException

        mock_linkedin_automation.is_authenticated = False
        mock_page = mock_linkedin_automation.page
        mock_page.goto = AsyncMock()
        mock_page.url = "https://www.linkedin.com/checkpoint/challenge/"

        with patch.object(
            mock_linkedin_automation.settings,
            "get_browser_settings",
            return_value={"headless": True},
        ), patch.object(
            mock_linkedin_automation,
            "_wait_for_login_redirect",
            new=AsyncMock(),
        ) as redirect:
            with pytest.raises(LoginFailedException):
                await mock_linkedin_automation.login()

        # Never waited on the 10-minute redirect under headless.
        redirect.assert_not_awaited()
        assert mock_linkedin_automation.is_authenticated is False

    @pytest.mark.asyncio
    async def test_manual_login_preserves_typed_challenge(
        self, mock_linkedin_automation
    ):
        """Manual login surfaces a challenge as its typed self, not a timeout.

        The DOM-backed confirmation can now raise CaptchaDetectedException; the
        manual-login branch must let it propagate (not wrap it into a generic
        "manual login timed out" LoginFailedException) so the caller stops.
        """
        from unittest.mock import PropertyMock
        from exceptions import CaptchaDetectedException
        from config.settings import AppSettings

        mock_linkedin_automation.is_authenticated = False
        mock_page = mock_linkedin_automation.page
        mock_page.goto = AsyncMock()
        # Feed probe lands on /login (a wall) -> proceed to authenticate.
        mock_page.url = "https://www.linkedin.com/login"

        with patch.object(
            AppSettings, "linkedin_email", new_callable=PropertyMock, return_value=""
        ), patch.object(
            AppSettings, "linkedin_password", new_callable=PropertyMock, return_value=""
        ), patch.object(
            mock_linkedin_automation.settings,
            "get_browser_settings",
            return_value={"headless": False},
        ), patch(
            "automation.interactions.detect_captcha", new=AsyncMock(return_value=False)
        ), patch.object(
            mock_linkedin_automation,
            "_wait_for_login_redirect",
            new=AsyncMock(side_effect=CaptchaDetectedException("wall")),
        ):
            with pytest.raises(CaptchaDetectedException):
                await mock_linkedin_automation.login()

    @pytest.mark.asyncio
    async def test_login_preserves_unexpected_landing(
        self, mock_linkedin_automation
    ):
        """A soft-block wrong landing during login keeps its typed self.

        confirm_logged_in_dom raises UnexpectedLandingException when the URL
        leaves the login flow but no logged-in nav landmark renders (a soft
        block / interstitial). The outer login handler must let it propagate, not
        wrap it into a generic LoginFailedException, so the caller can stop to
        protect the account rather than retrying credentials into a wall. This
        exercises the credentials path, whose _wait_for_login_redirect call has
        no inner guard and relies on the outer handler.
        """
        from unittest.mock import PropertyMock
        from exceptions import UnexpectedLandingException
        from config.settings import AppSettings

        mock_linkedin_automation.is_authenticated = False
        mock_page = mock_linkedin_automation.page
        mock_page.goto = AsyncMock()
        mock_page.url = "https://www.linkedin.com/login"

        with patch.object(
            AppSettings, "linkedin_email", new_callable=PropertyMock,
            return_value="user@example.com",
        ), patch.object(
            AppSettings, "linkedin_password", new_callable=PropertyMock,
            return_value="secret",
        ), patch(
            "automation.interactions.detect_captcha", new=AsyncMock(return_value=False)
        ), patch.object(
            mock_linkedin_automation,
            "_wait_for_login_redirect",
            new=AsyncMock(
                side_effect=UnexpectedLandingException(
                    "soft block", reason="login_landmark_missing"
                )
            ),
        ):
            with pytest.raises(UnexpectedLandingException):
                await mock_linkedin_automation.login()

    @pytest.mark.asyncio
    async def test_login_with_credentials(self, db_manager, app_settings, mock_page):
        """Login types credentials character-by-character (no instant fill)."""
        automation = LinkedInAutomation(db_manager, app_settings)
        automation.page = mock_page
        automation.context = AsyncMock()

        # Visiting /feed redirects to /login -> credentials flow is triggered.
        # The redirect is modeled by having goto always land on /login, so the
        # DOM-confirmed session probe (issue #16) reads a login wall and the
        # "already authenticated" early-return is correctly skipped.
        async def _goto(url, *_a, **_k):
            mock_page.url = "https://www.linkedin.com/login"

        mock_page.url = "https://www.linkedin.com/login"
        mock_page.goto = AsyncMock(side_effect=_goto)
        # No CAPTCHA present on the page.
        mock_page.query_selector = AsyncMock(return_value=None)
        mock_page.content = AsyncMock(return_value="")

        # Hand back a distinct locator per selector so we can assert on each.
        locators = {}

        def _locator(selector):
            loc = AsyncMock()
            loc.click = AsyncMock()
            loc.clear = AsyncMock()
            loc.press_sequentially = AsyncMock()
            loc.bounding_box = AsyncMock(return_value=None)
            loc.count = AsyncMock(return_value=0)
            loc.first = loc
            locators[selector] = loc
            return loc

        mock_page.locator = MagicMock(side_effect=_locator)

        result = await automation.login()

        assert result is True
        # Humanized typing: no instant fill, and each credential is typed key
        # by key (one press_sequentially() call per character).
        assert mock_page.fill.call_count == 0
        email = app_settings.linkedin_email
        password = app_settings.linkedin_password
        assert locators["input#username"].press_sequentially.call_count == len(email)
        assert locators["input#password"].press_sequentially.call_count == len(password)
        # Each field is cleared first so autofill/remembered values don't get
        # appended to (overwrite semantics, matching the old fill).
        assert locators["input#username"].clear.called
        assert locators["input#password"].clear.called
        # Submit button is clicked after a natural mouse move.
        assert locators["button[type=submit]"].click.called


# ============================================================================
# Search Location Tests
# ============================================================================

@pytest.mark.unit
class TestSearchLocation:
    """Test location search via the search filter UI."""

    @pytest.mark.asyncio
    async def test_search_location_valid_query(self, mock_linkedin_automation):
        """Test searching for a location with valid query."""
        with patch.object(
            mock_linkedin_automation,
            "_search_location_via_filter_ui",
            new_callable=AsyncMock,
            return_value=[{"name": "San Francisco Bay Area", "geoUrn": "90000084"}],
        ):
            results = await mock_linkedin_automation.search_location("San Francisco")

        assert len(results) == 1
        assert results[0]["name"] == "San Francisco Bay Area"
        assert results[0]["geoUrn"] == "90000084"

    @pytest.mark.asyncio
    async def test_search_location_empty_query(self, mock_linkedin_automation):
        """Test searching with empty query."""
        results = await mock_linkedin_automation.search_location("")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_location_not_authenticated(self, db_manager, app_settings):
        """Test location search when not authenticated."""
        automation = LinkedInAutomation(db_manager, app_settings)
        automation.is_authenticated = False

        with pytest.raises(Exception, match="Not authenticated"):
            await automation.search_location("test")

    @pytest.mark.asyncio
    async def test_search_location_ui_error(self, mock_linkedin_automation):
        """Test location search when driving the filter UI fails."""
        with patch.object(
            mock_linkedin_automation,
            "_search_location_via_filter_ui",
            new_callable=AsyncMock,
            side_effect=Exception("UI structure changed"),
        ):
            results = await mock_linkedin_automation.search_location("test")

        assert results == []


# ============================================================================
# Search Profiles Tests
# ============================================================================

@pytest.mark.unit
class TestSearchProfiles:
    """Test profile searching."""

    @pytest.mark.asyncio
    async def test_search_profiles_not_authenticated(self, db_manager, app_settings):
        """Test that search fails when not authenticated."""
        automation = LinkedInAutomation(db_manager, app_settings)
        automation.is_authenticated = False

        campaign = Campaign(name="Test")

        with pytest.raises(Exception, match="Not authenticated"):
            await automation.search_profiles(campaign)

    @pytest.mark.asyncio
    async def test_search_profiles_with_results(self, mock_linkedin_automation):
        """Test searching profiles returns results."""
        campaign = Campaign(
            name="Test",
            keywords="software engineer"
        )

        # Mock profile elements
        mock_element = AsyncMock()
        mock_linkedin_automation.page.query_selector_all = AsyncMock(return_value=[mock_element])

        # Mock profile extraction
        mock_profile = LinkedInProfile(
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe",
            headline="Software Engineer"
        )

        with patch.object(
            mock_linkedin_automation,
            '_extract_profile_info',
            return_value=mock_profile
        ):
            profiles = await mock_linkedin_automation.search_profiles(campaign, limit=10)

            assert len(profiles) > 0
            assert isinstance(profiles[0], LinkedInProfile)

    @pytest.mark.asyncio
    async def test_search_profiles_with_progress_callback(self, mock_linkedin_automation):
        """Test that progress callback is called during search."""
        campaign = Campaign(name="Test")
        mock_linkedin_automation.page.query_selector_all = AsyncMock(return_value=[])

        callback_calls = []

        def progress_callback(message):
            callback_calls.append(message)

        await mock_linkedin_automation.search_profiles(
            campaign,
            limit=10,
            progress_callback=progress_callback
        )

        assert len(callback_calls) > 0
        assert any("Starting profile search" in call for call in callback_calls)


# ============================================================================
# Context Manager Tests
# ============================================================================

@pytest.mark.unit
class TestContextManager:
    """Test async context manager functionality."""

    @pytest.mark.asyncio
    async def test_context_manager_enter_starts_browser(self, db_manager, app_settings):
        """Test that entering context manager starts browser."""
        automation = LinkedInAutomation(db_manager, app_settings)

        with patch.object(automation, 'start_browser', new_callable=AsyncMock) as mock_start:
            async with automation:
                mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_exit_closes_browser(self, db_manager, app_settings):
        """Test that exiting context manager closes browser."""
        automation = LinkedInAutomation(db_manager, app_settings)

        with patch.object(automation, 'start_browser', new_callable=AsyncMock):
            with patch.object(automation, 'close_browser', new_callable=AsyncMock) as mock_close:
                async with automation:
                    pass
                mock_close.assert_called_once()


# ============================================================================
# Browser Hardening Tests (navigator.webdriver / AutomationControlled)
# ============================================================================

@pytest.mark.unit
class TestBrowserHardening:
    """Passive automation tells are masked on every launch path."""

    AUTOMATION_ARG = "--disable-blink-features=AutomationControlled"

    @staticmethod
    def _patched_playwright(monkeypatch, *, persistent_pages=None):
        """Patch async_playwright/force_close_chrome and return the
        playwright mock plus the context that start_browser will use."""
        from automation import linkedin as linkedin_module

        context = AsyncMock()
        context.add_init_script = AsyncMock()
        context.new_page = AsyncMock()
        if persistent_pages is not None:
            context.pages = persistent_pages
            context.browser = AsyncMock()

        browser = AsyncMock()
        browser.new_context = AsyncMock(return_value=context)

        playwright = AsyncMock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        playwright.chromium.launch_persistent_context = AsyncMock(
            return_value=context
        )
        playwright.stop = AsyncMock()

        starter = AsyncMock(return_value=playwright)
        monkeypatch.setattr(
            linkedin_module, "async_playwright", lambda: AsyncMock(start=starter)
        )
        monkeypatch.setattr(linkedin_module, "force_close_chrome", lambda: None)
        return playwright, browser, context

    @pytest.mark.asyncio
    async def test_transient_launch_includes_automation_arg(
        self, db_manager, app_settings, monkeypatch
    ):
        """The transient browser launch carries the AutomationControlled flag."""
        # No executable / Chrome channel -> persistent path is skipped.
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "none")
        monkeypatch.delenv("PLAYWRIGHT_BROWSER_EXECUTABLE", raising=False)

        playwright, browser, context = self._patched_playwright(monkeypatch)

        automation = LinkedInAutomation(db_manager, app_settings)
        await automation.start_browser()

        args = playwright.chromium.launch.call_args.kwargs["args"]
        assert self.AUTOMATION_ARG in args

    @pytest.mark.asyncio
    async def test_persistent_launch_includes_automation_arg(
        self, db_manager, app_settings, monkeypatch
    ):
        """The persistent-context launch carries the AutomationControlled flag."""
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "chrome")

        playwright, browser, context = self._patched_playwright(
            monkeypatch, persistent_pages=[]
        )

        automation = LinkedInAutomation(db_manager, app_settings)
        await automation.start_browser()

        kwargs = playwright.chromium.launch_persistent_context.call_args.kwargs
        assert self.AUTOMATION_ARG in kwargs["args"]

    @pytest.mark.asyncio
    async def test_init_script_registered_on_context(
        self, db_manager, app_settings, monkeypatch
    ):
        """A navigator.webdriver mask is registered at the context level."""
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "none")
        monkeypatch.delenv("PLAYWRIGHT_BROWSER_EXECUTABLE", raising=False)

        playwright, browser, context = self._patched_playwright(monkeypatch)

        automation = LinkedInAutomation(db_manager, app_settings)
        await automation.start_browser()

        context.add_init_script.assert_called_once()
        script = context.add_init_script.call_args.args[0]
        assert "navigator" in script and "webdriver" in script

    @pytest.mark.asyncio
    async def test_init_script_registered_on_persistent_context_before_page_reuse(
        self, db_manager, app_settings, monkeypatch
    ):
        """On the persistent path the mask is registered before the
        pre-existing page is reused.

        An init script only applies to documents created/navigated after
        registration, so registering after binding the persistent context's
        existing page would leave that page's current document unmasked.
        """
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "chrome")

        existing_page = AsyncMock()
        playwright, browser, context = self._patched_playwright(
            monkeypatch, persistent_pages=[existing_page]
        )

        automation = LinkedInAutomation(db_manager, app_settings)

        # Record whether the existing page had already been bound to
        # automation.page when the mask was registered.
        page_at_registration = {}

        async def record(*_args, **_kwargs):
            page_at_registration["value"] = automation.page

        context.add_init_script.side_effect = record

        await automation.start_browser()

        context.add_init_script.assert_called_once()
        # Pre-existing page reused (no extra tab), and the mask was registered
        # before that page was bound.
        context.new_page.assert_not_called()
        assert automation.page is existing_page
        assert page_at_registration["value"] is None
        # The reused page's current document predates the init script, so it is
        # reloaded to apply the mask before navigating to LinkedIn.
        existing_page.reload.assert_called_once()

    @pytest.mark.asyncio
    async def test_persistent_failure_falls_back_and_masks_transient_context(
        self, db_manager, app_settings, monkeypatch
    ):
        """When the persistent context fails, the transient fallback still
        registers the webdriver mask exactly once on the new context."""
        from automation import linkedin as linkedin_module

        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "chrome")

        transient_context = AsyncMock()
        transient_context.add_init_script = AsyncMock()
        transient_context.new_page = AsyncMock()

        browser = AsyncMock()
        browser.new_context = AsyncMock(return_value=transient_context)

        playwright = AsyncMock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        playwright.chromium.launch_persistent_context = AsyncMock(
            side_effect=Exception("persistent context unavailable")
        )
        playwright.stop = AsyncMock()

        starter = AsyncMock(return_value=playwright)
        monkeypatch.setattr(
            linkedin_module, "async_playwright", lambda: AsyncMock(start=starter)
        )
        monkeypatch.setattr(linkedin_module, "force_close_chrome", lambda: None)

        automation = LinkedInAutomation(db_manager, app_settings)
        await automation.start_browser()

        # Fallback engaged on a fresh transient context, mask registered once.
        assert automation.context is transient_context
        transient_context.add_init_script.assert_called_once()


# ============================================================================
# Fingerprint Consistency Tests (locale / timezone / user-agent on context)
# ============================================================================

@pytest.mark.unit
class TestFingerprintConsistency:
    """locale and timezone are applied coherently on every launch path; the
    user-agent is left to real Chrome unless explicitly overridden."""

    _patched_playwright = staticmethod(TestBrowserHardening._patched_playwright)

    @pytest.mark.asyncio
    async def test_transient_context_receives_locale_and_timezone(
        self, db_manager, app_settings, monkeypatch
    ):
        """The transient new_context call carries locale + timezone_id and, by
        default, no user_agent override."""
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "none")
        monkeypatch.delenv("PLAYWRIGHT_BROWSER_EXECUTABLE", raising=False)
        monkeypatch.setenv("BROWSER_LOCALE", "en-US")
        monkeypatch.setenv("BROWSER_TIMEZONE", "America/New_York")
        monkeypatch.delenv("BROWSER_USER_AGENT", raising=False)

        playwright, browser, context = self._patched_playwright(monkeypatch)

        automation = LinkedInAutomation(db_manager, app_settings)
        await automation.start_browser()

        kwargs = browser.new_context.call_args.kwargs
        assert kwargs["locale"] == "en-US"
        assert kwargs["timezone_id"] == "America/New_York"
        # No override -> real Chrome's UA is left untouched.
        assert "user_agent" not in kwargs

    @pytest.mark.asyncio
    async def test_persistent_context_receives_locale_and_timezone(
        self, db_manager, app_settings, monkeypatch
    ):
        """The persistent-context launch carries locale + timezone_id too, so
        both launch paths produce one coherent fingerprint."""
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "chrome")
        monkeypatch.setenv("BROWSER_LOCALE", "es-ES")
        monkeypatch.setenv("BROWSER_TIMEZONE", "Europe/Madrid")
        monkeypatch.delenv("BROWSER_USER_AGENT", raising=False)

        playwright, browser, context = self._patched_playwright(
            monkeypatch, persistent_pages=[]
        )

        automation = LinkedInAutomation(db_manager, app_settings)
        await automation.start_browser()

        kwargs = playwright.chromium.launch_persistent_context.call_args.kwargs
        assert kwargs["locale"] == "es-ES"
        assert kwargs["timezone_id"] == "Europe/Madrid"
        assert "user_agent" not in kwargs

    @pytest.mark.asyncio
    async def test_user_agent_override_applied_when_set(
        self, db_manager, app_settings, monkeypatch
    ):
        """When BROWSER_USER_AGENT is set it is passed through to the context."""
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "none")
        monkeypatch.delenv("PLAYWRIGHT_BROWSER_EXECUTABLE", raising=False)
        monkeypatch.setenv("BROWSER_USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64) Custom")

        playwright, browser, context = self._patched_playwright(monkeypatch)

        automation = LinkedInAutomation(db_manager, app_settings)
        await automation.start_browser()

        kwargs = browser.new_context.call_args.kwargs
        assert kwargs["user_agent"] == "Mozilla/5.0 (X11; Linux x86_64) Custom"

    @pytest.mark.asyncio
    async def test_timezone_omitted_when_host_zone_undetectable(
        self, db_manager, app_settings, monkeypatch
    ):
        """When the host timezone cannot be resolved, timezone_id is left out of
        the context options so the browser keeps its own host zone (not UTC)."""
        from config.settings import AppSettings

        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "none")
        monkeypatch.delenv("PLAYWRIGHT_BROWSER_EXECUTABLE", raising=False)
        monkeypatch.delenv("BROWSER_TIMEZONE", raising=False)
        monkeypatch.setattr(
            AppSettings, "_detect_host_timezone", classmethod(lambda cls: None)
        )

        playwright, browser, context = self._patched_playwright(monkeypatch)

        automation = LinkedInAutomation(db_manager, app_settings)
        await automation.start_browser()

        kwargs = browser.new_context.call_args.kwargs
        assert "timezone_id" not in kwargs
        assert "locale" in kwargs

    @pytest.mark.asyncio
    async def test_storage_state_path_carries_locale_and_timezone(
        self, db_manager, app_settings, monkeypatch, tmp_path
    ):
        """When a saved session.json exists, the storage_state context still
        receives locale + timezone_id alongside storage_state."""
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "none")
        monkeypatch.delenv("PLAYWRIGHT_BROWSER_EXECUTABLE", raising=False)
        monkeypatch.setenv("BROWSER_TIMEZONE", "America/New_York")

        session_file = tmp_path / "session.json"
        session_file.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(app_settings, "session_path", session_file)

        playwright, browser, context = self._patched_playwright(monkeypatch)

        automation = LinkedInAutomation(db_manager, app_settings)
        await automation.start_browser()

        kwargs = browser.new_context.call_args.kwargs
        assert kwargs["storage_state"] == str(session_file)
        assert kwargs["timezone_id"] == "America/New_York"
        assert "locale" in kwargs

    @pytest.mark.asyncio
    async def test_session_load_failure_fallback_carries_locale_and_timezone(
        self, db_manager, app_settings, monkeypatch, tmp_path
    ):
        """If loading session.json fails, the fresh fallback context still
        carries locale + timezone_id (the coherent fingerprint is not lost)."""
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "none")
        monkeypatch.delenv("PLAYWRIGHT_BROWSER_EXECUTABLE", raising=False)
        monkeypatch.setenv("BROWSER_TIMEZONE", "Europe/Madrid")

        session_file = tmp_path / "session.json"
        session_file.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(app_settings, "session_path", session_file)

        playwright, browser, context = self._patched_playwright(monkeypatch)
        # First new_context (storage_state load) fails; second (fallback) wins.
        fallback_context = context
        bad_load = Exception("corrupt session")
        browser.new_context = AsyncMock(side_effect=[bad_load, fallback_context])

        automation = LinkedInAutomation(db_manager, app_settings)
        await automation.start_browser()

        # The fallback call (second) carries locale/timezone and no storage_state.
        fallback_kwargs = browser.new_context.call_args_list[-1].kwargs
        assert "storage_state" not in fallback_kwargs
        assert fallback_kwargs["timezone_id"] == "Europe/Madrid"
        assert "locale" in fallback_kwargs


# ============================================================================
# LinkedInProfile Dataclass Tests
# ============================================================================

@pytest.mark.unit
class TestLinkedInProfile:
    """Test LinkedInProfile dataclass."""

    def test_create_profile_with_required_fields(self):
        """Test creating profile with required fields."""
        profile = LinkedInProfile(
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe"
        )

        assert profile.name == "John Doe"
        assert profile.profile_url == "https://linkedin.com/in/johndoe"
        assert profile.headline is None
        assert profile.location is None
        assert profile.company is None
        assert profile.mutual_connections == 0

    def test_create_profile_with_all_fields(self):
        """Test creating profile with all fields."""
        profile = LinkedInProfile(
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe",
            headline="Software Engineer at Tech Co",
            location="San Francisco, CA",
            company="Tech Co",
            mutual_connections=5
        )

        assert profile.name == "John Doe"
        assert profile.headline == "Software Engineer at Tech Co"
        assert profile.location == "San Francisco, CA"
        assert profile.company == "Tech Co"
        assert profile.mutual_connections == 5

    def test_profile_is_dataclass(self):
        """Test that LinkedInProfile is a dataclass."""
        profile = LinkedInProfile(
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe"
        )

        # Dataclasses have __dataclass_fields__
        assert hasattr(LinkedInProfile, '__dataclass_fields__')


# ============================================================================
# Text Normalization Tests
# ============================================================================

@pytest.mark.unit
class TestNormalize:
    """Test the accent-insensitive text normalizer used for matching
    profile names and the invitation-cooldown toast."""

    def test_strips_accents(self):
        # Accent stripping is what lets the cooldown toast ("invitación")
        # match the plain marker ("invitacion").
        assert LinkedInAutomation._normalize("invitación") == "invitacion"
        assert LinkedInAutomation._normalize("Martí Altimira") == "marti altimira"

    def test_casefolds_and_collapses_whitespace(self):
        assert LinkedInAutomation._normalize("  Hello   WORLD  ") == "hello world"

    def test_handles_none(self):
        assert LinkedInAutomation._normalize(None) == ""

    def test_name_substring_match_is_accent_insensitive(self):
        name = LinkedInAutomation._normalize("Martí Altimira Cebrian")
        aria = LinkedInAutomation._normalize("Invita a Martí Altimira Cebrian a conectar")
        assert name in aria


# ============================================================================
# Integration Tests with Mocks
# ============================================================================

@pytest.mark.integration
class TestLinkedInAutomationIntegration:
    """Integration tests for LinkedIn automation with mocked browser."""

    @pytest.mark.asyncio
    async def test_full_search_flow(self, db_manager, app_settings):
        """Test complete search flow with mocks."""
        automation = LinkedInAutomation(db_manager, app_settings)
        automation.is_authenticated = True

        # Create test campaign
        campaign = db_manager.create_campaign({
            "name": "Test Campaign",
            "keywords": "software engineer",
            "geo_urn": "90000084",
        })

        # Mock page. goto sets page.url to the navigated target so the
        # navigation landing guard (issue #16) sees a clean, on-path landing
        # (a real browser reports the landed URL after goto). The overlay sweep
        # counts the blocking-overlay selector via page.locator(...).count().
        mock_page = AsyncMock()

        async def _goto(url, *_a, **_k):
            mock_page.url = url

        mock_page.url = "https://www.linkedin.com/feed/"
        mock_page.goto = AsyncMock(side_effect=_goto)
        mock_page.wait_for_selector = AsyncMock()
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()
        mock_page.query_selector_all = AsyncMock(return_value=[])
        overlay_loc = MagicMock()
        overlay_loc.count = AsyncMock(return_value=0)
        mock_page.locator = MagicMock(return_value=overlay_loc)

        automation.page = mock_page

        # Execute search
        profiles = await automation.search_profiles(campaign, limit=10)

        # Verify search was executed
        assert mock_page.goto.called
        assert mock_page.wait_for_selector.called
        assert isinstance(profiles, list)


# ============================================================================
# Persisted Daily Cap Tests (restart-safe rate limiting)
# ============================================================================

@pytest.mark.unit
class TestPersistedDailyCap:
    """Test that the daily connection cap survives across CLI restarts."""

    def _profiles(self, n):
        return [
            LinkedInProfile(
                name=f"Person {i}",
                profile_url=f"https://www.linkedin.com/in/person{i}/",
            )
            for i in range(n)
        ]

    @pytest.mark.asyncio
    async def test_cap_already_reached_stops_before_sending(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A new run cannot exceed the limit reached by a prior run today."""
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Test Campaign"})

        # Simulate 20 connections already sent today by a previous run.
        today = date.today().isoformat()
        for _ in range(20):
            db.increment_daily_connection_count(today)

        messages = []
        result = await mock_linkedin_automation.send_connection_requests(
            campaign, self._profiles(5), progress_callback=messages.append
        )

        # No new requests sent; the browser was never driven.
        assert result["sent"] == 0
        assert not mock_linkedin_automation.page.goto.called
        # Persisted count is untouched and still capped.
        assert db.get_daily_connection_count(today) == 20
        assert any("already reached" in m for m in messages)

    @pytest.mark.asyncio
    async def test_partial_prior_run_does_not_reset(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A partial prior count is the starting point, not zero."""
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Test Campaign"})

        today = date.today().isoformat()
        for _ in range(15):
            db.increment_daily_connection_count(today)

        # 15 < 20, so the run proceeds (does not early-return).
        assert db.get_daily_connection_count(today) == 15

    def _wire_success_page(self, automation):
        """Wire the mocked page/helpers so each profile reaches the success path.

        Returns the configured automation. Patches the connect-control lookup,
        the blocked/limit/captcha checks, and the modal locators so a Connect
        click flows straight through to a successful "Send without a note".
        """
        button = AsyncMock()
        button.click = AsyncMock()
        button.evaluate = AsyncMock()
        # bounding_box None -> the human mouse move is a clean no-op.
        button.bounding_box = AsyncMock(return_value=None)

        automation._find_connect_control = AsyncMock(return_value=(button, "connect"))
        automation._invitation_blocked_toast = AsyncMock(return_value=False)
        automation._handle_invitation_limit_modal = AsyncMock(return_value=False)

        # No email-request modal.
        automation.page.query_selector = AsyncMock(return_value=None)
        automation.page.goto = AsyncMock()
        automation.page.wait_for_timeout = AsyncMock()

        # Every modal locator reports one matching button that clicks cleanly.
        loc = AsyncMock()
        loc.count = AsyncMock(return_value=1)
        loc.click = AsyncMock()
        loc.bounding_box = AsyncMock(return_value=None)
        first = MagicMock()
        first.first = loc
        automation.page.locator = MagicMock(return_value=first)
        return automation

    @pytest.mark.asyncio
    async def test_success_persists_count_and_reports_quota(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A successful send increments the persisted count and reports quota."""
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Test Campaign"})
        self._wire_success_page(mock_linkedin_automation)

        messages = []
        with patch("automation.linkedin.random_wait", new=AsyncMock()), \
             patch("automation.interactions.detect_captcha", new=AsyncMock(return_value=False)):
            result = await mock_linkedin_automation.send_connection_requests(
                campaign, self._profiles(3), progress_callback=messages.append
            )

        today = date.today().isoformat()
        assert result["sent"] == 3
        assert db.get_daily_connection_count(today) == 3
        # Acceptance criterion: remaining quota surfaced to the user.
        assert any("1/20 used today" in m for m in messages)
        assert any("3/20 used today" in m for m in messages)

    @pytest.mark.asyncio
    async def test_cumulative_break_stops_at_limit(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A run starting partway through today stops exactly at the cap."""
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Test Campaign"})

        today = date.today().isoformat()
        for _ in range(18):  # prior run left 18/20
            db.increment_daily_connection_count(today)

        self._wire_success_page(mock_linkedin_automation)
        messages = []
        with patch("automation.linkedin.random_wait", new=AsyncMock()), \
             patch("automation.interactions.detect_captcha", new=AsyncMock(return_value=False)):
            result = await mock_linkedin_automation.send_connection_requests(
                campaign, self._profiles(10), progress_callback=messages.append
            )

        # Only 2 more sends allowed; cumulative count caps at 20.
        assert result["sent"] == 2
        assert db.get_daily_connection_count(today) == 20
        assert any("Daily connection limit reached" in m for m in messages)

    @pytest.mark.asyncio
    async def test_cooldown_warns_when_within_window(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A run started within the cooldown window warns the user."""
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        monkeypatch.setenv("CONNECTION_COOLDOWN", "3600")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Test Campaign"})

        # Record a recent connection (sets last_action_at to now).
        db.increment_daily_connection_count(date.today().isoformat())

        messages = []
        with patch("automation.linkedin.random_wait", new=AsyncMock()), \
             patch("automation.interactions.detect_captcha", new=AsyncMock(return_value=False)):
            await mock_linkedin_automation.send_connection_requests(
                campaign, [], progress_callback=messages.append
            )

        assert any("Cooldown active" in m for m in messages)

    @pytest.mark.asyncio
    async def test_new_day_starts_fresh(
        self, mock_linkedin_automation, monkeypatch, freeze_time
    ):
        """A new local day starts the counter at zero (self-clearing)."""
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Test Campaign"})

        # Yesterday was capped; today's key is a different row.
        yesterday = "2025-01-14"
        for _ in range(20):
            db.increment_daily_connection_count(yesterday)

        today = date.today().isoformat()  # frozen to 2025-01-15
        assert today != yesterday
        assert db.get_daily_connection_count(today) == 0

        messages = []
        result = await mock_linkedin_automation.send_connection_requests(
            campaign, [], progress_callback=messages.append
        )
        # Empty profile list, but it must NOT early-return on the cap.
        assert result["sent"] == 0
        assert not any("already reached" in m for m in messages)

    @pytest.mark.asyncio
    async def test_non_send_outcome_releases_reserved_slot(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A profile that doesn't end in a confirmed send must not burn a slot.

        The blocked-invitation path reserves a slot before the send then bails,
        so the reservation has to be released — otherwise the day's budget
        leaks and the user loses real capacity.
        """
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Test Campaign"})
        self._wire_success_page(mock_linkedin_automation)
        # Force the post-reservation blocked-invitation exit for every profile.
        mock_linkedin_automation._invitation_blocked_toast = AsyncMock(
            return_value=True
        )

        with patch("automation.linkedin.random_wait", new=AsyncMock()), \
             patch("automation.interactions.detect_captcha", new=AsyncMock(return_value=False)):
            result = await mock_linkedin_automation.send_connection_requests(
                campaign, self._profiles(3), progress_callback=None
            )

        today = date.today().isoformat()
        assert result["sent"] == 0
        # Every reservation was released: no slots consumed.
        assert db.get_daily_connection_count(today) == 0
        # And a failed send must NOT stamp the cooldown timestamp.
        assert db.get_last_connection_at() is None

    @pytest.mark.asyncio
    async def test_successful_send_stamps_cooldown_timestamp(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A confirmed send records last_action_at (drives the cooldown)."""
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Test Campaign"})
        self._wire_success_page(mock_linkedin_automation)

        assert db.get_last_connection_at() is None
        with patch("automation.linkedin.random_wait", new=AsyncMock()), \
             patch("automation.interactions.detect_captcha", new=AsyncMock(return_value=False)):
            await mock_linkedin_automation.send_connection_requests(
                campaign, self._profiles(1), progress_callback=None
            )
        assert db.get_last_connection_at() is not None


# ============================================================================
# Resilient Send/Finalize Tail Tests (issue #31)
# ============================================================================

@pytest.mark.unit
class TestResilientSendTail:
    """The irreversible send tail is wedge-safe without mis-accounting sends.

    A renderer wedge BEFORE the Send click is safe to retry/skip (the slot is
    released, the contact is a plain retryable failure). A wedge AFTER the
    (irreversible) Send click is recorded conservatively as ``possibly_sent``:
    the reserved daily slot is KEPT (no cap drift) and the contact is recorded
    non-retryable (no re-contact), because the invitation may already be out.
    """

    def _profile(self):
        return LinkedInProfile(
            name="Wedge Target",
            profile_url="https://www.linkedin.com/in/wedge/",
        )

    def _wire_send_tail(self, automation):
        """Wire the page so _attempt_connect reaches the send-control locate.

        The click+modal unit (``invite:`` run_bounded) returns a ready modal so
        the flow falls through to the send tail; the send control clicks cleanly
        and the post-click limit check is a no-op. Tests then override the
        targeted send-tail unit via a label-aware run_bounded fake (below).
        """
        # Move helpers are no-ops (bounding_box None -> clean mouse move).
        automation._throttle_action = AsyncMock()
        automation._invitation_blocked_toast = AsyncMock(return_value=False)
        automation._handle_invitation_limit_modal = AsyncMock(return_value=False)
        automation.page.query_selector = AsyncMock(return_value=None)  # no email
        automation.page.wait_for_timeout = AsyncMock()

        send_loc = AsyncMock()
        send_loc.count = AsyncMock(return_value=1)
        send_loc.click = AsyncMock()
        send_loc.bounding_box = AsyncMock(return_value=None)
        first = MagicMock()
        first.first = send_loc
        automation.page.locator = MagicMock(return_value=first)
        return send_loc

    def _label_aware_run_bounded(self, automation, *, wedge_label, on_wedge=None):
        """Build a run_bounded fake that passes through except for ``wedge_label``.

        The matching unit closes its coroutine, optionally runs ``on_wedge``
        (e.g. to fire the real recover), then raises asyncio.TimeoutError —
        mirroring run_bounded's contract (refresh-then-raise).
        """
        async def _run_bounded(awaitable, **kwargs):
            label = kwargs.get("label", "")
            if label.startswith(wedge_label):
                awaitable.close()
                if on_wedge is not None:
                    await on_wedge(kwargs)
                raise asyncio.TimeoutError()
            return await awaitable
        return _run_bounded

    @pytest.mark.asyncio
    async def test_wedge_before_send_click_releases_slot(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A wedge while locating the Send control releases the reserved slot.

        Nothing irreversible happened yet, so the TimeoutError propagates out of
        _attempt_connect, the finally releases the reservation (count back to 0),
        and NO possibly_sent contact is recorded (it is safe to retry/skip).
        """
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Send Tail"})
        self._wire_send_tail(mock_linkedin_automation)
        profile = self._profile()
        connect_button = AsyncMock()

        refreshed = {"n": 0}

        async def _recover(kwargs):
            refreshed["n"] += 1

        fake = self._label_aware_run_bounded(
            mock_linkedin_automation, wedge_label="send-locate", on_wedge=_recover
        )

        today = date.today().isoformat()
        with patch("automation.linkedin.random_wait", new=AsyncMock()), \
             patch("automation.linkedin.move_to_element", new=AsyncMock()), \
             patch("automation.linkedin.run_bounded", new=AsyncMock(side_effect=fake)):
            with pytest.raises(asyncio.TimeoutError):
                await mock_linkedin_automation._attempt_connect(
                    campaign, profile, connect_button, progress_callback=None
                )

        # Pre-click wedge: the reserved slot was given back (no cap drift toward
        # consuming a slot that never sent).
        assert db.get_daily_connection_count(today) == 0
        # No possibly_sent contact — the profile is safe to retry/skip.
        with db.get_session() as session:
            from sqlmodel import select
            contacts = session.exec(select(Contact)).all()
        assert all(c.status != "possibly_sent" for c in contacts)
        # A failed send must NOT stamp the cooldown timestamp.
        assert db.get_last_connection_at() is None

    @pytest.mark.asyncio
    async def test_wedge_after_send_click_records_possibly_sent(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A wedge after the irreversible Send click is a conservative possibly_sent.

        The slot stays consumed (no cap drift, no re-contact) and the contact is
        recorded with status ``possibly_sent`` (non-retryable), because the
        invite may already have been delivered.
        """
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Send Tail"})
        send_loc = self._wire_send_tail(mock_linkedin_automation)
        profile = self._profile()
        connect_button = AsyncMock()

        # The Send click lands cleanly; the POST-click limit check wedges.
        fake = self._label_aware_run_bounded(
            mock_linkedin_automation, wedge_label="send-limit"
        )

        today = date.today().isoformat()
        with patch("automation.linkedin.random_wait", new=AsyncMock()), \
             patch("automation.linkedin.move_to_element", new=AsyncMock()), \
             patch("automation.linkedin.run_bounded", new=AsyncMock(side_effect=fake)):
            result = await mock_linkedin_automation._attempt_connect(
                campaign, profile, connect_button, progress_callback=None
            )

        # The irreversible click did fire.
        assert send_loc.click.await_count == 1
        # Conservative outcome: assume sent on ambiguity.
        assert result.outcome == "possibly_sent"
        assert result.total_today == 1
        # The reserved slot is KEPT (counts against the daily cap).
        assert db.get_daily_connection_count(today) == 1
        # The contact is recorded non-retryable so it is not re-contacted.
        with db.get_session() as session:
            from sqlmodel import select
            contact = session.exec(
                select(Contact).where(Contact.profile_url == profile.profile_url)
            ).first()
        assert contact is not None
        assert contact.status == "possibly_sent"
        # An assumed send stamps the cooldown timestamp.
        assert db.get_last_connection_at() is not None

    @pytest.mark.asyncio
    async def test_crash_after_send_click_records_possibly_sent_and_refreshes(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A crash-shaped raise after the Send click is possibly_sent + refresh.

        A renderer crash that surfaces by *raising* (not hanging) after the click
        is the ambiguous case: keep the slot, record possibly_sent, and refresh
        the wedged browser so the rest of the run continues on a live page.
        """
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Send Tail"})
        send_loc = self._wire_send_tail(mock_linkedin_automation)
        profile = self._profile()
        connect_button = AsyncMock()

        # The post-click limit check raises a crash-shaped error (escapes the
        # watchdog by raising rather than hanging).
        async def _run_bounded(awaitable, **kwargs):
            label = kwargs.get("label", "")
            if label.startswith("send-limit"):
                awaitable.close()
                raise RuntimeError("Page crashed")
            return await awaitable

        refreshed = {"n": 0}

        async def _refresh():
            refreshed["n"] += 1
            return mock_linkedin_automation.page

        today = date.today().isoformat()
        with patch("automation.linkedin.random_wait", new=AsyncMock()), \
             patch("automation.linkedin.move_to_element", new=AsyncMock()), \
             patch.object(
                 mock_linkedin_automation, "_refresh_context", new=_refresh
             ), \
             patch("automation.linkedin.run_bounded", new=AsyncMock(side_effect=_run_bounded)):
            result = await mock_linkedin_automation._attempt_connect(
                campaign, profile, connect_button, progress_callback=None
            )

        assert send_loc.click.await_count == 1
        assert result.outcome == "possibly_sent"
        # Crash-shaped raise after the click triggered a browser refresh.
        assert refreshed["n"] == 1
        # Slot kept; contact non-retryable.
        assert db.get_daily_connection_count(today) == 1
        with db.get_session() as session:
            from sqlmodel import select
            contact = session.exec(
                select(Contact).where(Contact.profile_url == profile.profile_url)
            ).first()
        assert contact is not None
        assert contact.status == "possibly_sent"

    @pytest.mark.asyncio
    async def test_possibly_sent_keeps_slot_even_if_contact_write_fails(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A failed possibly_sent record write must NOT release the slot.

        We are in the possibly_sent branch because the irreversible click fired,
        so the slot decision must not hinge on the contact-record write. If
        create_contact raises (DB locked / disk full), the slot stays consumed
        (cap stays conservative) and the outcome is still possibly_sent.
        """
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Send Tail"})
        send_loc = self._wire_send_tail(mock_linkedin_automation)
        profile = self._profile()
        connect_button = AsyncMock()

        # The post-click limit check wedges (possibly_sent), AND the possibly_sent
        # contact write then fails.
        fake = self._label_aware_run_bounded(
            mock_linkedin_automation, wedge_label="send-limit"
        )
        original_create = db.create_contact
        db.create_contact = MagicMock(side_effect=RuntimeError("db locked"))

        today = date.today().isoformat()
        try:
            with patch("automation.linkedin.random_wait", new=AsyncMock()), \
                 patch("automation.linkedin.move_to_element", new=AsyncMock()), \
                 patch("automation.linkedin.run_bounded", new=AsyncMock(side_effect=fake)):
                result = await mock_linkedin_automation._attempt_connect(
                    campaign, profile, connect_button, progress_callback=None
                )
        finally:
            db.create_contact = original_create

        assert send_loc.click.await_count == 1
        assert result.outcome == "possibly_sent"
        # The slot is KEPT despite the record-write failure (no cap drift).
        assert db.get_daily_connection_count(today) == 1

    @pytest.mark.asyncio
    async def test_non_crash_send_click_failure_is_plain_send_failed(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A non-crash Send-click failure stays a retryable send_failed.

        A plain click error (element not actionable, its own 5s timeout) means
        the click never landed, so the slot is released and the contact is a
        retryable failure — NOT a possibly_sent.
        """
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Send Tail"})
        send_loc = self._wire_send_tail(mock_linkedin_automation)
        # The Send click raises a non-crash error.
        send_loc.click = AsyncMock(side_effect=Exception("not clickable"))
        profile = self._profile()
        connect_button = AsyncMock()

        # Pass-through run_bounded (no wedge); only the click fails.
        async def _run_bounded(awaitable, **kwargs):
            return await awaitable

        today = date.today().isoformat()
        with patch("automation.linkedin.random_wait", new=AsyncMock()), \
             patch("automation.linkedin.move_to_element", new=AsyncMock()), \
             patch("automation.linkedin.run_bounded", new=AsyncMock(side_effect=_run_bounded)):
            result = await mock_linkedin_automation._attempt_connect(
                campaign, profile, connect_button, progress_callback=None
            )

        assert result.outcome == "send_failed"
        # Slot released; not counted against the cap.
        assert db.get_daily_connection_count(today) == 0
        with db.get_session() as session:
            from sqlmodel import select
            contact = session.exec(
                select(Contact).where(Contact.profile_url == profile.profile_url)
            ).first()
        # Recorded, but NOT as possibly_sent (it is retryable).
        assert contact is not None
        assert contact.status != "possibly_sent"
        assert db.get_last_connection_at() is None

    @pytest.mark.asyncio
    async def test_possibly_sent_counted_in_send_loop_not_failed(
        self, mock_linkedin_automation, monkeypatch
    ):
        """send_connection_requests tallies possibly_sent apart from failed.

        End-to-end: a possibly_sent from _attempt_connect must surface in the
        ``possibly_sent`` bucket (not ``failed``) and must not be re-contacted.
        """
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        monkeypatch.setenv("CONNECTION_DELAY_MIN", "0")
        monkeypatch.setenv("CONNECTION_DELAY_MAX", "0")
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Send Tail"})

        profiles = [
            LinkedInProfile(
                name=f"P{i}", profile_url=f"https://www.linkedin.com/in/p{i}/"
            )
            for i in range(2)
        ]
        # First profile is an ambiguous send; the second is a clean send.
        outcomes = iter([
            ConnectResult("possibly_sent", total_today=1),
            ConnectResult("sent", total_today=2),
        ])
        mock_linkedin_automation._attempt_connect = AsyncMock(
            side_effect=lambda *a, **k: next(outcomes)
        )
        mock_linkedin_automation._find_connect_control = AsyncMock(
            return_value=(AsyncMock(), "connect")
        )

        async def _passthrough(awaitable, **kwargs):
            return await awaitable

        messages = []
        with patch("automation.linkedin.navigate_guarded",
                   new=AsyncMock(side_effect=lambda page, *a, **k: page)), \
             patch("automation.linkedin.run_bounded",
                   new=AsyncMock(side_effect=_passthrough)), \
             patch("automation.linkedin.scroll_down", new=AsyncMock()), \
             patch("automation.interactions.detect_captcha",
                   new=AsyncMock(return_value=False)):
            result = await mock_linkedin_automation.send_connection_requests(
                campaign, profiles, progress_callback=messages.append
            )

        assert result["sent"] == 1
        assert result["possibly_sent"] == 1
        # The ambiguous send is NOT mis-counted as a retryable failure.
        assert result["failed"] == 0


# ============================================================================
# Navigation Landing Guard Wiring (issue #16)
# ============================================================================

@pytest.mark.unit
class TestNavigationGuardWiring:
    """The login/search/per-profile navigations go through the landing guard."""

    def _profiles(self, n):
        return [
            LinkedInProfile(
                name=f"Person {i}",
                profile_url=f"https://www.linkedin.com/in/person{i}/",
            )
            for i in range(n)
        ]

    @pytest.mark.asyncio
    async def test_search_navigation_uses_guard_with_strict_path(
        self, mock_linkedin_automation
    ):
        """The search goto is routed through navigate_guarded(strict_path=...)."""
        campaign = Campaign(name="Wiring", keywords="eng")
        with patch(
            "automation.linkedin.navigate_guarded", new=AsyncMock()
        ) as guarded:
            await mock_linkedin_automation.search_profiles(campaign, limit=1)

        guarded.assert_awaited()
        call = guarded.await_args
        # Second positional arg is the search URL; strict_path pins the people
        # results path.
        assert "/search/results/people" in call.args[1]
        assert call.kwargs["strict_path"] == "/search/results/people"
        assert call.kwargs["context"]["campaign"] == "Wiring"

    @pytest.mark.asyncio
    async def test_search_disambiguates_via_verify_listing_rendered(
        self, mock_linkedin_automation
    ):
        """The search readiness wait goes through verify_listing_rendered."""
        campaign = Campaign(name="Listing")
        with patch(
            "automation.linkedin.verify_listing_rendered", new=AsyncMock(return_value=True)
        ) as verify, patch.object(
            mock_linkedin_automation, "_extract_profiles_new_ui",
            new=AsyncMock(return_value=[]),
        ):
            await mock_linkedin_automation.search_profiles(campaign, limit=1)

        verify.assert_awaited()
        # Wired with the readiness selector and the campaign context.
        assert verify.await_args.args[1] is sel.SEARCH_RESULTS_READY
        assert verify.await_args.kwargs["context"]["campaign"] == "Listing"

    @pytest.mark.asyncio
    async def test_search_empty_results_returns_clean_empty_list(
        self, mock_linkedin_automation
    ):
        """A genuine no-results page returns [] cleanly (not a harvest-loop failure)."""
        campaign = Campaign(name="Empty")
        # verify_listing_rendered reports a rendered-but-empty page (False).
        with patch(
            "automation.linkedin.verify_listing_rendered",
            new=AsyncMock(return_value=False),
        ), patch(
            "automation.linkedin.snapshot_page", new=AsyncMock()
        ) as snapshot:
            messages = []
            result = await mock_linkedin_automation.search_profiles(
                campaign, limit=10, progress_callback=messages.append
            )

        assert result == []
        # Short-circuited before the harvest loop: no page snapshot, no
        # result-cards wait that would otherwise time out and raise.
        snapshot.assert_not_awaited()
        assert any("no results" in m.lower() for m in messages)

    @pytest.mark.asyncio
    async def test_search_guard_challenge_reraises(self, mock_linkedin_automation):
        """A challenge raised during the search nav is NOT swallowed as 'no results'."""
        from exceptions import CaptchaDetectedException

        campaign = Campaign(name="Wall")
        with patch(
            "automation.linkedin.navigate_guarded",
            new=AsyncMock(side_effect=CaptchaDetectedException("search wall")),
        ):
            # Must propagate (a walled session read as [] would let the caller
            # drive the connection run straight through the wall).
            with pytest.raises(CaptchaDetectedException):
                await mock_linkedin_automation.search_profiles(campaign, limit=1)

    @pytest.mark.asyncio
    async def test_search_guard_wrong_landing_reraises(self, mock_linkedin_automation):
        """A wrong landing (path/param) during search is NOT swallowed as 'no results'.

        navigate_guarded raises UnexpectedLandingException on a strict_path miss
        or a reset requested param. That is a hard navigation failure, not an
        empty result set, so search_profiles must re-raise it (not return []).
        """
        from exceptions import UnexpectedLandingException

        campaign = Campaign(name="WrongLanding")
        with patch(
            "automation.linkedin.navigate_guarded",
            new=AsyncMock(
                side_effect=UnexpectedLandingException(
                    "landed elsewhere", reason="strict_path_miss"
                )
            ),
        ):
            with pytest.raises(UnexpectedLandingException):
                await mock_linkedin_automation.search_profiles(campaign, limit=1)

    @pytest.mark.asyncio
    async def test_search_scroll_runs_after_guard(self, mock_linkedin_automation):
        """#15's humanized scroll_down survives and runs after the guard."""
        campaign = Campaign(name="Order")
        order = []

        def _guard(page, *a, **k):
            order.append("guard")
            # navigate_guarded returns the page it finished on; search_profiles
            # rebinds self.page to it, so the mock must hand the page back.
            return page

        with patch(
            "automation.linkedin.navigate_guarded",
            new=AsyncMock(side_effect=_guard),
        ), patch(
            "automation.linkedin.scroll_down",
            new=AsyncMock(side_effect=lambda *a, **k: order.append("scroll")),
        ), patch.object(
            mock_linkedin_automation, "_extract_profiles_new_ui",
            new=AsyncMock(return_value=[]),
        ):
            await mock_linkedin_automation.search_profiles(campaign, limit=1)

        # The guard ran before the humanized scroll (landing verified first).
        assert order.index("guard") < order.index("scroll")

    @pytest.mark.asyncio
    async def test_profile_navigation_uses_guard_check_path_off(
        self, mock_linkedin_automation
    ):
        """The per-profile goto uses navigate_guarded(check_path=False)."""
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Wiring"})
        # Make _find_connect_control a no-op so the loop reaches navigation only.
        mock_linkedin_automation._find_connect_control = AsyncMock(
            return_value=(None, "none")
        )
        with patch(
            "automation.linkedin.navigate_guarded", new=AsyncMock()
        ) as guarded, patch(
            "automation.linkedin.scroll_down", new=AsyncMock()
        ), patch(
            "automation.interactions.detect_captcha", new=AsyncMock(return_value=False)
        ):
            await mock_linkedin_automation.send_connection_requests(
                campaign, self._profiles(1)
            )

        guarded.assert_awaited()
        assert guarded.await_args.kwargs["check_path"] is False
        assert guarded.await_args.args[1] == "https://www.linkedin.com/in/person0/"

    @pytest.mark.asyncio
    async def test_profile_guard_challenge_stops_run(self, mock_linkedin_automation):
        """A guard challenge bounce stops the whole run (protects the account)."""
        from exceptions import CaptchaDetectedException

        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Wiring"})
        find = AsyncMock(return_value=(None, "none"))
        mock_linkedin_automation._find_connect_control = find

        # First profile bounces to a challenge; the run must break, never
        # reaching _find_connect_control for any profile.
        with patch(
            "automation.linkedin.navigate_guarded",
            new=AsyncMock(side_effect=CaptchaDetectedException("challenge")),
        ), patch(
            "automation.linkedin.scroll_down", new=AsyncMock()
        ), patch(
            "automation.interactions.detect_captcha", new=AsyncMock(return_value=False)
        ):
            messages = []
            result = await mock_linkedin_automation.send_connection_requests(
                campaign, self._profiles(3), progress_callback=messages.append
            )

        find.assert_not_awaited()  # broke out before the connect lookup
        assert result["sent"] == 0
        assert any("Challenge/login wall" in m for m in messages)

    @pytest.mark.asyncio
    async def test_login_redirect_delegates_to_dom_confirmation(
        self, mock_linkedin_automation
    ):
        """_wait_for_login_redirect is now DOM-backed (confirm_logged_in_dom)."""
        with patch(
            "automation.linkedin.confirm_logged_in_dom", new=AsyncMock()
        ) as confirm:
            await mock_linkedin_automation._wait_for_login_redirect(timeout_ms=1234)
        confirm.assert_awaited_once()
        assert confirm.await_args.kwargs["timeout"] == 1234

    @pytest.mark.asyncio
    async def test_search_nav_passes_recover_and_tunables(
        self, mock_linkedin_automation
    ):
        """The search nav carries the crash-recovery callback + tuned timeout."""
        campaign = Campaign(name="Recover")
        with patch(
            "automation.linkedin.navigate_guarded",
            new=AsyncMock(side_effect=lambda page, *a, **k: page),
        ) as guarded, patch.object(
            mock_linkedin_automation, "_extract_profiles_new_ui",
            new=AsyncMock(return_value=[]),
        ):
            await mock_linkedin_automation.search_profiles(campaign, limit=1)

        kwargs = guarded.await_args.kwargs
        assert kwargs["recover"] == mock_linkedin_automation._recover
        assert kwargs["timeout"] == 30000  # NAV_GOTO_TIMEOUT_MS default
        assert kwargs["max_retries"] == 2

    @pytest.mark.asyncio
    async def test_profile_nav_runs_under_run_bounded_with_recover(
        self, mock_linkedin_automation
    ):
        """The per-profile nav is bounded by run_bounded and carries recover."""
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Bounded"})
        mock_linkedin_automation._find_connect_control = AsyncMock(
            return_value=(None, "none")
        )

        async def _passthrough(awaitable, **kwargs):
            # Mirror run_bounded's contract: await the unit, return its result.
            return await awaitable

        with patch(
            "automation.linkedin.navigate_guarded",
            new=AsyncMock(side_effect=lambda page, *a, **k: page),
        ), patch(
            "automation.linkedin.run_bounded", new=AsyncMock(side_effect=_passthrough)
        ) as bounded, patch(
            "automation.linkedin.scroll_down", new=AsyncMock()
        ), patch(
            "automation.interactions.detect_captcha", new=AsyncMock(return_value=False)
        ):
            await mock_linkedin_automation.send_connection_requests(
                campaign, self._profiles(1)
            )

        bounded.assert_awaited()
        kwargs = bounded.await_args.kwargs
        assert kwargs["recover"] == mock_linkedin_automation._recover
        assert kwargs["timeout_s"] == 240  # NAV_INTERACTION_WATCHDOG_S default

    @pytest.mark.asyncio
    async def test_profile_watchdog_timeout_skips_item_not_whole_run(
        self, mock_linkedin_automation
    ):
        """A per-item watchdog timeout skips that profile and keeps going."""
        import asyncio

        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Skip"})
        find = AsyncMock(return_value=(None, "none"))
        mock_linkedin_automation._find_connect_control = find

        calls = {"n": 0}
        # A DISTINCT sentinel page (not the one already on self.page) so the
        # final assertion truly proves the recover rebind happened, rather than
        # passing trivially because both sides reference the same starting page.
        fresh_page = MagicMock(name="recovered_page")
        assert fresh_page is not mock_linkedin_automation.page
        seen_pages = []

        async def _run_bounded(awaitable, **kwargs):
            calls["n"] += 1
            # Close the un-awaited coroutine so it doesn't leak a warning.
            awaitable.close()
            seen_pages.append(mock_linkedin_automation.page)
            if calls["n"] == 1:
                # Mirror run_bounded's contract: refresh (rebinds self.page),
                # then re-raise so the caller skips the item.
                await kwargs["recover"]()
                raise asyncio.TimeoutError()
            return mock_linkedin_automation.page

        async def _recover():
            mock_linkedin_automation.page = fresh_page
            return fresh_page

        with patch.object(
            mock_linkedin_automation, "_recover", new=_recover
        ), patch(
            "automation.linkedin.navigate_guarded",
            new=AsyncMock(side_effect=lambda page, *a, **k: page),
        ), patch(
            "automation.linkedin.run_bounded", new=AsyncMock(side_effect=_run_bounded)
        ), patch(
            "automation.linkedin.scroll_down", new=AsyncMock()
        ), patch(
            "automation.interactions.detect_captcha", new=AsyncMock(return_value=False)
        ):
            messages = []
            result = await mock_linkedin_automation.send_connection_requests(
                campaign, self._profiles(2), progress_callback=messages.append
            )

        # Both profiles were attempted (the run did NOT break on the timeout).
        assert calls["n"] == 2
        assert result["failed"] >= 1
        assert any("Timed out" in m for m in messages)
        # The second iteration ran on the recover-returned page (the watchdog's
        # whole purpose: leave a live page for the rest of the worklist).
        assert seen_pages[1] is fresh_page

    @pytest.mark.asyncio
    async def test_recover_refreshes_context_and_rebinds_page(
        self, mock_linkedin_automation
    ):
        """_recover refreshes the context (close + reopen) and returns the page."""
        fresh_page = MagicMock()

        async def _fake_start():
            mock_linkedin_automation.page = fresh_page

        with patch.object(
            mock_linkedin_automation, "close_browser", new=AsyncMock()
        ) as close, patch.object(
            mock_linkedin_automation, "start_browser",
            new=AsyncMock(side_effect=_fake_start),
        ) as start:
            returned = await mock_linkedin_automation._recover()

        close.assert_awaited_once()
        start.assert_awaited_once()
        assert returned is fresh_page
        assert mock_linkedin_automation.page is fresh_page

    @pytest.mark.asyncio
    async def test_refresh_context_survives_a_failing_close(
        self, mock_linkedin_automation
    ):
        """A close whose underlying steps throw must not wedge the refresh.

        close_browser is per-step bounded and never raises, so _refresh_context
        drops the partial handles and start_browser relaunches from a clean slate
        even when the crashed context/browser closes throw — the load-bearing
        'the refresh can't itself wedge' guarantee.
        """
        ctx = AsyncMock()
        ctx.storage_state = AsyncMock(side_effect=RuntimeError("dead context"))
        ctx.close = AsyncMock(side_effect=RuntimeError("close failed"))
        mock_linkedin_automation.context = ctx
        mock_linkedin_automation.browser = AsyncMock()
        mock_linkedin_automation.playwright = AsyncMock()

        fresh_page = MagicMock()

        async def _fake_start():
            mock_linkedin_automation.page = fresh_page

        with patch.object(
            mock_linkedin_automation, "start_browser",
            new=AsyncMock(side_effect=_fake_start),
        ) as start:
            returned = await mock_linkedin_automation._refresh_context()

        start.assert_awaited_once()
        # Handles were dropped before the relaunch and the fresh page is returned.
        assert returned is fresh_page
        assert mock_linkedin_automation.page is fresh_page

    @pytest.mark.asyncio
    async def test_close_browser_stops_playwright_even_if_context_close_raises(
        self, mock_linkedin_automation
    ):
        """A throwing context.close() must not skip browser.close()/playwright.stop().

        On a crash-recovery refresh close_browser runs against half-closed
        objects; an unguarded throw on any step would leak the still-running
        Chrome/Playwright driver. Every step must get its own attempt.
        """
        ctx = AsyncMock()
        ctx.storage_state = AsyncMock(side_effect=RuntimeError("dead context"))
        ctx.close = AsyncMock(side_effect=RuntimeError("close failed"))
        browser = AsyncMock()
        playwright = AsyncMock()
        mock_linkedin_automation.context = ctx
        mock_linkedin_automation.browser = browser
        mock_linkedin_automation.playwright = playwright

        # Must not raise despite storage_state AND context.close throwing.
        await mock_linkedin_automation.close_browser()

        # browser.close and (critically) playwright.stop still ran — the driver
        # subprocess is freed.
        browser.close.assert_awaited_once()
        playwright.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_browser_bounds_a_hanging_step_and_still_stops(
        self, mock_linkedin_automation
    ):
        """A HUNG context.close() is bounded so playwright.stop() still runs.

        On a wedged renderer a close can hang (not just throw); the per-step
        watchdog must cap it so the later steps — especially the stop() that
        frees the driver — are not starved.
        """
        import asyncio

        async def _hang():
            await asyncio.sleep(60)

        ctx = AsyncMock()
        ctx.storage_state = AsyncMock()
        ctx.close = AsyncMock(side_effect=_hang)
        browser = AsyncMock()
        playwright = AsyncMock()
        mock_linkedin_automation.context = ctx
        mock_linkedin_automation.browser = browser
        mock_linkedin_automation.playwright = playwright

        # Shrink the per-step budget so the test is fast.
        with patch.object(
            type(mock_linkedin_automation), "_CLOSE_STEP_TIMEOUT_S", 0.01
        ):
            await mock_linkedin_automation.close_browser()

        playwright.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invite_flow_runs_under_run_bounded(self, mock_linkedin_automation):
        """The connect-click + modal-poll page work is bounded by run_bounded too.

        The read unit and the invite unit are BOTH wrapped, so a wedge after the
        page loaded (clicking Connect / waiting for the modal) is bounded, not
        just the navigation/read.
        """
        from automation.linkedin import LinkedInProfile

        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "InviteBounded"})
        # A real Connect control so the flow reaches the invite unit.
        connect_btn = AsyncMock()
        mock_linkedin_automation._find_connect_control = AsyncMock(
            return_value=(connect_btn, "connect")
        )

        labels_seen = []

        async def _passthrough(awaitable, **kwargs):
            labels_seen.append(kwargs.get("label", ""))
            return await awaitable

        with patch(
            "automation.linkedin.run_bounded", new=AsyncMock(side_effect=_passthrough)
        ) as bounded, patch(
            "automation.linkedin.navigate_guarded",
            new=AsyncMock(side_effect=lambda page, *a, **k: page),
        ), patch(
            "automation.linkedin.scroll_down", new=AsyncMock()
        ), patch(
            "automation.linkedin.move_to_and_click", new=AsyncMock()
        ), patch(
            "automation.linkedin.random_wait", new=AsyncMock()
        ), patch(
            "automation.interactions.detect_captcha", new=AsyncMock(return_value=False)
        ), patch.object(
            mock_linkedin_automation, "_invitation_blocked_toast",
            new=AsyncMock(return_value=False),
        ):
            await mock_linkedin_automation.send_connection_requests(
                campaign,
                [LinkedInProfile(name="P", profile_url="https://x/in/p/")],
            )

        # run_bounded was used for BOTH the profile read and the invite flow.
        assert any(lbl.startswith("profile:") for lbl in labels_seen)
        assert any(lbl.startswith("invite:") for lbl in labels_seen)
        bounded.assert_awaited()

    @pytest.mark.asyncio
    async def test_dom_captcha_in_read_unit_stops_run(self, mock_linkedin_automation):
        """A DOM-level CAPTCHA detected inside the bounded read unit stops the run.

        The captcha check now lives inside the run_bounded read unit and returns
        a flag; the loop must still break (protect the account) on it, before
        ever looking up the connect control.
        """
        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "Captcha"})
        find = AsyncMock(return_value=(None, "none"))
        mock_linkedin_automation._find_connect_control = find

        with patch(
            "automation.linkedin.navigate_guarded",
            new=AsyncMock(side_effect=lambda page, *a, **k: page),
        ), patch(
            "automation.linkedin.scroll_down", new=AsyncMock()
        ), patch(
            "automation.interactions.detect_captcha", new=AsyncMock(return_value=True)
        ):
            messages = []
            result = await mock_linkedin_automation.send_connection_requests(
                campaign, self._profiles(3), progress_callback=messages.append
            )

        # Broke on the first profile's captcha; never reached the connect lookup.
        find.assert_not_awaited()
        assert result["sent"] == 0
        assert any("CAPTCHA detected" in m for m in messages)

    @pytest.mark.asyncio
    async def test_crash_shaped_failure_in_loop_refreshes_once(
        self, mock_linkedin_automation
    ):
        """A crash that *raises* (not hangs) past the watchdog refreshes once.

        run_bounded only catches a hang; a renderer that crashes by raising
        surfaces in the generic except. Without a refresh self.page stays dead
        and every later profile fails on it — so the handler must recover once.
        """
        from playwright.async_api import Error as PWError

        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "CrashRaise"})
        mock_linkedin_automation._find_connect_control = AsyncMock(
            return_value=(None, "none")
        )

        async def _run_bounded(awaitable, **kwargs):
            awaitable.close()
            raise PWError("Page crashed")

        with patch.object(
            mock_linkedin_automation, "_refresh_context", new=AsyncMock()
        ) as refresh, patch(
            "automation.linkedin.navigate_guarded",
            new=AsyncMock(side_effect=lambda page, *a, **k: page),
        ), patch(
            "automation.linkedin.run_bounded", new=AsyncMock(side_effect=_run_bounded)
        ), patch(
            "automation.linkedin.scroll_down", new=AsyncMock()
        ), patch(
            "automation.interactions.detect_captcha", new=AsyncMock(return_value=False)
        ):
            await mock_linkedin_automation.send_connection_requests(
                campaign, self._profiles(1)
            )

        refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_crash_with_failing_refresh_does_not_abort_run(
        self, mock_linkedin_automation
    ):
        """A failed crash-refresh + backoff must not abort the whole run.

        Regression guard: _refresh_context nulls self.page before relaunching,
        so a *failing* refresh leaves self.page = None. The post-failure backoff
        sleep must therefore not be a page operation (else AttributeError on None
        escapes the handler and kills the run). Drive 4 crash-shaped failures so
        the >=3 backoff branch runs, with a refresh that always fails.
        """
        from playwright.async_api import Error as PWError

        db = mock_linkedin_automation.db_manager
        campaign = db.create_campaign({"name": "CrashNoAbort"})
        mock_linkedin_automation._find_connect_control = AsyncMock(
            return_value=(None, "none")
        )

        async def _run_bounded(awaitable, **kwargs):
            awaitable.close()
            raise PWError("Page crashed")

        async def _failing_refresh():
            # Mirror _refresh_context's contract: it nulls self.page, then the
            # relaunch fails — leaving self.page None.
            mock_linkedin_automation.page = None
            raise RuntimeError("relaunch failed")

        with patch.object(
            mock_linkedin_automation, "_refresh_context",
            new=AsyncMock(side_effect=_failing_refresh),
        ), patch(
            "automation.linkedin.navigate_guarded",
            new=AsyncMock(side_effect=lambda page, *a, **k: page),
        ), patch(
            "automation.linkedin.run_bounded", new=AsyncMock(side_effect=_run_bounded)
        ), patch(
            "automation.linkedin.scroll_down", new=AsyncMock()
        ), patch(
            "automation.interactions.detect_captcha", new=AsyncMock(return_value=False)
        ), patch(
            "automation.linkedin.asyncio.sleep", new=AsyncMock()
        ):
            # Must complete (not raise AttributeError out of the loop).
            result = await mock_linkedin_automation.send_connection_requests(
                campaign, self._profiles(4)
            )

        # All four were attempted and counted as failures; the run survived.
        assert result["failed"] == 4


# ============================================================================
# Error Handling Tests
# ============================================================================

@pytest.mark.unit
class TestErrorHandling:
    """Test error handling in automation."""

    @pytest.mark.asyncio
    async def test_search_profiles_handles_timeout(self, mock_linkedin_automation):
        """Test that search handles timeout errors gracefully."""
        campaign = Campaign(name="Test")

        # Mock timeout error
        from playwright.async_api import TimeoutError
        mock_linkedin_automation.page.wait_for_selector = AsyncMock(
            side_effect=TimeoutError("Timeout")
        )

        profiles = await mock_linkedin_automation.search_profiles(campaign, limit=10)

        # Should return empty list, not raise exception
        assert isinstance(profiles, list)

    @pytest.mark.asyncio
    async def test_extract_profile_logs_warnings(self, mock_linkedin_automation, caplog):
        """Test that profile extraction logs warnings on errors."""
        mock_element = AsyncMock()
        mock_element.query_selector = AsyncMock(side_effect=Exception("Test error"))

        await mock_linkedin_automation._extract_profile_info(mock_element)

        # Check that warning was logged
        assert any("Failed to extract profile info" in record.message for record in caplog.records)


@pytest.mark.unit
class TestLimitModalResolution:
    """The weekly-limit modal must resolve to the OUTER wrapper via the combined
    selector CSS (DOM-order), not the registry's candidate order — the handle is
    the search root for _is_true_limit and the close-button queries."""

    @pytest.mark.asyncio
    async def test_limit_modal_resolved_via_combined_css(self, mock_linkedin_automation):
        from automation import selectors as sel

        # No modal present -> returns False, but the lookup must have used the
        # combined comma-list CSS (DOM-order first match), guaranteeing the
        # outer wrapper wins over an inner data-test node.
        mock_linkedin_automation.page.query_selector = AsyncMock(return_value=None)
        profile = LinkedInProfile(name="X", profile_url="u")

        result = await mock_linkedin_automation._handle_invitation_limit_modal(profile)

        assert result is False
        mock_linkedin_automation.page.query_selector.assert_awaited_once_with(
            sel.LIMIT_MODAL.css
        )


@pytest.mark.unit
class TestFindCardConnectControl:
    """_find_card_connect_control resolves the connect/pending control scoped to
    a single search-result card (foundation for issue #25)."""

    @staticmethod
    def _control(aria_label, *, visible=True):
        """A fake control element with async is_visible / get_attribute."""
        ctrl = AsyncMock()
        ctrl.is_visible = AsyncMock(return_value=visible)
        ctrl.get_attribute = AsyncMock(return_value=aria_label)
        return ctrl

    @staticmethod
    def _card(controls):
        """A fake card whose query_selector_all returns the given controls."""
        card = AsyncMock()
        card.query_selector_all = AsyncMock(return_value=controls)
        return card

    @pytest.mark.asyncio
    async def test_connect_control_present_returns_connect(
        self, mock_linkedin_automation
    ):
        connect = self._control("Invitar a Jane Roe a conectar")
        card = self._card([connect])
        handle, kind = await mock_linkedin_automation._find_card_connect_control(
            card
        )

        assert kind == "connect"
        assert handle is connect

    @pytest.mark.asyncio
    async def test_only_pending_control_returns_pending(
        self, mock_linkedin_automation
    ):
        pending = self._control("Invitación pendiente para Jane Roe")
        card = self._card([pending])
        handle, kind = await mock_linkedin_automation._find_card_connect_control(
            card
        )

        assert kind == "pending"
        assert handle is pending

    @pytest.mark.asyncio
    async def test_no_connect_or_pending_returns_none(
        self, mock_linkedin_automation
    ):
        other = self._control("Enviar un mensaje a Jane Roe")
        card = self._card([other])
        handle, kind = await mock_linkedin_automation._find_card_connect_control(
            card
        )

        assert kind == "none"
        assert handle is None

    @pytest.mark.asyncio
    async def test_invisible_connect_control_is_skipped(
        self, mock_linkedin_automation
    ):
        invisible = self._control("Conectar con Jane Roe", visible=False)
        card = self._card([invisible])
        handle, kind = await mock_linkedin_automation._find_card_connect_control(
            card
        )

        assert kind == "none"
        assert handle is None

    @pytest.mark.asyncio
    async def test_connect_wins_over_pending_in_same_card(
        self, mock_linkedin_automation
    ):
        # A card exposing BOTH a Connect and a Pending control must resolve to
        # Connect (the actionable invite). The keyword precedence wins
        # regardless of DOM order, so put Pending first.
        pending = self._control("Invitación pendiente para Jane Roe")
        connect = self._control("Invitar a Jane Roe a conectar")
        card = self._card([pending, connect])

        handle, kind = await mock_linkedin_automation._find_card_connect_control(
            card
        )

        assert kind == "connect"
        assert handle is connect


@pytest.mark.unit
class TestSearchAndConnect:
    """search_and_connect invites straight from result cards and defers cards
    with no Connect control to the profile-page path (issue #25, PR 2)."""

    @staticmethod
    def _profile(i):
        return LinkedInProfile(
            name=f"Person {i}",
            profile_url=f"https://www.linkedin.com/in/person{i}/",
        )

    def _wire(self, automation, cards, monkeypatch):
        """Drive the card scan from canned data (one results page).

        ``cards`` is a list of ``(profile, kind)`` where kind is
        'connect'/'pending'/'none'. Replaces the page-walk with a one-page async
        generator and wires _extract_profile_cards / _find_card_connect_control to
        return the canned profiles/controls. Each card carries the intended
        ``(button, kind)`` on ``_wanted`` so the lookup is deterministic. Patches
        detect_captcha to False so the results-page CAPTCHA guard (which would
        otherwise fire against the truthy mock page) stays clear.
        """
        monkeypatch.setattr(
            "automation.interactions.detect_captcha", AsyncMock(return_value=False)
        )

        pairs = []
        for profile, kind in cards:
            card = AsyncMock(name=f"card:{profile.name}")
            button = (
                AsyncMock(name=f"button:{profile.name}")
                if kind in ("connect", "pending")
                else None
            )
            card._wanted = (button, kind)
            pairs.append((profile, card))

        async def _walk(campaign, progress_callback=None):
            yield 1

        async def _find(card):
            return card._wanted

        automation._walk_search_pages = _walk
        automation._extract_profile_cards = AsyncMock(return_value=pairs)
        automation._find_card_connect_control = AsyncMock(side_effect=_find)
        return pairs

    def test_effective_daily_limit_prefers_campaign_over_settings(self):
        """The per-campaign daily_limit is authoritative; the settings/env default
        is only the fallback for a missing/zero campaign value."""
        settings = {"daily_connection_limit": 20}
        assert LinkedInAutomation._effective_daily_limit(
            Campaign(name="c", daily_limit=7), settings
        ) == 7
        assert LinkedInAutomation._effective_daily_limit(
            Campaign(name="c", daily_limit=0), settings
        ) == 20

    @pytest.mark.asyncio
    async def test_card_connect_happy_path_skips_profile_visit(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A card exposing Connect is invited from the card — no profile goto."""
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        auto = mock_linkedin_automation
        campaign = auto.db_manager.create_campaign({"name": "Cards"})
        pairs = self._wire(auto, [(self._profile(0), "connect")], monkeypatch)

        attempt = AsyncMock(return_value=ConnectResult("sent", total_today=1))
        auto._attempt_connect = attempt
        auto.send_connection_requests = AsyncMock()  # fallback must NOT run

        result = await auto.search_and_connect(campaign, limit=10)

        assert result["sent"] == 1
        attempt.assert_awaited_once()
        # The card's own connect button reached the shared connect core...
        assert attempt.await_args.args[2] is pairs[0][1]._wanted[0]
        # ...without any per-profile navigation or profile-page fallback.
        assert not auto.page.goto.called
        auto.send_connection_requests.assert_not_called()

    @pytest.mark.asyncio
    async def test_card_possibly_sent_is_tallied_and_ends_card_pass(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A possibly_sent from a card is tallied apart from failed and ends the pass.

        The browser may have refreshed mid-walk, so the remaining card handles
        are stale: the card pass ends, the possibly_sent card is NOT deferred to
        the profile fallback (it's already recorded — no re-contact), and the
        result surfaces it in the ``possibly_sent`` bucket.
        """
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        monkeypatch.setenv("CONNECTION_DELAY_MIN", "0")
        monkeypatch.setenv("CONNECTION_DELAY_MAX", "0")
        auto = mock_linkedin_automation
        campaign = auto.db_manager.create_campaign({"name": "Cards"})
        # Two connectable cards; the first is an ambiguous send.
        self._wire(
            auto,
            [(self._profile(0), "connect"), (self._profile(1), "connect")],
            monkeypatch,
        )

        auto._attempt_connect = AsyncMock(
            return_value=ConnectResult("possibly_sent", total_today=1)
        )
        # The fallback must NOT re-contact the possibly_sent card.
        auto.send_connection_requests = AsyncMock(
            return_value={
                "sent": 0, "possibly_sent": 0, "failed": 0,
                "existing": 0, "total_processed": 0,
            }
        )

        result = await auto.search_and_connect(campaign, limit=10)

        assert result["possibly_sent"] == 1
        assert result["sent"] == 0
        # Not mis-counted as a retryable failure.
        assert result["failed"] == 0
        # The card pass ended after the first card (stale handles); the second
        # card was never attempted.
        assert auto._attempt_connect.await_count == 1
        # The possibly_sent card was not handed to the profile fallback.
        if auto.send_connection_requests.await_args is not None:
            fb_profiles = auto.send_connection_requests.await_args.args[1]
            assert all(
                p.profile_url != self._profile(0).profile_url for p in fb_profiles
            )

    @pytest.mark.asyncio
    async def test_card_without_connect_falls_back_to_profile_path(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A card with no actionable Connect control is deferred to the profile path."""
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        auto = mock_linkedin_automation
        campaign = auto.db_manager.create_campaign({"name": "Cards"})
        profile = self._profile(0)
        self._wire(auto, [(profile, "none")], monkeypatch)

        auto._attempt_connect = AsyncMock()  # never invoked for a none card
        fallback = AsyncMock(
            return_value={"sent": 1, "failed": 0, "existing": 0, "total_processed": 1}
        )
        auto.send_connection_requests = fallback

        result = await auto.search_and_connect(campaign, limit=10)

        auto._attempt_connect.assert_not_called()
        fallback.assert_awaited_once()
        # The deferred profile (and only it) is handed to the profile-page path.
        fb_profiles = fallback.await_args.args[1]
        assert [p.profile_url for p in fb_profiles] == [profile.profile_url]
        assert result["sent"] == 1

    @pytest.mark.asyncio
    async def test_pending_card_is_recorded_and_skipped(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A card already showing Pending is recorded without a send or visit."""
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        auto = mock_linkedin_automation
        db = auto.db_manager
        campaign = db.create_campaign({"name": "Cards"})
        profile = self._profile(0)
        self._wire(auto, [(profile, "pending")], monkeypatch)

        auto._attempt_connect = AsyncMock()
        auto.send_connection_requests = AsyncMock()

        result = await auto.search_and_connect(campaign, limit=10)

        assert result["existing"] == 1
        assert result["sent"] == 0
        auto._attempt_connect.assert_not_called()
        auto.send_connection_requests.assert_not_called()
        assert not auto.page.goto.called
        # A pending contact row was persisted (no profile visit needed).
        with db.get_session() as session:
            from sqlmodel import select

            row = session.exec(
                select(Contact).where(Contact.profile_url == profile.profile_url)
            ).first()
        assert row is not None
        assert row.status == "pending"

    @pytest.mark.asyncio
    async def test_daily_cap_hit_during_cards_skips_fallback(
        self, mock_linkedin_automation, monkeypatch
    ):
        """Hitting the cap mid-scan stops the run, skipping the fallback pass.

        The persisted cap is shared across the card pass and the profile-page
        pass, so a no-control profile queued before the cap is reached is NOT
        visited once the cap stops the run.
        """
        auto = mock_linkedin_automation
        # The per-campaign daily_limit (now authoritative) caps the run at 1.
        campaign = auto.db_manager.create_campaign({"name": "Cards", "daily_limit": 1})
        # none card first (queued for fallback), then a connect card hitting the cap.
        self._wire(
            auto,
            [(self._profile(0), "none"), (self._profile(1), "connect")],
            monkeypatch,
        )

        auto._attempt_connect = AsyncMock(
            return_value=ConnectResult("sent", total_today=1)
        )
        fallback = AsyncMock(
            return_value={"sent": 0, "failed": 0, "existing": 0, "total_processed": 0}
        )
        auto.send_connection_requests = fallback

        messages = []
        result = await auto.search_and_connect(
            campaign, limit=10, progress_callback=messages.append
        )

        assert result["sent"] == 1
        fallback.assert_not_called()
        assert any("limit reached" in m.lower() for m in messages)

    @pytest.mark.asyncio
    async def test_prior_run_at_cap_sends_nothing(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A cap reached by a prior run blocks the card path before any send."""
        auto = mock_linkedin_automation
        db = auto.db_manager
        # The per-campaign daily_limit (now authoritative) caps the run at 2.
        campaign = db.create_campaign({"name": "Cards", "daily_limit": 2})
        today = date.today().isoformat()
        for _ in range(2):  # prior run already used the day's quota
            db.increment_daily_connection_count(today)
        self._wire(auto, [(self._profile(0), "connect")], monkeypatch)

        auto._attempt_connect = AsyncMock()
        auto.send_connection_requests = AsyncMock()

        result = await auto.search_and_connect(campaign, limit=10)

        assert result["sent"] == 0
        auto._attempt_connect.assert_not_called()
        auto.send_connection_requests.assert_not_called()
        assert db.get_daily_connection_count(today) == 2

    @pytest.mark.asyncio
    async def test_inline_captcha_on_results_stops_run(
        self, mock_linkedin_automation, monkeypatch
    ):
        """An inline CAPTCHA on the results page stops before reading any card."""
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        auto = mock_linkedin_automation
        campaign = auto.db_manager.create_campaign({"name": "Cards"})
        self._wire(auto, [(self._profile(0), "connect")], monkeypatch)
        # Override the _wire default: LinkedIn renders a verification widget
        # inline on /search/results/people (no URL bounce for the guard to catch).
        monkeypatch.setattr(
            "automation.interactions.detect_captcha",
            AsyncMock(return_value=True),
        )
        auto._attempt_connect = AsyncMock()
        auto.send_connection_requests = AsyncMock()

        messages = []
        result = await auto.search_and_connect(
            campaign, limit=10, progress_callback=messages.append
        )

        # Stopped to protect the account: no cards read, no fallback pass.
        assert result["sent"] == 0
        auto._extract_profile_cards.assert_not_called()
        auto._attempt_connect.assert_not_called()
        auto.send_connection_requests.assert_not_called()
        assert any("captcha" in m.lower() for m in messages)

    @pytest.mark.asyncio
    async def test_card_timeout_defers_wedged_card_to_profile_pass(
        self, mock_linkedin_automation, monkeypatch
    ):
        """A card-connect timeout ends the card pass and retries THAT card via the
        profile page.

        Single-pass trade-off: the wedged card is deferred to the resilient
        profile-page fallback (so it isn't lost), but later un-scanned cards are
        NOT recovered — there is no pre-scan to fall back on.
        """
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        auto = mock_linkedin_automation
        campaign = auto.db_manager.create_campaign({"name": "Cards"})
        p0, p1 = self._profile(0), self._profile(1)
        self._wire(auto, [(p0, "connect"), (p1, "connect")], monkeypatch)

        # The first card-connect wedges (bounded click+modal raises TimeoutError).
        auto._attempt_connect = AsyncMock(side_effect=asyncio.TimeoutError())
        fallback = AsyncMock(
            return_value={"sent": 1, "failed": 0, "existing": 0, "total_processed": 1}
        )
        auto.send_connection_requests = fallback

        result = await auto.search_and_connect(campaign, limit=10)

        # Card pass stopped after the first wedge (p1 never card-attempted).
        auto._attempt_connect.assert_awaited_once()
        # Only the wedged p0 is deferred to the profile-page pass; the un-scanned
        # p1 is not recovered (documented single-pass trade-off).
        fallback.assert_awaited_once()
        deferred = [p.profile_url for p in fallback.await_args.args[1]]
        assert deferred == [p0.profile_url]
        assert result["sent"] == 1

    @pytest.mark.asyncio
    async def test_unexpected_card_pass_error_propagates(
        self, mock_linkedin_automation, monkeypatch
    ):
        """An unexpected card-pass error propagates so the CLI surfaces a failure,
        rather than being swallowed into a partial 'success' result."""
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        auto = mock_linkedin_automation
        campaign = auto.db_manager.create_campaign({"name": "Cards"})
        self._wire(auto, [(self._profile(0), "connect")], monkeypatch)
        # Card extraction blows up unexpectedly (e.g. selector drift in the walk).
        auto._extract_profile_cards = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await auto.search_and_connect(campaign, limit=10)


@pytest.mark.unit
class TestParseCardProfile:
    """_parse_card_profile turns a card's href + visible text into a profile."""

    def test_parses_name_dropping_degree_marker_and_actions(self):
        profile = LinkedInAutomation._parse_card_profile(
            "https://www.linkedin.com/in/jane/",
            "Jane Roe • 2º\nSenior Engineer\nMadrid, Spain\nConectar",
        )
        assert profile is not None
        assert profile.name == "Jane Roe"
        # The "Conectar" action label is dropped, so headline/location land right.
        assert profile.headline == "Senior Engineer"
        assert profile.location == "Madrid, Spain"
        assert profile.profile_url == "https://www.linkedin.com/in/jane/"

    def test_action_words_filtered_case_insensitively(self):
        profile = LinkedInAutomation._parse_card_profile(
            "https://www.linkedin.com/in/x/",
            "John Doe\nConnect\nMessage\nCEO at Foo",
        )
        # Both EN action words are dropped; the first real line becomes headline.
        assert profile.headline == "CEO at Foo"

    def test_empty_text_returns_none(self):
        assert LinkedInAutomation._parse_card_profile("https://x/in/y/", "") is None
        assert LinkedInAutomation._parse_card_profile("https://x/in/y/", None) is None

    def test_blank_name_returns_none(self):
        # First line is only the degree marker → no usable name.
        assert (
            LinkedInAutomation._parse_card_profile("https://x/in/y/", "• 2º\nfoo")
            is None
        )


@pytest.mark.unit
class TestExtractProfileCards:
    """_extract_profile_cards keeps a card handle per profile and normalizes URLs."""

    @staticmethod
    def _card(href, text="Jane Roe • 2º\nEngineer", *, has_link=True, raises=False):
        card = AsyncMock(name=f"card:{href}")
        if raises:
            card.query_selector = AsyncMock(side_effect=RuntimeError("detached"))
        elif has_link:
            link = AsyncMock()
            link.get_attribute = AsyncMock(return_value=href)
            card.query_selector = AsyncMock(return_value=link)
        else:
            card.query_selector = AsyncMock(return_value=None)
        card.inner_text = AsyncMock(return_value=text)
        return card

    @pytest.mark.asyncio
    async def test_enumerate_card_handles_returns_first_matching_candidate(
        self, mock_linkedin_automation
    ):
        """_enumerate_card_handles tries SEARCH_RESULT_CARD candidates in order and
        returns the first that matches any node (SDUI list item leads)."""
        auto = mock_linkedin_automation
        cands = sel.SEARCH_RESULT_CARD.candidates
        handle = AsyncMock()

        async def qsa(selector):
            # First candidate matches nothing; second yields a card.
            return [handle] if selector == cands[1] else []

        auto.page.query_selector_all = AsyncMock(side_effect=qsa)
        assert await auto._enumerate_card_handles() == [handle]

    @pytest.mark.asyncio
    async def test_relative_href_normalized_to_absolute(self, mock_linkedin_automation):
        auto = mock_linkedin_automation
        card = self._card("/in/jane/")
        auto._enumerate_card_handles = AsyncMock(return_value=[card])

        pairs = await auto._extract_profile_cards()

        assert len(pairs) == 1
        profile, handle = pairs[0]
        # Relative href is resolved against BASE_URL so the fallback goto and the
        # contact-book dedup match the other harvest paths.
        assert profile.profile_url == "https://www.linkedin.com/in/jane/"
        assert handle is card

    @pytest.mark.asyncio
    async def test_absolute_href_query_stripped_and_passed_through(
        self, mock_linkedin_automation
    ):
        auto = mock_linkedin_automation
        card = self._card("https://www.linkedin.com/in/bob/?miniProfileUrn=x")
        auto._enumerate_card_handles = AsyncMock(return_value=[card])

        pairs = await auto._extract_profile_cards()

        assert pairs[0][0].profile_url == "https://www.linkedin.com/in/bob/"

    @pytest.mark.asyncio
    async def test_card_without_in_link_is_skipped(self, mock_linkedin_automation):
        auto = mock_linkedin_automation
        auto._enumerate_card_handles = AsyncMock(
            return_value=[self._card("/in/x/", has_link=False)]
        )

        assert await auto._extract_profile_cards() == []

    @pytest.mark.asyncio
    async def test_duplicate_href_deduped(self, mock_linkedin_automation):
        auto = mock_linkedin_automation
        auto._enumerate_card_handles = AsyncMock(
            return_value=[self._card("/in/jane/"), self._card("/in/jane/")]
        )

        pairs = await auto._extract_profile_cards()
        assert len(pairs) == 1

    @pytest.mark.asyncio
    async def test_detached_handle_is_skipped(self, mock_linkedin_automation):
        auto = mock_linkedin_automation
        good = self._card("/in/jane/")
        auto._enumerate_card_handles = AsyncMock(
            return_value=[self._card("/in/x/", raises=True), good]
        )

        pairs = await auto._extract_profile_cards()
        # The detached card is skipped; the live one still harvests.
        assert [p.profile_url for p, _ in pairs] == [
            "https://www.linkedin.com/in/jane/"
        ]
