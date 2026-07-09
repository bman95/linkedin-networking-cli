"""Read-only Settings screen (issue #24).

Shows the live configuration the classic CLI surfaces under Settings —
credentials status, browser, rate limiting, and data locations — using the same
labels for parity. It is strictly informational: secrets are never displayed
(email masked, password shown only as Set / Not set).

``AppSettings()`` creates the app directory on construction (a filesystem
write), and the quota read is blocking SQLite, so the load runs in a threaded
worker with the same race discipline as the other screens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Static

from cli.helpers import mask_email
from database.operations import DatabaseManager
from utils.logging import get_logger

from .base import BaseScreen

logger = get_logger(__name__)


@dataclass(frozen=True)
class SettingsData:
    """Display-ready settings, computed off the UI thread. No raw secrets."""

    credentials_status: str
    email_masked: str
    password_state: str
    browser: dict
    automation: dict
    used_today: int | None
    paths: dict


class SettingsScreen(BaseScreen):
    """Read-only view of the application's effective configuration."""

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("r", "refresh", "Refresh"),
        ("q", "app.quit", "Quit"),
    ]

    SCREEN_TITLE = "Settings"

    def __init__(self, db_manager: DatabaseManager | None) -> None:
        super().__init__()
        self._db_manager = db_manager

    def compose_body(self) -> ComposeResult:
        with Container(id="settings-body"):
            for section_id, title in (
                ("credentials", "Credentials Status"),
                ("browser", "Browser Configuration"),
                ("limits", "Rate Limiting"),
                ("data", "Data Storage"),
            ):
                with Container(classes="settings-section", id=f"section-{section_id}"):
                    yield Static(title, classes="settings-section-title")
                    # markup=False: bodies render filesystem paths and env-derived
                    # values (executable path, user-data dir, …) that could
                    # contain Rich markup characters; treat them literally so a
                    # stray "[/]" can't raise MarkupError and crash the screen.
                    yield Static("", id=f"body-{section_id}", markup=False)
        yield Static("Loading settings…", id="settings-status", classes="status-line")

    def on_mount(self) -> None:
        self.load_settings()

    def action_refresh(self) -> None:
        self.query_one("#settings-status", Static).update("Refreshing…")
        self.load_settings()

    def load_settings(self) -> None:
        self._run_load(*self.begin_load())

    @work(thread=True, exclusive=True)
    def _run_load(self, app: App, generation: int) -> None:
        try:
            data = self._gather()
        except Exception as exc:
            self.marshal_load(
                app, generation, self._populate, None, f"Error loading settings: {exc}"
            )
            return
        self.marshal_load(app, generation, self._populate, data, None)

    def _gather(self) -> SettingsData:
        """Build the display-ready snapshot. Reads secrets only to derive flags.

        ``AppSettings()`` is constructed here (off the UI thread) because it
        writes to disk; a failure propagates to the worker's except and renders
        a friendly "unavailable" state rather than a traceback.
        """
        from config.settings import AppSettings

        settings = AppSettings()
        # Secrets are read only to compute display-safe flags; the values never
        # enter a widget.
        email = settings.linkedin_email
        has_password = bool(settings.linkedin_password)
        configured = settings.validate_credentials()

        browser = settings.get_browser_settings()
        automation = settings.get_automation_settings()

        used_today: int | None = None
        if self._db_manager is not None:
            try:
                used_today = self._db_manager.get_daily_connection_count(
                    date.today().isoformat()
                )
            except Exception:
                logger.debug("Could not read today's connection count", exc_info=True)
                used_today = None

        return SettingsData(
            credentials_status="Configured" if configured else "Not configured",
            email_masked=mask_email(email),
            password_state="Set" if has_password else "Not set",
            browser=browser,
            automation=automation,
            used_today=used_today,
            paths={
                "app_dir": str(settings.app_dir),
                "db_path": str(settings.db_path),
                "session_path": str(settings.session_path),
                "browser_data": str(settings.app_dir / "browser_data"),
            },
        )

    def _populate(self, data: SettingsData | None, error: str | None) -> None:
        status = self.query_one("#settings-status", Static)
        if error is not None:
            for section_id in ("credentials", "browser", "limits", "data"):
                self.query_one(f"#body-{section_id}", Static).update("")
            status.update("Settings unavailable.")
            return

        assert data is not None
        self._render_sections(data)
        status.update("Read-only view — values come from environment variables (.env).")

    def _render_sections(self, data: SettingsData) -> None:
        self.query_one("#body-credentials", Static).update(
            f"Status: {data.credentials_status}\n"
            f"Email: {data.email_masked}\n"
            f"Password: {data.password_state}"
        )

        b = data.browser
        viewport = b.get("viewport", {})
        self.query_one("#body-browser", Static).update(
            f"Channel: {b.get('channel') or 'bundled Chromium'}\n"
            f"Executable: {b.get('executable_path') or 'default'}\n"
            f"Headless Mode: {b.get('headless')}\n"
            f"Viewport: {viewport.get('width')}x{viewport.get('height')}\n"
            f"User Data Dir: {b.get('user_data_dir')}"
        )

        a = data.automation
        daily_limit = a.get("daily_connection_limit")
        used_today_line = ""
        if data.used_today is not None:
            used_today_line = f"Used Today: {data.used_today}/{daily_limit}\n"
        self.query_one("#body-limits", Static).update(
            f"Connection Delay: {a.get('connection_delay_min')}-"
            f"{a.get('connection_delay_max')} seconds\n"
            f"Daily Connection Limit: {daily_limit}\n"
            f"{used_today_line}"
            f"Inter-session Cooldown: {a.get('connection_cooldown')} seconds\n"
            f"Search Limit: {a.get('search_limit')}"
        )

        p = data.paths
        self.query_one("#body-data", Static).update(
            f"App Directory: {p['app_dir']}\n"
            f"Database: {p['db_path']}\n"
            f"Session Data: {p['session_path']}\n"
            f"Browser Data: {p['browser_data']}"
        )
