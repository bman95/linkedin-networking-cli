"""Tests for the automation screens (Execute / Check / Extract) — issue #24.

The real browser run is user-initiated and not exercised here; instead we test
every surface around it: the typed-error mapping (pure), the select → confirm →
run → summary/error pipeline (via a browser-free fake that overrides the single
``run_body`` seam), and each real screen's gating / selection / validation.
"""

import pytest

from textual.widgets import Input, RichLog, Select, Static

from exceptions import (
    CaptchaDetectedException,
    NotAuthenticatedException,
    RateLimitExceededException,
    SelectorNotFoundException,
)
from database.operations import DatabaseManager
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
