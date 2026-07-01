"""Tests for the home launcher's UX affordances (issue #24).

Covers the worker-populated workspace summary (onboarding vs. counts vs. degraded)
and the number-key quick-navigation, using Textual's headless harness with a real
``DatabaseManager`` — the actual data-flow path, not mocks.
"""

import pytest

from textual.widgets import Static

from database.operations import DatabaseManager
from tui.app import CampaignsScreen, HomeScreen, LinkedInTUI


async def wait_home_status(pilot, needle: str, tries: int = 60) -> str:
    status = pilot.app.screen.query_one("#home-status", Static)
    for _ in range(tries):
        if needle in str(status.render()):
            break
        await pilot.pause()
    return str(status.render())


@pytest.mark.unit
async def test_home_summary_onboarding_when_unconfigured(db_manager: DatabaseManager):
    """With no credentials in the env, the summary guides the next step."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        text = await wait_home_status(pilot, "Not configured")
        assert "Not configured" in text
        assert "LINKEDIN_EMAIL" in text


@pytest.mark.unit
async def test_home_summary_reports_counts_when_configured(
    db_manager: DatabaseManager, monkeypatch
):
    """When configured, the summary reports live counts and readiness."""
    from config.settings import AppSettings

    monkeypatch.setattr(AppSettings, "validate_credentials", lambda self: True)
    db_manager.create_campaign({"name": "Solo", "daily_limit": 5})

    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        text = await wait_home_status(pilot, "campaign")
        assert "1 campaign" in text
        assert "ready" in text


@pytest.mark.unit
async def test_home_summary_degraded_without_db(
    db_manager: DatabaseManager, monkeypatch
):
    """A degraded app (no DB) still renders a friendly summary."""
    from config.settings import AppSettings

    monkeypatch.setattr(AppSettings, "validate_credentials", lambda self: True)
    app = LinkedInTUI(db_manager=db_manager)
    app.db_manager = None
    async with app.run_test() as pilot:
        await pilot.pause()
        text = await wait_home_status(pilot, "unavailable")
        assert "database unavailable" in text


@pytest.mark.unit
async def test_home_shows_mascot(db_manager: DatabaseManager):
    """The home hero renders the image-based 'Bit' mascot (half-block art)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        mascot = str(app.screen.query_one("#home-mascot", Static).render())
        # Filled cells are the universal Block-Elements half blocks ``▀`` / ``█``.
        assert any(ch in mascot for ch in "▀█")
        assert mascot.strip()


@pytest.mark.unit
async def test_home_number_key_opens_destination(db_manager: DatabaseManager):
    """Pressing a number key jumps straight to the matching screen."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, HomeScreen)
        await pilot.press("2")  # 2 -> Campaigns
        await pilot.pause()
        assert isinstance(app.screen, CampaignsScreen)
