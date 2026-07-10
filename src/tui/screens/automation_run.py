"""Shared base for the standalone long-running automation screens (issue #24).

The **gate → select → confirm → run (streaming log) → summary / error** pipeline
itself lives in :mod:`tui.screens.run_panel` (extracted for issue #42 so the
campaign detail screen can embed it); this base is the *screen-shaped* host for
flows that need their own selection surface. None ship currently: issue #44
removed the last one (``ExtractProfilesScreen``) pending the Voyager extraction
rework (``DESIGN-PROPOSALS.md`` §6), which would restore a subclass here. Until
then the run-pipeline tests keep exercising the panel through this host shape.
It owns the gate on ``db_manager`` + ``settings``,
the selection widgets and their threaded population, the visible **Start**
control, and delegates the confirm/run/stop/summary machinery to an embedded
:class:`~tui.screens.run_panel.AutomationRunPanel`.

Interaction design (owner rule, 2026-07-09; no accelerators, 2026-07-10):
starting a run is arrows + Enter — focus the Start button and press Enter,
then confirm on the focused inline confirm. There is no key accelerator for
either Start or the run panel's Stop button; both are reached by focus alone.

``run_body`` is the single seam a test overrides to exercise the
run/log/summary/error pipeline without a browser.
"""

from __future__ import annotations

from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Button

from config.settings import AppSettings
from database.operations import DatabaseManager
from utils.logging import get_logger

from .base import BaseScreen
from .run_panel import AutomationRunPanel, ConfirmBar, RunSpec, run_with_linkedin

logger = get_logger(__name__)


