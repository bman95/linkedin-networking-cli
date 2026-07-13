"""Tests for the campaign form's non-curated location flows (issue #24 parity).

The classic CLI's Create/Edit campaign flows offer an online location search
(browser + login) and a custom-geoUrn entry beyond the curated location list;
these tests cover the TUI port in ``tui.screens.campaign_form``. The browser
never runs: ``perform_location_search`` is the seam (the worker calls it on the
screen), overridden per-test with canned results — the same approach as the
automation screens' ``run_body``. Everything else is the real pipeline:
threaded worker, marshaling, widgets, validation, and a real ``DatabaseManager``
on a temp SQLite path.
"""

import pytest
from textual.widgets import Button, Input, ListView, OptionList, Select, Static

from database.operations import DatabaseManager
from tui.app import CampaignEditScreen, CreateCampaignScreen, HomeScreen, LinkedInTUI
from tui.screens.campaign_form import CUSTOM_GEO, SEARCH_ONLINE

MADRID = {"name": "Madrid, Community of Madrid, Spain", "geoUrn": "100994331"}
TOKYO = {"name": "Greater Tokyo Area", "geoUrn": "90009620"}


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
    """Tab to the form's submit button and press Enter (the only submit path)."""
    button = screen.query_one("#form-submit", Button)
    while pilot.app.focused is not button:
        await pilot.press("tab")
    await pilot.press("enter")


async def wait_status(pilot, needle: str, status_id: str = "#create-status", tries: int = 60) -> str:
    status = pilot.app.screen.query_one(status_id, Static)
    for _ in range(tries):
        if needle in str(status.render()):
            break
        await pilot.pause()
    return str(status.render())


async def start_search(pilot, screen, query: str) -> None:
    """Select the online-search mode, type a query, and submit it."""
    screen.query_one("#field-location", Select).value = SEARCH_ONLINE
    await pilot.pause()
    box = screen.query_one("#field-location-query", Input)
    box.value = query
    box.focus()
    await pilot.press("enter")
    await pilot.pause()


@pytest.mark.unit
async def test_location_modes_reveal_their_widgets(db_manager: DatabaseManager):
    """The search/custom widgets start hidden and follow the select's mode."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        query_box = screen.query_one("#field-location-query", Input)
        geourn_box = screen.query_one("#field-location-geourn", Input)
        assert not query_box.display and not geourn_box.display

        screen.query_one("#field-location", Select).value = SEARCH_ONLINE
        await pilot.pause()
        assert query_box.display and not geourn_box.display

        screen.query_one("#field-location", Select).value = CUSTOM_GEO
        await pilot.pause()
        assert geourn_box.display and not query_box.display

        screen.query_one("#field-location", Select).value = "Any"
        await pilot.pause()
        assert not query_box.display and not geourn_box.display


@pytest.mark.unit
async def test_online_search_pick_persists_geourn(db_manager: DatabaseManager):
    """Searching, picking a result and saving stores the picked geoUrn.

    Drives the real keyboard sequence — search lands focus on the results
    list with the first result highlighted, Enter picks it — rather than
    assigning a value in Python, so this pins the actual keyboard path
    (regression: a plain Select here silently ate Enter on a blank sentinel).
    """
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        screen.perform_location_search = lambda db, settings, query: [MADRID, TOKYO]

        screen.query_one("#field-name", Input).value = "Madrid outreach"
        await start_search(pilot, screen, "Madrid")
        await wait_status(pilot, "pick one")

        picker = screen.query_one("#field-location-results", OptionList)
        assert picker.display
        assert screen.app.focused is picker
        assert picker.highlighted == 0  # first (Madrid) result pre-highlighted
        await pilot.press("enter")
        await pilot.pause()

        # The pick became the selected Location option and the search UI closed.
        assert screen.query_one("#field-location", Select).value == MADRID["name"]
        assert not screen.query_one("#field-location-query", Input).display

        await submit_form(pilot, screen)
        await wait_status(pilot, "created")

    c = db_manager.get_campaigns(active_only=False)[0]
    assert c.location_display == MADRID["name"]
    assert c.geo_urn == MADRID["geoUrn"]


@pytest.mark.unit
async def test_online_search_empty_auth_and_error_states(db_manager: DatabaseManager):
    """No results, failed auth and a raised error each land in the status line."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)

        screen.perform_location_search = lambda db, settings, query: []
        await start_search(pilot, screen, "Atlantis")
        text = await wait_status(pilot, "No locations found")
        assert "Atlantis" in text

        screen.perform_location_search = lambda db, settings, query: None
        await start_search(pilot, screen, "Madrid")
        await wait_status(pilot, "Could not authenticate")

        def _boom(db, settings, query):
            raise RuntimeError("browser exploded")

        screen.perform_location_search = _boom
        await start_search(pilot, screen, "Madrid")
        text = await wait_status(pilot, "location search")
        assert "location search" in text  # typed-error headline mentions the action


