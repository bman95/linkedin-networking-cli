"""Read-only Dashboard screen (issue #24).

The highest-leverage overview: campaign / contact / connection stats plus a
recent-campaigns mini-table, all from ``DatabaseManager`` (reused as-is). It is
read-only — no credentials and no browser — so it stays driveable in the
headless harness.

All reads are synchronous/blocking SQLite, so a single threaded worker fetches
everything off the event loop and marshals one immutable payload back. The
worker discipline mirrors ``CampaignsScreen`` exactly: the app reference is
captured on the UI thread at schedule time; late callbacks after quit are
no-ops; a monotonic generation token drops superseded (stale) results; and the
populate step bails if the screen was detached.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Grid
from textual.widgets import DataTable, Static

from database.operations import DatabaseManager
from utils.logging import get_logger

from .base import BaseScreen
from .campaigns import acceptance_rate

logger = get_logger(__name__)

# How many campaigns the recent-campaigns mini-table shows.
RECENT_LIMIT = 5


@dataclass(frozen=True)
class DashboardData:
    """An immutable snapshot handed from the worker thread to the UI thread."""

    stats: dict
    recent: List[Tuple[str, str, str, str, str]]  # name, status, sent, accepted, rate
    used_today: Optional[int]
    daily_limit: Optional[int]


class DashboardScreen(BaseScreen):
    """Overview of campaigns, contacts and connection stats."""

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("r", "refresh", "Refresh"),
        ("q", "app.quit", "Quit"),
    ]

    SCREEN_TITLE = "Dashboard"

    RECENT_COLUMNS = ("Name", "Status", "Sent", "Accepted", "Rate")

    # (card id, label). The card values are filled in once data loads. Labels
    # mirror the classic CLI's vocabulary (e.g. "Success Rate") for parity.
    CARDS = (
        ("active-campaigns", "Active Campaigns"),
        ("total-connections", "Total Connections"),
        ("success-rate", "Success Rate"),
        ("total-contacts", "Total Contacts"),
        ("pending", "Pending"),
        ("used-today", "Used Today"),
    )

    def __init__(self, db_manager: Optional[DatabaseManager]) -> None:
        super().__init__()
        self._db_manager = db_manager
        self._load_generation = 0

    def compose_body(self) -> ComposeResult:
        yield Static("LinkedIn Networking Dashboard", classes="screen-subtitle")
        with Container(id="dashboard-body"):
            with Grid(id="stat-grid"):
                for card_id, label in self.CARDS:
                    accent = " -accent" if card_id == "success-rate" else ""
                    with Container(classes=f"stat-card{accent}", id=f"card-{card_id}"):
                        yield Static(label, classes="stat-label")
                        yield Static("—", classes="stat-value", id=f"value-{card_id}")
            yield Static("Recent Campaigns", classes="section-title")
            yield DataTable(
                id="dashboard-recent", zebra_stripes=True, cursor_type="row"
            )
        yield Static("Loading dashboard…", id="dashboard-status", classes="status-line")

    def on_mount(self) -> None:
        table = self.query_one("#dashboard-recent", DataTable)
        table.add_columns(*self.RECENT_COLUMNS)
        self.load_dashboard()

    def action_refresh(self) -> None:
        self.load_dashboard()

    def load_dashboard(self) -> None:
        """Start a fresh load, invalidating any in-flight (slower) one."""
        self._load_generation += 1
        # Capture the app on the UI thread (see CampaignsScreen for why the
        # deferred worker body must not resolve self.app itself).
        self._run_load(self.app, self._load_generation)

    @work(thread=True, exclusive=True)
    def _run_load(self, app: App, generation: int) -> None:
        """Fetch every dashboard datum off the event loop, then populate."""
        if self._db_manager is None:
            self._marshal_populate(app, generation, None, "Database unavailable.")
            return
        try:
            stats = self._db_manager.get_dashboard_stats()
            campaigns = self._db_manager.get_campaigns(active_only=False)
            used_today, daily_limit = self._load_quota()
        except Exception as exc:  # surface in-place, never crash the UI
            self._marshal_populate(app, generation, None, f"Error loading dashboard: {exc}")
            return

        recent = self._recent_rows(campaigns)
        data = DashboardData(
            stats=stats, recent=recent, used_today=used_today, daily_limit=daily_limit
        )
        self._marshal_populate(app, generation, data, None)

    def _load_quota(self) -> Tuple[Optional[int], Optional[int]]:
        """Today's connection usage and the configured daily limit.

        ``AppSettings()`` touches the filesystem (it creates the app dir), so a
        read-only/sandboxed home can fail; degrade to ``None`` rather than
        crashing the whole dashboard, consistent with the app's startup posture.
        Imported lazily so this module stays cheap to import.
        """
        try:
            from config.settings import AppSettings

            used_today = self._db_manager.get_daily_connection_count(
                date.today().isoformat()
            )
            daily_limit = AppSettings().get_automation_settings()["daily_connection_limit"]
            return used_today, daily_limit
        except Exception:
            logger.debug("Could not resolve daily quota; omitting", exc_info=True)
            return None, None

    @staticmethod
    def _recent_rows(campaigns) -> List[Tuple[str, str, str, str, str]]:
        """Top-N campaigns, most recently active first, as table-ready rows."""

        def _key(c):
            # Newest activity first: last_run, else created_at; None sorts last.
            return (c.last_run or c.created_at) or date.min

        ordered = sorted(campaigns, key=_key, reverse=True)[:RECENT_LIMIT]
        return [
            (
                c.name,
                "Active" if c.active else "Inactive",
                str(c.total_sent),
                str(c.total_accepted),
                f"{acceptance_rate(c):.1f}%",
            )
            for c in ordered
        ]

    def _marshal_populate(
        self, app: App, generation: int, data: Optional[DashboardData], error: Optional[str]
    ) -> None:
        """Hand results back to the UI thread, but only while the app runs."""
        if not app.is_running:
            return
        try:
            app.call_from_thread(self._populate, generation, data, error)
        except RuntimeError:
            # App stopped between the is_running check and the call; ignore.
            return

    def _populate(
        self, generation: int, data: Optional[DashboardData], error: Optional[str]
    ) -> None:
        if not self.is_mounted:
            return
        if generation != self._load_generation:
            return
        status = self.query_one("#dashboard-status", Static)
        if error is not None:
            self._set_cards_blank()
            self.query_one("#dashboard-recent", DataTable).clear()
            status.update(error)
            return

        assert data is not None  # error is None ⇒ data is present
        self._fill_cards(data)
        self._fill_recent(data.recent)
        if data.stats.get("total_campaigns", 0) == 0:
            status.update("No campaigns yet. Create one in the classic CLI (linkedin-cli).")
        else:
            status.update("Updated. Press r to refresh, Esc to go back.")

    # ── rendering helpers ─────────────────────────────────────────────────

    def _value(self, card_id: str) -> Static:
        return self.query_one(f"#value-{card_id}", Static)

    def _set_cards_blank(self) -> None:
        for card_id, _ in self.CARDS:
            value = self._value(card_id)
            value.update("—")
            value.set_classes("stat-value")

    def _fill_cards(self, data: DashboardData) -> None:
        s = data.stats
        active = s.get("active_campaigns", 0)
        total = s.get("total_campaigns", 0)
        rate = s.get("acceptance_rate", 0.0)

        self._value("active-campaigns").update(f"{active}/{total}")
        self._value("total-connections").update(
            f"{s.get('total_sent', 0)} sent / {s.get('total_accepted', 0)} accepted"
        )

        rate_value = self._value("success-rate")
        rate_value.update(f"{rate}%")
        # A positive rate reads as healthy (green); zero stays neutral.
        rate_value.set_classes("stat-value -good" if rate > 0 else "stat-value")

        self._value("total-contacts").update(str(s.get("total_contacts", 0)))
        self._value("pending").update(str(s.get("total_pending", 0)))

        used_value = self._value("used-today")
        if data.used_today is None or data.daily_limit is None:
            used_value.update("—")
            used_value.set_classes("stat-value")
        else:
            used_value.update(f"{data.used_today}/{data.daily_limit}")
            # Warn when at/over the configured daily cap.
            at_cap = data.daily_limit > 0 and data.used_today >= data.daily_limit
            used_value.set_classes("stat-value -warn" if at_cap else "stat-value")

    def _fill_recent(self, rows: List[Tuple[str, str, str, str, str]]) -> None:
        table = self.query_one("#dashboard-recent", DataTable)
        table.clear()
        for row in rows:
            table.add_row(*row)
