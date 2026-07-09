"""Campaign detail screen (issue #24).

Opened by activating a row on the Campaigns list. Shows one campaign's full
configuration and performance in the shared elevated-panel layout, and offers the
campaign-management actions the classic CLI exposes under "Manage Campaigns":
toggle active (``a``), edit (``e``), delete (``d``). The read is blocking SQLite,
so it runs in the same threaded-worker discipline as the other data screens; the
mutating actions (toggle/delete) likewise run off the UI thread.

Edit is delegated to ``CampaignEditScreen`` (imported lazily to keep the package
bootstrap free of eager screen imports). Delete asks for confirmation first — an
irreversible action gets an explicit y/n gate, never a single-keystroke wipe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Static

from cli.helpers import acceptance_rate, contacts_csv_filename, write_contacts_csv
from database.operations import DatabaseManager
from utils.logging import get_logger

from .base import BaseScreen

logger = get_logger(__name__)


def export_contacts_csv(campaign_name: str, contacts) -> Path:
    """Write a campaign's contacts to a timestamped CSV under the exports dir.

    The field list and writing logic are shared with the classic CLI
    (``cli.helpers``); only the destination policy (a fixed exports directory,
    since the TUI has no path prompt) lives here.
    """
    export_dir = Path.home() / ".linkedin-networking-cli" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / contacts_csv_filename(campaign_name)
    write_contacts_csv(path, contacts)
    return path


@dataclass(frozen=True)
class CampaignDetail:
    """Display-ready snapshot of one campaign, computed off the UI thread."""

    id: int
    name: str
    active: bool
    overview: str
    targeting: str
    message: str
    performance: str


class CampaignDetailScreen(BaseScreen):
    """Full view of a single campaign, with manage actions."""

    BINDINGS = [
        ("escape", "back", "Back"),
        ("r", "refresh", "Refresh"),
        Binding("e", "edit", "Edit"),
        Binding("a", "toggle_active", "Toggle active"),
        Binding("x", "export", "Export CSV"),
        Binding("d", "delete", "Delete"),
        ("q", "app.quit", "Quit"),
    ]

    SCREEN_TITLE = "Campaign"

    HINTS = (
        ("e", "edit"),
        ("a", "active"),
        ("x", "export"),
        ("d", "delete"),
        ("esc", "back"),
        ("q", "quit"),
    )

    def __init__(self, db_manager: DatabaseManager | None, campaign_id: int) -> None:
        super().__init__()
        self._db_manager = db_manager
        self._campaign_id = campaign_id
        # Cached active flag so the toggle action knows the current state without
        # re-reading; refreshed on every load.
        self._active: bool | None = None
        self._busy = False  # a mutating action is in flight
        self._confirming_delete = False

    def compose_body(self) -> ComposeResult:
        with Container(id="detail-body"):
            for section_id, title in (
                ("overview", "Overview"),
                ("targeting", "Targeting"),
                ("message", "Message Template"),
                ("performance", "Performance"),
            ):
                with Container(classes="settings-section", id=f"detail-section-{section_id}"):
                    yield Static(title, classes="settings-section-title")
                    # markup=False: campaign name / message template are
                    # user-controlled and may contain Rich markup characters.
                    yield Static("", id=f"detail-body-{section_id}", markup=False)
        yield Static("Loading campaign…", id="detail-status", classes="status-line")

    def on_mount(self) -> None:
        self.load_detail()

    def on_screen_resume(self) -> None:
        # Returning from the edit screen: re-read so edits are reflected.
        if self.is_mounted:
            self.load_detail()

    def action_refresh(self) -> None:
        self._confirming_delete = False
        self._set_status("Refreshing…")
        self.load_detail()

    # ── load ──────────────────────────────────────────────────────────────

    def load_detail(self) -> None:
        self._run_load(*self.begin_load())

    @work(thread=True, exclusive=True)
    def _run_load(self, app: App, generation: int) -> None:
        if self._db_manager is None:
            self.marshal_load(app, generation, self._populate, None, "Database unavailable.")
            return
        try:
            detail = self._gather()
        except Exception as exc:
            self.marshal_load(
                app, generation, self._populate, None, f"Error loading campaign: {exc}"
            )
            return
        if detail is None:
            self.marshal_load(app, generation, self._populate, None, "Campaign not found.")
            return
        self.marshal_load(app, generation, self._populate, detail, None)

    def _gather(self) -> CampaignDetail | None:
        assert self._db_manager is not None  # guarded in _run_load
        c = self._db_manager.get_campaign(self._campaign_id)
        if c is None:
            return None
        sent, accepted, pending = c.total_sent, c.total_accepted, c.total_pending
        rate = acceptance_rate(sent, accepted)
        overview = (
            f"Name: {c.name}\n"
            f"Status: {'Active' if c.active else 'Inactive'}\n"
            f"Daily Limit: {c.daily_limit}\n"
            f"Description: {c.description or 'None'}"
        )
        targeting = (
            f"Keywords: {c.keywords or 'Any'}\n"
            f"Location: {c.location_display or 'Any'}\n"
            f"Connection Degree: {c.network_display or 'Any'}\n"
            f"Industry: {c.industry_display or 'Any'}"
        )
        performance = (
            f"Sent: {sent}\n"
            f"Accepted: {accepted}\n"
            f"Pending: {pending}\n"
            f"Acceptance Rate: {rate:.1f}%"
        )
        return CampaignDetail(
            id=self._campaign_id,
            name=c.name,
            active=c.active,
            overview=overview,
            targeting=targeting,
            message=c.message_template or "None",
            performance=performance,
        )

    def _populate(self, detail: CampaignDetail | None, error: str | None) -> None:
        if error is not None:
            self._active = None
            self._set_status(error, "error")
            return
        assert detail is not None
        self._active = detail.active
        self.query_one("#detail-body-overview", Static).update(detail.overview)
        self.query_one("#detail-body-targeting", Static).update(detail.targeting)
        self.query_one("#detail-body-message", Static).update(detail.message)
        self.query_one("#detail-body-performance", Static).update(detail.performance)
        # The hint bar below already lists the actions; don't repeat them here.
        self._set_status("Read-only view.")

    # ── actions ───────────────────────────────────────────────────────────

    def action_back(self) -> None:
        """``esc``: cancel an armed delete first; only then leave the screen.

        The delete prompt promises "esc to cancel", so esc while confirming must
        cancel the confirmation — not pop the whole screen.
        """
        if self._confirming_delete:
            self._confirming_delete = False
            self._set_status("Delete cancelled.")
            return
        self.app.pop_screen()

    def action_edit(self) -> None:
        self._confirming_delete = False
        if self._db_manager is None or self._busy:
            return
        from .campaign_edit import CampaignEditScreen

        self.app.push_screen(CampaignEditScreen(self._db_manager, self._campaign_id))

    def action_toggle_active(self) -> None:
        self._confirming_delete = False
        if self._db_manager is None or self._busy or self._active is None:
            return
        self._busy = True
        new_state = not self._active
        self._set_status("Activating…" if new_state else "Deactivating…")
        self._run_update(self.app, {"active": new_state})

    def action_export(self) -> None:
        self._confirming_delete = False
        if self._db_manager is None or self._busy:
            return
        self._busy = True
        self._set_status("Exporting contacts…")
        self._run_export(self.app)

    def action_delete(self) -> None:
        if self._db_manager is None or self._busy:
            return
        # First press arms the confirmation; second confirms. A destructive
        # action never fires on a single keystroke.
        if not self._confirming_delete:
            self._confirming_delete = True
            self._set_status(
                "Delete this campaign and all its contacts? "
                "Press d again to confirm, esc to cancel.",
                "warn",
            )
            return
        self._confirming_delete = False
        self._busy = True
        self._set_status("Deleting…", "warn")
        self._run_delete(self.app)

    @work(thread=True, exclusive=True)
    def _run_update(self, app: App, updates: dict) -> None:
        assert self._db_manager is not None  # guarded in action_toggle_active
        try:
            self._db_manager.update_campaign(self._campaign_id, updates)
        except Exception as exc:
            self.marshal(app, self._after_action, f"Error updating campaign: {exc}", False)
            return
        self.marshal(app, self._after_action, None, True)

    @work(thread=True, exclusive=True, group="export")
    def _run_export(self, app: App) -> None:
        assert self._db_manager is not None  # guarded in action_export
        try:
            campaign = self._db_manager.get_campaign(self._campaign_id)
            contacts = self._db_manager.get_contacts(self._campaign_id)
        except Exception as exc:
            self.marshal(app, self._after_export, f"Error loading contacts: {exc}", "error")
            return
        if campaign is None:
            self.marshal(app, self._after_export, "Campaign not found.", "error")
            return
        if not contacts:
            self.marshal(app, self._after_export, "No contacts to export yet.", "warn")
            return
        try:
            path = export_contacts_csv(campaign.name, contacts)
        except Exception as exc:
            self.marshal(app, self._after_export, f"Error writing CSV: {exc}", "error")
            return
        self.marshal(
            app, self._after_export, f"✓ Exported {len(contacts)} contacts to {path}", "good"
        )

    def _after_export(self, message: str, kind: str) -> None:
        self._busy = False
        self._set_status(message, kind)

    @work(thread=True, exclusive=True)
    def _run_delete(self, app: App) -> None:
        assert self._db_manager is not None  # guarded in action_delete
        try:
            ok = self._db_manager.delete_campaign(self._campaign_id)
        except Exception as exc:
            self.marshal(app, self._after_action, f"Error deleting campaign: {exc}", False)
            return
        self.marshal(app, self._after_action, "__deleted__" if ok else "Campaign not found.", False)

    def _after_action(self, message: str | None, reload: bool) -> None:
        self._busy = False
        if message == "__deleted__":
            # Pop back to the (refreshed) campaigns list.
            self.app.pop_screen()
            return
        if message is not None:
            self._set_status(message, "error")
            return
        if reload:
            self.load_detail()

    # ── helpers ───────────────────────────────────────────────────────────

    def _set_status(self, message: str, kind: str = "") -> None:
        status = self.query_one("#detail-status", Static)
        status.set_classes(f"status-line {('-' + kind) if kind else ''}".strip())
        status.update(message)
