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

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Static

from database.operations import DatabaseManager
from utils.logging import get_logger

from .base import BaseScreen

logger = get_logger(__name__)

# CSV columns, matching the classic CLI's export_contacts for parity.
_CSV_FIELDS = (
    "name",
    "profile_url",
    "headline",
    "location",
    "company",
    "status",
    "connection_sent_at",
    "connection_accepted_at",
    "notes",
)


def _csv_value(value) -> str:
    """Normalize a value for CSV output (mirrors the classic ``_csv_value``)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def export_contacts_csv(campaign_name: str, contacts) -> Path:
    """Write a campaign's contacts to a timestamped CSV under the exports dir."""
    safe = "".join(
        c if c.isalnum() or c in ("-", "_") else "_" for c in campaign_name
    ).strip("_") or "campaign"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_dir = Path.home() / ".linkedin-networking-cli" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"{safe}_contacts_{timestamp}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_CSV_FIELDS))
        writer.writeheader()
        for contact in contacts:
            writer.writerow(
                {field: _csv_value(getattr(contact, field, None)) for field in _CSV_FIELDS}
            )
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


def acceptance_rate(sent: int, accepted: int) -> float:
    return (accepted / sent * 100) if sent > 0 else 0.0


class CampaignDetailScreen(BaseScreen):
    """Full view of a single campaign, with manage actions."""

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
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

    def __init__(self, db_manager: Optional[DatabaseManager], campaign_id: int) -> None:
        super().__init__()
        self._db_manager = db_manager
        self._campaign_id = campaign_id
        self._load_generation = 0
        # Cached active flag so the toggle action knows the current state without
        # re-reading; refreshed on every load.
        self._active: Optional[bool] = None
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
        self._load_generation += 1
        self._run_load(self.app, self._load_generation)

    @work(thread=True, exclusive=True)
    def _run_load(self, app: App, generation: int) -> None:
        if self._db_manager is None:
            self._marshal(app, generation, None, "Database unavailable.")
            return
        try:
            detail = self._gather()
        except Exception as exc:
            self._marshal(app, generation, None, f"Error loading campaign: {exc}")
            return
        if detail is None:
            self._marshal(app, generation, None, "Campaign not found.")
            return
        self._marshal(app, generation, detail, None)

    def _gather(self) -> Optional[CampaignDetail]:
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

    def _marshal(
        self, app: App, generation: int, detail: Optional[CampaignDetail], error: Optional[str]
    ) -> None:
        if not app.is_running:
            return
        try:
            app.call_from_thread(self._populate, generation, detail, error)
        except RuntimeError:
            return

    def _populate(
        self, generation: int, detail: Optional[CampaignDetail], error: Optional[str]
    ) -> None:
        if not self.is_mounted:
            return
        if generation != self._load_generation:
            return
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
        self._set_status("Read-only view.  e edit · a toggle active · x export · d delete")

    # ── actions ───────────────────────────────────────────────────────────

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
            self._set_status("Delete this campaign and all its contacts? Press d again to confirm, esc to cancel.", "warn")
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
            self._marshal_action(app, f"Error updating campaign: {exc}", reload=False)
            return
        self._marshal_action(app, None, reload=True)

    @work(thread=True, exclusive=True, group="export")
    def _run_export(self, app: App) -> None:
        assert self._db_manager is not None  # guarded in action_export
        try:
            campaign = self._db_manager.get_campaign(self._campaign_id)
            contacts = self._db_manager.get_contacts(self._campaign_id)
        except Exception as exc:
            self._marshal_export(app, f"Error loading contacts: {exc}", "error")
            return
        if campaign is None:
            self._marshal_export(app, "Campaign not found.", "error")
            return
        if not contacts:
            self._marshal_export(app, "No contacts to export yet.", "warn")
            return
        try:
            path = export_contacts_csv(campaign.name, contacts)
        except Exception as exc:
            self._marshal_export(app, f"Error writing CSV: {exc}", "error")
            return
        self._marshal_export(app, f"✓ Exported {len(contacts)} contacts to {path}", "good")

    def _marshal_export(self, app: App, message: str, kind: str) -> None:
        if not app.is_running:
            return
        try:
            app.call_from_thread(self._after_export, message, kind)
        except RuntimeError:
            return

    def _after_export(self, message: str, kind: str) -> None:
        if not self.is_mounted:
            return
        self._busy = False
        self._set_status(message, kind)

    @work(thread=True, exclusive=True)
    def _run_delete(self, app: App) -> None:
        assert self._db_manager is not None  # guarded in action_delete
        try:
            ok = self._db_manager.delete_campaign(self._campaign_id)
        except Exception as exc:
            self._marshal_action(app, f"Error deleting campaign: {exc}", reload=False)
            return
        self._marshal_action(app, "__deleted__" if ok else "Campaign not found.", reload=False)

    def _marshal_action(self, app: App, message: Optional[str], reload: bool) -> None:
        if not app.is_running:
            return
        try:
            app.call_from_thread(self._after_action, message, reload)
        except RuntimeError:
            return

    def _after_action(self, message: Optional[str], reload: bool) -> None:
        if not self.is_mounted:
            return
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
