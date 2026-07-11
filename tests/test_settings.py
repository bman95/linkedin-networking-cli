"""
Unit tests for application settings.

Tests AppSettings configuration and environment variable handling.
"""

import os
from pathlib import Path
from unittest.mock import patch
from zoneinfo import TZPATH, available_timezones

import pytest

from config.settings import AppSettings, _env_int


def _zone_bytes(name):
    """Return the raw zoneinfo bytes for an IANA ``name`` from the on-disk DB.

    Used to compare two zone names for equivalence without instantiating
    ``ZoneInfo`` (which can raise on hosts whose tzdata lacks a given alias).
    Two names are equivalent when their zoneinfo files are byte-identical.
    """
    for base in TZPATH:
        path = Path(base) / name
        if path.is_file():
            return path.read_bytes()
    return None


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

    def test_timezone_is_iana_or_none(self, monkeypatch):
        """The resolved timezone is either a valid IANA id (never an
        abbreviation like 'CEST' that Playwright rejects) or None when the host
        zone cannot be determined."""
        self._clear_env(monkeypatch)
        settings = AppSettings()
        tz = settings.get_browser_settings()["timezone_id"]

        assert tz is None or tz in available_timezones()

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

    def test_timezone_none_when_undetectable(self, monkeypatch):
        """With no override and no detectable host zone, timezone_id is None so
        the browser keeps its own host timezone rather than being forced to
        UTC."""
        self._clear_env(monkeypatch)
        monkeypatch.setattr(
            AppSettings, "_detect_host_timezone", classmethod(lambda cls: None)
        )
        settings = AppSettings()
        assert settings.get_browser_settings()["timezone_id"] is None


@pytest.mark.unit
class TestTimezoneDetection:
    """The host-timezone helper returns a valid IANA id or None — never an
    abbreviation or other value Playwright would reject."""

    def test_returns_valid_iana_or_none(self):
        """Whatever the host looks like, the result is a real IANA zone or
        None (never an invalid id)."""
        tz = AppSettings._detect_host_timezone()
        assert tz is None or tz in available_timezones()

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

    def test_copied_localtime_matched_by_bytes(self, monkeypatch):
        """A copied (non-symlink) /etc/localtime with no /etc/timezone — the
        common container layout — is resolved by byte-matching the zoneinfo DB
        rather than silently flattening a non-UTC host to UTC."""
        monkeypatch.delenv("TZ", raising=False)
        monkeypatch.setattr(Path, "is_symlink", lambda self: False)
        monkeypatch.setattr(Path, "exists", lambda self: False)

        from zoneinfo import TZPATH

        target_zone = "America/New_York"
        zone_path = next(
            (Path(base) / target_zone for base in TZPATH
             if (Path(base) / target_zone).is_file()),
            None,
        )
        if zone_path is None:
            pytest.skip("zoneinfo database not available on disk")
        target_bytes = zone_path.read_bytes()

        real_read_bytes = Path.read_bytes

        def fake_read_bytes(self):
            if str(self) == "/etc/localtime":
                return target_bytes
            return real_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
        detected = AppSettings._detect_host_timezone()
        # The byte-matcher returns *an* IANA name whose zoneinfo data is
        # identical to the target. Several aliases (e.g. "America/New_York" and
        # "US/Eastern") can ship byte-for-byte identical files, and which one
        # wins depends on the host's tzdata build and set iteration order. Any
        # equivalent alias is correct, so accept any valid IANA name whose
        # zoneinfo bytes match the target rather than a single expected string.
        assert detected is not None
        assert detected in available_timezones()
        assert _zone_bytes(detected) == target_bytes

    def test_no_sources_returns_none(self, monkeypatch):
        """Host with no TZ, no symlink, no /etc/timezone and an unreadable
        /etc/localtime (e.g. native Windows) -> None, so the caller leaves the
        timezone to the browser's host default instead of forcing UTC."""
        monkeypatch.delenv("TZ", raising=False)
        monkeypatch.setattr(Path, "is_symlink", lambda self: False)
        monkeypatch.setattr(Path, "exists", lambda self: False)
        monkeypatch.setattr(
            Path, "read_bytes",
            lambda self: (_ for _ in ()).throw(OSError("no localtime")),
        )
        assert AppSettings._detect_host_timezone() is None


