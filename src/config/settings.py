import os
from pathlib import Path
from typing import Any
from zoneinfo import TZPATH, available_timezones

from utils.logging import get_logger

logger = get_logger(__name__)


def _env_int(name: str, default: int) -> int:
    """Parse an integer environment variable, tolerating malformed values.

    Returns ``default`` when ``name`` is unset or holds a value that is not a
    valid ``int`` (logging a warning in the malformed case). This keeps a typo
    like ``DAILY_CONNECTION_LIMIT=twenty`` from crashing startup with an
    unhandled ``ValueError``; the tunable simply degrades to its default.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "Invalid integer for %s=%r; falling back to default %s",
            name,
            raw,
            default,
        )
        return default


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
    def linkedin_email(self) -> str | None:
        """Get LinkedIn email from environment"""
        email = os.getenv("LINKEDIN_EMAIL")
        if email:
            logger.debug(f"LinkedIn email configured: {email[:3]}***@{email.split('@')[1] if '@' in email else '***'}")
        else:
            logger.warning("LINKEDIN_EMAIL environment variable not set")
        return email

    @property
    def linkedin_password(self) -> str | None:
        """Get LinkedIn password from environment"""
        password = os.getenv("LINKEDIN_PASSWORD")
        if password:
            logger.debug("LinkedIn password configured")
        else:
            logger.warning("LINKEDIN_PASSWORD environment variable not set")
        return password

    @property
    def weekly_invitation_limit(self) -> int:
        """Proactive weekly invitation budget (env ``WEEKLY_INVITATION_LIMIT``).

        LinkedIn's binding constraint is a rolling ~weekly invitation cap, not
        the per-day one; without this the weekly limit is only discovered
        reactively by hitting the limit modal mid-run. Parsed with the same
        guarded ``_env_int`` helper as ``DAILY_CONNECTION_LIMIT`` so a malformed
        value degrades to the default (100) instead of crashing startup.
        """
        return _env_int("WEEKLY_INVITATION_LIMIT", 100)

    @staticmethod
    def _normalize_timezone(candidate: str | None) -> str | None:
        """Return ``candidate`` only if it is a valid IANA timezone id.

        Playwright's ``timezone_id`` must be an IANA name (e.g.
        ``Europe/Madrid``); abbreviations like ``CEST``, leap-second variants
        like ``posix/Europe/Madrid``, glibc forms like ``:/etc/localtime`` and
        plain typos all make ``new_context``/``launch_persistent_context`` raise
        and would crash ``start_browser``. Anything not recognised by the stdlib
        zoneinfo database returns ``None`` so callers can fall back safely.
        """
        if not candidate:
            return None
        candidate = candidate.strip()
        if not candidate:
            return None
        # Tolerate the leap-second/posix zoneinfo subtrees by stripping the
        # leading prefix, since the inner path is a valid IANA id.
        for prefix in ("posix/", "right/"):
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix):]
        return candidate if candidate in available_timezones() else None

    @staticmethod
    def _match_localtime_by_bytes() -> str | None:
        """Identify the host zone by matching ``/etc/localtime``'s bytes.

        Containers (and some distros) ship ``/etc/localtime`` as a *copy* of a
        zoneinfo file rather than a symlink, and often without ``/etc/timezone``.
        The symlink/text branches miss that layout, so as a last resort we read
        the file and compare it byte-for-byte against the zoneinfo database,
        returning the matching IANA id. Returns ``None`` on any read error or no
        match so the caller can fall back to ``UTC``.
        """
        try:
            localtime_bytes = Path("/etc/localtime").read_bytes()
        except OSError:
            return None

        for name in available_timezones():
            for base in TZPATH:
                candidate = Path(base) / name
                try:
                    if candidate.is_file() and candidate.read_bytes() == localtime_bytes:
                        return name
                except OSError:
                    continue
        return None

    @classmethod
    def _detect_host_timezone(cls) -> str | None:
        """Best-effort *valid* IANA timezone name for the host, or ``None``.

        Reads the host (``TZ``, then ``/etc/localtime``, then ``/etc/timezone``,
        then a byte-match of ``/etc/localtime`` against the zoneinfo database)
        rather than hardcoding a zone, so ``timezone_id`` stays coherent with the
        OS the browser actually runs on. Every candidate is validated. Returns
        ``None`` when nothing reliable is found (e.g. a Windows host with no
        Linux timezone files) so the caller can leave the context's timezone to
        the browser's own host default rather than forcing an incoherent ``UTC``.
        """
        normalized = cls._normalize_timezone(os.getenv("TZ"))
        if normalized:
            return normalized

        localtime = Path("/etc/localtime")
        try:
            if localtime.is_symlink():
                target = os.readlink(localtime)
                marker = "zoneinfo/"
                idx = target.find(marker)
                if idx != -1:
                    normalized = cls._normalize_timezone(target[idx + len(marker):])
                    if normalized:
                        return normalized
        except OSError:
            pass

        etc_tz = Path("/etc/timezone")
        try:
            if etc_tz.exists():
                normalized = cls._normalize_timezone(
                    etc_tz.read_text(encoding="utf-8").strip()
                )
                if normalized:
                    return normalized
        except OSError:
            pass

        # Copied (non-symlink) /etc/localtime without /etc/timezone — common in
        # containers. Match the file's bytes against the zoneinfo database so a
        # non-UTC host is not silently flattened.
        return cls._match_localtime_by_bytes()

    def get_browser_settings(self) -> dict[str, Any]:
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

        # A user-supplied BROWSER_TIMEZONE is validated like the host-detected
        # one: an invalid id (typo, abbreviation) would crash the browser launch,
        # so fall back to host detection rather than trusting it blindly.
        timezone_id = self._normalize_timezone(os.getenv("BROWSER_TIMEZONE"))
        if not timezone_id:
            timezone_id = self._detect_host_timezone()

        # User-agent is intentionally left to real Chrome by default: Chrome's
        # own UA already matches its platform and version, so forcing one risks
        # introducing the very inconsistency this whole config exists to avoid.
        # The override is opt-in and the user owns keeping it coherent.
        user_agent_env = os.getenv("BROWSER_USER_AGENT")
        user_agent = user_agent_env.strip() if user_agent_env else None
        user_agent = user_agent or None

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
            f"timezone_id={timezone_id or 'host-default'}, "
            f"user_agent={'custom' if user_agent else 'default'}"
        )
        return settings

    def get_automation_settings(self) -> dict[str, Any]:
        """Get automation settings"""
        settings = {
            "connection_delay_min": _env_int("CONNECTION_DELAY_MIN", 2),
            "connection_delay_max": _env_int("CONNECTION_DELAY_MAX", 5),
            "daily_connection_limit": _env_int("DAILY_CONNECTION_LIMIT", 20),
            "connection_cooldown": _env_int("CONNECTION_COOLDOWN", 0),
            "search_limit": _env_int("SEARCH_LIMIT", 100),
            # Humanization tunables (issue #15). Typing is per-keystroke in ms;
            # action dwell is between major actions in seconds; the per-minute
            # cap throttles a sliding 60s window of actions.
            "typing_delay_min": _env_int("TYPING_DELAY_MIN", 50),
            "typing_delay_max": _env_int("TYPING_DELAY_MAX", 150),
            "action_delay_min": _env_int("ACTION_DELAY_MIN", 1),
            "action_delay_max": _env_int("ACTION_DELAY_MAX", 4),
            "max_actions_per_minute": _env_int("MAX_ACTIONS_PER_MINUTE", 20),
        }

        logger.debug(
            "Automation settings: delay=%s-%ss, daily_limit=%s, cooldown=%ss, "
            "search_limit=%s, typing_delay=%s-%sms, action_delay=%s-%ss, "
            "max_actions_per_minute=%s",
            settings["connection_delay_min"],
            settings["connection_delay_max"],
            settings["daily_connection_limit"],
            settings["connection_cooldown"],
            settings["search_limit"],
            settings["typing_delay_min"],
            settings["typing_delay_max"],
            settings["action_delay_min"],
            settings["action_delay_max"],
            settings["max_actions_per_minute"],
        )
        return settings

    def get_navigation_settings(self) -> dict[str, Any]:
        """Get resilient-navigation tunables (issue #17).

        These bound the retry/watchdog layer around the guarded navigation:

        - ``goto_timeout_ms`` — the per-``page.goto`` navigation timeout.
        - ``max_retries`` — extra ``goto`` attempts on a *transient* network
          error (``net::ERR_*``); the total attempt count is this + 1.
        - ``retry_backoff_base_s`` — base seconds for the inter-retry backoff
          (attempt *n* waits ``base * (n + 1)`` seconds).
        - ``hard_timeout_margin_s`` — slack added on top of the goto timeout for
          the *outer* ``asyncio`` watchdog. A renderer that crashes
          mid-navigation detaches the CDP session ``page.goto``'s own timer is
          bound to, so the call would deadlock forever; the watchdog converts
          that into a crash-shaped error the caller can recover from.
        - ``interaction_watchdog_s`` — hard cap on one per-item (per-profile /
          per-card) unit of page interaction. A crashed renderer defeats even
          ``locator.count()`` (it carries no timeout), so each item runs under
          this watchdog and a wedged unit is refreshed and skipped.
        """
        settings = {
            "goto_timeout_ms": _env_int("NAV_GOTO_TIMEOUT_MS", 30000),
            "max_retries": _env_int("NAV_MAX_RETRIES", 2),
            "retry_backoff_base_s": _env_int("NAV_RETRY_BACKOFF_BASE_S", 3),
            "hard_timeout_margin_s": _env_int("NAV_HARD_TIMEOUT_MARGIN_S", 15),
            "interaction_watchdog_s": _env_int("NAV_INTERACTION_WATCHDOG_S", 240),
        }

        logger.debug(
            "Navigation settings: goto_timeout=%sms, max_retries=%s, "
            "retry_backoff_base=%ss, hard_timeout_margin=%ss, "
            "interaction_watchdog=%ss",
            settings["goto_timeout_ms"],
            settings["max_retries"],
            settings["retry_backoff_base_s"],
            settings["hard_timeout_margin_s"],
            settings["interaction_watchdog_s"],
        )
        return settings

    def get_llm_settings(self) -> dict[str, Any]:
        """AI-assist (LLM) settings for the "describe your campaign" feature.

        ``api_key`` is env-only and carries the same trust tier as
        ``LINKEDIN_PASSWORD`` — never persisted to the SQLite ``Settings``
        table. ``mode`` derives from whether ``LLM_API_KEY`` is set unless
        ``LLM_MODE`` explicitly overrides it (an invalid override falls back
        to the derived value, with a warning, rather than crashing).
        """
        base_url = (os.getenv("LLM_BASE_URL") or "http://localhost:11434").strip().rstrip("/")
        api_key = os.getenv("LLM_API_KEY") or None
        model = os.getenv("LLM_MODEL") or None

        mode_env = (os.getenv("LLM_MODE") or "").strip().lower()
        if mode_env in ("local", "hosted"):
            mode = mode_env
        else:
            if mode_env:
                logger.warning(
                    "Invalid LLM_MODE=%r (expected 'local' or 'hosted'); "
                    "deriving from LLM_API_KEY instead",
                    mode_env,
                )
            mode = "hosted" if api_key else "local"

        settings = {
            "mode": mode,
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "timeout_s": _env_int("LLM_TIMEOUT_S", 60),
            "pull_timeout_s": _env_int("LLM_PULL_TIMEOUT_S", 1800),
            "max_tokens": _env_int("LLM_MAX_TOKENS", 1024),
            "max_input_chars": _env_int("LLM_MAX_INPUT_CHARS", 4000),
        }

        logger.debug(
            "LLM settings: mode=%s, base_url=%s, model=%s, api_key=%s, "
            "timeout_s=%s, pull_timeout_s=%s, max_tokens=%s, max_input_chars=%s",
            settings["mode"],
            settings["base_url"],
            settings["model"] or "unset",
            "set" if settings["api_key"] else "unset",
            settings["timeout_s"],
            settings["pull_timeout_s"],
            settings["max_tokens"],
            settings["max_input_chars"],
        )
        return settings

    def validate_credentials(self) -> bool:
        """Check if LinkedIn credentials are available"""
        is_valid = bool(self.linkedin_email and self.linkedin_password)
        if is_valid:
            logger.info("LinkedIn credentials validated successfully")
        else:
            logger.error("LinkedIn credentials validation failed - missing email or password")
        return is_valid