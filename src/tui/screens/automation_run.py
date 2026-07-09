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
import threading
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Button, RichLog, Static

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
        ("escape", "back", "Back"),
        # priority so it fires while a Select/Input holds focus.
        Binding("ctrl+r", "start", "Start", priority=True),
        # Optional accelerator for the Stop button (issue #43). Deliberately
        # NOT priority: while an Input holds focus, "s" must keep typing; the
        # binding matters mid-run, when focus sits on the Stop button anyway.
        ("s", "stop", "Stop"),
        ("q", "app.quit", "Quit"),
    ]

    HINTS = (
        ("ctrl+r", "start"),
        ("s", "stop"),
        ("esc", "back"),
        ("q", "quit"),
        ("ctrl+p", "commands"),
    )

    def __init__(
        self, db_manager: DatabaseManager | None, settings: AppSettings | None
    ) -> None:
        super().__init__()
        self._db_manager = db_manager
        self._settings = settings
        self._app_ref: App | None = None
        # NB: distinct names — a bare ``_running`` shadows Textual's
        # MessagePump._running (always True once mounted), which would make the
        # start guard a permanent no-op. See docs/tui-migration.md §9.
        self._run_confirming = False
        self._run_active = False
        self._run_done = False
        # First esc mid-run warns (the run would keep going headless); the
        # second one leaves anyway.
        self._leave_confirming = False
        # Subclasses flip this off (in apply_options) to block start, e.g. when
        # there are no eligible campaigns.
        self._run_can_start = True
        # Cooperative cancellation (issue #43): a fresh Event per run, shared
        # with the engine loops (checked between profiles). _stop_requested
        # keeps the request idempotent and drives the "Stopping…" status.
        self._stop_event: threading.Event | None = None
        self._stop_requested = False

    # ── compose ───────────────────────────────────────────────────────────

    def compose_body(self) -> ComposeResult:
        with VerticalScroll(id="run-body"):
            yield from self.compose_selection()
            yield RichLog(id="run-log", highlight=False, markup=False, wrap=True)
        # The stop control (issue #43): a visible, focusable button — arrows/
        # tab + Enter first, with "s" as the accelerator. Hidden until a run
        # starts; focused by default while the run is active so a bare Enter
        # requests the stop.
        yield Button("Stop after current profile", id="run-stop")
        yield Static("", id="run-status", classes="status-line")

    def compose_selection(self) -> ComposeResult:
        """Yield the selection widgets. Override; default is empty."""
        return iter(())

    def on_mount(self) -> None:
        # The log and stop control are hidden until a run starts; selection
        # comes first.
        self.query_one("#run-log", RichLog).display = False
        self.query_one("#run-stop", Button).display = False
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

    def validate(self) -> str | None:
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

    def action_back(self) -> None:
        """``esc``: cancel an armed confirmation, warn once mid-run, else leave.

        The confirm prompt promises "esc to cancel", so esc while confirming
        cancels the confirmation. Mid-run, leaving does NOT stop the automation
        (the worker keeps driving the browser), so the first esc says exactly
        that and only a second esc leaves.
        """
        if self._run_confirming:
            self._run_confirming = False
            self._set_status("Cancelled. " + self.ready_hint())
            return
        if self._run_active and not self._leave_confirming:
            self._leave_confirming = True
            self._set_status(
                "Run in progress — leaving does not stop it; use the Stop "
                "button (Enter or s) to stop after the current profile. "
                "Press esc again to leave anyway.",
                "warn",
            )
            return
        self.app.pop_screen()

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
        # Fresh stop flag per run, created BEFORE the worker starts so the
        # engine sees the same Event the stop control sets.
        self._stop_event = threading.Event()
        self._stop_requested = False
        stop = self.query_one("#run-stop", Button)
        stop.display = True
        stop.disabled = False
        # Focus lands on the stop control while the run is active, so a bare
        # Enter stops the run (owner rule: arrows + Enter first).
        stop.focus()
        self._set_status(
            "Running…  Enter (or s) stops after the current profile."
        )
        # Capture the app on the UI thread for the worker's progress marshaling.
        self._app_ref = self.app
        self._run_worker(self.app)

    # ── stop (issue #43) ──────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-stop":
            self._request_stop()

    def action_stop(self) -> None:
        self._request_stop()

    def _request_stop(self) -> None:
        """Ask the run to stop after the current profile (idempotent).

        Sets the shared ``threading.Event``; the engine loops poll it between
        profiles, so the in-flight send always completes and the run returns a
        normal partial summary (rendered by :meth:`_finish` as ``cancelled``).
        """
        if not self._run_active or self._stop_requested or self._stop_event is None:
            return
        self._stop_requested = True
        # A stop supersedes a pending leave warning: the status line it wrote
        # is replaced below, so the armed second-esc must not survive it.
        self._leave_confirming = False
        self._stop_event.set()
        stop = self.query_one("#run-stop", Button)
        stop.disabled = True
        self._append_log("Stop requested — finishing the current profile…")
        self._set_status(
            "Stopping…  The run ends after the current profile.", "warn"
        )

    @work(thread=True, exclusive=True, group="run")
    def _run_worker(self, app: App) -> None:
        try:
            result = asyncio.run(self.run_body())
        except Exception as exc:  # any automation failure → friendly stop
            self.marshal(app, self._finish, None, exc)
            return
        self.marshal(app, self._finish, result, None)

    # ── progress (worker thread → UI) ─────────────────────────────────────

    def progress(self, message: Any) -> None:
        """Progress sink passed to the automation as its ``progress_callback``.

        Called from the worker thread; marshals a line into the log on the UI
        thread, and is a silent no-op once the app has stopped.
        """
        self.marshal(self._app_ref, self._append_log, str(message))

    def _append_log(self, message: str) -> None:
        self.query_one("#run-log", RichLog).write(message)

    # ── finish (worker thread → UI) ───────────────────────────────────────

    def _finish(self, result: dict | None, exc: Exception | None) -> None:
        self._run_active = False
        self._run_done = True
        self._leave_confirming = False
        self.query_one("#run-stop", Button).display = False
        log = self.query_one("#run-log", RichLog)
        if exc is not None:
            headline, evidence = describe_automation_error(exc, self.ACTION_LABEL)
            log.write(headline)
            log.write(evidence)
            self._set_status("Stopped. Press esc to return.", "error")
            return
        status = (result or {}).get("status")
        # A stop requested during the final profile can race the loop's own
        # flag check: the work finishes before the flag is observed and the
        # body reports success. The user still asked to stop and saw
        # "Stopping…", so honor the request — one policy for every screen.
        if status == "success" and self._stop_requested:
            result = dict(result or {}, status="cancelled")
            status = "cancelled"
        if status == "login_failed":
            log.write("Login to LinkedIn failed — could not start the run.")
            self._set_status("Login failed. Press esc to return.", "error")
            return
        if status == "safety_stop":
            # A protective CAPTCHA/challenge stop: show the subclass's summary
            # but never a green "Done." for a run cut short mid-flight.
            log.write(self.render_result(result or {}))
            self._set_status(
                "Stopped early to protect the account. Press esc to return.",
                "error",
            )
            return
        if status == "cancelled":
            # A user-requested stop (issue #43) renders like a completion, not
            # an error — the loop stopped at a safe point, so the partial
            # counts are consistent. Neutral status: not the green "Done.".
            log.write(self.render_result(result or {}))
            self._set_status("Stopped at your request. Press esc to return.")
            return
        log.write(self.render_result(result or {}))
        self._set_status("Done. Press esc to return.", "good")

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

    # ── helpers ───────────────────────────────────────────────────────────

    def _disable_selection(self) -> None:
        for widget in self.query("#run-body Input, #run-body Select"):
            widget.disabled = True

    def _set_status(self, message: str, kind: str = "") -> None:
        status = self.query_one("#run-status", Static)
        status.set_classes(f"status-line {('-' + kind) if kind else ''}".strip())
        # Text() renders literally: messages carry raw exception text and user
        # data (campaign names), whose square brackets must not be parsed as
        # markup — see automation_errors' plain-text contract.
        status.update(Text(message))