# ============================================================================
# Automation Settings Tests
# ============================================================================

@pytest.mark.unit
class TestAutomationSettings:
    """Test automation settings configuration."""

    def test_get_automation_settings_defaults(self, monkeypatch):
        """Test default automation settings."""
        # Clear all automation-related env vars
        for var in (
            "CONNECTION_DELAY_MIN",
            "CONNECTION_DELAY_MAX",
            "DAILY_CONNECTION_LIMIT",
            "CONNECTION_COOLDOWN",
            "SEARCH_LIMIT",
            "TYPING_DELAY_MIN",
            "TYPING_DELAY_MAX",
            "ACTION_DELAY_MIN",
            "ACTION_DELAY_MAX",
            "MAX_ACTIONS_PER_MINUTE",
        ):
            monkeypatch.delenv(var, raising=False)

        settings = AppSettings()
        auto_settings = settings.get_automation_settings()

        assert auto_settings["connection_delay_min"] == 2
        assert auto_settings["connection_delay_max"] == 5
        assert auto_settings["daily_connection_limit"] == 20
        assert auto_settings["connection_cooldown"] == 0
        assert auto_settings["search_limit"] == 100
        # Humanization defaults (issue #15).
        assert auto_settings["typing_delay_min"] == 50
        assert auto_settings["typing_delay_max"] == 150
        assert auto_settings["action_delay_min"] == 1
        assert auto_settings["action_delay_max"] == 4
        assert auto_settings["max_actions_per_minute"] == 20

    def test_get_automation_settings_custom_values(self, monkeypatch):
        """Test automation settings with custom values."""
        monkeypatch.setenv("CONNECTION_DELAY_MIN", "5")
        monkeypatch.setenv("CONNECTION_DELAY_MAX", "10")
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "50")
        monkeypatch.setenv("CONNECTION_COOLDOWN", "3600")
        monkeypatch.setenv("SEARCH_LIMIT", "200")
        monkeypatch.setenv("TYPING_DELAY_MIN", "80")
        monkeypatch.setenv("TYPING_DELAY_MAX", "200")
        monkeypatch.setenv("ACTION_DELAY_MIN", "2")
        monkeypatch.setenv("ACTION_DELAY_MAX", "6")
        monkeypatch.setenv("MAX_ACTIONS_PER_MINUTE", "10")

        settings = AppSettings()
        auto_settings = settings.get_automation_settings()

        assert auto_settings["connection_delay_min"] == 5
        assert auto_settings["connection_delay_max"] == 10
        assert auto_settings["daily_connection_limit"] == 50
        assert auto_settings["connection_cooldown"] == 3600
        assert auto_settings["search_limit"] == 200
        assert auto_settings["typing_delay_min"] == 80
        assert auto_settings["typing_delay_max"] == 200
        assert auto_settings["action_delay_min"] == 2
        assert auto_settings["action_delay_max"] == 6
        assert auto_settings["max_actions_per_minute"] == 10

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
        assert isinstance(auto_settings["typing_delay_min"], int)
        assert isinstance(auto_settings["typing_delay_max"], int)
        assert isinstance(auto_settings["action_delay_min"], int)
        assert isinstance(auto_settings["action_delay_max"], int)
        assert isinstance(auto_settings["max_actions_per_minute"], int)

    @pytest.mark.parametrize("env_var,default_value", [
        ("CONNECTION_DELAY_MIN", 2),
        ("CONNECTION_DELAY_MAX", 5),
        ("DAILY_CONNECTION_LIMIT", 20),
        ("CONNECTION_COOLDOWN", 0),
        ("SEARCH_LIMIT", 100),
        ("TYPING_DELAY_MIN", 50),
        ("TYPING_DELAY_MAX", 150),
        ("ACTION_DELAY_MIN", 1),
        ("ACTION_DELAY_MAX", 4),
        ("MAX_ACTIONS_PER_MINUTE", 20),
    ])
    def test_automation_settings_individual_defaults(self, monkeypatch, env_var, default_value):
        """Test individual automation setting defaults."""
        monkeypatch.delenv(env_var, raising=False)
        settings = AppSettings()
        auto_settings = settings.get_automation_settings()

        key = env_var.lower()
        assert auto_settings[key] == default_value