class AutomationRunScreen(BaseScreen):
    """Base screen for a select → confirm → run automation flow."""

    # Used in the typed-error headline ("…during {ACTION_LABEL}").
    ACTION_LABEL = "automation"

    BINDINGS = [
        ("escape", "back", "Back"),
    ]

    HINTS = (
        ("tab", "fields"),
        ("enter", "activate"),
        ("esc", "back"),
    )

    def __init__(
        self, db_manager: DatabaseManager | None, settings: AppSettings | None
    ) -> None:
        super().__init__()
        self._db_manager = db_manager
        self._settings = settings
        self._panel: AutomationRunPanel | None = None
        # Subclasses flip this off (in apply_options) to block start, e.g. when
        # there are no eligible campaigns.
        self._run_can_start = True

    # ── compose ───────────────────────────────────────────────────────────

    def compose_body(self) -> ComposeResult:
        with VerticalScroll(id="run-body"):
            yield from self.compose_selection()
            # The start control: a visible, focusable button — tab + Enter is
            # the only path to it.
            yield Button("Start", id="run-start")
        yield AutomationRunPanel(id="run-panel")

    def compose_selection(self) -> ComposeResult:
        """Yield the selection widgets. Override; default is empty."""
        return iter(())

    def on_mount(self) -> None:
        # Cached on the UI thread: the worker-thread seams (progress,
        # _stop_event) must not run a DOM query per call.
        self._panel = self.query_one("#run-panel", AutomationRunPanel)
        if self._db_manager is None or self._settings is None:
            self.panel.set_status(
                "Automation unavailable: a database and app settings are required.",
                "error",
            )
            self._run_can_start = False
            self._disable_selection(True)
            return
        self.populate_selection()

    @property
    def panel(self) -> AutomationRunPanel:
        assert self._panel is not None  # set in on_mount
        return self._panel

    # ── subclass hooks ────────────────────────────────────────────────────

    def populate_selection(self) -> None:
        """Load data into the selection widgets off the UI thread."""
        self._run_populate(self.app)

    def fetch_options(self) -> Any:
        """Fetch selection data (runs in a worker thread). Override."""
        return None

    def apply_options(self, data: Any) -> None:
        """Fill the selection widgets and set the ready status (UI thread)."""
        self._set_status(self.ready_hint())

    def ready_hint(self) -> str:
        return "Configure the run, then Start."

    def validate(self) -> str | None:
        """Return a one-line confirmation summary, or None if the current
        selection is invalid (after setting an error status). Override."""
        return "Start automation?"

    async def run_body(self) -> dict:
        """Do the automation and return a result dict carrying a ``status``.

        Default enters ``LinkedInAutomation``, logs in, then calls
        :meth:`automate`. Tests override this whole method to avoid a browser.
        """
        # action_start gates the run on both being present.
        assert self._db_manager is not None and self._settings is not None
        return await run_with_linkedin(
            self._db_manager, self._settings, self.panel, self.automate
        )

    async def automate(self, automation) -> dict:
        """The flow-specific automation, given a logged-in automation. Override."""
        raise NotImplementedError

    def render_result(self, result: dict) -> str:
        """Render a successful result dict to a summary string. Override."""
        return "Run complete."

    # ── start / stop / esc ────────────────────────────────────────────────

    def action_back(self) -> None:
        """``esc``: let the panel cancel/warn first; only then leave."""
        if self.panel.handle_escape():
            return
        self.app.pop_screen()

    def action_start(self) -> None:
        if (
            self._db_manager is None
            or self._settings is None
            or not self._run_can_start
            or self.panel.run_active
        ):
            return
        summary = self.validate()
        if summary is None:
            return
        self.panel.request(
            RunSpec(
                action_label=self.ACTION_LABEL,
                confirm=summary,
                body=self.run_body,
                render=self.render_result,
            )
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-start":
            event.stop()
            self.action_start()

    def on_automation_run_panel_started(self, event: AutomationRunPanel.Started) -> None:
        self._disable_selection(True)

    def on_automation_run_panel_finished(self, event: AutomationRunPanel.Finished) -> None:
        # Re-enable the selection for a follow-up run (the panel allows a new
        # request once the previous run has finished).
        self._disable_selection(False)
        self.query_one("#run-start", Button).focus()

    def on_automation_run_panel_confirm_dismissed(
        self, event: AutomationRunPanel.ConfirmDismissed
    ) -> None:
        # The dismissed bar held focus; hand it back to the Start control so
        # arrows/Enter keep working (the degraded screen keeps it disabled).
        # The message arrives one pump after the dismissal, so only refocus if
        # focus is still stranded on the hidden bar — never steal a focus some
        # same-tick action placed deliberately.
        focused = self.focused
        bar = event.panel.query_one("#run-confirm", ConfirmBar)
        if focused is not None and bar not in focused.ancestors_with_self:
            return
        if self._run_can_start and not self.panel.run_active:
            self.query_one("#run-start", Button).focus()

    # ── progress / stop seams (used by subclass automate bodies) ──────────

    def progress(self, message: Any) -> None:
        self.panel.progress(message)

    @property
    def _stop_event(self):
        # The engine-facing stop flag lives on the panel (fresh per run).
        return self.panel.stop_event

    # ── populate worker ───────────────────────────────────────────────────

    @work(thread=True, exclusive=True, group="populate")
    def _run_populate(self, app: App) -> None:
        try:
            data = self.fetch_options()
        except Exception as exc:
            self.marshal(app, self._apply_populate, None, f"Error loading options: {exc}")
            return
        self.marshal(app, self._apply_populate, data, None)

    def _apply_populate(self, data: Any, error: str | None) -> None:
        if error is not None:
            self._run_can_start = False
            self._set_status(error, "error")
            return
        self.apply_options(data)
        # The panel echoes this hint after a cancelled confirmation.
        self.panel.idle_hint = self.ready_hint()

    # ── helpers ───────────────────────────────────────────────────────────

    def _disable_selection(self, disabled: bool = True) -> None:
        for widget in self.query("#run-body Input, #run-body Select, #run-start"):
            widget.disabled = disabled

    def _set_status(self, message: str, kind: str = "") -> None:
        self.panel.set_status(message, kind)
