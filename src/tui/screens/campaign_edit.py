"""Edit Campaign screen (issue #24).

Reuses the shared campaign form (``campaign_form``) — same fields, validation, and
mapping as Create — but pre-fills it from the existing campaign and persists via
``DatabaseManager.update_campaign``. Reached from the campaign detail screen's
Edit action. The prefill read and the save write both run off the UI thread in
their own worker groups, under the established race discipline; on success the
form locks and ``esc`` returns to the detail screen, which reloads on resume.
"""

from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.widgets import Button, Static

from database.models import Campaign
from database.operations import DatabaseManager
from utils.logging import get_logger

from .campaign_form import CampaignFormScreen, campaign_form_widgets, fill_form, read_form

logger = get_logger(__name__)


class CampaignEditScreen(CampaignFormScreen):
    """Pre-filled campaign form that updates an existing campaign.

    Interaction design (owner rule, 2026-07-09; no accelerators, 2026-07-10):
    a visible, focusable "Save" button sits below the fields — tab + Enter is
    the only path to it.
    """

    BINDINGS = [
        # "back" (not app.pop_screen): the shared form owns esc so a dirty form
        # warns before discarding (see CampaignFormScreen.action_back).
        ("escape", "back", "Back"),
    ]

    SCREEN_TITLE = "Edit Campaign"

    STATUS_ID = "#edit-status"

    HINTS = (
        ("↑↓", "fields"),
        ("enter", "activate"),
        ("esc", "back"),
    )

    def __init__(self, db_manager: DatabaseManager | None, campaign_id: int) -> None:
        super().__init__()
        self._db_manager = db_manager
        self._campaign_id = campaign_id
        self._saving = False
        self._saved = False

    def compose_body(self) -> ComposeResult:
        yield from campaign_form_widgets()
        yield Button("Save", id="form-submit", classes="flat-button")
        yield Static("Loading campaign…", id="edit-status", classes="status-line")

    def on_mount(self) -> None:
        if self._db_manager is None:
            self._set_status("Database unavailable. Cannot edit a campaign.", "error")
            return
        self.load_campaign()

    # ── prefill load ──────────────────────────────────────────────────────

    def load_campaign(self) -> None:
        self._run_load(*self.begin_load())

    @work(thread=True, exclusive=True, group="load")
    def _run_load(self, app: App, generation: int) -> None:
        assert self._db_manager is not None  # guarded in on_mount
        try:
            campaign = self._db_manager.get_campaign(self._campaign_id)
        except Exception as exc:
            self.marshal_load(
                app, generation, self._prefill, None, f"Error loading campaign: {exc}"
            )
            return
        self.marshal_load(app, generation, self._prefill, campaign, None)

    def _prefill(self, campaign: Campaign | None, error: str | None) -> None:
        if error is not None:
            self._set_status(error, "error")
            return
        if campaign is None:
            self._set_status("Campaign not found.", "error")
            return
        fill_form(self, campaign)
        self._set_status("Edit the fields, then Save.")
        self.query_one("#field-name").focus()
        # The prefilled values are the pristine state for the esc guard.
        self.mark_clean()

    # ── save ──────────────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "form-submit":
            event.stop()
            self.action_save()

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
            self.marshal(app, self._done, None, str(exc))
            return
        name = campaign.name if campaign is not None else None
        self.marshal(app, self._done, name, None if campaign is not None else "Campaign not found.")

    def _done(self, name: str | None, error: str | None) -> None:
        self._saving = False
        if error is not None:
            self._set_status(f"Error saving campaign: {error}", "error")
            return
        self._saved = True
        for widget in self.query(
            "#form-body Input, #form-body Select, #form-body OptionList, #form-submit"
        ):
            widget.disabled = True
        self._set_status(f"✓ Campaign '{name}' updated. Press esc to return.", "good")
