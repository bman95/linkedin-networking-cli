"""Edit Campaign screen (issue #24).

Reuses the shared campaign form (``campaign_form``) — same fields, validation, and
mapping as Create — but pre-fills it from the existing campaign and persists via
``DatabaseManager.update_campaign``. Reached from the campaign detail screen's
``e`` action. The prefill read and the save write both run off the UI thread in
their own worker groups, under the established race discipline; on success the
form locks and ``esc`` returns to the detail screen, which reloads on resume.
"""

from __future__ import annotations

from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from database.models import Campaign
from database.operations import DatabaseManager
from utils.logging import get_logger

from .base import BaseScreen
from .campaign_form import campaign_form_widgets, fill_form, read_form

logger = get_logger(__name__)


class CampaignEditScreen(BaseScreen):
    """Pre-filled campaign form that updates an existing campaign."""

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        Binding("ctrl+s", "save", "Save", priority=True),
        ("q", "app.quit", "Quit"),
    ]

    SCREEN_TITLE = "Edit Campaign"

    HINTS = (
        ("ctrl+s", "save"),
        ("esc", "cancel"),
        ("q", "quit"),
        ("ctrl+p", "commands"),
    )

    def __init__(self, db_manager: Optional[DatabaseManager], campaign_id: int) -> None:
        super().__init__()
        self._db_manager = db_manager
        self._campaign_id = campaign_id
        self._load_generation = 0
        self._saving = False
        self._saved = False

    def compose_body(self) -> ComposeResult:
        yield from campaign_form_widgets()
        yield Static("Loading campaign…", id="edit-status", classes="status-line")

    def on_mount(self) -> None:
        if self._db_manager is None:
            self._set_status("Database unavailable. Cannot edit a campaign.", "error")
            return
        self.load_campaign()

    def _set_status(self, message: str, kind: str = "") -> None:
        status = self.query_one("#edit-status", Static)
        status.set_classes(f"status-line {('-' + kind) if kind else ''}".strip())
        status.update(message)

    # ── prefill load ──────────────────────────────────────────────────────

    def load_campaign(self) -> None:
        self._load_generation += 1
        self._run_load(self.app, self._load_generation)

    @work(thread=True, exclusive=True, group="load")
    def _run_load(self, app: App, generation: int) -> None:
        assert self._db_manager is not None  # guarded in on_mount
        try:
            campaign = self._db_manager.get_campaign(self._campaign_id)
        except Exception as exc:
            self._marshal_load(app, generation, None, f"Error loading campaign: {exc}")
            return
        self._marshal_load(app, generation, campaign, None)

    def _marshal_load(
        self, app: App, generation: int, campaign: Optional[Campaign], error: Optional[str]
    ) -> None:
        if not app.is_running:
            return
        try:
            app.call_from_thread(self._prefill, generation, campaign, error)
        except RuntimeError:
            return

    def _prefill(
        self, generation: int, campaign: Optional[Campaign], error: Optional[str]
    ) -> None:
        if not self.is_mounted:
            return
        if generation != self._load_generation:
            return
        if error is not None:
            self._set_status(error, "error")
            return
        if campaign is None:
            self._set_status("Campaign not found.", "error")
            return
        fill_form(self, campaign)
        self._set_status("Edit the fields, then ctrl+s to save.")
        self.query_one("#field-name").focus()

    # ── save ──────────────────────────────────────────────────────────────

    def action_save(self) -> None:
        if self._db_manager is None or self._saving or self._saved:
            return
        data, error = read_form(self)
        if error is not None:
            message, field_id = error
            self._set_status(message, "error")
            self.query_one(field_id).focus()
            return
        self._saving = True
        self._set_status("Saving…")
        self._run_save(self.app, data)

    @work(thread=True, exclusive=True, group="save")
    def _run_save(self, app: App, updates: dict) -> None:
        assert self._db_manager is not None  # guarded in action_save
        try:
            campaign = self._db_manager.update_campaign(self._campaign_id, updates)
        except Exception as exc:
            self._marshal_done(app, None, str(exc))
            return
        name = campaign.name if campaign is not None else None
        self._marshal_done(app, name, None if campaign is not None else "Campaign not found.")

    def _marshal_done(self, app: App, name: Optional[str], error: Optional[str]) -> None:
        if not app.is_running:
            return
        try:
            app.call_from_thread(self._done, name, error)
        except RuntimeError:
            return

    def _done(self, name: Optional[str], error: Optional[str]) -> None:
        if not self.is_mounted:
            return
        self._saving = False
        if error is not None:
            self._set_status(f"Error saving campaign: {error}", "error")
            return
        self._saved = True
        for widget in self.query("#form-body Input, #form-body Select"):
            widget.disabled = True
        self._set_status(f"✓ Campaign '{name}' updated. Press esc to return.", "good")
