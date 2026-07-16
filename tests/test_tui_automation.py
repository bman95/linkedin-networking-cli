"""Tests for the automation run pipeline and its hosts (issues #24, #42, #43).

The real browser run is user-initiated and not exercised here; instead we test
every surface around it: the typed-error mapping (pure), the confirm → run →
summary/error pipeline (via browser-free fakes that override the ``run_body`` /
``run_now_body`` / ``check_body`` seams), the result mapping (pure), the
cooperative stop control, and the campaign-detail run/check flows issue #42
folded in.

Interaction design under test (owner rule, 2026-07-09; no accelerators,
2026-07-10): every flow is driven arrows + Enter alone — the Start control,
the inline confirm, and the campaign detail's ACTIONS list are all focusable,
and there are no letter/ctrl-chord shortcuts anywhere.
"""

import asyncio
import threading
import time

import pytest
from textual.widgets import Button, ListView, RichLog, Static

from database.operations import DatabaseManager
from exceptions import (
    CaptchaDetectedException,
    NotAuthenticatedException,
    RateLimitExceededException,
    SelectorNotFoundException,
)
from tui.app import CampaignDetailScreen, LinkedInTUI
from tui.screens.automation_errors import describe_automation_error
from tui.screens.automation_run import AutomationRunScreen
from tui.screens.campaign_detail import (
    map_check_stats,
    map_connect_results,
    render_check_result,
    render_connect_result,
)
from tui.screens.run_panel import ConfirmBar, RunSpec


class _DummySettings:
    """Stand-in so the db+settings gate passes without a real AppSettings."""


def make_campaign(db, name="Campaign", active=True, **extra):
    data = {"name": name, "daily_limit": 20, "active": active,
            "message_template": "Hi {name}!"}
    data.update(extra)
    return db.create_campaign(data)


async def wait_text(pilot, status_id: str, needle: str, tries: int = 80) -> str:
    last = ""
    for _ in range(tries):
        await pilot.pause()
        try:
            node = pilot.app.screen.query_one(status_id, Static)
        except Exception:
            continue
        last = str(node.render())
        if needle in last:
            return last
    return last


def log_text(screen) -> str:
    log = screen.query_one("#run-log", RichLog)
    return "\n".join(strip.text for strip in log.lines)


# ── error mapping (pure) ────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc, needle",
    [
        (CaptchaDetectedException("x"), "CAPTCHA"),
        (RateLimitExceededException("x"), "rate limit"),
        (NotAuthenticatedException("x"), "no longer authenticated"),
        (SelectorNotFoundException("x"), "page element was not found"),
        (ValueError("boom"), "Unexpected error"),
    ],
)
def test_describe_automation_error(exc, needle):
    headline, evidence = describe_automation_error(exc, "test run")
    assert needle in headline
    assert "Evidence" in evidence


# ── result mapping (pure — moved out of the old Execute/Check screens) ──────


@pytest.mark.unit
def test_map_connect_results_stopped_reason_to_safety_stop():
    """A protective stop is a safety_stop — even (especially) when nothing was
    scanned yet — and its rendering names the CAPTCHA, never 'no profiles'."""
    for scanned in (3, 0):
        result = map_connect_results({
            "sent": 1 if scanned else 0, "possibly_sent": 0, "failed": 0,
            "existing": 0, "total_processed": scanned, "scanned": scanned,
            "stopped_reason": "captcha",
        })
        assert result["status"] == "safety_stop"
        text = render_connect_result(result)
        assert "CAPTCHA" in text
        assert "No profiles matched" not in text


@pytest.mark.unit
def test_map_connect_results_cancelled_to_partial_completion():
    """A 'cancelled' stopped_reason maps to the cancelled status and renders as
    a partial completion — never the CAPTCHA safety-stop copy."""
    result = map_connect_results({
        "sent": 2, "possibly_sent": 0, "failed": 0, "existing": 1,
        "total_processed": 3, "scanned": 5, "stopped_reason": "cancelled",
    })
    assert result["status"] == "cancelled"
    text = render_connect_result(result)
    assert "stopped at your request" in text
    assert "Invites sent: 2" in text
    assert "CAPTCHA" not in text


@pytest.mark.unit
def test_map_connect_results_empty_scan_and_success():
    assert map_connect_results({"scanned": 0})["status"] == "no_profiles"
    assert "No profiles matched" in render_connect_result({"status": "no_profiles"})
    ok = map_connect_results({"scanned": 4, "sent": 1})
    assert ok["status"] == "success"
    assert "Run complete." in render_connect_result(ok)


@pytest.mark.unit
def test_map_check_stats_maps_stopped_to_cancelled():
    result = map_check_stats({"checked": 2, "newly_accepted": 1, "stopped": True})
    assert result == {"status": "cancelled", "checked": 2, "newly_accepted": 1}
    assert "stopped at your request" in render_check_result(result)
    done = map_check_stats({"checked": 4, "newly_accepted": 2})
    assert done["status"] == "success"
    assert "Connection check complete." in render_check_result(done)


