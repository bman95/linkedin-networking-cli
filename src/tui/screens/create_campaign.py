"""Create Campaign screen — the first *write* flow (issue #24).

The read-only screens (Dashboard, Campaigns, Settings) established the shell, the
theme, and the threaded-worker data-flow conventions. This is the first screen
that *mutates* state: it gathers the same fields the classic InquirerPy
``create_campaign`` flow does, validates them with the same rules, and persists
through ``DatabaseManager.create_campaign`` — reused untouched.

Two deliberate divergences from the classic flow, both to keep this first write
flow **browser-free** (online targeting belongs to the deferred automation
slice, ``docs/tui-migration.md`` §3/§4):

- The classic "🔎 Search location online (requires login)" option is dropped — it
  drives Playwright + a LinkedIn login. The static location list (and so the
  common cases) is fully supported; a custom-geoUrn entry path is deferred.

A write is blocking SQLite I/O, so it runs in a ``@work(thread=True,
exclusive=True)`` worker under the same race discipline as the read screens
(capture ``self.app`` on the UI thread; guard ``app.is_running`` + ``RuntimeError``
and ``self.is_mounted``). ``exclusive=True`` plus a ``_submitting`` guard make a
double ``ctrl+s`` a no-op so a campaign can't be created twice.
"""

from __future__ import annotations

from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Input, Label, Select, Static

from automation.linkedin_mappings import (
    get_industry_display_names,
    get_industry_id,
    get_location_display_names,
    get_location_urn,
    get_network_display_names,
    get_network_value,
)
from database.operations import DatabaseManager
from utils.logging import get_logger

from .base import BaseScreen

logger = get_logger(__name__)

# Defaults mirror the classic InquirerPy flow so wording/behaviour stay at parity.
DEFAULT_NETWORK = "1st + 2nd degree connections"
DEFAULT_MESSAGE = "Hi {name}, I'd like to connect with you!"
ANY = "Any"


