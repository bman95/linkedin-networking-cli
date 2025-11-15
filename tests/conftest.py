"""
Pytest configuration and shared fixtures for LinkedIn Networking CLI tests.

This module provides fixtures that are available to all test modules.
"""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, AsyncGenerator
from unittest.mock import Mock, AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

# Add src to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from database.models import Campaign, Contact, Analytics, Settings
from database.operations import DatabaseManager
from config.settings import AppSettings


# ============================================================================
# Database Fixtures
# ============================================================================

@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    """Create a temporary database path for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_linkedin.db"
        yield db_path


@pytest.fixture
def in_memory_engine():
    """
    Create an in-memory SQLite engine for testing.
    Uses StaticPool to ensure the same connection is reused.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(in_memory_engine) -> Generator[Session, None, None]:
    """Create a database session for testing."""
    with Session(in_memory_engine) as session:
        yield session


@pytest.fixture
def db_manager(temp_db_path) -> Generator[DatabaseManager, None, None]:
    """Create a DatabaseManager instance for testing."""
    manager = DatabaseManager(str(temp_db_path))
    yield manager
    # Cleanup is automatic when temp directory is removed


# ============================================================================
# Model Fixtures - Sample Data
# ============================================================================

@pytest.fixture
def sample_campaign() -> Campaign:
    """Create a sample campaign for testing."""
    return Campaign(
        name="Test Campaign",
        keywords="software engineer",
        geo_urn="90000084",
        location_display="San Francisco Bay Area",
        industry_ids="4,6",
        industry_display="Computer Software, Internet",
        network='["F","S"]',
        network_display="1st + 2nd degree connections",
        message_template="Hi {name}, I'd like to connect!",
        status="active",
        daily_limit=10,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_contact() -> Contact:
    """Create a sample contact for testing."""
    return Contact(
        campaign_id=1,
        name="John Doe",
        profile_url="https://www.linkedin.com/in/johndoe/",
        headline="Software Engineer at Tech Co",
        location="San Francisco, CA",
        company="Tech Co",
        status="sent",
        connection_sent_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_analytics() -> Analytics:
    """Create sample analytics for testing."""
    return Analytics(
        campaign_id=1,
        date=datetime.now(timezone.utc).date(),
        profiles_found=100,
        connections_sent=10,
        connections_accepted=3,
        response_rate=30.0,
    )


@pytest.fixture
def sample_settings() -> Settings:
    """Create sample settings for testing."""
    return Settings(
        key="daily_connection_limit",
        value="10",
        description="Maximum connections per day",
    )


# ============================================================================
# Settings Fixtures
# ============================================================================

@pytest.fixture
def mock_env_vars(monkeypatch, temp_db_path):
    """Mock environment variables for testing."""
    monkeypatch.setenv("LINKEDIN_EMAIL", "test@example.com")
    monkeypatch.setenv("LINKEDIN_PASSWORD", "test_password")
    monkeypatch.setenv("DB_PATH", str(temp_db_path))
    monkeypatch.setenv("HEADLESS", "true")


@pytest.fixture
def app_settings(mock_env_vars, temp_db_path) -> AppSettings:
    """Create an AppSettings instance for testing."""
    # Override the data directory to use temp directory
    settings = AppSettings()
    settings._data_dir = temp_db_path.parent
    return settings


# ============================================================================
# Playwright/Browser Mocks
# ============================================================================

@pytest.fixture
def mock_page():
    """Create a mock Playwright page object."""
    page = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.query_selector = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[])
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.is_visible = AsyncMock(return_value=True)
    page.get_attribute = AsyncMock()
    page.inner_text = AsyncMock()

    # Mock request context
    request_mock = AsyncMock()
    request_mock.get = AsyncMock()
    page.request = request_mock

    return page


@pytest.fixture
def mock_browser():
    """Create a mock Playwright browser object."""
    browser = AsyncMock()
    browser.new_context = AsyncMock()
    browser.close = AsyncMock()
    return browser


@pytest.fixture
def mock_context():
    """Create a mock Playwright browser context."""
    context = AsyncMock()
    context.new_page = AsyncMock()
    context.close = AsyncMock()
    context.storage_state = AsyncMock()
    return context


@pytest.fixture
def mock_playwright(mock_browser):
    """Create a mock Playwright instance."""
    playwright = AsyncMock()
    playwright.chromium.launch = AsyncMock(return_value=mock_browser)
    playwright.chromium.launch_persistent_context = AsyncMock()
    playwright.stop = AsyncMock()
    return playwright


@pytest.fixture
def mock_element():
    """Create a mock Playwright element."""
    element = AsyncMock()
    element.query_selector = AsyncMock()
    element.query_selector_all = AsyncMock(return_value=[])
    element.get_attribute = AsyncMock()
    element.inner_text = AsyncMock()
    element.is_disabled = AsyncMock(return_value=False)
    element.click = AsyncMock()
    return element


# ============================================================================
# LinkedIn Automation Mocks
# ============================================================================

@pytest.fixture
def mock_linkedin_automation(mock_page, db_manager, app_settings):
    """Create a mock LinkedInAutomation instance."""
    from automation.linkedin import LinkedInAutomation

    automation = LinkedInAutomation(db_manager, app_settings)
    automation.page = mock_page
    automation.is_authenticated = True

    return automation


@pytest.fixture
def mock_profile_data():
    """Sample profile data for testing."""
    return {
        "name": "Jane Smith",
        "profile_url": "https://www.linkedin.com/in/janesmith/",
        "headline": "Senior Software Engineer",
        "location": "New York, NY",
        "company": "Tech Corp",
        "mutual_connections": 5,
    }


# ============================================================================
# Utility Fixtures
# ============================================================================

@pytest.fixture
def freeze_time():
    """Fixture to freeze time for consistent datetime testing."""
    from freezegun import freeze_time as _freeze_time
    frozen_time = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    with _freeze_time(frozen_time):
        yield frozen_time


@pytest.fixture
def caplog_debug(caplog):
    """Configure caplog to capture DEBUG level logs."""
    import logging
    caplog.set_level(logging.DEBUG)
    return caplog


# ============================================================================
# Parametrized Test Data
# ============================================================================

@pytest.fixture(params=["active", "paused", "completed"])
def campaign_status(request):
    """Parametrized fixture for campaign statuses."""
    return request.param


@pytest.fixture(params=["sent", "accepted", "rejected", "pending", "found"])
def contact_status(request):
    """Parametrized fixture for contact statuses."""
    return request.param


# ============================================================================
# Cleanup Hooks
# ============================================================================

@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset singleton instances between tests if needed."""
    yield
    # Add cleanup code here if you have singleton patterns


@pytest.fixture(autouse=True)
def cleanup_temp_files():
    """Cleanup temporary files created during tests."""
    yield
    # Cleanup happens automatically with temp directories
