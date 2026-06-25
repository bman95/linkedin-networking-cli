"""Shared base for the long-running automation screens (issue #24).

Execute Campaign, Check Connections and Extract Profile Data share one shape:
**gate → select → confirm → run (streaming log) → summary / error**. This base
owns everything generic to that shape — the gate on ``db_manager`` + ``settings``,
the two-press confirm before an irreversible run, the thread worker that drives
``asyncio.run`` around the automation, the progress sink that streams lines into a
``RichLog`` from the worker thread, and the typed-error mapping. Subclasses fill
in only what differs: the selection widgets, what to load into them, the
confirmation summary, the async automation body, and the success summary.

Why a thread worker around ``asyncio.run`` (not a native async worker): it mirrors
the classic CLI exactly (``asyncio.run(run_automation())``) and keeps the blocking
``LinkedInAutomation`` setup off Textual's event loop, reusing the same
``call_from_thread`` marshaling discipline the read/write screens already use.

Browser side effects are **user-initiated**: nothing runs until the user selects a
target and confirms with a second ``ctrl+r``. ``run_body`` is the single seam a
test overrides to exercise the run/log/summary/error pipeline without a browser.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import RichLog, Static

from config.settings import AppSettings
from database.operations import DatabaseManager
from utils.logging import get_logger

from .automation_errors import describe_automation_error
from .base import BaseScreen

logger = get_logger(__name__)


class AutomationRunScreen(BaseScreen):
    """Base screen for a select → confirm → run automation flow."""

    # Used in the typed-error headline ("…during {ACTION_LABEL}").
    ACTION_LABEL = "automation"

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        # priority so it fires while a Select/Input holds focus.
        Binding("ctrl+r", "start", "Start", priority=True),
        ("q", "app.quit", "Quit"),
    ]

    HINTS = (
        ("ctrl+r", "start"),
        ("esc", "back"),
        ("q", "quit"),
        ("ctrl+p", "commands"),
    )

    def __init__(
        self, db_manager: Optional[DatabaseManager], settings: Optional[AppSettings]
    ) -> None:
        super().__init__()
        self._db_manager = db_manager
        self._settings = settings
        self._app_ref: Optional[App] = None
        # NB: distinct names — a bare ``_running`` shadows Textual's
        # MessagePump._running (always True once mounted), which would make the
        # start guard a permanent no-op. See docs/tui-migration.md §9.
        self._run_confirming = False
        self._run_active = False
        self._run_done = False
        # Subclasses flip this off (in apply_options) to block start, e.g. when
        # there are no eligible campaigns.
        self._run_can_start = True

    # ── compose ───────────────────────────────────────────────────────────

    def compose_body(self) -> ComposeResult:
        with VerticalScroll(id="run-body"):
            yield from self.compose_selection()
            yield RichLog(id="run-log", highlight=False, markup=False, wrap=True)
        yield Static("", id="run-status", classes="status-line")

    def compose_selection(self) -> ComposeResult:
        """Yield the selection widgets. Override; default is empty."""
        return iter(())

    def on_mount(self) -> None:
        # The log is hidden until a run starts; selection comes first.
        self.query_one("#run-log", RichLog).display = False
        if self._db_manager is None or self._settings is None:
            self._set_status(
                "Automation unavailable: a database and app settings are required.",
                "error",
            )
            self._run_can_start = False
            self._disable_selection()
            return
        self.populate_selection()

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
        return "Configure the run, then ctrl+r to start."

    def validate(self) -> Optional[str]:
        """Return a one-line confirmation summary, or None if the current
        selection is invalid (after setting an error status). Override."""
        return "Start automation?"

    async def run_body(self) -> dict:
        """Do the automation and return a result dict carrying a ``status``.

        Default enters ``LinkedInAutomation``, logs in, then calls
        :meth:`automate`. Tests override this whole method to avoid a browser.
        """
        from automation.linkedin import LinkedInAutomation

        # action_start gates the run on both being present.
        assert self._db_manager is not None and self._settings is not None
        async with LinkedInAutomation(self._db_manager, self._settings) as automation:
            self.progress("Launching browser and attaching to Chrome…")
            ok = await automation.login(self.progress)
            if not ok:
                return {"status": "login_failed"}
            return await self.automate(automation)

    async def automate(self, automation) -> dict:
        """The flow-specific automation, given a logged-in automation. Override."""
        raise NotImplementedError

    def render_result(self, result: dict) -> str:
        """Render a successful result dict to a summary string. Override."""
        return "Run complete."

    # ── start / confirm / run ─────────────────────────────────────────────

    def action_start(self) -> None:
        if (
            self._db_manager is None
            or self._settings is None
            or not self._run_can_start
            or self._run_active
            or self._run_done
        ):
            return
        summary = self.validate()
        if summary is None:
            self._run_confirming = False
            return
        if not self._run_confirming:
            self._run_confirming = True
            self._set_status(
                f"{summary}  Press ctrl+r again to start, esc to cancel.", "warn"
            )
            return
        self._run_confirming = False
        self._begin_run()

    def _begin_run(self) -> None:
        self._run_active = True
        self._disable_selection()
        log = self.query_one("#run-log", RichLog)
        log.display = True
        log.clear()
        self._set_status("Running…  Keep this screen open until it finishes.")
        # Capture the app on the UI thread for the worker's progress marshaling.
        self._app_ref = self.app
        self._run_worker(self.app)

    @work(thread=True, exclusive=True, group="run")
    def _run_worker(self, app: App) -> None:
        try:
            result = asyncio.run(self.run_body())
        except Exception as exc:  # any automation failure → friendly stop
            self._marshal_finish(app, None, exc)
            return
        self._marshal_finish(app, result, None)

    # ── progress (worker thread → UI) ─────────────────────────────────────

    def progress(self, message: Any) -> None:
        """Progress sink passed to the automation as its ``progress_callback``.

        Called from the worker thread; marshals a line into the log on the UI
        thread, and is a silent no-op once the app has stopped.
        """
        app = self._app_ref
        if app is None or not app.is_running:
            return
        try:
            app.call_from_thread(self._append_log, str(message))
        except RuntimeError:
            return

    def _append_log(self, message: str) -> None:
        if not self.is_mounted:
            return
        self.query_one("#run-log", RichLog).write(message)

    # ── finish (worker thread → UI) ───────────────────────────────────────

    def _marshal_finish(
        self, app: App, result: Optional[dict], exc: Optional[Exception]
    ) -> None:
        if not app.is_running:
            return
        try:
            app.call_from_thread(self._finish, result, exc)
        except RuntimeError:
            return

    def _finish(self, result: Optional[dict], exc: Optional[Exception]) -> None:
        if not self.is_mounted:
            return
        self._run_active = False
        self._run_done = True
        log = self.query_one("#run-log", RichLog)
        if exc is not None:
            headline, evidence = describe_automation_error(exc, self.ACTION_LABEL)
            log.write(headline)
            log.write(evidence)
            self._set_status("Stopped. Press esc to return.", "error")
            return
        status = (result or {}).get("status")
        if status == "login_failed":
            log.write("Login to LinkedIn failed — could not start the run.")
            self._set_status("Login failed. Press esc to return.", "error")
            return
        log.write(self.render_result(result or {}))
        self._set_status("Done. Press esc to return.", "good")

    # ── populate worker ───────────────────────────────────────────────────

    @work(thread=True, exclusive=True, group="populate")
    def _run_populate(self, app: App) -> None:
        try:
            data = self.fetch_options()
        except Exception as exc:
            self._marshal_populate(app, None, f"Error loading options: {exc}")
            return
        self._marshal_populate(app, data, None)

    def _marshal_populate(self, app: App, data: Any, error: Optional[str]) -> None:
        if not app.is_running:
            return
        try:
            app.call_from_thread(self._apply_populate, data, error)
        except RuntimeError:
            return

    def _apply_populate(self, data: Any, error: Optional[str]) -> None:
        if not self.is_mounted:
            return
        if error is not None:
            self._run_can_start = False
            self._set_status(error, "error")
            return
        self.apply_options(data)

    # ── helpers ───────────────────────────────────────────────────────────

    def _disable_selection(self) -> None:
        for widget in self.query("#run-body Input, #run-body Select"):
            widget.disabled = True

    def _set_status(self, message: str, kind: str = "") -> None:
        status = self.query_one("#run-status", Static)
        status.set_classes(f"status-line {('-' + kind) if kind else ''}".strip())
        status.update(message)
