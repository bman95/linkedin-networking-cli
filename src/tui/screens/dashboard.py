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
from datetime import datetime

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Grid
from textual.widgets import Button, DataTable, Static

from cli.helpers import acceptance_rate
from database.operations import DatabaseManager
from utils.logging import get_logger

from .base import BaseScreen

logger = get_logger(__name__)

# How many campaigns the recent-campaigns mini-table shows.
RECENT_LIMIT = 5


@dataclass(frozen=True)
class DashboardData:
    """An immutable snapshot handed from the worker thread to the UI thread."""

    stats: dict
    recent: list[tuple]  # (name: Text, status, sent, accepted, rate) — ready for add_row
    used_week: int | None


class DashboardScreen(BaseScreen):
    """Overview of campaigns, contacts and connection stats.

    Interaction design (owner rule, 2026-07-09; no accelerators, 2026-07-10):
    Refresh is a visible, focusable button below the recent-campaigns table —
    tab + Enter is the only path to it.
    """

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
    ]

    SCREEN_TITLE = "Dashboard"

    HINTS = (
        ("↑↓", "move"),
        ("enter", "open"),
        ("esc", "back"),
    )

    RECENT_COLUMNS = ("Name", "Status", "Sent", "Accepted", "Rate")

    # (card id, label). The card values are filled in once data loads. Labels
    # mirror the classic CLI's vocabulary (e.g. "Success Rate") for parity.
    CARDS = (
        ("active-campaigns", "Active Campaigns"),
        ("total-connections", "Total Connections"),
        ("success-rate", "Success Rate"),
        ("total-contacts", "Total Contacts"),
        ("pending", "Pending"),
        # Informational trailing-7-day usage. The daily cap is per-campaign, so
        # a single global usage tile can only be honest at the weekly level
        # (issue #46); there is no configured weekly budget to show against it.
        ("week-usage", "Used This Week"),
    )

    def __init__(self, db_manager: DatabaseManager | None) -> None:
        super().__init__()
        self._db_manager = db_manager

    def compose_body(self) -> ComposeResult:
        with Container(id="dashboard-body"):
            yield Static("OVERVIEW", classes="eyebrow")
            with Grid(id="stat-grid"):
                for card_id, label in self.CARDS:
                    with Container(classes="stat-card", id=f"card-{card_id}"):
                        yield Static(label, classes="stat-label")
                        yield Static("—", classes="stat-value", id=f"value-{card_id}")
            yield Static("RECENT CAMPAIGNS", classes="eyebrow")
            yield DataTable(
                id="dashboard-recent", zebra_stripes=True, cursor_type="row"
            )
            yield Button("Refresh", id="dashboard-refresh", classes="flat-button")
        yield Static("Loading dashboard…", id="dashboard-status", classes="status-line")

    def on_mount(self) -> None:
        table = self.query_one("#dashboard-recent", DataTable)
        table.add_columns(*self.RECENT_COLUMNS)
        self.load_dashboard()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dashboard-refresh":
            event.stop()
            self.action_refresh()

    def action_refresh(self) -> None:
        self.query_one("#dashboard-status", Static).update("Refreshing…")
        self.load_dashboard()

    def load_dashboard(self) -> None:
        """Start a fresh load, invalidating any in-flight (slower) one."""
        # begin_load captures the app on the UI thread (see workers.py for why
        # the deferred worker body must not resolve self.app itself).
        self._run_load(*self.begin_load())

    @work(thread=True, exclusive=True)
    def _run_load(self, app: App, generation: int) -> None:
        """Fetch every dashboard datum off the event loop, then populate."""
        if self._db_manager is None:
            self.marshal_load(app, generation, self._populate, None, "Database unavailable.")
            return
        try:
            stats = self._db_manager.get_dashboard_stats()
            campaigns = self._db_manager.get_campaigns(active_only=False)
            used_week = self._load_week_usage()
        except Exception as exc:  # surface in-place, never crash the UI
            self.marshal_load(
                app, generation, self._populate, None, f"Error loading dashboard: {exc}"
            )
            return

        recent = self._recent_rows(campaigns)
        data = DashboardData(stats=stats, recent=recent, used_week=used_week)
        self.marshal_load(app, generation, self._populate, data, None)

    def _load_week_usage(self) -> int | None:
        """This week's invitation usage (trailing 7 days), or ``None``.

        Purely informational — there is no configured weekly budget anymore.
        Degrades to ``None`` rather than crashing the whole dashboard.
        """
        try:
            return self._db_manager.get_weekly_connection_count()
        except Exception:
            logger.debug("Could not resolve weekly usage; omitting", exc_info=True)
            return None

    def _recent_rows(self, campaigns) -> list[tuple]:
        """Top-N campaigns, most recently active first, as table-ready rows.

        Sent/accepted/rate come from ``get_all_campaign_contact_stats`` — the
        same live-from-``contacts`` source the Campaigns list and Campaign
        detail use — so this table can't contradict them when the denormalized
        ``Campaign`` aggregates are stale (issue #66).
        """

        def _key(c):
            # Newest activity first: last_run, else created_at. Both are
            # datetimes; the datetime.min fallback keeps the key type-coherent
            # if a campaign ever lacks a created_at (sorting a datetime against a
            # date would raise).
            return (c.last_run or c.created_at) or datetime.min

        all_stats = self._db_manager.get_all_campaign_contact_stats()
        ordered = sorted(campaigns, key=_key, reverse=True)[:RECENT_LIMIT]
        rows = []
        for c in ordered:
            stats = all_stats.get(c.id, {})
            c_sent = stats.get("total_sent", 0)
            c_accepted = stats.get("total_accepted", 0)
            rate = acceptance_rate(c_sent, c_accepted)
            rows.append(
                (
                    # Row key first: it carries the campaign id so activating a
                    # row can open its detail screen.
                    str(c.id),
                    # User-controlled — render literally (see CampaignsScreen).
                    Text(c.name),
                    "Active" if c.active else "Inactive",
                    str(c_sent),
                    str(c_accepted),
                    f"{rate:.1f}%",
                )
            )
        return rows

    def _populate(self, data: DashboardData | None, error: str | None) -> None:
        status = self.query_one("#dashboard-status", Static)
        if error is not None:
            self._set_cards_blank()
            self.query_one("#dashboard-recent", DataTable).clear()
            # Literal render: raw exception text may contain markup-like brackets.
            status.update(Text(error))
            return

        assert data is not None  # error is None ⇒ data is present
        self._fill_cards(data)
        self._fill_recent(data.recent)
        if data.stats.get("total_campaigns", 0) == 0:
            status.update(
                "No campaigns yet. Use New Campaign on Home to add one."
            )
        else:
            status.update("Updated.")

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

        week_value = self._value("week-usage")
        week_value.update("—" if data.used_week is None else str(data.used_week))
        week_value.set_classes("stat-value")

    def _fill_recent(self, rows: list[tuple]) -> None:
        table = self.query_one("#dashboard-recent", DataTable)
        table.clear()
        for key, *row in rows:
            table.add_row(*row, key=key)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Activating a recent-campaigns row opens that campaign's detail."""
        if self._db_manager is None:
            return
        key = event.row_key.value if event.row_key is not None else None
        if key is None:
            return
        try:
            campaign_id = int(key)
        except (TypeError, ValueError):
            return
        from .campaign_detail import CampaignDetailScreen

        self.app.push_screen(CampaignDetailScreen(self._db_manager, campaign_id))
