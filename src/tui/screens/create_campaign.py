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

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Static

from database.operations import DatabaseManager
from utils.logging import get_logger

from .campaign_ai_assist import CampaignAIAssistPanel
from .campaign_form import (
    CampaignFormScreen,
    campaign_form_widgets,
    fill_form_from_extraction,
    read_form,
)

logger = get_logger(__name__)


class CreateCampaignScreen(CampaignFormScreen):
    """Form screen that creates a campaign, then writes it in a thread worker.

    Interaction design (owner rule, 2026-07-09): a visible, focusable "Create"
    button sits below the fields — reachable with tab + Enter — with ``ctrl+s``
    kept as an optional accelerator, never the only path.
    """

    BINDINGS = [
        # "back" (not app.pop_screen): the shared form owns esc so a dirty form
        # warns before discarding (see CampaignFormScreen.action_back).
        ("escape", "back", "Back"),
        # priority so ctrl+s fires even while an Input/Select holds focus.
        Binding("ctrl+s", "create", "Create", priority=True),
        ("q", "app.quit", "Quit"),
    ]

    SCREEN_TITLE = "Create Campaign"

    STATUS_ID = "#create-status"

    HINTS = (
        ("ctrl+s", "create"),
        ("esc", "cancel"),
        ("q", "quit"),
        ("ctrl+p", "commands"),
    )

    def __init__(self, db_manager: DatabaseManager | None) -> None:
        super().__init__()
        self._db_manager = db_manager
        self._submitting = False
        self._created = False

    def compose_body(self) -> ComposeResult:
        yield CampaignAIAssistPanel(id="ai-assist-panel")
        yield from campaign_form_widgets()
        yield Button("Create", id="form-submit", classes="flat-button")
        yield Static("", id="create-status", classes="status-line")

    def on_mount(self) -> None:
        if self._db_manager is None:
            self._set_status("Database unavailable. Cannot create a campaign.", "error")
            return
        # Keyboard-first: land in the first field so a user can start typing.
        self.query_one("#field-name").focus()
        # The freshly composed defaults are the pristine state for the
        # dirty-form esc guard.
        self.mark_clean()

    # ── AI assist ─────────────────────────────────────────────────────────

    # Textual's handler-name derivation collapses "AIAssist" to "aiassist"
    # (no underscore before "Assist") — this name is NOT a typo of the class.
    def on_campaign_aiassist_panel_extracted(
        self, event: CampaignAIAssistPanel.Extracted
    ) -> None:
        flagged = fill_form_from_extraction(self, event.result)
        filled = 8 - len(flagged)
        if flagged:
            self._set_status(
                f"Filled {filled} of 8 fields from your description — "
                "review the highlighted ones.",
                "warn",
            )
        else:
            self._set_status(f"Filled {filled} of 8 fields from your description.", "good")
        self.query_one(flagged[0] if flagged else "#field-name").focus()
        # Deliberately no mark_clean(): an AI prefill is unsaved work, so esc
        # still warns before discarding it.

    def action_back(self) -> None:
        panel = self.query_one(CampaignAIAssistPanel)
        if panel.handle_escape():
            return
        super().action_back()

    # ── submit ────────────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "form-submit":
            event.stop()
            self.action_create()

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
            self.marshal(app, self._done, None, None, str(exc))
            return
        self.marshal(app, self._done, campaign.name, campaign.id, None)

    def _done(self, name: str | None, cid: int | None, error: str | None) -> None:
        self._submitting = False
        if error is not None:
            self._set_status(f"Error creating campaign: {error}", "error")
            return
        # Terminal success state: lock the form (no re-submit) and invite esc,
        # where the home summary refreshes and reflects the new campaign.
        self._created = True
        for widget in self.query("#form-body Input, #form-body Select, #form-submit"):
            widget.disabled = True
        self._set_status(f"✓ Campaign '{name}' created (ID {cid}). Press esc to return.", "good")
