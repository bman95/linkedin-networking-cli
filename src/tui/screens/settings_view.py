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
from textual.widgets import Button, Static

from cli.helpers import mask_api_key, mask_email
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
    weekly_limit: int
    used_today: int | None
    used_week: int | None
    paths: dict
    llm: dict
    llm_api_key_state: str


class SettingsScreen(BaseScreen):
    """Read-only view of the application's effective configuration.

    Interaction design (owner rule, 2026-07-09): Refresh is a visible, focusable
    button below the sections — reachable with tab + Enter — with ``r`` kept as
    an optional accelerator, never the only path.
    """

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
                ("ai_assist", "AI Assist"),
            ):
                with Container(classes="settings-section", id=f"section-{section_id}"):
                    yield Static(title, classes="settings-section-title")
                    # markup=False: bodies render filesystem paths and env-derived
                    # values (executable path, user-data dir, …) that could
                    # contain Rich markup characters; treat them literally so a
                    # stray "[/]" can't raise MarkupError and crash the screen.
                    yield Static("", id=f"body-{section_id}", markup=False)
            yield Button("Refresh", id="settings-refresh", classes="flat-button")
        yield Static("Loading settings…", id="settings-status", classes="status-line")

    def on_mount(self) -> None:
        self.load_settings()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-refresh":
            event.stop()
            self.action_refresh()

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
        llm = settings.get_llm_settings()

        used_today: int | None = None
        used_week: int | None = None
        if self._db_manager is not None:
            try:
                used_today = self._db_manager.get_daily_connection_count(
                    date.today().isoformat()
                )
                used_week = self._db_manager.get_weekly_connection_count()
            except Exception:
                logger.debug("Could not read connection counts", exc_info=True)
                used_today = None
                used_week = None

        return SettingsData(
            credentials_status="Configured" if configured else "Not configured",
            email_masked=mask_email(email),
            password_state="Set" if has_password else "Not set",
            browser=browser,
            automation=automation,
            weekly_limit=settings.weekly_invitation_limit,
            used_today=used_today,
            used_week=used_week,
            paths={
                "app_dir": str(settings.app_dir),
                "db_path": str(settings.db_path),
                "session_path": str(settings.session_path),
                "browser_data": str(settings.app_dir / "browser_data"),
            },
            llm=llm,
            llm_api_key_state=mask_api_key(llm.get("api_key")),
        )

    def _populate(self, data: SettingsData | None, error: str | None) -> None:
        status = self.query_one("#settings-status", Static)
        if error is not None:
            for section_id in ("credentials", "browser", "limits", "data", "ai_assist"):
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
        # The per-campaign daily_limit is the enforced daily cap; the env value
        # is only its fallback, so it is labelled as such and today's usage is
        # never shown against it (issue #46). The weekly budget is LinkedIn's
        # actually-binding constraint (DESIGN-PROPOSALS.md §4).
        usage_lines = ""
        if data.used_today is not None:
            usage_lines = f"Used Today: {data.used_today}\n"
        if data.used_week is not None:
            usage_lines += f"Used This Week: {data.used_week}/{data.weekly_limit}\n"
        self.query_one("#body-limits", Static).update(
            f"Connection Delay: {a.get('connection_delay_min')}-"
            f"{a.get('connection_delay_max')} seconds\n"
            f"Default Daily Limit (fallback when a campaign sets none): "
            f"{a.get('daily_connection_limit')}\n"
            f"Weekly Invitation Limit: {data.weekly_limit}\n"
            f"{usage_lines}"
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

        llm = data.llm
        self.query_one("#body-ai_assist", Static).update(
            f"Mode: {llm.get('mode')}\n"
            f"Base URL: {llm.get('base_url')}\n"
            f"Model: {llm.get('model') or 'not set (local falls back to a RAM-based default)'}\n"
            f"API Key: {data.llm_api_key_state}"
        )
