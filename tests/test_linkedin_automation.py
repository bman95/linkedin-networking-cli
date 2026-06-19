"""
Unit tests for LinkedIn automation module.

Tests LinkedInAutomation class with mocked Playwright interactions.
"""

import pytest
from unittest.mock import AsyncMock, Mock, MagicMock, patch
from datetime import datetime, timezone, date

from automation.linkedin import LinkedInAutomation, LinkedInProfile
from database.models import Campaign


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
        # Login detection is URL-based: staying on /feed means we are authenticated.
        mock_page = mock_linkedin_automation.page
        mock_page.goto = AsyncMock()
        mock_page.url = "https://www.linkedin.com/feed/"

        result = await mock_linkedin_automation.login()

        assert result is True
        assert mock_linkedin_automation.is_authenticated is True
        # Already authenticated: no credentials should be entered.
        assert mock_page.fill.call_count == 0

    @pytest.mark.asyncio
    async def test_login_with_credentials(self, db_manager, app_settings, mock_page):
        """Test login with username and password after redirect to /login."""
        automation = LinkedInAutomation(db_manager, app_settings)
        automation.page = mock_page
        automation.context = AsyncMock()

        # Visiting /feed redirects to /login -> credentials flow is triggered.
        mock_page.url = "https://www.linkedin.com/login"
        # No CAPTCHA present on the page.
        mock_page.query_selector = AsyncMock(return_value=None)
        mock_page.content = AsyncMock(return_value="")

        result = await automation.login()

        assert mock_page.fill.call_count >= 2  # Email and password fields
        assert mock_page.click.called  # Submit button
        assert result is True


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

        # Mock page
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock()
        mock_page.wait_for_selector = AsyncMock()
        mock_page.query_selector_all = AsyncMock(return_value=[])

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