@pytest.mark.unit
class TestConfigOverrides:
    """Persisted setting overrides (config.json, saved from the Settings TUI).

    Precedence: config.json > env > default — otherwise editing a value in
    the app would silently do nothing whenever .env also sets it. The autouse
    ``isolate_app_home`` fixture points ``Path.home()`` at a temp dir, so each
    test gets a fresh, empty config.json location.
    """

    def test_no_file_uses_env_and_defaults(self, monkeypatch):
        monkeypatch.setenv("SEARCH_LIMIT", "42")
        monkeypatch.delenv("DAILY_CONNECTION_LIMIT", raising=False)
        auto = AppSettings().get_automation_settings()
        assert auto["search_limit"] == 42  # env
        assert auto["daily_connection_limit"] == 20  # default

    def test_override_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "20")
        settings = AppSettings()
        settings.config_path.write_text('{"daily_connection_limit": 7}')
        assert AppSettings().get_automation_settings()["daily_connection_limit"] == 7

    def test_save_overrides_round_trip(self):
        values = {
            "connection_delay_min": 3,
            "connection_delay_max": 9,
            "daily_connection_limit": 15,
            "connection_cooldown": 60,
            "search_limit": 50,
        }
        AppSettings().save_overrides(values)
        auto = AppSettings().get_automation_settings()
        for key, expected in values.items():
            assert auto[key] == expected

    def test_save_overrides_rejects_unknown_keys(self):
        with pytest.raises(ValueError):
            AppSettings().save_overrides({"linkedin_password": 1})

    def test_save_overrides_preserves_unknown_file_keys(self):
        settings = AppSettings()
        settings.config_path.write_text('{"future_section": {"a": 1}}')
        settings.save_overrides({"search_limit": 33})
        import json

        stored = json.loads(settings.config_path.read_text())
        assert stored["future_section"] == {"a": 1}
        assert stored["search_limit"] == 33

    def test_malformed_file_degrades_to_env(self, monkeypatch, caplog):
        monkeypatch.setenv("SEARCH_LIMIT", "42")
        settings = AppSettings()
        settings.config_path.write_text("{not json")
        with caplog.at_level("WARNING"):
            auto = AppSettings().get_automation_settings()
        assert auto["search_limit"] == 42
        assert any("config.json" in rec.message for rec in caplog.records)

    def test_non_int_and_unknown_values_ignored(self, monkeypatch):
        monkeypatch.delenv("SEARCH_LIMIT", raising=False)
        settings = AppSettings()
        settings.config_path.write_text(
            '{"search_limit": "lots", "connection_cooldown": true, "unknown": 5}'
        )
        auto = AppSettings().get_automation_settings()
        assert auto["search_limit"] == 100  # non-int override ignored
        assert auto["connection_cooldown"] == 0  # bool is not a tunable int
        assert "unknown" not in auto