@pytest.mark.unit
def test_map_check_stats_maps_truncated_to_incomplete():
    """Issue #59 #3: the checker's ``truncated`` flag (a stalled/backstop
    walk that never confirmed reaching the list end or a stop marker) must
    not be dropped into a plain "success" — it renders as a distinct,
    non-green "incomplete" status."""
    result = map_check_stats({"checked": 1, "newly_accepted": 0, "truncated": True})
    assert result["status"] == "incomplete"
    text = render_check_result(result)
    assert "may be incomplete" in text
    assert "Connection check complete." not in text


@pytest.mark.unit
def test_map_check_stats_stopped_takes_priority_over_truncated():
    """A user-requested stop is reported as 'cancelled' even if the walk also
    happened to end without confirming the list end — 'cancelled' already
    conveys 'partial results' and must not be overridden by 'incomplete'."""
    result = map_check_stats({"stopped": True, "truncated": True})
    assert result["status"] == "cancelled"


# ── run pipeline via a browser-free fake ────────────────────────────────────


class _FakeRunScreen(AutomationRunScreen):
    SCREEN_TITLE = "Fake Run"
    ACTION_LABEL = "fake run"

    def __init__(self, db, settings, outcome="success"):
        super().__init__(db, settings)
        self._outcome = outcome

    async def run_body(self) -> dict:
        self.progress("step one")
        self.progress("step two")
        if self._outcome == "captcha":
            raise CaptchaDetectedException("captcha wall")
        if self._outcome == "login_failed":
            return {"status": "login_failed"}
        if self._outcome == "safety_stop":
            return {"status": "safety_stop", "n": 3}
        if self._outcome == "incomplete":
            return {"status": "incomplete", "n": 5}
        return {"status": "success", "n": 7}

    def render_result(self, result: dict) -> str:
        return f"summary: n={result.get('n')}"


async def arm_run(pilot, screen) -> None:
    """Focus the Start button and press Enter to arm the inline confirmation."""
    screen.query_one("#run-start", Button).focus()
    await pilot.press("enter")


async def start_run(pilot, screen) -> None:
    """Arm, then confirm on the confirm bar's own focused button — the only
    path to starting a run (no accelerator)."""
    await arm_run(pilot, screen)
    await wait_text(pilot, "#run-status", "Enter to confirm")
    await pilot.press("enter")  # confirm bar's focused Yes button


async def _run_fake(pilot, app, outcome):
    """Push a fake run screen and start it via the Start button."""
    app.push_screen(_FakeRunScreen(app.db_manager, _DummySettings(), outcome=outcome))
    await pilot.pause()
    await start_run(pilot, app.screen)


