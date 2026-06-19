"""
Unit tests for application settings.

Tests AppSettings configuration and environment variable handling.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import available_timezones

from config.settings import AppSettings


# ============================================================================
# AppSettings Initialization Tests
# ============================================================================

@pytest.mark.unit
class TestAppSettingsInit:
    """Test AppSettings initialization."""

    def test_init_creates_app_directory(self, tmp_path, monkeypatch):
        """Test that initialization creates application directory."""
        # Use temporary directory
        app_dir = tmp_path / ".linkedin-networking-cli"

        with patch("pathlib.Path.home", return_value=tmp_path):
            settings = AppSettings()
            assert settings.app_dir.exists()
            assert settings.app_dir == app_dir

    def test_init_sets_paths(self, app_settings):
        """Test that initialization sets all paths correctly."""
        assert isinstance(app_settings.db_path, Path)
        assert isinstance(app_settings.session_path, Path)
        assert isinstance(app_settings.config_path, Path)

        assert app_settings.db_path.name == "linkedin_networking.db"
        assert app_settings.session_path.name == "session.json"
        assert app_settings.config_path.name == "config.json"


# ============================================================================
# Credentials Tests
# ============================================================================

@pytest.mark.unit
class TestCredentials:
    """Test credential properties."""

    def test_linkedin_email_from_env(self, monkeypatch):
        """Test getting LinkedIn email from environment."""
        monkeypatch.setenv("LINKEDIN_EMAIL", "test@example.com")
        settings = AppSettings()
        assert settings.linkedin_email == "test@example.com"

    def test_linkedin_email_not_set(self, monkeypatch):
        """Test LinkedIn email when not set."""
        monkeypatch.delenv("LINKEDIN_EMAIL", raising=False)
        settings = AppSettings()
        assert settings.linkedin_email is None

    def test_linkedin_password_from_env(self, monkeypatch):
        """Test getting LinkedIn password from environment."""
        monkeypatch.setenv("LINKEDIN_PASSWORD", "secret123")
        settings = AppSettings()
        assert settings.linkedin_password == "secret123"

    def test_linkedin_password_not_set(self, monkeypatch):
        """Test LinkedIn password when not set."""
        monkeypatch.delenv("LINKEDIN_PASSWORD", raising=False)
        settings = AppSettings()
        assert settings.linkedin_password is None

    def test_validate_credentials_both_set(self, monkeypatch):
        """Test credential validation when both are set."""
        monkeypatch.setenv("LINKEDIN_EMAIL", "test@example.com")
        monkeypatch.setenv("LINKEDIN_PASSWORD", "secret123")
        settings = AppSettings()
        assert settings.validate_credentials() is True

    def test_validate_credentials_email_only(self, monkeypatch):
        """Test credential validation when only email is set."""
        monkeypatch.setenv("LINKEDIN_EMAIL", "test@example.com")
        monkeypatch.delenv("LINKEDIN_PASSWORD", raising=False)
        settings = AppSettings()
        assert settings.validate_credentials() is False

    def test_validate_credentials_password_only(self, monkeypatch):
        """Test credential validation when only password is set."""
        monkeypatch.delenv("LINKEDIN_EMAIL", raising=False)
        monkeypatch.setenv("LINKEDIN_PASSWORD", "secret123")
        settings = AppSettings()
        assert settings.validate_credentials() is False

    def test_validate_credentials_none_set(self, monkeypatch):
        """Test credential validation when neither is set."""
        monkeypatch.delenv("LINKEDIN_EMAIL", raising=False)
        monkeypatch.delenv("LINKEDIN_PASSWORD", raising=False)
        settings = AppSettings()
        assert settings.validate_credentials() is False

    def test_validate_credentials_empty_strings(self, monkeypatch):
        """Test credential validation with empty strings."""
        monkeypatch.setenv("LINKEDIN_EMAIL", "")
        monkeypatch.setenv("LINKEDIN_PASSWORD", "")
        settings = AppSettings()
        assert settings.validate_credentials() is False


# ============================================================================
# Browser Settings Tests
# ============================================================================

@pytest.mark.unit
class TestBrowserSettings:
    """Test browser settings configuration."""

    def test_get_browser_settings_defaults(self, monkeypatch):
        """Test default browser settings."""
        # Clear all browser-related env vars
        monkeypatch.delenv("PLAYWRIGHT_BROWSER_CHANNEL", raising=False)
        monkeypatch.delenv("PLAYWRIGHT_BROWSER_EXECUTABLE", raising=False)
        monkeypatch.delenv("HEADLESS", raising=False)

        settings = AppSettings()
        browser_settings = settings.get_browser_settings()

        assert isinstance(browser_settings, dict)
        assert "headless" in browser_settings
        assert "user_data_dir" in browser_settings
        assert "viewport" in browser_settings
        assert "channel" in browser_settings
        assert "executable_path" in browser_settings

        # Check viewport defaults
        assert browser_settings["viewport"]["width"] == 1920
        assert browser_settings["viewport"]["height"] == 1080

    def test_get_browser_settings_with_custom_channel(self, monkeypatch):
        """Test browser settings with custom channel."""
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "chrome")
        settings = AppSettings()
        browser_settings = settings.get_browser_settings()

        assert browser_settings["channel"] == "chrome"
        # Chrome channel should default to headless=False
        assert browser_settings["headless"] is False

    def test_get_browser_settings_with_executable(self, monkeypatch):
        """Test browser settings with custom executable."""
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_EXECUTABLE", "/path/to/chrome")
        settings = AppSettings()
        browser_settings = settings.get_browser_settings()

        assert browser_settings["executable_path"] == "/path/to/chrome"
        # Custom executable should default to headless=False
        assert browser_settings["headless"] is False

    def test_get_browser_settings_headless_true(self, monkeypatch):
        """Test browser settings with headless=true."""
        monkeypatch.setenv("HEADLESS", "true")
        settings = AppSettings()
        browser_settings = settings.get_browser_settings()

        assert browser_settings["headless"] is True

    def test_get_browser_settings_headless_false(self, monkeypatch):
        """Test browser settings with headless=false."""
        monkeypatch.setenv("HEADLESS", "false")
        settings = AppSettings()
        browser_settings = settings.get_browser_settings()

        assert browser_settings["headless"] is False

    @pytest.mark.parametrize("value,expected", [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("off", False),
        ("invalid", False),
    ])
    def test_get_browser_settings_headless_values(self, monkeypatch, value, expected):
        """Test various headless environment variable values."""
        monkeypatch.setenv("HEADLESS", value)
        settings = AppSettings()
        browser_settings = settings.get_browser_settings()

        assert browser_settings["headless"] is expected

    def test_get_browser_settings_empty_channel(self, monkeypatch):
        """Test browser settings with empty channel."""
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "")
        settings = AppSettings()
        browser_settings = settings.get_browser_settings()

        assert browser_settings["channel"] is None

    def test_get_browser_settings_none_channel(self, monkeypatch):
        """Test browser settings with 'none' channel."""
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "none")
        settings = AppSettings()
        browser_settings = settings.get_browser_settings()

        assert browser_settings["channel"] is None

    def test_get_browser_settings_empty_executable(self, monkeypatch):
        """Test browser settings with empty executable."""
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_EXECUTABLE", "")
        settings = AppSettings()
        browser_settings = settings.get_browser_settings()

        assert browser_settings["executable_path"] is None

    def test_get_browser_settings_whitespace_trimming(self, monkeypatch):
        """Test that whitespace is trimmed from environment variables."""
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "  chrome  ")
        monkeypatch.setenv("PLAYWRIGHT_BROWSER_EXECUTABLE", "  /path/to/chrome  ")
        settings = AppSettings()
        browser_settings = settings.get_browser_settings()

        assert browser_settings["channel"] == "chrome"
        assert browser_settings["executable_path"] == "/path/to/chrome"

    def test_get_browser_settings_user_data_dir(self, app_settings):
        """Test that user data directory path is set correctly."""
        browser_settings = app_settings.get_browser_settings()
        user_data_dir = browser_settings["user_data_dir"]

        assert "browser_data" in user_data_dir
        assert isinstance(user_data_dir, str)


# ============================================================================
# Fingerprint Settings Tests (locale / timezone / user-agent)
# ============================================================================

@pytest.mark.unit
class TestFingerprintSettings:
    """Locale, timezone and user-agent stay coherent and configurable."""

    def _clear_env(self, monkeypatch):
        for var in ("BROWSER_LOCALE", "BROWSER_TIMEZONE", "BROWSER_USER_AGENT"):
            monkeypatch.delenv(var, raising=False)

    def test_browser_settings_expose_fingerprint_keys(self, monkeypatch):
        """locale, timezone_id and user_agent are always present."""
        self._clear_env(monkeypatch)
        settings = AppSettings()
        browser_settings = settings.get_browser_settings()

        assert "locale" in browser_settings
        assert "timezone_id" in browser_settings
        assert "user_agent" in browser_settings

    def test_locale_default(self, monkeypatch):
        """Locale defaults to en-US when unset."""
        self._clear_env(monkeypatch)
        settings = AppSettings()
        assert settings.get_browser_settings()["locale"] == "en-US"

    def test_locale_override(self, monkeypatch):
        """BROWSER_LOCALE overrides the default locale (and is trimmed)."""
        monkeypatch.setenv("BROWSER_LOCALE", "  es-ES  ")
        settings = AppSettings()
        assert settings.get_browser_settings()["locale"] == "es-ES"

    def test_locale_empty_falls_back_to_default(self, monkeypatch):
        """An empty BROWSER_LOCALE falls back to the default, never ''."""
        monkeypatch.setenv("BROWSER_LOCALE", "   ")
        settings = AppSettings()
        assert settings.get_browser_settings()["locale"] == "en-US"

    def test_user_agent_default_unset(self, monkeypatch):
        """User-agent defaults to None so real Chrome's UA is used."""
        self._clear_env(monkeypatch)
        settings = AppSettings()
        assert settings.get_browser_settings()["user_agent"] is None

    def test_user_agent_override(self, monkeypatch):
        """BROWSER_USER_AGENT sets an explicit override (trimmed)."""
        monkeypatch.setenv("BROWSER_USER_AGENT", "  CustomUA/1.0  ")
        settings = AppSettings()
        assert settings.get_browser_settings()["user_agent"] == "CustomUA/1.0"

    def test_user_agent_empty_is_none(self, monkeypatch):
        """A blank BROWSER_USER_AGENT yields None, not an empty string."""
        monkeypatch.setenv("BROWSER_USER_AGENT", "   ")
        settings = AppSettings()
        assert settings.get_browser_settings()["user_agent"] is None

    def test_timezone_override(self, monkeypatch):
        """BROWSER_TIMEZONE overrides host detection (trimmed)."""
        monkeypatch.setenv("BROWSER_TIMEZONE", "  America/New_York  ")
        settings = AppSettings()
        assert settings.get_browser_settings()["timezone_id"] == "America/New_York"

    def test_timezone_is_iana_not_abbreviation(self, monkeypatch):
        """The detected timezone is a non-empty IANA-style id, never an
        abbreviation like 'CEST' which Playwright rejects."""
        self._clear_env(monkeypatch)
        settings = AppSettings()
        tz = settings.get_browser_settings()["timezone_id"]

        assert isinstance(tz, str) and tz
        # Either a region/location IANA id (has a '/') or the UTC fallback.
        assert "/" in tz or tz == "UTC"

    def test_timezone_from_tz_env(self, monkeypatch):
        """A TZ env var holding an IANA name is honoured by detection."""
        self._clear_env(monkeypatch)
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        settings = AppSettings()
        assert settings.get_browser_settings()["timezone_id"] == "Asia/Tokyo"

    def test_invalid_timezone_override_falls_back(self, monkeypatch):
        """An invalid BROWSER_TIMEZONE (typo/abbreviation) is rejected and the
        host default is used instead, never a value Playwright would reject."""
        self._clear_env(monkeypatch)
        monkeypatch.setenv("BROWSER_TIMEZONE", "CEST")  # abbreviation, not IANA
        settings = AppSettings()
        tz = settings.get_browser_settings()["timezone_id"]
        assert tz != "CEST"
        assert tz in available_timezones()


