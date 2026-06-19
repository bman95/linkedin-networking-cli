from pathlib import Path
from typing import Any, Dict, Optional
import os
import sys

sys.path.append(str(Path(__file__).parent.parent))

from utils.logging import get_logger

logger = get_logger(__name__)


class AppSettings:
    """Application settings manager"""

    def __init__(self):
        self.app_dir = Path.home() / ".linkedin-networking-cli"
        self.app_dir.mkdir(exist_ok=True)
        logger.info(f"Application directory: {self.app_dir}")

        self.db_path = self.app_dir / "linkedin_networking.db"
        self.session_path = self.app_dir / "session.json"
        self.config_path = self.app_dir / "config.json"
        logger.debug(f"Database path: {self.db_path}")
        logger.debug(f"Session path: {self.session_path}")

    @property
    def linkedin_email(self) -> Optional[str]:
        """Get LinkedIn email from environment"""
        email = os.getenv("LINKEDIN_EMAIL")
        if email:
            logger.debug(f"LinkedIn email configured: {email[:3]}***@{email.split('@')[1] if '@' in email else '***'}")
        else:
            logger.warning("LINKEDIN_EMAIL environment variable not set")
        return email

    @property
    def linkedin_password(self) -> Optional[str]:
        """Get LinkedIn password from environment"""
        password = os.getenv("LINKEDIN_PASSWORD")
        if password:
            logger.debug("LinkedIn password configured")
        else:
            logger.warning("LINKEDIN_PASSWORD environment variable not set")
        return password

    @staticmethod
    def _detect_host_timezone() -> str:
        """Best-effort IANA timezone name for the host.

        Playwright's ``timezone_id`` needs an IANA name (e.g. ``Europe/Madrid``),
        not an abbreviation like ``CEST``. We read it from the host rather than
        hardcoding one so the value stays coherent with the OS the browser
        actually runs on. Falls back to ``UTC`` when nothing reliable is found.
        """
        tz = os.getenv("TZ")
        if tz and "/" in tz:
            return tz

        localtime = Path("/etc/localtime")
        try:
            if localtime.is_symlink():
                target = os.readlink(localtime)
                marker = "zoneinfo/"
                idx = target.find(marker)
                if idx != -1:
                    candidate = target[idx + len(marker):]
                    if candidate:
                        return candidate
        except OSError:
            pass

        etc_tz = Path("/etc/timezone")
        try:
            if etc_tz.exists():
                value = etc_tz.read_text(encoding="utf-8").strip()
                if value:
                    return value
        except OSError:
            pass

        return "UTC"

    def get_browser_settings(self) -> Dict[str, Any]:
        """Get browser settings"""
        channel_env = os.getenv("PLAYWRIGHT_BROWSER_CHANNEL", "chrome")
        channel = channel_env.strip() if channel_env else None
        if channel and channel.lower() in {"", "none"}:
            channel = None

        executable = os.getenv("PLAYWRIGHT_BROWSER_EXECUTABLE")
        if executable is not None:
            executable = executable.strip()
            if not executable:
                executable = None

        headless_env = os.getenv("HEADLESS")
        if headless_env is None:
            # Default to visible Chrome when using a custom executable or Chrome channel
            is_custom_chrome = bool(executable) or (channel and channel.lower() == "chrome")
            headless = not is_custom_chrome
        else:
            headless = headless_env.strip().lower() in {"1", "true", "yes", "on"}

        # Locale and timezone are set on the browser context so they stay
        # coherent with the host (and each other). Defaults derive from the
        # host; both are overridable for users on a differently-configured box.
        locale = os.getenv("BROWSER_LOCALE", "en-US").strip() or "en-US"

        timezone_env = os.getenv("BROWSER_TIMEZONE")
        if timezone_env is not None and timezone_env.strip():
            timezone_id = timezone_env.strip()
        else:
            timezone_id = self._detect_host_timezone()

        # User-agent is intentionally left to real Chrome by default: Chrome's
        # own UA already matches its platform and version, so forcing one risks
        # introducing the very inconsistency this whole config exists to avoid.
        # The override is opt-in and the user owns keeping it coherent.
        user_agent_env = os.getenv("BROWSER_USER_AGENT")
        user_agent = user_agent_env.strip() if user_agent_env else None
        if not user_agent:
            user_agent = None

        settings = {
            "headless": headless,
            "user_data_dir": str(self.app_dir / "browser_data"),
            "viewport": {"width": 1920, "height": 1080},
            "channel": channel,
            "executable_path": executable,
            "locale": locale,
            "timezone_id": timezone_id,
            "user_agent": user_agent,
        }

        logger.debug(
            f"Browser settings: headless={headless}, channel={channel}, "
            f"executable={executable is not None}, locale={locale}, "
            f"timezone_id={timezone_id}, user_agent={'custom' if user_agent else 'default'}"
        )
        return settings

    def get_automation_settings(self) -> Dict[str, Any]:
        """Get automation settings"""
        settings = {
            "connection_delay_min": int(os.getenv("CONNECTION_DELAY_MIN", "2")),
            "connection_delay_max": int(os.getenv("CONNECTION_DELAY_MAX", "5")),
            "daily_connection_limit": int(os.getenv("DAILY_CONNECTION_LIMIT", "20")),
            "connection_cooldown": int(os.getenv("CONNECTION_COOLDOWN", "0")),
            "search_limit": int(os.getenv("SEARCH_LIMIT", "100"))
        }

        logger.debug(f"Automation settings: delay={settings['connection_delay_min']}-{settings['connection_delay_max']}s, daily_limit={settings['daily_connection_limit']}, cooldown={settings['connection_cooldown']}s, search_limit={settings['search_limit']}")
        return settings

    def validate_credentials(self) -> bool:
        """Check if LinkedIn credentials are available"""
        is_valid = bool(self.linkedin_email and self.linkedin_password)
        if is_valid:
            logger.info("LinkedIn credentials validated successfully")
        else:
            logger.error("LinkedIn credentials validation failed - missing email or password")
        return is_valid