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

        settings = {
            "headless": headless,
            "user_data_dir": str(self.app_dir / "browser_data"),
            "viewport": {"width": 1920, "height": 1080},
            "channel": channel,
            "executable_path": executable
        }

        logger.debug(f"Browser settings: headless={headless}, channel={channel}, executable={executable is not None}")
        return settings

    def get_automation_settings(self) -> Dict[str, Any]:
        """Get automation settings"""
        settings = {
            "connection_delay_min": int(os.getenv("CONNECTION_DELAY_MIN", "2")),
            "connection_delay_max": int(os.getenv("CONNECTION_DELAY_MAX", "5")),
            "daily_connection_limit": int(os.getenv("DAILY_CONNECTION_LIMIT", "20")),
            "search_limit": int(os.getenv("SEARCH_LIMIT", "100"))
        }

        logger.debug(f"Automation settings: delay={settings['connection_delay_min']}-{settings['connection_delay_max']}s, daily_limit={settings['daily_connection_limit']}, search_limit={settings['search_limit']}")
        return settings

    def validate_credentials(self) -> bool:
        """Check if LinkedIn credentials are available"""
        is_valid = bool(self.linkedin_email and self.linkedin_password)
        if is_valid:
            logger.info("LinkedIn credentials validated successfully")
        else:
            logger.error("LinkedIn credentials validation failed - missing email or password")
        return is_valid