@pytest.mark.unit
async def test_run_pipeline_success(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _run_fake(pilot, app, "success")
        await wait_text(pilot, "#run-status", "Done")
        text = log_text(app.screen)
        assert "step one" in text and "step two" in text
        assert "summary: n=7" in text


@pytest.mark.unit
async def test_run_via_start_button_and_focused_confirm(db_manager: DatabaseManager):
    """The primary path (owner rule): focus the Start button, Enter arms the
    inline confirm with its confirm button focused, Enter starts the run."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = _FakeRunScreen(app.db_manager, _DummySettings())
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#run-start", Button).focus()
        await pilot.press("enter")
        await wait_text(pilot, "#run-status", "Enter to confirm")
        bar = screen.query_one("#run-confirm", ConfirmBar)
        assert bar.armed
        assert app.focused is bar.query_one(".confirm-yes", Button)
        await pilot.press("enter")
        await wait_text(pilot, "#run-status", "Done")
        assert "summary: n=7" in log_text(screen)


@pytest.mark.unit
async def test_confirm_cancel_button_reachable_with_arrows(
    db_manager: DatabaseManager,
):
    """Cancel is also arrows + Enter: an arrow moves focus from the confirm
    button to Cancel, and Enter there cancels without starting."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = _FakeRunScreen(app.db_manager, _DummySettings())
        app.push_screen(screen)
        await pilot.pause()
        await arm_run(pilot, screen)
        await wait_text(pilot, "#run-status", "Enter to confirm")
        await pilot.press("right")  # confirm → Cancel
        bar = screen.query_one("#run-confirm", ConfirmBar)
        assert app.focused is bar.query_one(".confirm-no", Button)
        await pilot.press("enter")
        await wait_text(pilot, "#run-status", "Cancelled")
        assert screen.panel.run_active is False
        assert not bar.armed


@pytest.mark.unit
async def test_confirm_cancel_restores_focus_to_start(db_manager: DatabaseManager):
    """Cancelling the confirm hands focus back to the Start control — the bar
    held focus while armed, and a hidden widget does not release it itself."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = _FakeRunScreen(app.db_manager, _DummySettings())
        app.push_screen(screen)
        await pilot.pause()
        await arm_run(pilot, screen)
        await wait_text(pilot, "#run-status", "Enter to confirm")
        await pilot.press("escape")
        await wait_text(pilot, "#run-status", "Cancelled")
        start = screen.query_one("#run-start", Button)
        assert app.focused is start
        # Button presses debounce a same-button re-press for their brief
        # "-active" flash animation (Textual: Button.action_press no-ops
        # while the class is still set) — real wall-clock time, not just a
        # pump, so it must actually elapse before the same button is pressed
        # again in one test.
        await pilot.pause(0.25)
        # The keyboard still drives the flow: Enter re-arms the confirmation.
        await pilot.press("enter")
        await wait_text(pilot, "#run-status", "Enter to confirm")
        assert screen.query_one("#run-confirm", ConfirmBar).armed


@pytest.mark.unit
async def test_rerun_after_completion(db_manager: DatabaseManager):
    """A finished run re-enables the surface for a follow-up run: focus lands
    back on Start, and a second confirm starts a fresh run over a cleared log."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _run_fake(pilot, app, "success")
        await wait_text(pilot, "#run-status", "Done")
        screen = app.screen
        assert app.focused is screen.query_one("#run-start", Button)
        # Real wall-clock gap so the first press's "-active" flash animation
        # (Button.action_press no-ops while it's still set) has cleared
        # before this same button is pressed again — see the same guard in
        # test_confirm_cancel_restores_focus_to_start.
        await pilot.pause(0.25)
        await pilot.press("enter")  # re-arm from the refocused Start control
        await wait_text(pilot, "#run-status", "Enter to confirm")
        await pilot.press("enter")  # confirm
        await wait_text(pilot, "#run-status", "Done")
        text = log_text(screen)
        # The log was cleared for the new run: one copy of each line, not two.
        assert text.count("step one") == 1
        assert text.count("summary: n=7") == 1


@pytest.mark.unit
async def test_run_pipeline_captcha_maps_error(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _run_fake(pilot, app, "captcha")
        status = await wait_text(pilot, "#run-status", "Stopped")
        assert "Stopped" in status
        assert "CAPTCHA" in log_text(app.screen)


@pytest.mark.unit
async def test_run_pipeline_login_failed(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _run_fake(pilot, app, "login_failed")
        status = await wait_text(pilot, "#run-status", "Login failed")
        assert "Login failed" in status


@pytest.mark.unit
async def test_run_pipeline_safety_stop_is_not_a_green_done(
    db_manager: DatabaseManager,
):
    """A safety_stop result renders the summary but never a green 'Done.'."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _run_fake(pilot, app, "safety_stop")
        status = await wait_text(pilot, "#run-status", "Stopped early")
        assert "Stopped early to protect the account" in status


@pytest.mark.unit
async def test_run_pipeline_incomplete_is_not_a_green_done(
    db_manager: DatabaseManager,
):
    """Issue #59: a result status of 'incomplete' (the checker gave up
    without confirming it saw everything) renders the summary but never a
    green 'Done.'."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _run_fake(pilot, app, "incomplete")
        status = await wait_text(pilot, "#run-status", "may be incomplete")
        assert "may be incomplete" in status
        assert "summary: n=5" in log_text(app.screen)


# ── cooperative cancellation (issue #43) ────────────────────────────────────


class _FakeStoppableRunScreen(AutomationRunScreen):
    """A slow, browser-free run body honoring the stop flag between 'profiles'.

    Mirrors the engine contract: the flag is polled between iterations only, the
    in-flight step always completes, and a stop returns the normal partial
    summary with status ``cancelled``.
    """

    SCREEN_TITLE = "Fake Stoppable Run"
    ACTION_LABEL = "fake stoppable run"

    async def run_body(self) -> dict:
        done = 0
        for i in range(40):
            if self._stop_event is not None and self._stop_event.is_set():
                return {"status": "cancelled", "n": done}
            self.progress(f"profile {i + 1}")
            await asyncio.sleep(0.12)
            done += 1
        return {"status": "success", "n": done}

    def render_result(self, result: dict) -> str:
        return f"summary: n={result.get('n')}"


async def wait_text_timed(pilot, status_id: str, needle: str, timeout=8.0) -> str:
    """Like wait_text, but on wall-clock time — the stoppable fake sleeps for
    real, so a fixed number of pauses is not enough."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        await pilot.pause()
        try:
            node = pilot.app.screen.query_one(status_id, Static)
        except Exception:
            continue
        last = str(node.render())
        if needle in last:
            return last
        await asyncio.sleep(0.02)
    return last


async def _wait_log(pilot, screen, needle: str, timeout=8.0) -> str:
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        await pilot.pause()
        text = log_text(screen)
        if needle in text:
            return text
        await asyncio.sleep(0.02)
    return text


async def _start_stoppable(pilot, app):
    app.push_screen(_FakeStoppableRunScreen(app.db_manager, _DummySettings()))
    await pilot.pause()
    await start_run(pilot, app.screen)
    await pilot.pause()
    return app.screen


@pytest.mark.unit
async def test_stop_control_hidden_until_run_starts(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(_FakeStoppableRunScreen(app.db_manager, _DummySettings()))
        await pilot.pause()
        assert app.screen.query_one("#run-stop", Button).display is False


@pytest.mark.unit
async def test_stop_via_enter_yields_partial_summary_and_reenables(
    db_manager: DatabaseManager,
):
    """The owner interaction rule and the issue's e2e criterion in one pass:
    focus lands on the visible Stop button when the run starts (so a bare
    Enter stops it), a mid-run stop shows 'Stopping…', and the run ends with a
    partial summary rendered like a completion — after which the screen is
    re-enabled (single esc leaves)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _start_stoppable(pilot, app)
        stop = screen.query_one("#run-stop", Button)
        assert stop.display is True
        assert app.focused is stop  # focus lands on the Stop control
        await _wait_log(pilot, screen, "profile 1")

        await pilot.press("enter")
        status = await wait_text_timed(pilot, "#run-status", "Stopped at your request")
        assert "Stopped at your request" in status
        # The stop acknowledgement was logged when the request was made (the
        # "Stopping…" status itself is transient — see the dedicated test).
        assert "Stop requested — finishing the current step" in log_text(screen)

        # The partial summary renders like a normal completion (not an error)
        # and its count matches the work actually done before the stop.
        text = log_text(screen)
        assert "summary: n=" in text
        n = int(text.split("summary: n=")[1].split()[0])
        assert n == text.count("profile ")
        assert 0 < n < 40

        # Screen re-enabled: run over, control hidden, a single esc leaves.
        assert screen.panel.run_done and not screen.panel.run_active
        assert stop.display is False
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not screen


@pytest.mark.unit
async def test_request_stop_shows_stopping_status_and_disables_button(
    db_manager: DatabaseManager,
):
    """The moment a stop is requested, the status flips to 'Stopping…' and the
    button goes disabled (idempotent request) — read synchronously on the UI
    thread, since the running loop replaces the status as soon as it observes
    the flag."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _start_stoppable(pilot, app)
        screen.panel.request_stop()  # what Enter on the focused Stop button dispatches to
        status = str(screen.query_one("#run-status", Static).render())
        assert "Stopping" in status
        assert screen.query_one("#run-stop", Button).disabled is True
        await wait_text_timed(pilot, "#run-status", "Stopped at your request")


@pytest.mark.unit
async def test_midrun_esc_warning_mentions_stop_control(
    db_manager: DatabaseManager,
):
    """The first mid-run esc still warns that leaving does not stop the run,
    and points at the stop control (issue #43 acceptance criterion)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _start_stoppable(pilot, app)
        await pilot.press("escape")
        status = await wait_text(pilot, "#run-status", "leaving does not stop it")
        assert "leaving does not stop it" in status
        assert "Stop button" in status
        assert app.screen is screen  # first esc warns, does not leave


@pytest.mark.unit
async def test_stop_after_armed_leave_rearms_the_esc_warning(
    db_manager: DatabaseManager,
):
    """esc (warn armed) → stop → esc must warn again, not leave: the stop
    request overwrote the warning status line, so the armed second-esc dies
    with it (state driven directly for determinism — no worker race)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(_FakeStoppableRunScreen(app.db_manager, _DummySettings()))
        await pilot.pause()
        screen = app.screen
        panel = screen.panel
        panel._active = True
        panel._stop_event = threading.Event()

        screen.action_back()  # first esc: arms the leave warning
        assert panel._leave_confirming is True
        panel.request_stop()  # stop supersedes the warning…
        assert panel._leave_confirming is False
        screen.action_back()  # …so the next esc warns again instead of leaving
        assert panel._leave_confirming is True
        assert app.screen is screen


@pytest.mark.unit
async def test_stop_racing_natural_completion_reports_finished(
    db_manager: DatabaseManager,
):
    """A stop requested during the final profile can lose the race with the
    loop's flag check — the body then honestly reports success. _finish shows
    the completed summary (not a false 'partial') while acknowledging the stop
    request instead of a bare green Done (one policy for every run surface)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(_FakeStoppableRunScreen(app.db_manager, _DummySettings()))
        await pilot.pause()
        screen = app.screen
        panel = screen.panel
        # A running panel mid-stop: the log is visible (as _begin_run leaves
        # it) and the stop was requested but not observed by the loop.
        screen.query_one("#run-log", RichLog).display = True
        panel._active = True
        panel._stop_requested = True

        async def _noop() -> dict:
            return {}

        spec = RunSpec("race", "", _noop, lambda r: f"summary: n={r.get('n')}")
        panel._finish(spec, {"status": "success", "n": 3}, None)
        status = str(screen.query_one("#run-status", Static).render())
        assert "finished before the stop took effect" in status
        await pilot.pause()  # let the RichLog render the summary line
        assert "summary: n=3" in log_text(screen)


@pytest.mark.unit
async def test_stop_during_login_ends_run_before_automation(
    db_manager: DatabaseManager, monkeypatch
):
    """A stop requested while login is underway takes effect the moment login
    completes: the REAL run_body returns cancelled without calling automate()
    (login's blocking waits themselves are not interruptible)."""
    automate_calls = []

    class _RunScreen(AutomationRunScreen):
        SCREEN_TITLE = "Login Stop"
        ACTION_LABEL = "login stop"

        async def automate(self, automation) -> dict:
            automate_calls.append(automation)
            return {"status": "success"}

        def render_result(self, result: dict) -> str:
            return "should not complete"

    class _FakeAutomation:
        """Stands in for LinkedInAutomation; the stop lands mid-login."""

        def __init__(self, db, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def login(self, progress_callback=None):
            screen._stop_event.set()  # the user pressed Stop during login
            return True

    import automation.linkedin as engine

    monkeypatch.setattr(engine, "LinkedInAutomation", _FakeAutomation)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = _RunScreen(app.db_manager, _DummySettings())
        app.push_screen(screen)
        await pilot.pause()
        await start_run(pilot, screen)
        status = await wait_text_timed(pilot, "#run-status", "Stopped at your request")
        assert "Stopped at your request" in status
        assert automate_calls == []  # the run ended before automation began


# ── campaign detail: run / check flows (issue #42) ──────────────────────────


class _FakeRunDetail(CampaignDetailScreen):
    """Detail screen with browser-free automation bodies (the test seams)."""

    def __init__(self, *args, run_result=None, check_result=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._run_result = run_result or {
            "status": "success", "scanned": 5, "sent": 2, "possibly_sent": 0,
            "existing": 1, "failed": 0, "total_processed": 3,
        }
        self._check_result = check_result or {
            "status": "success", "checked": 4, "newly_accepted": 2,
        }

    async def run_now_body(self) -> dict:
        self.panel.progress("run step one")
        return self._run_result

    async def check_body(self) -> dict:
        self.panel.progress("check step one")
        return self._check_result


async def _push_detail(pilot, app, campaign_id, screen_cls=_FakeRunDetail, **kwargs):
    screen = screen_cls(
        app.db_manager, campaign_id, settings=_DummySettings(), **kwargs
    )
    app.push_screen(screen)
    await wait_text(pilot, "#detail-status", "select an action")
    return screen


async def activate_action(pilot, screen, index: int) -> None:
    """Navigate the ACTIONS list to ``index`` and press Enter (arrows + Enter
    is the only path to any action — owner rule, 2026-07-10: no accelerators).
    ACTIONS order: run(0), check(1), edit(2), toggle(3), export(4), delete(5).
    """
    actions = screen.query_one("#detail-actions", ListView)
    while actions.index != index:
        await pilot.press("down")
    await pilot.press("enter")


@pytest.mark.unit
async def test_detail_run_now_via_actions_list(db_manager: DatabaseManager):
    """The issue #42 e2e criterion: run from the detail screen with a fake
    run_body seam, driven arrows + Enter only — the actions list's first item
    is Run now; Enter arms the focused confirm; Enter starts; the log streams
    into the panel and the summary renders."""
    campaign = make_campaign(db_manager, name="Detail Run")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_detail(pilot, app, campaign.id)
        actions = screen.query_one("#detail-actions", ListView)
        assert app.focused is actions  # the action list holds initial focus
        assert actions.index == 0  # "Run now" is the first action
        await pilot.press("enter")
        await wait_text(pilot, "#run-status", "Enter to confirm")
        status = str(screen.query_one("#run-status", Static).render())
        assert "Detail Run" in status  # the confirm names the campaign
        bar = screen.query_one("#detail-run-panel ConfirmBar", ConfirmBar)
        assert app.focused is bar.query_one(".confirm-yes", Button)
        await pilot.press("enter")
        await wait_text(pilot, "#run-status", "Done")
        text = log_text(screen)
        assert "run step one" in text
        assert "Run complete." in text
        assert "Invites sent: 2" in text
        # After the run the detail reloads and focus returns to the actions.
        await wait_text(pilot, "#detail-status", "select an action")
        assert app.focused is actions


@pytest.mark.unit
async def test_detail_run_cancelled_summary_is_neutral_status(
    db_manager: DatabaseManager,
):
    """A body-reported stop renders the partial summary with the neutral
    status, driven arrows + Enter over the ACTIONS list throughout."""
    campaign = make_campaign(db_manager, name="Cancelled Run")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_detail(
            pilot, app, campaign.id,
            run_result={
                "status": "cancelled", "scanned": 3, "sent": 1,
                "possibly_sent": 0, "existing": 0, "failed": 0,
                "total_processed": 2,
            },
        )
        await activate_action(pilot, screen, 0)  # Run now: arms the confirm
        await wait_text(pilot, "#run-status", "Enter to confirm")
        await pilot.press("enter")  # confirm bar's focused Yes button
        await wait_text(pilot, "#run-status", "Stopped at your request")
        assert "stopped at your request" in log_text(screen)


@pytest.mark.unit
async def test_detail_check_acceptances_from_detail(db_manager: DatabaseManager):
    """The issue #42 e2e criterion: check from the detail screen with a fake
    check_body seam — gated on pending invites, summary on the same surface.
    Driven arrows + Enter throughout: arm, esc-cancel, then re-arm and start."""
    campaign = make_campaign(db_manager, name="Detail Check")
    db_manager.create_contact({
        "campaign_id": campaign.id,
        "name": "Pending Person",
        "profile_url": "https://www.linkedin.com/in/pending/",
        "status": "sent",
    })
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_detail(pilot, app, campaign.id)
        await activate_action(pilot, screen, 1)  # Check acceptances: arms the confirm
        status = await wait_text(pilot, "#run-status", "Enter to confirm")
        assert "smart checker" in status
        await pilot.press("escape")
        await wait_text(pilot, "#run-status", "Cancelled")
        # After the cancel, focus is back on the actions list: the primary
        # arrows + Enter path arms and starts the same check.
        actions = screen.query_one("#detail-actions", ListView)
        assert app.focused is actions
        assert actions.index == 1
        await pilot.press("enter")
        await wait_text(pilot, "#run-status", "Enter to confirm")
        await pilot.press("enter")  # confirm button holds focus
        await wait_text(pilot, "#run-status", "Done")
        text = log_text(screen)
        assert "check step one" in text
        assert "Connection check complete." in text
        assert "Newly accepted: 2" in text


@pytest.mark.unit
async def test_detail_esc_cancels_armed_run_confirmation(
    db_manager: DatabaseManager,
):
    """esc during the run confirm cancels the confirmation and STAYS."""
    campaign = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_detail(pilot, app, campaign.id)
        await activate_action(pilot, screen, 0)  # Run now: arms the confirm
        await wait_text(pilot, "#run-status", "Enter to confirm")
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen  # still on the detail screen
        await wait_text(pilot, "#run-status", "Cancelled")
        assert screen.panel.run_active is False
        # A second esc (nothing armed) leaves the screen.
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not screen


class _SlowRunDetail(CampaignDetailScreen):
    async def run_now_body(self) -> dict:
        for _ in range(30):
            await asyncio.sleep(0.05)
        return {"status": "success", "scanned": 1, "sent": 0}


@pytest.mark.unit
async def test_detail_esc_mid_run_warns_then_leaves(db_manager: DatabaseManager):
    campaign = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_detail(pilot, app, campaign.id, screen_cls=_SlowRunDetail)
        await activate_action(pilot, screen, 0)  # Run now: arms the confirm
        await wait_text(pilot, "#run-status", "Enter to confirm")
        await pilot.press("enter")
        await wait_text(pilot, "#run-status", "Running")
        # First esc warns and stays; the run is not stopped by leaving.
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen
        await wait_text(pilot, "#run-status", "esc again")
        # Second esc leaves anyway.
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not screen


class _StoppableRunDetail(CampaignDetailScreen):
    async def run_now_body(self) -> dict:
        done = 0
        for i in range(40):
            if self.panel.stop_event is not None and self.panel.stop_event.is_set():
                return {
                    "status": "cancelled", "scanned": done, "sent": 0,
                    "possibly_sent": 0, "existing": 0, "failed": 0,
                    "total_processed": done,
                }
            self.panel.progress(f"profile {i + 1}")
            await asyncio.sleep(0.12)
            done += 1
        return {"status": "success", "scanned": done, "sent": 0}


@pytest.mark.unit
async def test_detail_stop_control_stops_run(db_manager: DatabaseManager):
    """The issue #43 stop semantics survive the move onto the detail screen:
    focus lands on the Stop button, Enter stops after the current 'profile',
    and the partial summary renders like a completion."""
    campaign = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_detail(
            pilot, app, campaign.id, screen_cls=_StoppableRunDetail
        )
        await activate_action(pilot, screen, 0)  # Run now: arms the confirm
        await wait_text(pilot, "#run-status", "Enter to confirm")
        await pilot.press("enter")
        await wait_text(pilot, "#run-status", "Running")
        stop = screen.query_one("#run-stop", Button)
        assert app.focused is stop
        await _wait_log(pilot, screen, "profile 1")
        await pilot.press("enter")
        status = await wait_text_timed(pilot, "#run-status", "Stopped at your request")
        assert "Stopped at your request" in status
        assert "stopped at your request — partial results" in log_text(screen)


@pytest.mark.unit
async def test_detail_mutation_disarms_armed_run_confirm(
    db_manager: DatabaseManager,
):
    """An armed run confirm must not survive a state change: deactivating the
    campaign disarms it, so a follow-up Enter cannot start a run whose 'active'
    gate no longer holds (reviewer finding, iteration 1)."""
    campaign = make_campaign(db_manager, name="Gate Hole")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_detail(pilot, app, campaign.id)
        await activate_action(pilot, screen, 0)  # Run now: arms the confirm
        await wait_text(pilot, "#run-status", "Enter to confirm")
        # Deactivate directly (not via the ACTIONS list): the point under test
        # is the state-change disarm guard, not this setup step's own path.
        screen.action_toggle_active()
        # Wait on the UI-applied signal (the reloaded detail), not just the DB
        # write, so the follow-up Enter runs against settled screen state.
        await wait_text(pilot, "#detail-status", "select an action")
        for _ in range(60):
            await pilot.pause()
            if screen._active is False:
                break
        assert screen._active is False
        bar = screen.query_one("#detail-run-panel ConfirmBar", ConfirmBar)
        assert not bar.armed  # the toggle disarmed the stale confirm
        await pilot.press("enter")  # actions list has focus: re-requests Run now
        await pilot.pause()
        assert screen.panel.run_active is False  # blocked by the inactive gate
        status = await wait_text(pilot, "#run-status", "inactive")
        assert "inactive" in status


@pytest.mark.unit
async def test_delete_confirm_keeps_focus_after_dismissing_run_confirm(
    db_manager: DatabaseManager,
):
    """Arming delete while Run now is already armed dismisses the run confirm,
    whose deferred ConfirmDismissed refocus must NOT steal focus from the
    freshly armed delete bar — otherwise the promised Enter lands on 'Run now'
    and a second Enter starts a run the user never asked for (reviewer
    finding, iteration 2). ``action_delete`` is called directly: with no
    accelerator left, arming delete while a *different* confirm is armed isn't
    reachable through the ACTIONS list (it isn't focused), but the dismiss
    race it exercises is still a real state-machine guard worth pinning."""
    campaign = make_campaign(db_manager, name="Focus Steal")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_detail(pilot, app, campaign.id)
        await activate_action(pilot, screen, 0)  # Run now: arms the confirm
        await wait_text(pilot, "#run-status", "Enter to confirm")
        screen.action_delete()
        await wait_text(pilot, "#detail-status", "Enter to confirm")
        await pilot.pause()  # let the queued ConfirmDismissed handler run
        await pilot.pause()
        delete_bar = screen.query_one("#detail-delete-confirm", ConfirmBar)
        assert delete_bar.armed
        # Focus stays on the delete confirm the whole time…
        assert app.focused is delete_bar.query_one(".confirm-yes", Button)
        # …and the run confirm is gone, so no stale prompt contradicts it.
        assert not screen.query_one("#detail-run-panel ConfirmBar", ConfirmBar).armed
        await pilot.press("enter")  # the promised Enter deletes — never runs
        for _ in range(60):
            if db_manager.get_campaign(campaign.id) is None:
                break
            await pilot.pause()
        assert db_manager.get_campaign(campaign.id) is None
        assert screen.panel.run_active is False  # no run was ever started


@pytest.mark.unit
async def test_failed_delete_returns_focus_to_actions(db_manager: DatabaseManager):
    """A delete that fails server-side (campaign already gone) must not strand
    focus on the hidden confirm bar."""
    campaign = make_campaign(db_manager, name="Ghost")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_detail(pilot, app, campaign.id)
        await activate_action(pilot, screen, 5)  # Delete: arms the confirm
        await wait_text(pilot, "#detail-status", "Enter to confirm")
        db_manager.delete_campaign(campaign.id)  # yanked from under the screen
        await pilot.press("enter")  # confirm → delete fails: "Campaign not found."
        await wait_text(pilot, "#detail-status", "not found")
        actions = screen.query_one("#detail-actions", ListView)
        assert app.focused is actions  # keyboard alive again
        assert app.screen is screen  # a failed delete does not pop the screen


@pytest.mark.unit
async def test_detail_mutations_blocked_while_run_active(
    db_manager: DatabaseManager,
):
    """Campaign mutations wait for the stop control while a run is active
    (codex gate finding): delete, toggle and edit are refused — the screen must
    never show a deleted/edited/inactive campaign while invites keep sending —
    while the read-only export stays available. Actions are invoked directly
    once the run is active: focus has moved to the Stop button by then (not
    the ACTIONS list), and there is no accelerator left to reach them by key —
    the gate under test is the same either way."""
    campaign = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_detail(
            pilot, app, campaign.id, screen_cls=_StoppableRunDetail
        )
        await activate_action(pilot, screen, 0)  # Run now: arms the confirm
        await wait_text(pilot, "#run-status", "Enter to confirm")
        await pilot.press("enter")
        await wait_text(pilot, "#run-status", "Running")

        screen.action_delete()
        status = await wait_text(pilot, "#detail-status", "stop it before deleting")
        assert "stop it before deleting" in status
        assert not screen.query_one("#detail-delete-confirm", ConfirmBar).armed
        assert db_manager.get_campaign(campaign.id) is not None

        screen.action_toggle_active()
        await wait_text(pilot, "#detail-status", "changing its active state")
        assert db_manager.get_campaign(campaign.id).active is True  # unchanged

        screen.action_edit()
        await wait_text(pilot, "#detail-status", "editing the campaign")
        assert app.screen is screen  # no edit screen was pushed

        # The read-only export is deliberately NOT blocked mid-run.
        screen.action_export()
        await wait_text(pilot, "#detail-status", "No contacts to export")

        screen.panel.request_stop()  # let the run wind down before teardown
        await wait_text_timed(pilot, "#run-status", "Stopped at your request")


@pytest.mark.unit
async def test_detail_run_blocked_when_campaign_inactive(
    db_manager: DatabaseManager,
):
    campaign = make_campaign(db_manager, name="Paused", active=False)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_detail(pilot, app, campaign.id)
        await activate_action(pilot, screen, 0)  # Run now
        status = await wait_text(pilot, "#run-status", "inactive")
        assert "inactive" in status
        assert screen.panel.run_active is False
        assert not screen.query_one("#detail-run-panel ConfirmBar", ConfirmBar).armed


@pytest.mark.unit
async def test_detail_check_blocked_without_pending(db_manager: DatabaseManager):
    campaign = make_campaign(db_manager, name="Nothing pending")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_detail(pilot, app, campaign.id)
        await activate_action(pilot, screen, 1)  # Check acceptances
        status = await wait_text(pilot, "#run-status", "No pending invites")
        assert "No pending invites" in status
        assert screen.panel.run_active is False


@pytest.mark.unit
async def test_detail_run_degraded_without_settings(db_manager: DatabaseManager):
    campaign = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    app.settings = None  # nothing to fall back to
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = CampaignDetailScreen(db_manager, campaign.id)  # no settings injected
        app.push_screen(screen)
        await wait_text(pilot, "#detail-status", "select an action")
        await activate_action(pilot, screen, 0)  # Run now
        status = await wait_text(pilot, "#run-status", "unavailable")
        assert "unavailable" in status


@pytest.mark.unit
async def test_detail_connect_automate_refuses_inactive_campaign(
    db_manager: DatabaseManager,
):
    """Defense in depth behind the action-time gate: the body re-reads the
    campaign and refuses to run it if it went inactive meanwhile."""
    campaign = make_campaign(db_manager, name="Deactivated meanwhile")

    class _Settings:
        def get_automation_settings(self):
            return {"search_limit": 10}

    calls = []

    class _Fake:
        async def search_and_connect(self, *args, **kwargs):
            calls.append(args)
            return {}

    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = CampaignDetailScreen(db_manager, campaign.id, settings=_Settings())
        app.push_screen(screen)
        await wait_text(pilot, "#detail-status", "select an action")
        screen._automation_settings = screen._resolve_settings()
        db_manager.update_campaign(campaign.id, {"active": False})
        with pytest.raises(RuntimeError, match="inactive"):
            await screen._connect_automate(_Fake())
        assert calls == []  # nothing was sent


@pytest.mark.unit
async def test_detail_mutation_racing_reload_is_not_lost(
    db_manager: DatabaseManager,
):
    """A reload scheduled in the same tick as a mutation must not cancel it:
    the mutation workers run in their own group, so the toggle lands and
    `_busy` clears (reviewer finding, iteration 1)."""
    campaign = make_campaign(db_manager, name="Race")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = CampaignDetailScreen(db_manager, campaign.id)
        app.push_screen(screen)
        await wait_text(pilot, "#detail-status", "select an action")
        screen.action_toggle_active()
        screen.load_detail()  # same-tick reload (screen resume / run finished)
        for _ in range(80):
            await pilot.pause()
            if db_manager.get_campaign(campaign.id).active is False and not screen._busy:
                break
        assert db_manager.get_campaign(campaign.id).active is False  # not lost
        assert screen._busy is False  # not wedged


@pytest.mark.unit
async def test_detail_connect_automate_maps_and_uses_search_limit(
    db_manager: DatabaseManager,
):
    """The real automate body hands the campaign and the settings' search limit
    to search_and_connect, wires the panel's progress/stop seams, and maps the
    engine results through map_connect_results."""
    campaign = make_campaign(db_manager, name="Wired")

    class _Settings:
        def get_automation_settings(self):
            return {"search_limit": 10}

    seen = {}

    class _Fake:
        async def search_and_connect(
            self, c, limit, progress_callback=None, stop_event=None
        ):
            seen["campaign_id"] = c.id
            seen["limit"] = limit
            seen["progress"] = progress_callback
            return {
                "sent": 2, "possibly_sent": 0, "failed": 0, "existing": 1,
                "total_processed": 3, "scanned": 5, "stopped_reason": None,
            }

    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = CampaignDetailScreen(
            db_manager, campaign.id, settings=_Settings()
        )
        app.push_screen(screen)
        await wait_text(pilot, "#detail-status", "select an action")
        screen._automation_settings = screen._resolve_settings()
        result = await screen._connect_automate(_Fake())
        assert seen["campaign_id"] == campaign.id
        assert seen["limit"] == 10
        assert seen["progress"] == screen.panel.progress
        assert result["status"] == "success"


# ── gating on the standalone screen base (issue #44 removed the last
# concrete example, ExtractProfilesScreen; the gate itself is generic to
# AutomationRunScreen, so it's exercised via the _FakeRunScreen double) ──────


@pytest.mark.unit
async def test_run_screen_degraded_without_settings(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(_FakeRunScreen(db_manager, None))
        text = await wait_text(pilot, "#run-status", "unavailable")
        assert "unavailable" in text
