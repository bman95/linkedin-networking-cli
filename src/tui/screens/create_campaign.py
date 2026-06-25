"""Create Campaign screen — the first *write* flow (issue #24).

The first screen that mutates state. Field composition, validation, and the
display-name → stored-value mapping live in ``campaign_form`` and are shared with
the Edit screen; this screen owns only the persistence path:
``DatabaseManager.create_campaign`` (reused untouched) in a
``@work(thread=True, exclusive=True)`` worker under the established race
discipline (app captured on the UI thread; ``is_running``/``RuntimeError``/
``is_mounted`` guards). ``exclusive=True`` plus the ``_submitting``/``_created``
flags make a double ``ctrl+s`` a no-op so a campaign can't be created twice.
"""

from __future__ import annotations

from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from database.operations import DatabaseManager
from utils.logging import get_logger

from .base import BaseScreen
from .campaign_form import campaign_form_widgets, read_form

logger = get_logger(__name__)


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
        self._submitting = False
        self._created = False

    def compose_body(self) -> ComposeResult:
        yield from campaign_form_widgets()
        yield Static("", id="create-status", classes="status-line")

    def on_mount(self) -> None:
        if self._db_manager is None:
            self._set_status("Database unavailable. Cannot create a campaign.", "error")
            return
        # Keyboard-first: land in the first field so a user can start typing.
        self.query_one("#field-name").focus()

    def _set_status(self, message: str, kind: str = "") -> None:
        status = self.query_one("#create-status", Static)
        status.set_classes(f"status-line {('-' + kind) if kind else ''}".strip())
        status.update(message)

    # ── submit ────────────────────────────────────────────────────────────

    def action_create(self) -> None:
        if self._db_manager is None or self._submitting or self._created:
            return

        data, error = read_form(self)
        if error is not None:
            message, field_id = error
            self._set_status(message, "error")
            self.query_one(field_id).focus()
            return

        self._submitting = True
        self._set_status("Creating campaign…")
        # Capture the app on the UI thread; the deferred worker body must not
        # resolve self.app after a pop/quit (see docs/tui-migration.md §6).
        self._run_create(self.app, data)

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
            return

    def _done(self, name: Optional[str], cid: Optional[int], error: Optional[str]) -> None:
        if not self.is_mounted:
            return
        self._submitting = False
        if error is not None:
            self._set_status(f"Error creating campaign: {error}", "error")
            return
        # Terminal success state: lock the form (no re-submit) and invite esc,
        # where the home summary refreshes and reflects the new campaign.
        self._created = True
        for widget in self.query("#form-body Input, #form-body Select"):
            widget.disabled = True
        self._set_status(f"✓ Campaign '{name}' created (ID {cid}). Press esc to return.", "good")