@pytest.mark.unit
class TestTimezoneDetection:
    """The host-timezone helper only ever returns a valid IANA id."""

    def test_returns_valid_iana_or_utc(self):
        """Whatever the host looks like, the result is a real IANA zone."""
        assert AppSettings._detect_host_timezone() in available_timezones()

    def test_tz_env_with_iana_name(self, monkeypatch):
        monkeypatch.setenv("TZ", "America/Sao_Paulo")
        assert AppSettings._detect_host_timezone() == "America/Sao_Paulo"

    def test_tz_env_invalid_is_skipped(self, monkeypatch):
        """A TZ value that is not a valid IANA id (e.g. the glibc ':/path'
        form) is ignored rather than returned and later crashing Playwright."""
        monkeypatch.setenv("TZ", ":/etc/localtime")
        # Falls through to other detection sources; result must still be valid.
        assert AppSettings._detect_host_timezone() in available_timezones()

    def test_symlink_branch_parses_zoneinfo_target(self, monkeypatch):
        """A normal /etc/localtime -> .../zoneinfo/Area/Loc symlink resolves."""
        monkeypatch.delenv("TZ", raising=False)
        monkeypatch.setattr(Path, "is_symlink", lambda self: True)
        monkeypatch.setattr(
            os, "readlink", lambda p: "/usr/share/zoneinfo/Europe/Berlin"
        )
        assert AppSettings._detect_host_timezone() == "Europe/Berlin"

    def test_symlink_posix_prefix_is_stripped(self, monkeypatch):
        """Leap-second 'posix/' and 'right/' zoneinfo subtrees still resolve to
        the valid inner IANA id rather than a Playwright-rejected value."""
        monkeypatch.delenv("TZ", raising=False)
        monkeypatch.setattr(Path, "is_symlink", lambda self: True)
        monkeypatch.setattr(
            os, "readlink", lambda p: "/usr/share/zoneinfo/posix/Europe/Madrid"
        )
        assert AppSettings._detect_host_timezone() == "Europe/Madrid"

    def test_etc_timezone_fallback(self, monkeypatch, tmp_path):
        """When there is no usable TZ/symlink, /etc/timezone contents are used."""
        monkeypatch.delenv("TZ", raising=False)
        monkeypatch.setattr(Path, "is_symlink", lambda self: False)

        real_read_text = Path.read_text

        def fake_read_text(self, *args, **kwargs):
            if str(self) == "/etc/timezone":
                return "Asia/Kolkata\n"
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fake_read_text)
        monkeypatch.setattr(
            Path, "exists", lambda self: str(self) == "/etc/timezone"
        )
        assert AppSettings._detect_host_timezone() == "Asia/Kolkata"

    def test_no_sources_falls_back_to_utc(self, monkeypatch):
        """Container with no TZ, no symlink and no /etc/timezone -> UTC."""
        monkeypatch.delenv("TZ", raising=False)
        monkeypatch.setattr(Path, "is_symlink", lambda self: False)
        monkeypatch.setattr(Path, "exists", lambda self: False)
        assert AppSettings._detect_host_timezone() == "UTC"


