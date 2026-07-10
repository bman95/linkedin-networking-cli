"""Tests for the Create Campaign screen — the first TUI *write* flow (issue #24).

Drives the real keyboard-first flow against a real ``DatabaseManager`` on a temp
SQLite path (no mocks): navigate from the home, fill the form, submit with
tab + Enter on the Create button, and assert the campaign is persisted with
the same field mapping the classic InquirerPy flow produces (owner rule,
2026-07-10: no letter/ctrl-chord accelerators anywhere, so this is now the
only submit path). Validation and the degraded (no-DB) state are covered too.
"""

import pytest
from textual.widgets import Button, Input, ListView, Select, Static

from automation.linkedin_mappings import get_location_urn, get_network_value
from database.operations import DatabaseManager
from tui.app import CreateCampaignScreen, HomeScreen, LinkedInTUI, SettingsScreen


async def goto_create(pilot) -> CreateCampaignScreen:
    """Open Create Campaign via arrows + Enter on the home nav list."""
    assert isinstance(pilot.app.screen, HomeScreen)
    nav = pilot.app.screen.query_one("#home-nav", ListView)
    while nav.index != 2:  # "New Campaign" is the third home item
        await pilot.press("down")
    await pilot.press("enter")
    await pilot.pause()
    screen = pilot.app.screen
    assert isinstance(screen, CreateCampaignScreen)
    return screen


async def submit_form(pilot, screen) -> None:
    """Tab to the Create button and press Enter (the only submit path)."""
    button = screen.query_one("#form-submit", Button)
    while pilot.app.focused is not button:
        await pilot.press("tab")
    await pilot.press("enter")


async def wait_status(pilot, needle: str, tries: int = 60) -> str:
    status = pilot.app.screen.query_one("#create-status", Static)
    for _ in range(tries):
        if needle in str(status.render()):
            break
        await pilot.pause()
    return str(status.render())


@pytest.mark.unit
async def test_home_nav_reaches_create_then_settings(db_manager: DatabaseManager):
    """New Campaign is the third home item; Settings is last (issue #42 shrank
    home to four destinations)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        assert isinstance(screen, CreateCampaignScreen)
        await pilot.press("escape")
        await pilot.pause()
        nav = app.screen.query_one("#home-nav", ListView)
        while nav.index != 3:  # Settings is the fourth home item
            await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)


@pytest.mark.unit
async def test_create_campaign_persists_with_classic_field_mapping(db_manager: DatabaseManager):
    """A filled form writes a campaign whose fields match the classic CLI mapping."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)

        screen.query_one("#field-name", Input).value = "Backend Engineers — SF"
        screen.query_one("#field-keywords", Input).value = "python"
        screen.query_one("#field-location", Select).value = "San Francisco Bay Area"
        screen.query_one("#field-daily", Input).value = "25"

        await submit_form(pilot, screen)
        await wait_status(pilot, "created")

    # Outside the harness: the write landed in the real DB.
    campaigns = db_manager.get_campaigns(active_only=False)
    assert len(campaigns) == 1
    c = campaigns[0]
    assert c.name == "Backend Engineers — SF"
    assert c.keywords == "python"
    assert c.daily_limit == 25
    assert c.location_display == "San Francisco Bay Area"
    assert c.geo_urn == get_location_urn("San Francisco Bay Area")
    assert c.network == get_network_value("1st + 2nd degree connections")
    assert "{name}" in c.message_template


@pytest.mark.unit
async def test_create_campaign_any_location_persists_as_none(db_manager: DatabaseManager):
    """Leaving location/industry at 'Any' stores None, mirroring the classic flow."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        screen.query_one("#field-name", Input).value = "Anywhere"
        await submit_form(pilot, screen)
        await wait_status(pilot, "created")

    c = db_manager.get_campaigns(active_only=False)[0]
    assert c.location_display is None
    assert c.geo_urn is None
    assert c.industry_display is None
    assert c.industry_ids is None


@pytest.mark.unit
async def test_create_campaign_empty_name_is_rejected(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        await submit_form(pilot, screen)
        text = await wait_status(pilot, "cannot be empty")
        assert "cannot be empty" in text
    assert db_manager.get_campaigns(active_only=False) == []


@pytest.mark.unit
async def test_create_campaign_daily_limit_out_of_range_is_rejected(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        screen.query_one("#field-name", Input).value = "Bad limit"
        screen.query_one("#field-daily", Input).value = "0"
        await submit_form(pilot, screen)
        text = await wait_status(pilot, "between 1 and 100")
        assert "between 1 and 100" in text
    assert db_manager.get_campaigns(active_only=False) == []


@pytest.mark.unit
async def test_create_campaign_message_requires_name_placeholder(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        screen.query_one("#field-name", Input).value = "No placeholder"
        screen.query_one("#field-message", Input).value = "Hello there"
        await submit_form(pilot, screen)
        text = await wait_status(pilot, "{name}")
        assert "{name}" in text
    assert db_manager.get_campaigns(active_only=False) == []


@pytest.mark.unit
async def test_create_campaign_degraded_without_db(db_manager: DatabaseManager):
    """No DB: the screen shows an unavailable state and submitting is a safe no-op."""
    app = LinkedInTUI(db_manager=db_manager)
    app.db_manager = None
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        text = await wait_status(pilot, "unavailable")
        assert "unavailable" in text
        # Submitting does not crash and does not flip into a submitting state.
        await submit_form(pilot, screen)
        await pilot.pause()
        assert screen._submitting is False
