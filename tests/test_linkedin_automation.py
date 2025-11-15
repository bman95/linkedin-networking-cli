"""
Unit tests for LinkedIn automation module.

Tests LinkedInAutomation class with mocked Playwright interactions.
"""

import pytest
from unittest.mock import AsyncMock, Mock, MagicMock, patch
from datetime import datetime, timezone

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
        """Test login when session already exists."""
        # Mock page to simulate existing session
        mock_page = mock_linkedin_automation.page
        mock_page.goto = AsyncMock()
        mock_page.is_visible = AsyncMock(return_value=True)

        result = await mock_linkedin_automation.login()

        assert result is True
        assert mock_linkedin_automation.is_authenticated is True

    @pytest.mark.asyncio
    async def test_login_with_credentials(self, db_manager, app_settings, mock_page):
        """Test login with username and password."""
        automation = LinkedInAutomation(db_manager, app_settings)
        automation.page = mock_page
        automation.context = AsyncMock()

        # Mock successful login
        mock_page.is_visible = AsyncMock(side_effect=[False, True])  # No session, then success

        result = await automation.login()

        assert mock_page.fill.call_count >= 2  # Email and password fields
        assert mock_page.click.called  # Submit button
        assert result is True


# ============================================================================
# Search Location Tests
# ============================================================================

@pytest.mark.unit
class TestSearchLocation:
    """Test location search via Voyager API."""

    @pytest.mark.asyncio
    async def test_search_location_valid_query(self, mock_linkedin_automation):
        """Test searching for a location with valid query."""
        # Mock response
        mock_response = AsyncMock()
        mock_response.ok = True
        mock_response.json = AsyncMock(return_value={
            "data": {
                "elements": [
                    {
                        "targetUrn": "urn:li:fs_geo:90000084",
                        "text": {"text": "San Francisco Bay Area"}
                    }
                ]
            }
        })

        mock_linkedin_automation.page.request.get = AsyncMock(return_value=mock_response)

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
    async def test_search_location_api_error(self, mock_linkedin_automation):
        """Test location search when API returns error."""
        mock_response = AsyncMock()
        mock_response.ok = False
        mock_response.status = 404

        mock_linkedin_automation.page.request.get = AsyncMock(return_value=mock_response)

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
