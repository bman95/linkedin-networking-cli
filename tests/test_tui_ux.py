"""Regression tests for the 2026-07-07 UX pass (issue #24).

Pins the escape semantics ("esc to cancel" must cancel, not leave), the
dirty-form discard guard, and the navigation affordances (New Campaign on the
campaigns list, activating a dashboard row). All flows run in the headless
harness with no browser.

Issue #42 (owner rule, 2026-07-09) superseded the two-press ``ctrl+r`` confirm
with a focused inline confirm (Enter confirms, esc cancels); the esc semantics
pinned here are unchanged — esc still cancels an armed confirmation and warns
once mid-run — only the arming/confirming control moved onto focusable buttons.

Issue #49 (same owner rule) swept the remaining key-only actions on every other
screen (Campaigns' New/Refresh, Dashboard's and Settings' Refresh, Create/Edit's
Save) onto visible, focusable buttons; issue #49's follow-up pass (2026-07-10)
then removed every letter/ctrl-chord accelerator app-wide, so tab + Enter (or
arrows + Enter over a list) is now the *only* path to any of these — the tests
below drive them that way throughout.
"""

import asyncio

import pytest
from textual.widgets import Button, Input, ListView, Static

from database.operations import DatabaseManager
from tui.app import LinkedInTUI
from tui.screens.automation_run import AutomationRunScreen
from tui.screens.campaign_detail import CampaignDetailScreen
from tui.screens.campaign_edit import CampaignEditScreen
from tui.screens.campaigns import CampaignsScreen
from tui.screens.create_campaign import CreateCampaignScreen
from tui.screens.dashboard import DashboardScreen
from tui.screens.settings_view import SettingsScreen


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


async def tab_until_focused(pilot, widget, tries: int = 40) -> None:
    """Press tab until focus lands on ``widget``.

    Proves the DOM tab order actually reaches the widget (owner rule: every
    action must be tab-reachable), rather than asserting on a hardcoded key
    count that would silently stop testing anything if the order shifted.
    """
    for _ in range(tries):
        if pilot.app.focused is widget:
            return
        await pilot.press("tab")
        await pilot.pause()
    raise AssertionError(f"tab never reached {widget!r}")


# ── campaign detail: esc cancels an armed delete ────────────────────────────


@pytest.mark.unit
async def test_detail_esc_cancels_armed_delete(db_manager: DatabaseManager):
    """Activating Delete on the ACTIONS list arms the confirm; esc cancels the
    confirmation and STAYS."""
    campaign = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignDetailScreen(db_manager, campaign.id))
        await wait_text(pilot, "#detail-status", "select an action")
        actions = app.screen.query_one("#detail-actions", ListView)
        while actions.index != 5:  # Delete is the last action
            await pilot.press("down")
        await pilot.press("enter")
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
        screen.query_one("#run-start", Button).focus()
        await pilot.press("enter")
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
        screen.query_one("#run-start", Button).focus()
        await pilot.press("enter")
        await wait_text(pilot, "#run-status", "Enter to confirm")
        await pilot.press("enter")  # the confirm bar's focused Yes button
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


# ── tab-reachable actions (issue #49): arrows/tab + Enter, no accelerator ───


@pytest.mark.unit
async def test_campaigns_new_reachable_by_tab_and_enter(db_manager: DatabaseManager):
    """New Campaign is a visible, focusable button below the table — tab +
    Enter reach it without ever pressing the 'n' accelerator."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignsScreen(db_manager))
        await wait_text(pilot, "#campaigns-status", "No campaigns")
        button = app.screen.query_one("#campaigns-new", Button)
        await tab_until_focused(pilot, button)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, CreateCampaignScreen)


@pytest.mark.unit
async def test_campaigns_refresh_reachable_by_tab_and_enter(db_manager: DatabaseManager):
    make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignsScreen(db_manager))
        await wait_text(pilot, "#campaigns-status", "campaign")
        button = app.screen.query_one("#campaigns-refresh", Button)
        await tab_until_focused(pilot, button)
        called = []
        app.screen.load_campaigns = lambda: called.append(True)
        await pilot.press("enter")
        await pilot.pause()
        assert called


@pytest.mark.unit
async def test_dashboard_refresh_reachable_by_tab_and_enter(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(DashboardScreen(db_manager))
        await wait_text(pilot, "#dashboard-status", "No campaigns")
        button = app.screen.query_one("#dashboard-refresh", Button)
        await tab_until_focused(pilot, button)
        called = []
        app.screen.load_dashboard = lambda: called.append(True)
        await pilot.press("enter")
        await pilot.pause()
        assert called


@pytest.mark.unit
async def test_settings_refresh_reachable_by_tab_and_enter(db_manager: DatabaseManager):
    """Settings previously had no focusable widget at all; Refresh is now one
    (regression guard for the most severe instance the issue #49 sweep found).
    The screen now also carries the editable rate-limit inputs and Save, so
    ``tab_until_focused`` proves Refresh stays tab-reachable behind them;
    Enter still drives the actual activation."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(SettingsScreen(db_manager))
        await wait_text(pilot, "#settings-status", "editable")
        button = app.screen.query_one("#settings-refresh", Button)
        await tab_until_focused(pilot, button)
        called = []
        app.screen.load_settings = lambda: called.append(True)
        await pilot.press("enter")
        await pilot.pause()
        assert called


@pytest.mark.unit
async def test_create_campaign_full_happy_path_via_tab_and_enter(
    db_manager: DatabaseManager,
):
    """The whole create flow — fill the name, tab to Create, Enter — is the
    only path now (owner rule: no accelerators, 2026-07-10)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CreateCampaignScreen(db_manager))
        await pilot.pause()
        app.screen.query_one("#field-name", Input).value = "Tab Reachable"
        button = app.screen.query_one("#form-submit", Button)
        await tab_until_focused(pilot, button)
        await pilot.press("enter")
        await wait_text(pilot, "#create-status", "created")
        # The submit button locks with the rest of the form on success (the
        # one behavior this PR added to the disable-selector).
        assert button.disabled is True

    assert db_manager.get_campaigns(active_only=False)[0].name == "Tab Reachable"


@pytest.mark.unit
async def test_edit_save_reachable_by_tab_and_enter(db_manager: DatabaseManager):
    """Editing end to end — change the name, tab to Save, Enter — is the only
    path now (owner rule: no accelerators, 2026-07-10)."""
    c = make_campaign(db_manager, name="Old Name")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignEditScreen(db_manager, c.id))
        await wait_text(pilot, "#edit-status", "Edit the fields")
        app.screen.query_one("#field-name", Input).value = "New Name"
        button = app.screen.query_one("#form-submit", Button)
        await tab_until_focused(pilot, button)
        await pilot.press("enter")
        await wait_text(pilot, "#edit-status", "updated")
        # The submit button locks with the rest of the form on success (the
        # one behavior this PR added to the disable-selector).
        assert button.disabled is True

    assert db_manager.get_campaign(c.id).name == "New Name"
