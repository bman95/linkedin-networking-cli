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

from typing import List, Optional

from textual import work
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Static

from database.models import Campaign
from database.operations import DatabaseManager
from utils.logging import get_logger

from .base import BaseScreen

logger = get_logger(__name__)


def acceptance_rate(campaign: Campaign) -> float:
    """Acceptance rate as a percentage, mirroring the InquirerPy CLI."""
    if campaign.total_sent > 0:
        return campaign.total_accepted / campaign.total_sent * 100
    return 0.0


class CampaignsScreen(BaseScreen):
    """Read-only screen listing campaigns from the database.

    Loads data through ``DatabaseManager.get_campaigns`` in a threaded worker so
    the blocking SQLite read does not stall the UI.
    """

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("r", "refresh", "Refresh"),
        ("q", "app.quit", "Quit"),
    ]

    SCREEN_TITLE = "Campaigns"

    COLUMNS = ("Name", "Status", "Sent", "Accepted", "Rate", "Daily Limit")

    def __init__(self, db_manager: Optional[DatabaseManager]) -> None:
        super().__init__()
        self._db_manager = db_manager
        # Monotonic token identifying the most recent load. A thread worker can't
        # be cancelled mid-read, so a superseded (slower) load would otherwise
        # overwrite the table with a stale snapshot; results are applied only if
        # their token still matches.
        self._load_generation = 0

    def compose_body(self) -> ComposeResult:
        yield DataTable(id="campaigns-table", zebra_stripes=True, cursor_type="row")
        yield Static("", id="campaigns-status", classes="status-line")

    def on_mount(self) -> None:
        table = self.query_one("#campaigns-table", DataTable)
        table.add_columns(*self.COLUMNS)
        self.query_one("#campaigns-status", Static).update("Loading campaigns…")
        self.load_campaigns()

    def action_refresh(self) -> None:
        self.load_campaigns()

    def load_campaigns(self) -> None:
        """Start a fresh load, invalidating any in-flight (slower) one."""
        self._load_generation += 1
        # Capture the app reference here, on the UI thread while the screen is
        # still attached. ``@work(thread=True)`` defers the worker body, so
        # resolving ``self.app`` inside it would run later on a worker thread —
        # and if the user popped/quit the screen first, that lookup would raise
        # before the shutdown guards in ``_marshal_populate`` get a chance to run.
        self._run_load(self.app, self._load_generation)

    @work(thread=True, exclusive=True)
    def _run_load(self, app: App, generation: int) -> None:
        """Fetch campaigns off the event loop, then populate the table."""
        if self._db_manager is None:
            self._marshal_populate(app, generation, [], "Database unavailable.")
            return
        try:
            campaigns = self._db_manager.get_campaigns(active_only=False)
        except Exception as exc:  # surface the failure in-place, don't crash the UI
            self._marshal_populate(app, generation, [], f"Error loading campaigns: {exc}")
            return
        self._marshal_populate(app, generation, campaigns, None)

    def _marshal_populate(
        self, app: App, generation: int, campaigns: List[Campaign], error: Optional[str]
    ) -> None:
        """Hand results back to the UI thread, but only while the app runs.

        The thread worker can't be interrupted, so it may finish after the user
        quit. ``call_from_thread`` raises ``RuntimeError`` once the event loop is
        torn down; treating a late callback as a no-op lets the worker thread
        exit cleanly instead of erroring (and hanging the ``linkedin-tui``
        process on shutdown).
        """
        if not app.is_running:
            return
        try:
            app.call_from_thread(self._populate, generation, campaigns, error)
        except RuntimeError:
            # App stopped between the is_running check and the call; ignore.
            return

    def _populate(
        self, generation: int, campaigns: List[Campaign], error: Optional[str]
    ) -> None:
        # The worker body can't be interrupted, so it may still call this after
        # the screen was popped (its widgets gone). Bail out if we're detached.
        if not self.is_mounted:
            return
        # Drop results from a superseded load so a slower older read can't
        # overwrite the table with a stale snapshot.
        if generation != self._load_generation:
            return
        table = self.query_one("#campaigns-table", DataTable)
        table.clear()
        status = self.query_one("#campaigns-status", Static)
        if error is not None:
            status.update(error)
            return
        for campaign in campaigns:
            table.add_row(
                campaign.name,
                "Active" if campaign.active else "Inactive",
                str(campaign.total_sent),
                str(campaign.total_accepted),
                f"{acceptance_rate(campaign):.1f}%",
                str(campaign.daily_limit),
            )
        if campaigns:
            status.update(f"{len(campaigns)} campaign(s). Press Esc to go back, r to refresh.")
        else:
            status.update("No campaigns yet. Create one in the classic CLI (linkedin-cli).")