@pytest.mark.unit
class TestNavigationSettings:
    """Resilient-navigation tunables (issue #17)."""

    _ENV_VARS = (
        "NAV_GOTO_TIMEOUT_MS",
        "NAV_MAX_RETRIES",
        "NAV_RETRY_BACKOFF_BASE_S",
        "NAV_HARD_TIMEOUT_MARGIN_S",
        "NAV_INTERACTION_WATCHDOG_S",
    )

    def test_defaults(self, monkeypatch):
        for var in self._ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        nav = AppSettings().get_navigation_settings()
        assert nav["goto_timeout_ms"] == 30000
        assert nav["max_retries"] == 2
        assert nav["retry_backoff_base_s"] == 3
        assert nav["hard_timeout_margin_s"] == 15
        assert nav["interaction_watchdog_s"] == 240

    def test_custom_values(self, monkeypatch):
        monkeypatch.setenv("NAV_GOTO_TIMEOUT_MS", "45000")
        monkeypatch.setenv("NAV_MAX_RETRIES", "4")
        monkeypatch.setenv("NAV_RETRY_BACKOFF_BASE_S", "5")
        monkeypatch.setenv("NAV_HARD_TIMEOUT_MARGIN_S", "20")
        monkeypatch.setenv("NAV_INTERACTION_WATCHDOG_S", "120")
        nav = AppSettings().get_navigation_settings()
        assert nav["goto_timeout_ms"] == 45000
        assert nav["max_retries"] == 4
        assert nav["retry_backoff_base_s"] == 5
        assert nav["hard_timeout_margin_s"] == 20
        assert nav["interaction_watchdog_s"] == 120

    def test_types_are_ints(self, app_settings):
        nav = app_settings.get_navigation_settings()
        for value in nav.values():
            assert isinstance(value, int)

    def test_zero_retries_allowed(self, monkeypatch):
        """max_retries=0 means a single attempt with no retry (a valid config)."""
        monkeypatch.setenv("NAV_MAX_RETRIES", "0")
        assert AppSettings().get_navigation_settings()["max_retries"] == 0


# ============================================================================
# Env-var int parsing guard
# ============================================================================

@pytest.mark.unit
class TestEnvIntHelper:
    """`_env_int` tolerates malformed values instead of crashing startup."""

    def test_valid_value_parsed(self, monkeypatch):
        """A well-formed value is parsed to an int."""
        monkeypatch.setenv("SOME_INT", "42")
        assert _env_int("SOME_INT", 7) == 42

    def test_missing_value_uses_default(self, monkeypatch):
        """An unset variable returns the default (no warning)."""
        monkeypatch.delenv("SOME_INT", raising=False)
        assert _env_int("SOME_INT", 7) == 7

    def test_malformed_value_falls_back_with_warning(self, monkeypatch, caplog):
        """A malformed value returns the default and logs a warning."""
        import logging

        monkeypatch.setenv("SOME_INT", "twenty")
        with caplog.at_level(logging.WARNING):
            assert _env_int("SOME_INT", 7) == 7
        assert any("SOME_INT" in rec.message for rec in caplog.records)

    def test_empty_value_falls_back(self, monkeypatch):
        """An empty string is malformed and degrades to the default."""
        monkeypatch.setenv("SOME_INT", "")
        assert _env_int("SOME_INT", 7) == 7

    def test_whitespace_padded_value_parsed(self, monkeypatch):
        """int() tolerates surrounding whitespace, so a padded value parses."""
        monkeypatch.setenv("SOME_INT", "  5  ")
        assert _env_int("SOME_INT", 7) == 5


