"""Tests for the home launcher's UX affordances (issue #24).

Covers the worker-populated workspace summary (onboarding vs. counts vs. degraded)
and the number-key quick-navigation, using Textual's headless harness with a real
``DatabaseManager`` — the actual data-flow path, not mocks.
"""

import pytest
from textual.widgets import Static

from database.operations import DatabaseManager
from tui.app import CampaignsScreen, HomeScreen, LinkedInTUI
from tui.screens.home import HomeSummary


async def wait_home_status(pilot, needle: str, tries: int = 60) -> str:
    status = pilot.app.screen.query_one("#home-status", Static)
    for _ in range(tries):
        if needle in str(status.render()):
            break
        await pilot.pause()
    return str(status.render())


# ── HomeSummary.line() unit tests (issue #46) ──────────────────────────────
# The enforced daily cap is per-campaign; the DAILY_CONNECTION_LIMIT env value
# is only the fallback, so the summary must never render usage against it.


@pytest.mark.unit
def test_home_summary_line_unconfigured():
    line = HomeSummary(
        configured=False, campaigns=None, used_today=None,
        active_limits=None, db_ok=True,
    ).line()
    assert "Not configured" in line
    assert "LINKEDIN_EMAIL" in line


@pytest.mark.unit
def test_home_summary_line_multiple_active_campaigns():
    """With several active campaigns no limit is quoted: the day counter is
    global, so any aggregate of per-campaign caps would misread as a combined
    budget enforcement cannot honor."""
    line = HomeSummary(
        configured=True, campaigns=3, used_today=7,
        active_limits=(80, 20), db_ok=True,
    ).line()
    assert "3 campaigns" in line
    assert "7 sent today" in line
    assert "limit" not in line
    assert "ready" in line
    assert "7/" not in line  # never "used/env-fallback"


@pytest.mark.unit
def test_home_summary_line_single_active_campaign():
    line = HomeSummary(
        configured=True, campaigns=1, used_today=4,
        active_limits=(80,), db_ok=True,
    ).line()
    assert "4 sent today" in line
    assert "limit 80 (1 active campaign)" in line
    assert "daily limit reached" not in line


@pytest.mark.unit
def test_home_summary_line_single_active_campaign_at_cap():
    """At/over the single active campaign's cap the exhausted state is called
    out — enforcement (used >= limit) would stop a run started now."""
    line = HomeSummary(
        configured=True, campaigns=1, used_today=20,
        active_limits=(20,), db_ok=True,
    ).line()
    assert "20 sent today" in line
    assert "limit 20 (1 active campaign) — daily limit reached" in line


@pytest.mark.unit
def test_home_summary_line_without_active_campaigns():
    """With nothing active there is no binding limit to show — just the count."""
    line = HomeSummary(
        configured=True, campaigns=2, used_today=7,
        active_limits=(), db_ok=True,
    ).line()
    assert "7 sent today" in line
    assert "limit" not in line
    assert "/" not in line


@pytest.mark.unit
def test_home_summary_line_degraded():
    line = HomeSummary(
        configured=True, campaigns=None, used_today=None,
        active_limits=None, db_ok=False,
    ).line()
    assert "database unavailable" in line


# ── screen-level tests (threaded worker → #home-status) ────────────────────


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
        assert "0 sent today" in text
        # The campaign's own limit — never the env fallback (issue #46).
        assert "limit 5 (1 active campaign)" in text
        assert "0/" not in text
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
async def test_home_nav_reaches_campaigns_via_arrows_and_enter(db_manager: DatabaseManager):
    """Arrows + Enter reach every destination (owner rule, 2026-07-09: number-
    key jumps were removed; the ListView is the only navigation path left)."""
    from textual.widgets import ListView

    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, HomeScreen)
        nav = app.screen.query_one("#home-nav", ListView)
        while nav.index != 1:  # Campaigns is the second home item
            await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, CampaignsScreen)


@pytest.mark.unit
async def test_home_esc_quit_guard_needs_two_presses(db_manager: DatabaseManager):
    """First esc arms the quit guard (shown in the workspace-summary line)
    without quitting; a second, immediate esc quits. Any other key disarms
    it, so a stray esc doesn't linger as an accidental trap."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, HomeScreen)
        text = await wait_home_status(pilot, "ready")  # summary settled first

        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen  # first esc does not quit
        armed = str(app.screen.query_one("#home-status", Static).render())
        assert "Press esc again to quit" in armed
        assert app.is_running

        # Any other key disarms it and restores the summary line.
        await pilot.press("down")
        await pilot.pause()
        restored = str(app.screen.query_one("#home-status", Static).render())
        assert restored == text
        assert app.is_running

        # Two esc presses in a row quit.
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not app.is_running


# ── campaign-centric navigation (issue #42) ─────────────────────────────────


@pytest.mark.unit
def test_home_shrinks_to_four_destinations():
    """Home lists exactly Dashboard · Campaigns · New Campaign · Settings;
    Execute/Check folded into the campaign detail screen (issue #42)."""
    from tui.nav import HOME_ITEMS

    assert [item.key for item in HOME_ITEMS] == [
        "dashboard", "campaigns", "create", "settings",
    ]


@pytest.mark.unit
async def test_home_renders_only_home_items(db_manager: DatabaseManager):
    from textual.widgets import ListView

    from tui.nav import HOME_ITEMS

    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        nav = app.screen.query_one("#home-nav", ListView)
        ids = [item.id for item in nav.children]
        assert ids == [f"nav-{item.key}" for item in HOME_ITEMS]
        assert "nav-execute" not in ids and "nav-check" not in ids
