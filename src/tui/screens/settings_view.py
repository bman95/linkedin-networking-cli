"""Settings screen (issue #24).

Shows the live configuration — credentials status, browser, rate limiting, and
data locations. The **Rate Limiting** section is editable: its values persist
to ``config.json`` via :meth:`AppSettings.save_overrides` and override the
``.env`` values from the next run on (owner rule, 2026-07-11: settings must be
changeable in the app, not only via ``.env``). Everything else stays
informational: secrets are never displayed (email masked, password shown only
as Set / Not set) and credentials/browser identity remain env-only.

``AppSettings()`` creates the app directory on construction (a filesystem
write), and the quota read is blocking SQLite, so the load — and the save —
run in threaded workers with the same race discipline as the other screens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Button, Input, Label, Static

from cli.helpers import mask_api_key, mask_email
from database.operations import DatabaseManager
from utils.logging import get_logger

from .base import BaseScreen

logger = get_logger(__name__)

# The editable Rate Limiting fields: (AppSettings override key, input id,
# label). Keys match ``config.settings.EDITABLE_SETTINGS``; labels keep the
# classic CLI vocabulary (issue #46: the env daily value is only the fallback
# when a campaign sets no limit, and is labelled as such).
EDITABLE_FIELDS = (
    ("connection_delay_min", "field-delay-min", "Connection Delay Min (seconds)"),
    ("connection_delay_max", "field-delay-max", "Connection Delay Max (seconds)"),
    (
        "daily_connection_limit",
        "field-daily-fallback",
        "Default Daily Limit (fallback when a campaign sets none)",
    ),
    ("connection_cooldown", "field-cooldown", "Inter-session Cooldown (seconds)"),
    ("search_limit", "field-search-limit", "Search Limit"),
)


@dataclass(frozen=True)
class SettingsData:
    """Display-ready settings, computed off the UI thread. No raw secrets."""

    credentials_status: str
    email_masked: str
    password_state: str
    browser: dict
    automation: dict
    used_today: int | None
    used_week: int | None
    paths: dict
    llm: dict
    llm_api_key_state: str


class SettingsScreen(BaseScreen):
    """Effective configuration, with an editable Rate Limiting section.

    Interaction design (owner rule, 2026-07-09; no accelerators, 2026-07-10):
    the rate-limit inputs, Save and Refresh are all plain focusable widgets —
    tab + Enter is the only path to them.
    """

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
    ]

    SCREEN_TITLE = "Settings"

    HINTS = (
        ("tab", "fields"),
        ("enter", "activate"),
        ("esc", "back"),
    )

    def __init__(self, db_manager: DatabaseManager | None) -> None:
        super().__init__()
        self._db_manager = db_manager
        self._save_in_flight = False

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
                    if section_id == "limits":
                        yield from self._compose_limits()
                    # markup=False: bodies render filesystem paths and env-derived
                    # values (executable path, user-data dir, …) that could
                    # contain Rich markup characters; treat them literally so a
                    # stray "[/]" can't raise MarkupError and crash the screen.
                    yield Static("", id=f"body-{section_id}", markup=False)
            yield Button("Refresh", id="settings-refresh", classes="flat-button")
        yield Static("Loading settings…", id="settings-status", classes="status-line")

    def _compose_limits(self) -> ComposeResult:
        """The editable Rate Limiting fields plus their Save button.

        The trailing ``#body-limits`` Static (yielded by the section loop)
        stays for the usage counters, which remain read-only.
        """
        for _key, input_id, label in EDITABLE_FIELDS:
            yield Label(label, classes="field-label")
            yield Input(type="integer", id=input_id)
        yield Button("Save", id="settings-save", classes="flat-button")

    def on_mount(self) -> None:
        self.load_settings()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-refresh":
            event.stop()
            self.action_refresh()
        elif event.button.id == "settings-save":
            event.stop()
            self.save_settings()

    def action_refresh(self) -> None:
        self._set_status("Refreshing…")
        self.load_settings()

    def load_settings(self) -> None:
        self._run_load(*self.begin_load())

    def _set_status(self, message: str, kind: str = "") -> None:
        status = self.query_one("#settings-status", Static)
        status.set_classes(f"status-line {('-' + kind) if kind else ''}".strip())
        # Text() renders literally: messages can carry raw exception text.
        status.update(Text(message))

    # ── load ──────────────────────────────────────────────────────────────

    @work(thread=True, exclusive=True, group="settings-load")
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
        if error is not None:
            for section_id in ("credentials", "browser", "limits", "data", "ai_assist"):
                self.query_one(f"#body-{section_id}", Static).update("")
            self._set_status("Settings unavailable.", "error")
            return

        assert data is not None
        self._render_sections(data)
        self._set_status(
            "Rate limits are editable — Save persists them to config.json "
            "(overrides .env). Other sections mirror .env."
        )

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

        # Editable fields show the *effective* values (config.json override,
        # else env, else default) so what you see is what the next run uses.
        a = data.automation
        for key, input_id, _label in EDITABLE_FIELDS:
            self.query_one(f"#{input_id}", Input).value = str(a.get(key, ""))

        # The per-campaign daily_limit is the enforced daily cap; the value
        # above is only its fallback, so today's usage is never shown against
        # it (issue #46). The weekly count is informational — there is no
        # configured weekly budget anymore.
        usage_lines = []
        if data.used_today is not None:
            usage_lines.append(f"Used Today: {data.used_today}")
        if data.used_week is not None:
            usage_lines.append(f"Used This Week: {data.used_week}")
        self.query_one("#body-limits", Static).update("\n".join(usage_lines))

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

    # ── save ──────────────────────────────────────────────────────────────

    def save_settings(self) -> None:
        if self._save_in_flight:
            return
        values, error = self._read_fields()
        if error is not None:
            message, selector = error
            self._set_status(message, "error")
            self.query_one(selector, Input).focus()
            return
        assert values is not None  # error is None ⇒ values is present
        self._save_in_flight = True
        self._set_status("Saving…")
        self._run_save(self.app, values)

    def _read_fields(self) -> tuple[dict | None, tuple[str, str] | None]:
        """Validate the editable fields into an override dict.

        Returns ``(values, None)`` on success or ``(None, (message, selector))``
        on the first failure so the caller can show it and focus the field.
        """
        values: dict[str, int] = {}
        for key, input_id, label in EDITABLE_FIELDS:
            raw = self.query_one(f"#{input_id}", Input).value.strip()
            try:
                values[key] = int(raw)
            except ValueError:
                return None, (f"{label} must be a whole number.", f"#{input_id}")

        checks = (
            (values["connection_delay_min"] < 0,
             "Connection Delay Min cannot be negative.", "#field-delay-min"),
            (values["connection_delay_max"] < values["connection_delay_min"],
             "Connection Delay Max must be ≥ the minimum.", "#field-delay-max"),
            (values["daily_connection_limit"] < 1,
             "Default Daily Limit must be at least 1.", "#field-daily-fallback"),
            (values["connection_cooldown"] < 0,
             "Inter-session Cooldown cannot be negative.", "#field-cooldown"),
            (values["search_limit"] < 1,
             "Search Limit must be at least 1.", "#field-search-limit"),
        )
        for failed, message, selector in checks:
            if failed:
                return None, (message, selector)
        return values, None

    @work(thread=True, exclusive=True, group="settings-save")
    def _run_save(self, app: App, values: dict) -> None:
        try:
            from config.settings import AppSettings

            AppSettings().save_overrides(values)
        except Exception as exc:
            logger.debug("Saving settings failed", exc_info=True)
            self.marshal(app, self._save_done, f"Could not save settings: {exc}")
            return
        self.marshal(app, self._save_done, None)

    def _save_done(self, error: str | None) -> None:
        self._save_in_flight = False
        if error is not None:
            self._set_status(error, "error")
            return
        self._set_status(
            "Saved to config.json — the new values apply from the next run.",
            "good",
        )