@pytest.mark.unit
async def test_submit_with_unresolved_search_is_rejected(db_manager: DatabaseManager):
    """Submitting while the select still says 'search online' must not persist."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        screen.query_one("#field-name", Input).value = "Unresolved"
        screen.query_one("#field-location", Select).value = SEARCH_ONLINE
        await pilot.pause()
        await submit_form(pilot, screen)
        await wait_status(pilot, "pick a location")
    assert db_manager.get_campaigns(active_only=False) == []


@pytest.mark.unit
async def test_custom_geourn_persists(db_manager: DatabaseManager):
    """The custom-geoUrn mode mirrors the classic 'Other (enter custom geoUrn)'."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        screen.query_one("#field-name", Input).value = "Custom geo"
        screen.query_one("#field-location", Select).value = CUSTOM_GEO
        await pilot.pause()
        screen.query_one("#field-location-geourn", Input).value = "90000084"
        # Display name left empty → classic default name.
        await submit_form(pilot, screen)
        await wait_status(pilot, "created")

    c = db_manager.get_campaigns(active_only=False)[0]
    assert c.geo_urn == "90000084"
    assert c.location_display == "Custom Location (90000084)"


@pytest.mark.unit
async def test_custom_geourn_requires_code(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        screen.query_one("#field-name", Input).value = "Missing code"
        screen.query_one("#field-location", Select).value = CUSTOM_GEO
        await pilot.pause()
        await submit_form(pilot, screen)
        await wait_status(pilot, "geoUrn")
    assert db_manager.get_campaigns(active_only=False) == []


@pytest.mark.unit
async def test_custom_geourn_requires_numeric_code(db_manager: DatabaseManager):
    """Non-numeric geoUrn is rejected — same validator as the classic CLI."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        screen.query_one("#field-name", Input).value = "Bad code"
        screen.query_one("#field-location", Select).value = CUSTOM_GEO
        await pilot.pause()
        screen.query_one("#field-location-geourn", Input).value = "not-a-code"
        await submit_form(pilot, screen)
        await wait_status(pilot, "numeric")
    assert db_manager.get_campaigns(active_only=False) == []


@pytest.mark.unit
async def test_edit_preserves_non_curated_location(db_manager: DatabaseManager):
    """Editing a campaign with a custom location keeps its geoUrn on save.

    Regression: the form previously reset a non-curated stored location to
    'Any', silently dropping the campaign's geoUrn on the next save.
    """
    created = db_manager.create_campaign({
        "name": "Tokyo campaign",
        "daily_limit": 10,
        "geo_urn": TOKYO["geoUrn"],
        "location_display": TOKYO["name"],
        "message_template": "Hi {name}!",
    })
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignEditScreen(db_manager, created.id))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, CampaignEditScreen)
        await wait_status(pilot, "Edit the fields", status_id="#edit-status")

        # The stored custom location is the selected option, not 'Any'.
        assert screen.query_one("#field-location", Select).value == TOKYO["name"]

        screen.query_one("#field-daily", Input).value = "15"
        await submit_form(pilot, screen)
        await wait_status(pilot, "updated", status_id="#edit-status")

    c = db_manager.get_campaign(created.id)
    assert c.daily_limit == 15
    assert c.geo_urn == TOKYO["geoUrn"]
    assert c.location_display == TOKYO["name"]


@pytest.mark.unit
async def test_stale_search_result_does_not_reveal_or_steal_focus(db_manager: DatabaseManager):
    """A result landing after the user switched Location away from "Search
    location online…" must be dropped silently — no reveal, no focus steal.

    Drives ``_search_done`` directly (the worker callback) rather than racing
    a real thread worker, mirroring the deterministic-guard style already used
    for the dashboard's stale-load test.
    """
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        screen._search_in_flight = True
        # The user switched away from "Search location online…" while the
        # search was still in flight.
        screen.query_one("#field-location", Select).value = "Any"
        await pilot.pause()

        screen._search_done("Madrid", [MADRID, TOKYO], None)
        await pilot.pause()

        picker = screen.query_one("#field-location-results", OptionList)
        assert not picker.display
        assert app.focused is not picker
        assert screen._search_in_flight is False  # bookkeeping still resets


@pytest.mark.unit
async def test_reentering_search_mode_reenables_stale_disabled_input(db_manager: DatabaseManager):
    """Re-selecting "Search location online…" must re-enable the query input
    even if it was left disabled by a search that completed after the user
    had switched away (issue #61).

    Sequence: start a search (disables the input) with Location on "Any"
    (the user switched away mid-flight) → re-enter search mode while still
    in flight (input must stay disabled, status says a search is running) →
    back to "Any" → the stale result lands and is dropped without
    re-enabling the input (`_search_done`'s stale guard) → switch back to
    "Search location online…". The input must be enabled and focused, not
    permanently disabled.
    """
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        # In-flight search state, as `_start_location_search` leaves it. The
        # Location select still sits on its default ("Any") — i.e. the user
        # switched away while the search runs — so no mode change is needed
        # before the stale completion below.
        screen._search_in_flight = True
        screen.query_one("#field-location-query", Input).disabled = True

        # Entering search mode while the search is *genuinely* still in
        # flight must keep the input disabled (and unfocused), and say a
        # search is running rather than invite input the field cannot take.
        screen.query_one("#field-location", Select).value = SEARCH_ONLINE
        await pilot.pause()
        query_box = screen.query_one("#field-location-query", Input)
        assert query_box.disabled
        assert app.focused is not query_box
        assert "still running" in str(
            screen.query_one("#create-status", Static).render()
        )
        screen.query_one("#field-location", Select).value = "Any"
        await pilot.pause()

        # The stale result lands and is dropped silently — input stays disabled.
        screen._search_done("Madrid", [MADRID, TOKYO], None)
        await pilot.pause()
        assert screen.query_one("#field-location-query", Input).disabled

        # Re-entering the search mode must reset the disabled state and show
        # the normal search prompt again.
        screen.query_one("#field-location", Select).value = SEARCH_ONLINE
        await pilot.pause()
        query_box = screen.query_one("#field-location-query", Input)
        assert not query_box.disabled
        assert app.focused is query_box
        assert "Type a location" in str(
            screen.query_one("#create-status", Static).render()
        )


@pytest.mark.unit
async def test_search_requires_db_and_settings(db_manager: DatabaseManager):
    """A degraded app (no DB) refuses the online search with a clear message."""
    app = LinkedInTUI(db_manager=db_manager)
    app.db_manager = None
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        await start_search(pilot, screen, "Madrid")
        await wait_status(pilot, "requires database access")
        assert screen._search_in_flight is False
