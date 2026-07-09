"""Regression tests for the 2026-07-07 UX pass (issue #24).

Pins the escape semantics ("esc to cancel" must cancel, not leave), the
dirty-form discard guard, and the navigation affordances (``n`` on the
campaigns list, activating a dashboard row). All flows run in the headless
harness with no browser.

Issue #42 (owner rule, 2026-07-09) superseded the two-press ``ctrl+r`` confirm
with a focused inline confirm (Enter confirms, esc cancels); the esc semantics
pinned here are unchanged — esc still cancels an armed confirmation and warns
once mid-run — only the arming/confirming control moved onto focusable buttons.
"""

import asyncio

import pytest
from textual.widgets import Input, Static

from database.operations import DatabaseManager
from tui.app import LinkedInTUI
from tui.screens.automation_run import AutomationRunScreen
from tui.screens.campaign_detail import CampaignDetailScreen
from tui.screens.campaigns import CampaignsScreen
from tui.screens.create_campaign import CreateCampaignScreen
from tui.screens.dashboard import DashboardScreen


class _DummySettings:
    """Stand-in so the db+settings gate passes without a real AppSettings."""


def make_campaign(db, name="Campaign", **extra):
    data = {"name": name, "daily_limit": 20, "message_template": "Hi {name}!"}
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


# ── campaign detail: esc cancels an armed delete ────────────────────────────


@pytest.mark.unit
async def test_detail_esc_cancels_armed_delete(db_manager: DatabaseManager):
    """First d arms the delete confirm; esc cancels the confirmation and STAYS."""
    campaign = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignDetailScreen(db_manager, campaign.id))
        await wait_text(pilot, "#detail-status", "select an action")
        await pilot.press("d")
        await wait_text(pilot, "#detail-status", "Enter to confirm")
        await pilot.press("escape")
        await pilot.pause()
        # Still on the detail screen, delete disarmed, campaign intact.
        assert isinstance(app.screen, CampaignDetailScreen)
        await wait_text(pilot, "#detail-status", "cancelled")
        assert db_manager.get_campaign(campaign.id) is not None
        # A second esc (nothing armed) leaves the screen.
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CampaignDetailScreen)


# ── automation run: esc semantics around confirm and mid-run ────────────────


class _SlowRunScreen(AutomationRunScreen):
    SCREEN_TITLE = "Slow Run"
    ACTION_LABEL = "slow run"

    async def run_body(self) -> dict:
        for _ in range(30):
            await asyncio.sleep(0.05)
        return {"status": "success"}


@pytest.mark.unit
async def test_run_esc_cancels_armed_confirmation(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = _SlowRunScreen(db_manager, _DummySettings())
        app.push_screen(screen)
        await pilot.pause()
        await pilot.press("ctrl+r")
        await wait_text(pilot, "#run-status", "Enter to confirm")
        await pilot.press("escape")
        await pilot.pause()
        # Confirmation cancelled; still on the screen; run never started.
        assert app.screen is screen
        assert screen.panel.confirming is False
        assert screen.panel.run_active is False
        await wait_text(pilot, "#run-status", "Cancelled")


@pytest.mark.unit
async def test_run_esc_mid_run_warns_then_leaves(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = _SlowRunScreen(db_manager, _DummySettings())
        app.push_screen(screen)
        await pilot.pause()
        await pilot.press("ctrl+r")
        await wait_text(pilot, "#run-status", "Enter to confirm")
        await pilot.press("ctrl+r")
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


# ── create form: esc guards unsaved edits ───────────────────────────────────


@pytest.mark.unit
async def test_create_esc_pristine_leaves_immediately(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CreateCampaignScreen(db_manager))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CreateCampaignScreen)


@pytest.mark.unit
async def test_create_esc_dirty_warns_then_discards(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = CreateCampaignScreen(db_manager)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#field-name", Input).value = "Half-typed"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen  # warned, not popped
        await wait_text(pilot, "#create-status", "Unsaved changes")
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not screen
        assert db_manager.get_campaigns(active_only=False) == []


# ── navigation affordances ──────────────────────────────────────────────────


@pytest.mark.unit
async def test_campaigns_n_opens_create(db_manager: DatabaseManager):
    make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignsScreen(db_manager))
        await wait_text(pilot, "#campaigns-status", "campaign")
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, CreateCampaignScreen)


@pytest.mark.unit
async def test_dashboard_recent_row_opens_detail(db_manager: DatabaseManager):
    campaign = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(DashboardScreen(db_manager))
        await wait_text(pilot, "#dashboard-status", "Updated")
        table = app.screen.query_one("#dashboard-recent")
        table.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, CampaignDetailScreen)
        assert app.screen._campaign_id == campaign.id
