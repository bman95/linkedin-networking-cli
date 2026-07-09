"""Home launcher screen (issue #24).

The entry screen sets the tone for the whole TUI: a curated brand masthead, a
live one-line workspace summary (campaign count, today's activity, readiness), and a
keyboard-first navigation list, plus a dim hint bar at the foot. The selection
idiom is borrowed straight from Claude Code's own list component — a ``❯``
pointer in the accent colour and the selected row's title recoloured, with **no**
background bar or border.

Navigation is fast: ``↑``/``↓`` + ``enter``, the number keys ``1``–``7`` jump
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

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Label, ListItem, ListView, Static

from utils.logging import get_logger

from ..nav import NAV_ITEMS
from ._mascot_large import MASCOT_LARGE
from ._wordmark import WORDMARK_TALL
from .base import hint_markup
from .workers import WorkerGuardMixin

logger = get_logger(__name__)

# Claude Code's focused-row pointer (``figures.pointer``); reserved on every row
# and revealed in the accent colour on the highlighted one.
POINTER = "❯"

# The mascot — "Bit", the LinkedIn-blue robot — is a faithful half-block
# rendering of the reference sprite, generated into ``_mascot_large.py`` (see its
# docstring). Imported rather than inlined to keep this module readable.
#
# (Real inline-image rendering — painting the bitmap via the Kitty/Sixel graphics
# protocol for true image sharpness — was investigated and rejected: Warp, the
# target terminal, does not implement the Unicode-placeholder placement a Textual
# TUI needs, so it renders placeholder garbage instead of the image. Half-block
# art is therefore the sharpest portable option.)

HINTS = (
    ("1-7 ↑↓", "navigate"),
    ("enter", "open"),
    ("q", "quit"),
    ("ctrl+p", "more"),
)


class HomeScreen(WorkerGuardMixin, Screen):
    """Curated home: brand masthead, live summary + keyboard-first navigation."""

    BINDINGS = [
        ("q", "app.quit", "Quit"),
        # Number keys jump straight to a destination (1-indexed over NAV_ITEMS,
        # the shared registry in ``tui.nav``).
        *(
            Binding(str(i + 1), f"open({i})", item.title, show=False)
            for i, item in enumerate(NAV_ITEMS)
        ),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="home"):
            # One hero band fills the screen: the full-fidelity mascot on the
            # left, and on the right a content column — brand lockup (LINKEDIN
            # eyebrow, big pixel NETWORKING, tagline, live workspace summary)
            # with the navigation directly beneath it.
            with Horizontal(id="home-hero"):
                yield Static(MASCOT_LARGE, id="home-mascot")
                with Vertical(id="home-heading"):
                    yield Static("L I N K E D I N", id="home-eyebrow")
                    yield Static(WORDMARK_TALL, id="home-wordmark")
                    # Narrow-terminal stand-in for the pixel type (see
                    # ``_maybe_narrow``); hidden by default in CSS.
                    yield Static("[b]N E T W O R K I N G[/]", id="home-wordmark-fallback")
                    yield Static("Outreach, measured.", id="home-tagline")
                    yield Static("Loading workspace…", id="home-status", markup=False)
                    yield Static("NAVIGATE", classes="eyebrow", id="home-nav-eyebrow")
                    yield ListView(
                        *(
                            ListItem(
                                Horizontal(
                                    Label(POINTER, classes="nav-caret"),
                                    Label(item.title, classes="nav-title"),
                                    classes="nav-row",
                                ),
                                Label(item.description, classes="nav-desc"),
                                id=f"nav-{item.key}",
                                classes="nav-item",
                            )
                            for item in NAV_ITEMS
                        ),
                        id="home-nav",
                    )
        yield Static(hint_markup(HINTS), classes="hint-bar")

    # The hero needs this many columns for the pixel wordmark (padding 4+4,
    # mascot 45 + its 3-col gap, wordmark 54); below it the art would wrap
    # into garbage, so a plain-text wordmark stands in.
    _WORDMARK_MIN_WIDTH = 110

    def _maybe_narrow(self, width: int) -> None:
        self.set_class(width < self._WORDMARK_MIN_WIDTH, "narrow")

    def on_resize(self, event) -> None:
        self._maybe_narrow(event.size.width)

    def on_mount(self) -> None:
        self._maybe_narrow(self.app.size.width)
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
        if 0 <= index < len(NAV_ITEMS):
            NAV_ITEMS[index].push(self.app)

    def _open_key(self, key: str) -> None:
        for item in NAV_ITEMS:
            if item.key == key:
                item.push(self.app)
                return

    # ── workspace summary (threaded load) ─────────────────────────────────

    def load_summary(self) -> None:
        # begin_load captures the app on the UI thread; the deferred worker body
        # must not resolve self.app itself (see workers.py for the rationale).
        self._run_load(*self.begin_load())

    @work(thread=True, exclusive=True)
    def _run_load(self, app: App, generation: int) -> None:
        try:
            summary = self._gather(app)
        except Exception:  # never crash the home screen
            logger.debug("Could not gather home summary", exc_info=True)
            summary = HomeSummary(configured=None, campaigns=None,
                                  used_today=None, active_limits=None, db_ok=False)
        self.marshal_load(app, generation, self._populate, summary)

    def _gather(self, app: App) -> HomeSummary:
        """Credential status, campaign count and today's activity — off the UI thread.

        ``AppSettings()`` writes to disk on construction, so it is built here in
        the worker; any failure degrades to a friendly summary rather than a crash.
        The DB is read off the captured ``app`` (never ``self.app``, which could
        raise if the screen were torn down mid-worker).
        """
        from cli.helpers import effective_daily_limit
        from config.settings import AppSettings

        settings = AppSettings()
        configured = settings.validate_credentials()
        # The enforced daily cap is per-campaign (Campaign.daily_limit); the
        # DAILY_CONNECTION_LIMIT env value is only the fallback for campaigns
        # without a valid positive limit (issue #46). effective_daily_limit is
        # the same rule LinkedInAutomation enforces with.
        fallback_limit = settings.get_automation_settings()["daily_connection_limit"]

        db = app.db_manager
        if db is None:
            return HomeSummary(configured=configured, campaigns=None,
                              used_today=None, active_limits=None, db_ok=False)
        try:
            campaigns = db.get_campaigns(active_only=False)
            active_limits = tuple(
                effective_daily_limit(c.daily_limit, fallback_limit)
                for c in campaigns
                if c.active
            )
            used_today = db.get_daily_connection_count(date.today().isoformat())
            return HomeSummary(configured=configured, campaigns=len(campaigns),
                              used_today=used_today, active_limits=active_limits,
                              db_ok=True)
        except Exception:
            logger.debug("Could not read home counts", exc_info=True)
            return HomeSummary(configured=configured, campaigns=None,
                              used_today=None, active_limits=None, db_ok=False)

    def _populate(self, summary: HomeSummary) -> None:
        self.query_one("#home-status", Static).update(summary.line())


@dataclass(frozen=True)
class HomeSummary:
    """Immutable workspace snapshot handed from the worker to the UI thread."""

    configured: bool | None
    campaigns: int | None
    used_today: int | None
    #: Effective daily limits of the *active* campaigns (per-campaign
    #: ``daily_limit``, falling back to the env default only when a campaign
    #: carries no valid positive value). ``None`` when the DB is unavailable.
    active_limits: tuple[int, ...] | None
    db_ok: bool

    def line(self) -> str:
        # Onboarding first: an unconfigured install gets a clear next step.
        if self.configured is False:
            return "Not configured — set LINKEDIN_EMAIL and LINKEDIN_PASSWORD to begin"

        parts = []
        if self.campaigns is not None:
            noun = "campaign" if self.campaigns == 1 else "campaigns"
            parts.append(f"{self.campaigns} {noun}")
        if self.used_today is not None:
            # The binding daily cap is per-campaign — never the env fallback
            # (issue #46). The count itself is global (the day counter has no
            # campaign key), so a limit is quoted only when exactly one
            # campaign is active and the pairing is unambiguous; with several,
            # any aggregate (e.g. "80+20") would misread as a combined budget
            # the global counter cannot honor. Per-campaign caps live on the
            # Campaigns and Execute screens.
            parts.append(f"{self.used_today} sent today")
            if self.active_limits is not None and len(self.active_limits) == 1:
                limit = self.active_limits[0]
                # Enforcement compares the global day count against this limit
                # (used >= limit), so the exhausted state can be called out
                # honestly here — a run started now would stop immediately.
                reached = " — daily limit reached" if self.used_today >= limit else ""
                parts.append(f"limit {limit} (1 active campaign){reached}")
        parts.append("ready" if self.db_ok else "database unavailable")
        return "  ·  ".join(parts)
