"""Reusable automation-run pipeline (issue #42).

The **gate → confirm → run (streaming log) → summary / error** shape used to
live in ``AutomationRunScreen`` and was only reachable through the standalone
Execute/Check screens. Issue #42 folds those flows into the campaign detail
screen, so the pipeline is extracted here as an embeddable widget:

- :class:`AutomationRunPanel` owns everything generic to a run — the inline
  confirmation, the thread worker that drives ``asyncio.run`` around the
  automation, the progress sink that streams lines into a ``RichLog`` from the
  worker thread, the cooperative stop control (issue #43), and the typed-error
  mapping. Hosts (the campaign detail screen, ``AutomationRunScreen``) hand it
  a :class:`RunSpec` per run and listen for :class:`AutomationRunPanel.Started`
  / :class:`AutomationRunPanel.Finished`.
- :class:`ConfirmBar` is the interaction-design rule made widget: every
  confirmation is a **visible, focused button** — reach it with arrows/tab,
  Enter confirms, esc cancels — never a "press the same chord twice" pattern,
  and (2026-07-10) never a letter/ctrl-chord shortcut either.
- :func:`run_with_linkedin` is the shared browser wrapper (enter
  ``LinkedInAutomation``, log in, honor stops requested before/during login).

Why a thread worker around ``asyncio.run`` (not a native async worker): it
mirrors the classic CLI exactly (``asyncio.run(run_automation())``) and keeps
the blocking ``LinkedInAutomation`` setup off Textual's event loop, reusing the
same ``call_from_thread`` marshaling discipline the data screens use.

Browser side effects are **user-initiated**: nothing runs until the user
confirms the armed run. The ``RunSpec.body`` coroutine is the single seam a
test overrides to exercise the run/log/summary/error pipeline without a
browser.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, RichLog, Static

from utils.logging import get_logger

from .automation_errors import describe_automation_error
from .workers import WorkerGuardMixin

logger = get_logger(__name__)


@dataclass(frozen=True)
class RunSpec:
    """One requested automation run, as the host screen defines it.

    ``action_label`` names the flow in the typed-error headline ("…during
    {action_label}") and lets a repeated same-action request confirm the run
    it armed (see :meth:`AutomationRunPanel.request`); ``confirm`` is the
    one-line confirmation summary; ``body`` is the zero-argument coroutine
    function doing the work (the browser-free test seam); ``render`` turns
    the result dict into the summary text.
    """

    action_label: str
    confirm: str
    body: Callable[[], Awaitable[dict]]
    render: Callable[[dict], str]


async def run_with_linkedin(db_manager, settings, panel: AutomationRunPanel, automate) -> dict:
    """Shared browser wrapper for a run body: login, stop checks, ``automate``.

    Enters ``LinkedInAutomation``, logs in, then calls ``automate(automation)``.
    A stop requested before anything started skips the browser launch entirely;
    one requested while login was underway takes effect right after it (the
    login confirmation waits are blocking Playwright calls and cannot be
    interrupted).
    """
    from automation.linkedin import LinkedInAutomation

    stop = panel.stop_event
    if stop is not None and stop.is_set():
        return {"status": "cancelled"}
    async with LinkedInAutomation(db_manager, settings) as automation:
        panel.progress("Launching browser and attaching to Chrome…")
        ok = await automation.login(panel.progress)
        if not ok:
            return {"status": "login_failed"}
        if stop is not None and stop.is_set():
            panel.progress("Stop requested — ending the run before automation began.")
            return {"status": "cancelled"}
        return await automate(automation)


class ConfirmBar(Horizontal):
    """An inline confirmation: a focused confirm button beside a Cancel one.

    Hidden until armed. :meth:`arm` reveals it and focuses the confirm button,
    so a bare Enter confirms; arrows/tab move between the two buttons; the host
    handles esc by calling :meth:`disarm`. Posts :class:`Confirmed` /
    :class:`Cancelled` (the host decides what they mean).
    """

    class Confirmed(Message):
        def __init__(self, bar: ConfirmBar) -> None:
            super().__init__()
            self.bar = bar

    class Cancelled(Message):
        def __init__(self, bar: ConfirmBar) -> None:
            super().__init__()
            self.bar = bar

    def __init__(self, confirm_label: str = "Confirm", *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        yield Button(self._confirm_label, classes="confirm-yes")
        yield Button("Cancel", classes="confirm-no")

    def on_mount(self) -> None:
        self.display = False

    @property
    def armed(self) -> bool:
        return self.display

    def arm(self) -> None:
        self.display = True
        self.query_one(".confirm-yes", Button).focus()

    def disarm(self) -> None:
        self.display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        confirmed = event.button.has_class("confirm-yes")
        self.disarm()
        self.post_message(self.Confirmed(self) if confirmed else self.Cancelled(self))

    def on_key(self, event) -> None:
        # A disarmed bar must never eat keys: focus can still sit on one of its
        # (now hidden) buttons for the instant before the host refocuses.
        if not self.armed:
            return
        # Arrows move between the two buttons (arrows + Enter first).
        if event.key in ("left", "right", "up", "down"):
            event.stop()
            yes = self.query_one(".confirm-yes", Button)
            no = self.query_one(".confirm-no", Button)
            (no if self.app.focused is yes else yes).focus()


class AutomationRunPanel(WorkerGuardMixin, Vertical):
    """Embeddable select-free run pipeline: confirm → run → summary / error."""

    class Started(Message):
        """A run began (hosts disable their selection widgets)."""

        def __init__(self, panel: AutomationRunPanel) -> None:
            super().__init__()
            self.panel = panel

    class Finished(Message):
        """A run ended — successfully, cancelled, or with an error."""

        def __init__(
            self, panel: AutomationRunPanel, result: dict | None, error: Exception | None
        ) -> None:
            super().__init__()
            self.panel = panel
            self.result = result
            self.error = error

    class ConfirmDismissed(Message):
        """An armed confirmation went away without starting a run.

        Hosts restore focus to their primary control on this: the confirm bar
        held focus while armed, and hiding a widget does not move focus off it
        — without the hand-back, arrows/Enter would land in an invisible bar.
        """

        def __init__(self, panel: AutomationRunPanel) -> None:
            super().__init__()
            self.panel = panel

    def __init__(self, *, idle_hint: str = "", id: str | None = None) -> None:
        super().__init__(id=id)
        #: Shown when idle and after a cancelled confirmation; hosts update it
        #: once their options are loaded (e.g. the screen's ready hint).
        self.idle_hint = idle_hint
        self._spec: RunSpec | None = None
        self._app_ref: App | None = None
        # NB: distinct names — a bare ``_running`` shadows Textual's
        # MessagePump._running (always True once mounted), which would make the
        # start guard a permanent no-op. See docs/tui-migration.md §9.
        self._active = False
        self._done = False
        # First esc mid-run warns (the run would keep going headless); the
        # second one leaves anyway. Owned here, surfaced via handle_escape().
        self._leave_confirming = False
        # Cooperative cancellation (issue #43): a fresh Event per run, shared
        # with the engine loops (checked between profiles). _stop_requested
        # keeps the request idempotent and drives the "Stopping…" status.
        self._stop_event: threading.Event | None = None
        self._stop_requested = False

    # ── compose ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield RichLog(id="run-log", highlight=False, markup=False, wrap=True)
        # The confirm control: a visible, focused Start button — arrows/tab +
        # Enter; a re-invoked same-action request also confirms it (see
        # AutomationRunPanel.request).
        yield ConfirmBar("Start", id="run-confirm")
        # The stop control (issue #43): visible and focused while a run is
        # active so a bare Enter requests the stop.
        yield Button("Stop after current profile", id="run-stop")
        yield Static("", id="run-status", classes="status-line")

    def on_mount(self) -> None:
        self.query_one("#run-log", RichLog).display = False
        self.query_one("#run-stop", Button).display = False
        if self.idle_hint:
            self.set_status(self.idle_hint)

    # ── public state ──────────────────────────────────────────────────────

    @property
    def run_active(self) -> bool:
        return self._active

    @property
    def run_done(self) -> bool:
        return self._done

    @property
    def confirming(self) -> bool:
        return self.query_one("#run-confirm", ConfirmBar).armed

    @property
    def stop_event(self) -> threading.Event | None:
        return self._stop_event

    # ── request / confirm ─────────────────────────────────────────────────

    def request(self, spec: RunSpec) -> None:
        """Arm the confirmation for ``spec`` (or confirm a re-invoked one).

        A repeated request for the *same* action while its confirmation is
        armed confirms it — so re-activating Start/Run-now a second time
        (tabbing back to it and pressing Enter again, rather than moving to
        the confirm bar's own focused button) still completes — while a
        request for a different action re-arms with the new spec.
        """
        if self._active:
            return
        bar = self.query_one("#run-confirm", ConfirmBar)
        if bar.armed and self._spec is not None and self._spec.action_label == spec.action_label:
            # The freshly validated spec supersedes the armed one (the user may
            # have changed the selection between the two presses).
            self._spec = spec
            bar.disarm()
            self._begin_run()
            return
        self._spec = spec
        self.set_status(f"{spec.confirm}  Enter to confirm, esc to cancel.", "warn")
        bar.arm()

    def on_confirm_bar_confirmed(self, event: ConfirmBar.Confirmed) -> None:
        event.stop()
        if self._spec is None or self._active:
            return
        self._begin_run()

    def on_confirm_bar_cancelled(self, event: ConfirmBar.Cancelled) -> None:
        event.stop()
        self._cancel_confirm()

    def _cancel_confirm(self) -> None:
        self._spec = None
        self.set_status(("Cancelled. " + self.idle_hint).strip())
        # The bar held focus while armed; hosts refocus their primary control.
        self.post_message(self.ConfirmDismissed(self))

    def dismiss_confirm(self) -> None:
        """Disarm an armed confirmation (no-op otherwise).

        Called by hosts when another action supersedes the armed run — e.g.
        toggling/editing/deleting the campaign the confirmation was validated
        against. The armed spec captured gates (like "campaign is active") at
        request time, so it must never survive a state change.
        """
        bar = self.query_one("#run-confirm", ConfirmBar)
        if bar.armed:
            bar.disarm()
            self._cancel_confirm()

    def handle_escape(self) -> bool:
        """esc, as the host delegates it: cancel an armed confirmation, warn
        once mid-run, else decline (the host then leaves the screen).

        The confirm prompt promises "esc to cancel", so esc while confirming
        cancels the confirmation. Mid-run, leaving does NOT stop the automation
        (the worker keeps driving the browser), so the first esc says exactly
        that and only a second esc leaves.
        """
        bar = self.query_one("#run-confirm", ConfirmBar)
        if bar.armed:
            self.dismiss_confirm()
            return True
        if self._active and not self._leave_confirming:
            self._leave_confirming = True
            self.set_status(
                "Run in progress — leaving does not stop it; use the Stop "
                "button (it holds focus) to stop after the current profile. "
                "Press esc again to leave anyway.",
                "warn",
            )
            return True
        return False

    # ── run ───────────────────────────────────────────────────────────────

    def _begin_run(self) -> None:
        spec = self._spec
        assert spec is not None
        self._active = True
        self._done = False
        self._leave_confirming = False
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
        # Enter requests the stop (owner rule: arrows + Enter first).
        stop.focus()
        self.set_status("Running…  Enter stops after the current profile.")
        # Capture the app on the UI thread for the worker's progress marshaling.
        self._app_ref = self.app
        self.post_message(self.Started(self))
        self._run_worker(self.app, spec)

    # ── stop (issue #43) ──────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-stop":
            event.stop()
            self.request_stop()

    def request_stop(self) -> None:
        """Ask the run to stop after the current profile (idempotent).

        Sets the shared ``threading.Event``; the engine loops poll it between
        profiles, so the in-flight send always completes and the run returns a
        normal partial summary (rendered by :meth:`_finish` as ``cancelled``).
        """
        if not self._active or self._stop_requested or self._stop_event is None:
            return
        self._stop_requested = True
        # A stop supersedes a pending leave warning: the status line it wrote
        # is replaced below, so the armed second-esc must not survive it.
        self._leave_confirming = False
        self._stop_event.set()
        stop = self.query_one("#run-stop", Button)
        stop.disabled = True
        # Phase-neutral copy: during login there is no "current profile" yet —
        # the run ends at the next safe point the loop (or the body) checks.
        self._append_log("Stop requested — finishing the current step…")
        self.set_status("Stopping…  The run ends at the next safe point.", "warn")

    @work(thread=True, exclusive=True, group="run")
    def _run_worker(self, app: App, spec: RunSpec) -> None:
        try:
            result = asyncio.run(spec.body())
        except Exception as exc:  # any automation failure → friendly stop
            self.marshal(app, self._finish, spec, None, exc)
            return
        self.marshal(app, self._finish, spec, result, None)

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

    def _finish(self, spec: RunSpec, result: dict | None, exc: Exception | None) -> None:
        self._active = False
        self._done = True
        self._leave_confirming = False
        self.query_one("#run-stop", Button).display = False
        log = self.query_one("#run-log", RichLog)
        try:
            if exc is not None:
                headline, evidence = describe_automation_error(exc, spec.action_label)
                log.write(headline)
                log.write(evidence)
                self.set_status("Stopped. Press esc to return.", "error")
                return
            status = (result or {}).get("status")
            # A stop requested during the final profile can race the loop's own
            # flag check: all the work finishes before the flag is observed and
            # the body honestly reports success. Report the completion — calling
            # it "partial" would misstate a finished run — but acknowledge the
            # request instead of showing a bare green "Done." after "Stopping…".
            # One policy for every run surface; engine-observed stops arrive
            # here as status "cancelled" and take the branch below instead.
            if status == "success" and self._stop_requested:
                log.write(spec.render(result or {}))
                self.set_status(
                    "Done — the run finished before the stop took effect. "
                    "Press esc to return.",
                    "good",
                )
                return
            if status == "login_failed":
                log.write("Login to LinkedIn failed — could not start the run.")
                self.set_status("Login failed. Press esc to return.", "error")
                return
            if status == "safety_stop":
                # A protective CAPTCHA/challenge stop: show the summary but
                # never a green "Done." for a run cut short mid-flight.
                log.write(spec.render(result or {}))
                self.set_status(
                    "Stopped early to protect the account. Press esc to return.",
                    "error",
                )
                return
            if status == "cancelled":
                # A user-requested stop (issue #43) renders like a completion,
                # not an error — the loop stopped at a safe point, so the
                # partial counts are consistent. Neutral status: not the green
                # "Done.".
                log.write(spec.render(result or {}))
                self.set_status("Stopped at your request. Press esc to return.")
                return
            log.write(spec.render(result or {}))
            self.set_status("Done. Press esc to return.", "good")
        finally:
            self.post_message(self.Finished(self, result, exc))

    # ── helpers ───────────────────────────────────────────────────────────

    def set_status(self, message: str, kind: str = "") -> None:
        status = self.query_one("#run-status", Static)
        status.set_classes(f"status-line {('-' + kind) if kind else ''}".strip())
        # Text() renders literally: messages carry raw exception text and user
        # data (campaign names), whose square brackets must not be parsed as
        # markup — see automation_errors' plain-text contract.
        status.update(Text(message))