# ============================================================================
# Automation Settings Tests
# ============================================================================

@pytest.mark.unit
class TestAutomationSettings:
    """Test automation settings configuration."""

    def test_get_automation_settings_defaults(self, monkeypatch):
        """Test default automation settings."""
        # Clear all automation-related env vars
        monkeypatch.delenv("CONNECTION_DELAY_MIN", raising=False)
        monkeypatch.delenv("CONNECTION_DELAY_MAX", raising=False)
        monkeypatch.delenv("DAILY_CONNECTION_LIMIT", raising=False)
        monkeypatch.delenv("CONNECTION_COOLDOWN", raising=False)
        monkeypatch.delenv("SEARCH_LIMIT", raising=False)

        settings = AppSettings()
        auto_settings = settings.get_automation_settings()

        assert auto_settings["connection_delay_min"] == 2
        assert auto_settings["connection_delay_max"] == 5
        assert auto_settings["daily_connection_limit"] == 20
        assert auto_settings["connection_cooldown"] == 0
        assert auto_settings["search_limit"] == 100

    def test_get_automation_settings_custom_values(self, monkeypatch):
        """Test automation settings with custom values."""
        monkeypatch.setenv("CONNECTION_DELAY_MIN", "5")
        monkeypatch.setenv("CONNECTION_DELAY_MAX", "10")
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "50")
        monkeypatch.setenv("CONNECTION_COOLDOWN", "3600")
        monkeypatch.setenv("SEARCH_LIMIT", "200")

        settings = AppSettings()
        auto_settings = settings.get_automation_settings()

        assert auto_settings["connection_delay_min"] == 5
        assert auto_settings["connection_delay_max"] == 10
        assert auto_settings["daily_connection_limit"] == 50
        assert auto_settings["connection_cooldown"] == 3600
        assert auto_settings["search_limit"] == 200

    def test_get_automation_settings_zero_values(self, monkeypatch):
        """Test automation settings with zero values."""
        monkeypatch.setenv("CONNECTION_DELAY_MIN", "0")
        monkeypatch.setenv("CONNECTION_DELAY_MAX", "0")
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "0")

        settings = AppSettings()
        auto_settings = settings.get_automation_settings()

        assert auto_settings["connection_delay_min"] == 0
        assert auto_settings["connection_delay_max"] == 0
        assert auto_settings["daily_connection_limit"] == 0

    def test_get_automation_settings_types(self, app_settings):
        """Test that automation settings returns integers."""
        auto_settings = app_settings.get_automation_settings()

        assert isinstance(auto_settings["connection_delay_min"], int)
        assert isinstance(auto_settings["connection_delay_max"], int)
        assert isinstance(auto_settings["daily_connection_limit"], int)
        assert isinstance(auto_settings["connection_cooldown"], int)
        assert isinstance(auto_settings["search_limit"], int)

    @pytest.mark.parametrize("env_var,default_value", [
        ("CONNECTION_DELAY_MIN", 2),
        ("CONNECTION_DELAY_MAX", 5),
        ("DAILY_CONNECTION_LIMIT", 20),
        ("CONNECTION_COOLDOWN", 0),
        ("SEARCH_LIMIT", 100),
    ])
    def test_automation_settings_individual_defaults(self, monkeypatch, env_var, default_value):
        """Test individual automation setting defaults."""
        monkeypatch.delenv(env_var, raising=False)
        settings = AppSettings()
        auto_settings = settings.get_automation_settings()

        key = env_var.lower()
        assert auto_settings[key] == default_value


