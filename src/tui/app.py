"""Textual application: main menu + the first real screen (Campaigns).

Vertical slice for issue #24. The app reuses the existing business logic:
``AppSettings`` for the DB path and ``DatabaseManager`` for reads. The chosen
real screen is read-only (list campaigns) so the slice locks the data-flow
pattern without triggering any automation side effects or requiring credentials.

``DatabaseManager`` reads are synchronous and blocking (each opens its own
short-lived SQLite session), so they are run in a threaded worker to keep the
Textual event loop responsive. A threaded worker body cannot be interrupted
mid-call, so a read contended by another writer on the same SQLite file (e.g.
the classic CLI mid-campaign) holds the worker until the read returns; the read
here is a single small query, so that window stays short.
"""

from __future__ import annotations

from typing import List, Optional

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Center, Middle
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, ListItem, ListView, Static

from config.settings import AppSettings
from database.models import Campaign
from database.operations import DatabaseManager
from utils.logging import get_logger

logger = get_logger(__name__)


def _acceptance_rate(campaign: Campaign) -> float:
    """Acceptance rate as a percentage, mirroring the InquirerPy CLI."""
    if campaign.total_sent > 0:
        return campaign.total_accepted / campaign.total_sent * 100
    return 0.0


class CampaignsScreen(Screen):
    """Read-only screen listing campaigns from the database.

    Loads data through ``DatabaseManager.get_campaigns`` in a threaded worker so
    the blocking SQLite read does not stall the UI.
    """

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("r", "refresh", "Refresh"),
    ]

    COLUMNS = ("Name", "Status", "Sent", "Accepted", "Rate", "Daily Limit")

    def __init__(self, db_manager: Optional[DatabaseManager]) -> None:
        super().__init__()
        self._db_manager = db_manager

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("Campaigns", id="campaigns-title")
        yield DataTable(id="campaigns-table", zebra_stripes=True, cursor_type="row")
        yield Static("", id="campaigns-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#campaigns-table", DataTable)
        table.add_columns(*self.COLUMNS)
        self.load_campaigns()

    def action_refresh(self) -> None:
        self.load_campaigns()

    @work(thread=True, exclusive=True)
    def load_campaigns(self) -> None:
        """Fetch campaigns off the event loop, then populate the table."""
        # Capture the app reference now, while the screen is still attached. The
        # worker can't be interrupted, so by the time it finishes the screen may
        # be detached and ``self.app`` would no longer resolve.
        app = self.app
        if self._db_manager is None:
            self._marshal_populate(app, [], "Database unavailable.")
            return
        try:
            campaigns = self._db_manager.get_campaigns(active_only=False)
        except Exception as exc:  # surface the failure in-place, don't crash the UI
            self._marshal_populate(app, [], f"Error loading campaigns: {exc}")
            return
        self._marshal_populate(app, campaigns, None)

    def _marshal_populate(
        self, app: App, campaigns: List[Campaign], error: Optional[str]
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
            app.call_from_thread(self._populate, campaigns, error)
        except RuntimeError:
            # App stopped between the is_running check and the call; ignore.
            return

    def _populate(self, campaigns: List[Campaign], error: Optional[str]) -> None:
        # The worker body can't be interrupted, so it may still call this after
        # the screen was popped (its widgets gone). Bail out if we're detached.
        if not self.is_mounted:
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
                f"{_acceptance_rate(campaign):.1f}%",
                str(campaign.daily_limit),
            )
        if campaigns:
            status.update(f"{len(campaigns)} campaign(s). Press Esc to go back, r to refresh.")
        else:
            status.update("No campaigns yet. Create one in the classic CLI (linkedin-cli).")


class MainMenuScreen(Screen):
    """Full-screen main menu rendered in place."""

    BINDINGS = [("q", "app.quit", "Quit")]

    # Each entry: (item id, label). Only "campaigns" is wired in this slice;
    # "quit" exits. The remaining flows are migrated in later PRs of issue #24.
    MENU_ITEMS = (
        ("campaigns", "Campaigns"),
        ("quit", "Quit"),
    )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Middle():
            with Center():
                yield Label("LinkedIn Networking CLI", id="menu-title")
            with Center():
                yield ListView(
                    *(
                        ListItem(Label(label), id=f"menu-{item_id}")
                        for item_id, label in self.MENU_ITEMS
                    ),
                    id="main-menu",
                )
        yield Footer()

    def on_mount(self) -> None:
        # Focus the menu so the highlighted first item responds to Enter on the
        # very first launch (without it, a keyboard user pressing Enter sees
        # nothing happen).
        self.query_one("#main-menu", ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        if item_id == "menu-campaigns":
            self.app.push_screen(CampaignsScreen(self.app.db_manager))
        elif item_id == "menu-quit":
            self.app.exit()


class LinkedInTUI(App):
    """Full-screen Textual front end (vertical slice for issue #24)."""

    TITLE = "LinkedIn Networking CLI"

    CSS = """
    #menu-title {
        text-style: bold;
        padding: 1 0;
    }

    #main-menu {
        width: 40;
        height: auto;
        border: round $accent;
    }

    #campaigns-title {
        text-style: bold;
        padding: 1 2 0 2;
    }

    #campaigns-status {
        padding: 0 2 1 2;
        color: $text-muted;
    }
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        super().__init__()
        if db_manager is None:
            # Build the default manager, but degrade gracefully on a startup
            # failure (e.g. read-only home, bad DB path) the same way the classic
            # CLI does, rather than crashing before any screen is shown. A None
            # manager surfaces "Database unavailable." in the Campaigns screen.
            try:
                settings = AppSettings()
                db_manager = DatabaseManager(str(settings.db_path))
            except Exception:
                logger.exception("Failed to initialize database; running degraded")
                db_manager = None
        self.db_manager = db_manager

    def on_mount(self) -> None:
        self.push_screen(MainMenuScreen())


def run() -> None:
    """Launch the Textual TUI."""
    LinkedInTUI().run()