class CreateCampaignScreen(BaseScreen):
    """Form screen that creates a campaign, then writes it in a thread worker."""

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        # priority so ctrl+s fires even while an Input/Select holds focus.
        Binding("ctrl+s", "create", "Create", priority=True),
        ("q", "app.quit", "Quit"),
    ]

    SCREEN_TITLE = "Create Campaign"

    HINTS = (
        ("ctrl+s", "create"),
        ("esc", "cancel"),
        ("q", "quit"),
        ("ctrl+p", "commands"),
    )

    def __init__(self, db_manager: Optional[DatabaseManager]) -> None:
        super().__init__()
        self._db_manager = db_manager
        # True while a write is in flight; blocks a second submit. Stays True
        # after success so the terminal "created" state can't be re-submitted.
        self._submitting = False
        self._created = False

    def compose_body(self) -> ComposeResult:
        with VerticalScroll(id="create-body"):
            yield Static("CAMPAIGN", classes="eyebrow")
            yield Label("Name", classes="field-label")
            yield Input(placeholder="e.g. Senior Backend Engineers — SF", id="field-name")
            yield Label("Description", classes="field-label")
            yield Input(placeholder="Optional", id="field-description")
            yield Label("Keywords", classes="field-label")
            yield Input(placeholder="e.g. software engineer (optional)", id="field-keywords")

            yield Static("TARGETING", classes="eyebrow")
            yield Label("Location", classes="field-label")
            yield Select(
                [(n, n) for n in get_location_display_names()],
                value=ANY,
                allow_blank=False,
                id="field-location",
            )
            yield Label("Connection degree", classes="field-label")
            yield Select(
                [(n, n) for n in get_network_display_names()],
                value=DEFAULT_NETWORK,
                allow_blank=False,
                id="field-network",
            )
            yield Label("Industry", classes="field-label")
            yield Select(
                [(n, n) for n in get_industry_display_names()],
                value=ANY,
                allow_blank=False,
                id="field-industry",
            )

            yield Static("LIMITS & MESSAGE", classes="eyebrow")
            yield Label("Daily connection limit", classes="field-label")
            yield Input(value="20", type="integer", id="field-daily")
            yield Label("Connection message template", classes="field-label")
            yield Input(value=DEFAULT_MESSAGE, id="field-message")
        yield Static("", id="create-status", classes="status-line")

    def on_mount(self) -> None:
        if self._db_manager is None:
            self._set_status("Database unavailable. Cannot create a campaign.", "error")
            return
        # Keyboard-first: land in the first field so a user can start typing.
        self.query_one("#field-name", Input).focus()

    # ── status helper ─────────────────────────────────────────────────────

    def _set_status(self, message: str, kind: str = "") -> None:
        status = self.query_one("#create-status", Static)
        status.set_classes(f"status-line {('-' + kind) if kind else ''}".strip())
        status.update(message)

    # ── submit / validation ───────────────────────────────────────────────

    def action_create(self) -> None:
        if self._db_manager is None or self._submitting or self._created:
            return

        name = self.query_one("#field-name", Input).value.strip()
        if not name:
            self._set_status("Campaign name cannot be empty.", "error")
            self.query_one("#field-name", Input).focus()
            return

        daily_raw = self.query_one("#field-daily", Input).value.strip()
        try:
            daily_limit = int(daily_raw)
        except ValueError:
            daily_limit = 0
        if not 1 <= daily_limit <= 100:
            self._set_status("Daily limit must be a number between 1 and 100.", "error")
            self.query_one("#field-daily", Input).focus()
            return

        message_template = self.query_one("#field-message", Input).value
        if "{name}" not in message_template:
            self._set_status("Message must contain the {name} placeholder.", "error")
            self.query_one("#field-message", Input).focus()
            return

        description = self.query_one("#field-description", Input).value.strip()
        keywords = self.query_one("#field-keywords", Input).value.strip()
        location_display = self.query_one("#field-location", Select).value
        network_display = self.query_one("#field-network", Select).value
        industry_display = self.query_one("#field-industry", Select).value

        # Map display names to stored values, mirroring the classic CLI exactly:
        # "Any" location/industry persist as None; geo_urn/industry_ids come from
        # the lookup tables; network always resolves to its ["F","S"]-style value.
        geo_urn = get_location_urn(location_display) if location_display != ANY else None
        industry_id = get_industry_id(industry_display) if industry_display != ANY else None

        campaign_data = {
            "name": name,
            "description": description or None,
            "keywords": keywords or None,
            "geo_urn": geo_urn or None,
            "location_display": location_display if location_display != ANY else None,
            "network": get_network_value(network_display),
            "network_display": network_display,
            "industry_ids": industry_id or None,
            "industry_display": industry_display if industry_display != ANY else None,
            "daily_limit": daily_limit,
            "message_template": message_template,
        }

        self._submitting = True
        self._set_status("Creating campaign…")
        # Capture the app on the UI thread; the deferred worker body must not
        # resolve self.app after a pop/quit (see docs/tui-migration.md §6).
        self._run_create(self.app, campaign_data)

    @work(thread=True, exclusive=True)
    def _run_create(self, app: App, campaign_data: dict) -> None:
        """Persist the campaign off the event loop, then report back."""
        # action_create only starts this worker once db_manager is non-None.
        assert self._db_manager is not None
        try:
            campaign = self._db_manager.create_campaign(campaign_data)
        except Exception as exc:  # surface in-place, never crash the UI
            self._marshal_done(app, None, None, str(exc))
            return
        self._marshal_done(app, campaign.name, campaign.id, None)

    def _marshal_done(
        self, app: App, name: Optional[str], cid: Optional[int], error: Optional[str]
    ) -> None:
        """Hand the result back to the UI thread, but only while the app runs."""
        if not app.is_running:
            return
        try:
            app.call_from_thread(self._done, name, cid, error)
        except RuntimeError:
            # App stopped between the is_running check and the call; ignore.
            return

    def _done(self, name: Optional[str], cid: Optional[int], error: Optional[str]) -> None:
        # The worker can't be interrupted, so it may call this after a pop.
        if not self.is_mounted:
            return
        self._submitting = False
        if error is not None:
            self._set_status(f"Error creating campaign: {error}", "error")
            return
        # Terminal success state: lock the form (no re-submit) and invite esc,
        # where the home summary refreshes and reflects the new campaign.
        self._created = True
        for widget in self.query("#create-body Input, #create-body Select"):
            widget.disabled = True
        self._set_status(f"✓ Campaign '{name}' created (ID {cid}). Press esc to return.", "good")
