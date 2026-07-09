"""Read-only Campaigns screen (issue #24).

Lists campaigns from the database. ``DatabaseManager`` reads are synchronous and
blocking (each opens its own short-lived SQLite session), so they run in a
threaded worker to keep the Textual event loop responsive. A threaded worker
body cannot be interrupted mid-call, so a read contended by another writer on
the same SQLite file (e.g. the classic CLI mid-campaign) holds the worker until
the read returns; the read here is a single small query, so that window stays
short.
"""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Static

from cli.helpers import acceptance_rate as _acceptance_rate
from cli.helpers import campaign_get_field
from database.models import Campaign
from database.operations import DatabaseManager
from utils.logging import get_logger

from .base import BaseScreen
from .campaign_detail import CampaignDetailScreen

logger = get_logger(__name__)


def acceptance_rate(campaign: Campaign) -> float:
    """Acceptance rate as a percentage, shared with the classic CLI."""
    return _acceptance_rate(
        campaign_get_field(campaign, "total_sent", 0),
        campaign_get_field(campaign, "total_accepted", 0),
    )


class CampaignsScreen(BaseScreen):
    """Read-only screen listing campaigns from the database.

    Loads data through ``DatabaseManager.get_campaigns`` in a threaded worker so
    the blocking SQLite read does not stall the UI.

    Interaction design (owner rule, 2026-07-09): New Campaign and Refresh are
    visible, focusable buttons below the table — reachable with tab + Enter —
    with ``n``/``r`` kept as optional accelerators, never the only path.
    """

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("n", "new", "New campaign"),
        ("r", "refresh", "Refresh"),
        ("q", "app.quit", "Quit"),
    ]

    SCREEN_TITLE = "Campaigns"

    HINTS = (
        ("enter", "open"),
        ("n", "new"),
        ("esc", "back"),
        ("r", "refresh"),
        ("q", "quit"),
        ("ctrl+p", "commands"),
    )

    COLUMNS = ("Name", "Status", "Sent", "Accepted", "Rate", "Daily Limit")

    def __init__(self, db_manager: DatabaseManager | None) -> None:
        super().__init__()
        self._db_manager = db_manager

    def compose_body(self) -> ComposeResult:
        yield DataTable(id="campaigns-table", zebra_stripes=True, cursor_type="row")
        with Horizontal(id="campaigns-toolbar"):
            yield Button("New Campaign", id="campaigns-new", classes="flat-button")
            yield Button("Refresh", id="campaigns-refresh", classes="flat-button")
        yield Static("", id="campaigns-status", classes="status-line")

    def on_mount(self) -> None:
        table = self.query_one("#campaigns-table", DataTable)
        table.add_columns(*self.COLUMNS)
        self.query_one("#campaigns-status", Static).update("Loading campaigns…")
        self.load_campaigns()

    def on_screen_resume(self) -> None:
        # Returning from a campaign's detail (it may have been edited, toggled or
        # deleted): reload so the list reflects the change.
        if self.is_mounted:
            self.load_campaigns()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Activating a row opens that campaign's detail screen."""
        if self._db_manager is None:
            return
        key = event.row_key.value if event.row_key is not None else None
        if key is None:
            return
        try:
            campaign_id = int(key)
        except (TypeError, ValueError):
            return
        self.app.push_screen(CampaignDetailScreen(self._db_manager, campaign_id))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "campaigns-new":
            event.stop()
            self.action_new()
        elif event.button.id == "campaigns-refresh":
            event.stop()
            self.action_refresh()

    def action_refresh(self) -> None:
        self.query_one("#campaigns-status", Static).update("Refreshing…")
        self.load_campaigns()

    def action_new(self) -> None:
        """Create a campaign without detouring back through the home screen."""
        if self._db_manager is None:
            return
        from .create_campaign import CreateCampaignScreen

        self.app.push_screen(CreateCampaignScreen(self._db_manager))

    def load_campaigns(self) -> None:
        """Start a fresh load, invalidating any in-flight (slower) one.

        ``begin_load`` captures the app on the UI thread at schedule time and
        bumps the generation token; the mixin's ``marshal_load`` applies the
        shutdown/unmount/stale guards on the way back (see ``workers.py``).
        """
        self._run_load(*self.begin_load())

    @work(thread=True, exclusive=True)
    def _run_load(self, app: App, generation: int) -> None:
        """Fetch campaigns off the event loop, then populate the table."""
        if self._db_manager is None:
            self.marshal_load(app, generation, self._populate, [], "Database unavailable.")
            return
        try:
            campaigns = self._db_manager.get_campaigns(active_only=False)
        except Exception as exc:  # surface the failure in-place, don't crash the UI
            self.marshal_load(
                app, generation, self._populate, [], f"Error loading campaigns: {exc}"
            )
            return
        self.marshal_load(app, generation, self._populate, campaigns, None)

    def _populate(self, campaigns: list[Campaign], error: str | None) -> None:
        table = self.query_one("#campaigns-table", DataTable)
        table.clear()
        status = self.query_one("#campaigns-status", Static)
        if error is not None:
            # Literal render: raw exception text may contain markup-like brackets.
            status.update(Text(error))
            return
        for campaign in campaigns:
            table.add_row(
                # User-controlled — render literally so a name containing Rich
                # markup (e.g. "Q4 [/] Outreach") can't raise MarkupError and
                # tear down the UI.
                Text(campaign.name),
                "Active" if campaign.active else "Inactive",
                str(campaign.total_sent),
                str(campaign.total_accepted),
                f"{acceptance_rate(campaign):.1f}%",
                str(campaign.daily_limit),
                # Row key carries the id so activating a row opens its detail.
                key=str(campaign.id),
            )
        if campaigns:
            noun = "campaign" if len(campaigns) == 1 else "campaigns"
            status.update(f"{len(campaigns)} {noun}.")
        else:
            status.update("No campaigns yet — press n to create one.")
