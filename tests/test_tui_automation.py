"""Tests for the automation screens (Execute / Check / Extract) — issue #24.

The real browser run is user-initiated and not exercised here; instead we test
every surface around it: the typed-error mapping (pure), the select → confirm →
run → summary/error pipeline (via a browser-free fake that overrides the single
``run_body`` seam), and each real screen's gating / selection / validation.
"""

import asyncio
import time

import pytest
from textual.widgets import Button, Input, RichLog, Select, Static

from database.operations import DatabaseManager
from exceptions import (
    CaptchaDetectedException,
    NotAuthenticatedException,
    RateLimitExceededException,
    SelectorNotFoundException,
)
from tui.app import (
    CheckConnectionsScreen,
    ExecuteCampaignScreen,
    ExtractProfilesScreen,
    LinkedInTUI,
)
from tui.screens.automation_errors import describe_automation_error
from tui.screens.automation_run import AutomationRunScreen


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
        return {"status": "success", "n": 7}

    def render_result(self, result: dict) -> str:
        return f"summary: n={result.get('n')}"


async def _run_fake(pilot, app, outcome):
    app.push_screen(_FakeRunScreen(app.db_manager, _DummySettings(), outcome=outcome))
    await pilot.pause()
    await pilot.press("ctrl+r")  # arm confirmation
    await wait_text(pilot, "#run-status", "ctrl+r again")
    await pilot.press("ctrl+r")  # start


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
async def test_execute_automate_maps_stopped_reason_to_safety_stop(
    db_manager: DatabaseManager,
):
    """The Execute screen mirrors the classic CLI: a protective stop is a
    safety_stop status — even (especially) when nothing was scanned yet — and
    its rendering names the CAPTCHA, not 'no profiles matched'."""

    class _Settings:
        def get_automation_settings(self):
            return {"search_limit": 10}

    def _fake_automation(scanned):
        class _Fake:
            async def search_and_connect(
                self, campaign, limit, progress_callback=None, max_sends=None,
                stop_event=None,
            ):
                return {
                    "sent": 1 if scanned else 0, "possibly_sent": 0,
                    "failed": 0, "existing": 0, "total_processed": scanned,
                    "scanned": scanned, "stopped_reason": "captcha",
                }
        return _Fake()

    screen = ExecuteCampaignScreen(db_manager, _Settings())
    screen._selected = make_campaign(db_manager, name="Exec safety")

    # Mid-run stop (some cards scanned) and first-page stop (nothing scanned):
    # both must map to safety_stop, never to success or no_profiles.
    for scanned in (3, 0):
        result = await screen.automate(_fake_automation(scanned))
        assert result["status"] == "safety_stop"
        text = screen.render_result(result)
        assert "CAPTCHA" in text
        assert "No profiles matched" not in text


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
    await pilot.press("ctrl+r")  # arm confirmation
    await wait_text(pilot, "#run-status", "ctrl+r again")
    await pilot.press("ctrl+r")  # start
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
        assert "Stop requested — finishing the current profile" in log_text(screen)

        # The partial summary renders like a normal completion (not an error)
        # and its count matches the work actually done before the stop.
        text = log_text(screen)
        assert "summary: n=" in text
        n = int(text.split("summary: n=")[1].split()[0])
        assert n == text.count("profile ")
        assert 0 < n < 40

        # Screen re-enabled: run over, control hidden, a single esc leaves.
        assert screen._run_done and not screen._run_active
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
        screen._request_stop()  # what Enter on the button and 's' dispatch to
        status = str(screen.query_one("#run-status", Static).render())
        assert "Stopping" in status
        assert screen.query_one("#run-stop", Button).disabled is True
        await wait_text_timed(pilot, "#run-status", "Stopped at your request")