@pytest.mark.unit
class TestAutomationSettingsGuardedParsing:
    """Malformed automation env vars degrade to defaults (block 205-217)."""

    def test_valid_value(self, monkeypatch):
        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "35")
        assert AppSettings().get_automation_settings()["daily_connection_limit"] == 35

    def test_missing_value_uses_default(self, monkeypatch):
        monkeypatch.delenv("DAILY_CONNECTION_LIMIT", raising=False)
        assert AppSettings().get_automation_settings()["daily_connection_limit"] == 20

    def test_malformed_value_falls_back_and_warns(self, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("DAILY_CONNECTION_LIMIT", "twenty")
        with caplog.at_level(logging.WARNING):
            auto = AppSettings().get_automation_settings()
        assert auto["daily_connection_limit"] == 20
        assert any("DAILY_CONNECTION_LIMIT" in rec.message for rec in caplog.records)

    def test_one_malformed_var_does_not_break_the_others(self, monkeypatch):
        """A single bad value degrades only itself; the rest still parse."""
        monkeypatch.setenv("CONNECTION_DELAY_MIN", "oops")
        monkeypatch.setenv("SEARCH_LIMIT", "250")
        auto = AppSettings().get_automation_settings()
        assert auto["connection_delay_min"] == 2   # default (malformed)
        assert auto["search_limit"] == 250          # honored


@pytest.mark.unit
class TestNavigationSettingsGuardedParsing:
    """Malformed navigation env vars degrade to defaults (block 258-264)."""

    def test_valid_value(self, monkeypatch):
        monkeypatch.setenv("NAV_GOTO_TIMEOUT_MS", "45000")
        assert AppSettings().get_navigation_settings()["goto_timeout_ms"] == 45000

    def test_missing_value_uses_default(self, monkeypatch):
        monkeypatch.delenv("NAV_GOTO_TIMEOUT_MS", raising=False)
        assert AppSettings().get_navigation_settings()["goto_timeout_ms"] == 30000

    def test_malformed_value_falls_back_and_warns(self, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("NAV_MAX_RETRIES", "lots")
        with caplog.at_level(logging.WARNING):
            nav = AppSettings().get_navigation_settings()
        assert nav["max_retries"] == 2
        assert any("NAV_MAX_RETRIES" in rec.message for rec in caplog.records)


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
        """A malformed int env var degrades to the default instead of crashing."""
        monkeypatch.setenv("CONNECTION_DELAY_MIN", "not_a_number")
        settings = AppSettings()

        auto_settings = settings.get_automation_settings()
        # Falls back to the CONNECTION_DELAY_MIN default rather than raising.
        assert auto_settings["connection_delay_min"] == 2

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


# ============================================================================
# LLM (AI Assist) Settings Tests
# ============================================================================

_LLM_ENV_VARS = (
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_MODE",
    "LLM_TIMEOUT_S",
    "LLM_PULL_TIMEOUT_S",
    "LLM_MAX_TOKENS",
    "LLM_MAX_INPUT_CHARS",
)


def _clear_llm_env(monkeypatch):
    for var in _LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.unit
class TestLLMSettings:
    """Test get_llm_settings() defaults, derivation, and overrides."""

    def test_defaults_are_local_mode(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        llm = AppSettings().get_llm_settings()

        assert llm["mode"] == "local"
        assert llm["base_url"] == "http://localhost:11434"
        assert llm["api_key"] is None
        assert llm["model"] is None
        assert llm["timeout_s"] == 60
        assert llm["pull_timeout_s"] == 1800
        assert llm["max_tokens"] == 1024
        assert llm["max_input_chars"] == 4000

    def test_api_key_present_derives_hosted_mode(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_API_KEY", "sk-test")

        llm = AppSettings().get_llm_settings()

        assert llm["mode"] == "hosted"
        assert llm["api_key"] == "sk-test"

    def test_explicit_mode_overrides_derivation(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_MODE", "local")

        llm = AppSettings().get_llm_settings()

        assert llm["mode"] == "local"

    def test_invalid_mode_falls_back_to_derived_and_warns(self, monkeypatch, caplog):
        import logging

        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_MODE", "cloud")
        with caplog.at_level(logging.WARNING):
            llm = AppSettings().get_llm_settings()

        assert llm["mode"] == "local"  # derived (no API key), not crashed
        assert any("LLM_MODE" in rec.message for rec in caplog.records)

    def test_base_url_trims_whitespace_and_trailing_slash(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_BASE_URL", "  http://localhost:11434/  ")

        llm = AppSettings().get_llm_settings()

        assert llm["base_url"] == "http://localhost:11434"

    def test_model_env_var_honored(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_MODEL", "gemma3:4b")

        assert AppSettings().get_llm_settings()["model"] == "gemma3:4b"


@pytest.mark.unit
class TestLLMSettingsGuardedParsing:
    """Malformed numeric LLM env vars degrade to defaults, never crash."""

    def test_valid_value(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_TIMEOUT_S", "30")
        assert AppSettings().get_llm_settings()["timeout_s"] == 30

    def test_missing_value_uses_default(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        assert AppSettings().get_llm_settings()["timeout_s"] == 60

    def test_malformed_value_falls_back_and_warns(self, monkeypatch, caplog):
        import logging

        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_MAX_TOKENS", "lots")
        with caplog.at_level(logging.WARNING):
            llm = AppSettings().get_llm_settings()
        assert llm["max_tokens"] == 1024
        assert any("LLM_MAX_TOKENS" in rec.message for rec in caplog.records)
