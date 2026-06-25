"""Home launcher screen (issue #24).

The entry screen sets the tone for the whole TUI: a curated brand masthead, a
live one-line workspace summary (campaign count, today's quota, readiness), and a
keyboard-first navigation list, plus a dim hint bar at the foot. The selection
idiom is borrowed straight from Claude Code's own list component — a ``❯``
pointer in the accent colour and the selected row's title recoloured, with **no**
background bar or border.

Navigation is fast: ``↑``/``↓`` + ``enter``, the number keys ``1``–``4`` jump
straight to a destination, and the command palette (ctrl+p) reaches the same
screens from anywhere.

The workspace summary is a DB/settings read, so it follows the threaded-worker
race discipline (``docs/tui-migration.md`` §6): the app is captured on the UI
thread, a monotonic generation token drops superseded loads, and late callbacks
after quit are no-ops.

This screen is the app's base; it has nowhere to pop back to, so it does not
inherit ``BaseScreen``'s ``escape`` binding. ``q`` quits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Label, ListItem, ListView, Static

from utils.logging import get_logger

from .base import hint_markup
from .campaigns import CampaignsScreen
from .create_campaign import CreateCampaignScreen
from .dashboard import DashboardScreen
from .settings_view import SettingsScreen

logger = get_logger(__name__)

# Claude Code's focused-row pointer (``figures.pointer``); reserved on every row
# and revealed in the accent colour on the highlighted one.
POINTER = "❯"

# The mascot: "Bit", a pixel robot that carries both identities — a head with
# binary 0/1 eyes (the app is a LinkedIn *bot* given a digital "bit" identity)
# over a torso whose chest says "in" (the LinkedIn mark). Brand-blue body, accent
# antenna / eyes / "in". Drawn with half-block characters; coloured via theme
# tokens (markup is static, no injection risk). The compact `in 01` chip in the
# sub-screen breadcrumb echoes the same in + 0/1 motif.
MASCOT = (
    "[$secondary]    ▟▙[/]\n"
    "[$primary]  ▟█████▙[/]\n"
    "[$primary]  █ [/][$secondary]0[/][$primary] [/][$secondary]1[/][$primary] █[/]\n"
    "[$primary]  █  ◡  █[/]\n"
    "[$primary]  ▟█████▙[/]\n"
    "[$primary]  █ [/][$secondary]in[/][$primary]  █[/]\n"
    "[$primary]  ▜█████▛[/]\n"
    "[$primary]   ▝   ▝[/]"
)

# Plan B: the head-only variant (0/1 eyes + antenna, no "in" torso). Kept as the
# documented fallback — to switch, render this instead of MASCOT in compose().
MASCOT_PLAN_B = (
    "[$secondary]    ▟▙[/]\n"
    "[$primary]  ▟█████▙[/]\n"
    "[$primary]  █ [/][$secondary]0[/][$primary] [/][$secondary]1[/][$primary] █[/]\n"
    "[$primary]  █  ◡  █[/]\n"
    "[$primary]  ▜█████▛[/]\n"
    "[$primary]   ▝   ▝[/]"
)

HINTS = (
    ("1-4 ↑↓", "navigate"),
    ("enter", "open"),
    ("q", "quit"),
    ("ctrl+p", "more"),
)


class HomeScreen(Screen):
    """Curated home: brand masthead, live summary + keyboard-first navigation."""

    BINDINGS = [
        ("q", "app.quit", "Quit"),
        # Number keys jump straight to a destination (1-indexed over NAV_ITEMS).
        Binding("1", "open(0)", "Dashboard", show=False),
        Binding("2", "open(1)", "Campaigns", show=False),
        Binding("3", "open(2)", "Create Campaign", show=False),
        Binding("4", "open(3)", "Settings", show=False),
    ]

    # (key, title, description). The key doubles as the nav item id suffix and
    # selects the destination screen on activation.
    NAV_ITEMS = (
        ("dashboard", "Dashboard", "Campaign overview, connection stats, recent activity"),
        ("campaigns", "Campaigns", "Browse and review your outreach campaigns"),
        ("create", "Create Campaign", "Set up a new outreach campaign"),
        ("settings", "Settings", "Credentials, browser, rate limits, data locations"),
    )

    def __init__(self) -> None:
        super().__init__()
        self._load_generation = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="home"):
            # Hero lockup: the mascot beside the wordmark, tagline and the live
            # workspace summary.
            with Horizontal(id="home-hero"):
                yield Static(MASCOT, id="home-mascot")
                with Vertical(id="home-heading"):
                    yield Static("[b]LinkedIn Networking[/]", id="home-wordmark")
                    yield Static("Outreach, measured.", id="home-tagline")
                    yield Static("Loading workspace…", id="home-status", markup=False)
            yield Static("NAVIGATE", classes="eyebrow")
            yield ListView(
                *(
                    ListItem(
                        Horizontal(
                            Label(POINTER, classes="nav-caret"),
                            Label(title, classes="nav-title"),
                            classes="nav-row",
                        ),
                        Label(desc, classes="nav-desc"),
                        id=f"nav-{key}",
                        classes="nav-item",
                    )
                    for key, title, desc in self.NAV_ITEMS
                ),
                id="home-nav",
            )
        yield Static(hint_markup(HINTS), classes="hint-bar")

    def on_mount(self) -> None:
        # Focus the nav so the highlighted first item responds to Enter on the
        # very first launch (a keyboard user shouldn't have to click first).
        self.query_one("#home-nav", ListView).focus()
        self.load_summary()

    def on_screen_resume(self) -> None:
        # Returning from a pushed screen: restore focus and refresh the summary
        # so counts reflect anything that changed while away.
        if self.is_mounted:
            self.query_one("#home-nav", ListView).focus()
            self.load_summary()

    # ── navigation ────────────────────────────────────────────────────────

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        key = (event.item.id or "").removeprefix("nav-")
        self._open_key(key)

    def action_open(self, index: int) -> None:
        """Number-key jump: open the nav item at ``index`` (0-based)."""
        if 0 <= index < len(self.NAV_ITEMS):
            self._open_key(self.NAV_ITEMS[index][0])

    def _open_key(self, key: str) -> None:
        db = self.app.db_manager
        if key == "dashboard":
            self.app.push_screen(DashboardScreen(db))
        elif key == "campaigns":
            self.app.push_screen(CampaignsScreen(db))
        elif key == "create":
            self.app.push_screen(CreateCampaignScreen(db))
        elif key == "settings":
            self.app.push_screen(SettingsScreen(db))

    # ── workspace summary (threaded load) ─────────────────────────────────

    def load_summary(self) -> None:
        self._load_generation += 1
        # Capture the app on the UI thread; the deferred worker body must not
        # resolve self.app itself (see CampaignsScreen for the rationale).
        self._run_load(self.app, self._load_generation)

    @work(thread=True, exclusive=True)
    def _run_load(self, app: App, generation: int) -> None:
        try:
            summary = self._gather(app)
        except Exception:  # never crash the home screen
            logger.debug("Could not gather home summary", exc_info=True)
            summary = HomeSummary(configured=None, campaigns=None,
                                  used_today=None, daily_limit=None, db_ok=False)
        self._marshal_populate(app, generation, summary)

    def _gather(self, app: App) -> "HomeSummary":
        """Credential status, campaign count and today's quota — off the UI thread.

        ``AppSettings()`` writes to disk on construction, so it is built here in
        the worker; any failure degrades to a friendly summary rather than a crash.
        The DB is read off the captured ``app`` (never ``self.app``, which could
        raise if the screen were torn down mid-worker).
        """
        from config.settings import AppSettings

        settings = AppSettings()
        configured = settings.validate_credentials()
        daily_limit = settings.get_automation_settings().get("daily_connection_limit")

        db = app.db_manager
        if db is None:
            return HomeSummary(configured=configured, campaigns=None,
                              used_today=None, daily_limit=daily_limit, db_ok=False)
        try:
            campaigns = len(db.get_campaigns(active_only=False))
            used_today = db.get_daily_connection_count(date.today().isoformat())
            return HomeSummary(configured=configured, campaigns=campaigns,
                              used_today=used_today, daily_limit=daily_limit, db_ok=True)
        except Exception:
            logger.debug("Could not read home counts", exc_info=True)
            return HomeSummary(configured=configured, campaigns=None,
                              used_today=None, daily_limit=daily_limit, db_ok=False)

    def _marshal_populate(self, app: App, generation: int, summary: "HomeSummary") -> None:
        if not app.is_running:
            return
        try:
            app.call_from_thread(self._populate, generation, summary)
        except RuntimeError:
            return

    def _populate(self, generation: int, summary: "HomeSummary") -> None:
        if not self.is_mounted or generation != self._load_generation:
            return
        self.query_one("#home-status", Static).update(summary.line())


@dataclass(frozen=True)
class HomeSummary:
    """Immutable workspace snapshot handed from the worker to the UI thread."""

    configured: Optional[bool]
    campaigns: Optional[int]
    used_today: Optional[int]
    daily_limit: Optional[int]
    db_ok: bool

    def line(self) -> str:
        # Onboarding first: an unconfigured install gets a clear next step.
        if self.configured is False:
            return "Not configured — set LINKEDIN_EMAIL and LINKEDIN_PASSWORD to begin"

        parts = []
        if self.campaigns is not None:
            noun = "campaign" if self.campaigns == 1 else "campaigns"
            parts.append(f"{self.campaigns} {noun}")
        if self.used_today is not None and self.daily_limit is not None:
            parts.append(f"{self.used_today}/{self.daily_limit} sent today")
        parts.append("ready" if self.db_ok else "database unavailable")
        return "  ·  ".join(parts)