# ============================================================================
# Path Tests
# ============================================================================

@pytest.mark.unit
class TestPaths:
    """Test path configurations."""

    def test_db_path_in_app_dir(self, app_settings):
        """Test that database path is in app directory."""
        assert app_settings.db_path.parent == app_settings.app_dir

    def test_session_path_in_app_dir(self, app_settings):
        """Test that session path is in app directory."""
        assert app_settings.session_path.parent == app_settings.app_dir

    def test_config_path_in_app_dir(self, app_settings):
        """Test that config path is in app directory."""
        assert app_settings.config_path.parent == app_settings.app_dir

    def test_paths_are_pathlib_objects(self, app_settings):
        """Test that paths are Path objects."""
        assert isinstance(app_settings.db_path, Path)
        assert isinstance(app_settings.session_path, Path)
        assert isinstance(app_settings.config_path, Path)
        assert isinstance(app_settings.app_dir, Path)


# ============================================================================
# Integration and Edge Cases
# ============================================================================

@pytest.mark.unit
class TestSettingsEdgeCases:
    """Test edge cases and special scenarios."""

    def test_app_settings_can_be_instantiated_multiple_times(self):
        """Test that AppSettings can be instantiated multiple times."""
        settings1 = AppSettings()
        settings2 = AppSettings()

        assert settings1.app_dir == settings2.app_dir
        assert settings1.db_path == settings2.db_path

    def test_app_settings_with_unicode_in_env_vars(self, monkeypatch):
        """Test settings with unicode characters in environment variables."""
        monkeypatch.setenv("LINKEDIN_EMAIL", "josé@example.com")
        settings = AppSettings()

        assert settings.linkedin_email == "josé@example.com"

    def test_browser_settings_case_insensitive_headless(self, monkeypatch):
        """Test that headless value comparison is case-insensitive."""
        monkeypatch.setenv("HEADLESS", "TrUe")
        settings = AppSettings()
        browser_settings = settings.get_browser_settings()

        assert browser_settings["headless"] is True

    def test_automation_settings_with_invalid_int_value(self, monkeypatch):
        """Test that invalid integer values raise ValueError."""
        monkeypatch.setenv("CONNECTION_DELAY_MIN", "not_a_number")
        settings = AppSettings()

        with pytest.raises(ValueError):
            settings.get_automation_settings()

    def test_multiple_browser_settings_calls(self, app_settings):
        """Test that browser settings can be called multiple times."""
        settings1 = app_settings.get_browser_settings()
        settings2 = app_settings.get_browser_settings()

        assert settings1 == settings2

    def test_multiple_automation_settings_calls(self, app_settings):
        """Test that automation settings can be called multiple times."""
        settings1 = app_settings.get_automation_settings()
        settings2 = app_settings.get_automation_settings()

        assert settings1 == settings2

    def test_settings_properties_are_read_only(self, app_settings):
        """Test that credential properties can't be directly set."""
        # Properties don't have setters, so this should fail
        with pytest.raises(AttributeError):
            app_settings.linkedin_email = "new@example.com"

    def test_app_dir_exists_after_init(self, tmp_path, monkeypatch):
        """Test that app directory is created if it doesn't exist."""
        app_dir = tmp_path / ".linkedin-networking-cli"
        assert not app_dir.exists()

        with patch("pathlib.Path.home", return_value=tmp_path):
            settings = AppSettings()
            assert app_dir.exists()