@pytest.mark.unit
async def test_stop_via_s_accelerator(db_manager: DatabaseManager):
    """The optional 's' accelerator requests the same stop as the button."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _start_stoppable(pilot, app)
        await _wait_log(pilot, screen, "profile 1")
        await pilot.press("s")
        status = await wait_text_timed(pilot, "#run-status", "Stopped at your request")
        assert "Stopped at your request" in status
        assert "summary: n=" in log_text(screen)


@pytest.mark.unit
async def test_midrun_esc_warning_mentions_stop_control(
    db_manager: DatabaseManager,
):
    """The first mid-run esc still warns that leaving does not stop the run,
    and now points at the stop control (issue #43 acceptance criterion)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _start_stoppable(pilot, app)
        await pilot.press("escape")
        status = await wait_text(pilot, "#run-status", "leaving does not stop it")
        assert "leaving does not stop it" in status
        assert "Stop" in status and "s)" in status
        assert app.screen is screen  # first esc warns, does not leave


@pytest.mark.unit
async def test_stop_racing_natural_completion_still_reports_cancelled(
    db_manager: DatabaseManager,
):
    """A stop requested during the final profile can lose the race with the
    loop's flag check — the body then reports success. The user asked to stop
    and saw 'Stopping…', so _finish honors the request (one policy for every
    run screen)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(_FakeStoppableRunScreen(app.db_manager, _DummySettings()))
        await pilot.pause()
        screen = app.screen
        # A running screen mid-stop: the log is visible (as _begin_run leaves
        # it) and the stop was requested but not observed by the loop.
        screen.query_one("#run-log", RichLog).display = True
        screen._run_active = True
        screen._stop_requested = True
        screen._finish({"status": "success", "n": 3}, None)
        status = str(screen.query_one("#run-status", Static).render())
        assert "Stopped at your request" in status
        await pilot.pause()  # let the RichLog render the summary line
        assert "summary: n=3" in log_text(screen)


@pytest.mark.unit
async def test_check_automate_direct_mode_uses_real_partial_counts(
    db_manager: DatabaseManager,
):
    """Direct mode sums the checker's real per-contact counts, so a stopped
    batch reports only the contacts actually visited — not the worklist size —
    and maps the checker's stopped flag to the cancelled status."""
    campaign = make_campaign(db_manager, name="Direct check")
    for i in range(5):
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": f"Pending {i}",
            "profile_url": f"https://www.linkedin.com/in/pending{i}/",
            "status": "sent",
        })

    class _Fake:
        async def check_connection_status(
            self, contacts, progress_callback=None, stop_event=None
        ):
            assert len(contacts) == 5  # the whole worklist was handed over…
            return {"checked": 2, "newly_accepted": 1, "failed": 0, "stopped": True}

    screen = CheckConnectionsScreen(db_manager, _DummySettings())
    screen._mode = "direct"
    screen._target_ids = [campaign.id]
    result = await screen.automate(_Fake())
    assert result["status"] == "cancelled"
    assert result["checked"] == 2  # …but only the visited count is reported
    assert result["newly_accepted"] == 1
    assert "stopped at your request" in screen.render_result(result)


@pytest.mark.unit
async def test_execute_automate_maps_cancelled_to_partial_completion(
    db_manager: DatabaseManager,
):
    """A 'cancelled' stopped_reason maps to the cancelled status and renders as
    a partial completion — never the CAPTCHA safety-stop copy."""

    class _Settings:
        def get_automation_settings(self):
            return {"search_limit": 10}

    class _Fake:
        async def search_and_connect(
            self, campaign, limit, progress_callback=None, max_sends=None,
            stop_event=None,
        ):
            return {
                "sent": 2, "possibly_sent": 0, "failed": 0, "existing": 1,
                "total_processed": 3, "scanned": 5, "stopped_reason": "cancelled",
            }

    screen = ExecuteCampaignScreen(db_manager, _Settings())
    screen._selected = make_campaign(db_manager, name="Exec cancel")
    result = await screen.automate(_Fake())
    assert result["status"] == "cancelled"
    text = screen.render_result(result)
    assert "stopped at your request" in text
    assert "Invites sent: 2" in text
    assert "CAPTCHA" not in text


# ── gating / selection on the real screens ──────────────────────────────────


@pytest.mark.unit
async def test_execute_degraded_without_settings(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(ExecuteCampaignScreen(db_manager, None))
        text = await wait_text(pilot, "#run-status", "unavailable")
        assert "unavailable" in text


@pytest.mark.unit
async def test_execute_no_active_campaigns(db_manager: DatabaseManager):
    make_campaign(db_manager, name="Paused", active=False)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(ExecuteCampaignScreen(db_manager, _DummySettings()))
        text = await wait_text(pilot, "#run-status", "No active campaigns")
        assert "No active campaigns" in text


@pytest.mark.unit
async def test_execute_selection_validates(db_manager: DatabaseManager):
    make_campaign(db_manager, name="Active One", active=True)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(ExecuteCampaignScreen(db_manager, _DummySettings()))
        await wait_text(pilot, "#run-status", "ctrl+r")
        summary = app.screen.validate()  # first campaign is pre-selected
        assert summary is not None
        assert "Active One" in summary


@pytest.mark.unit
async def test_check_no_pending_connections(db_manager: DatabaseManager):
    make_campaign(db_manager, name="No pending")  # no contacts at all
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CheckConnectionsScreen(db_manager, _DummySettings()))
        text = await wait_text(pilot, "#run-status", "No campaigns with pending")
        assert "No campaigns with pending" in text


@pytest.mark.unit
async def test_extract_manual_url_validation(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(ExtractProfilesScreen(db_manager, _DummySettings()))
        await wait_text(pilot, "#run-status", "ctrl+r")
        screen = app.screen
        screen.query_one("#run-mode", Select).value = "manual"

        screen.query_one("#run-url", Input).value = "not-a-url"
        assert screen.validate() is None  # rejected

        screen.query_one("#run-url", Input).value = "https://www.linkedin.com/in/jane"
        summary = screen.validate()
        assert summary is not None and "profile" in summary.lower()
