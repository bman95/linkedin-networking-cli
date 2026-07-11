"""Arrow-only focus movement across screens (``tui.focus_nav``).

The owner rule (2026-07-09) is arrows + Enter first; this pass removes the
last Tab *requirement*: bare arrows now move focus between a screen's widgets
once the focused widget is done with the key (list/table cursor on its edge,
vertical arrows in a single-line input, any arrow on a button). These tests
pin the policy end-to-end in the headless harness:

- edge-aware handoff: a table keeps ↑/↓ while the cursor can still move, and
  hands focus to the buttons below only from its last row;
- side-by-side buttons are walked with ←/→;
- form fields chain vertically with ↑/↓ while inputs keep ←/→ for the caret;
- a *closed* Select moves focus on ↑/↓ (Enter still opens it) and an *open*
  dropdown keeps every arrow until dismissed.
"""

import pytest
from textual.widgets import Button, DataTable, Input, ListView, Select

from database.operations import DatabaseManager
from tui.app import LinkedInTUI
from tui.screens.campaign_detail import CampaignDetailScreen
from tui.screens.campaigns import CampaignsScreen
from tui.screens.create_campaign import CreateCampaignScreen
from tui.screens.dashboard import DashboardScreen

from .test_tui_ux import make_campaign, wait_text


@pytest.mark.unit
async def test_campaigns_arrows_walk_table_then_buttons(db_manager: DatabaseManager):
    """↓ keeps moving the row cursor until the last row, then jumps to the
    toolbar; ←/→ walk the two buttons; ↑ returns into the table."""
    make_campaign(db_manager, name="First")
    make_campaign(db_manager, name="Second")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignsScreen(db_manager))
        await wait_text(pilot, "#campaigns-status", "campaign")
        table = app.screen.query_one("#campaigns-table", DataTable)
        new = app.screen.query_one("#campaigns-new", Button)
        refresh = app.screen.query_one("#campaigns-refresh", Button)
        table.focus()
        await pilot.pause()
        assert table.cursor_coordinate.row == 0

        await pilot.press("down")  # row 0 → row 1: the table keeps the key
        assert app.focused is table
        assert table.cursor_coordinate.row == 1
        await pilot.press("down")  # last row → the toolbar
        assert app.focused is new
        await pilot.press("right")
        assert app.focused is refresh
        await pilot.press("left")
        assert app.focused is new
        await pilot.press("up")  # back into the table
        assert app.focused is table


@pytest.mark.unit
async def test_dashboard_arrows_reach_refresh_and_return(db_manager: DatabaseManager):
    make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(DashboardScreen(db_manager))
        await wait_text(pilot, "#dashboard-status", "Updated")
        table = app.screen.query_one("#dashboard-recent", DataTable)
        refresh = app.screen.query_one("#dashboard-refresh", Button)
        table.focus()
        await pilot.pause()

        await pilot.press("down")  # single row: already on the last row
        assert app.focused is refresh
        await pilot.press("up")
        assert app.focused is table


@pytest.mark.unit
async def test_form_fields_chain_with_vertical_arrows(db_manager: DatabaseManager):
    """↑/↓ walk the field stack; ←/→ stay in the focused input for the caret."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CreateCampaignScreen(db_manager))
        await pilot.pause()
        name = app.screen.query_one("#field-name", Input)
        description = app.screen.query_one("#field-description", Input)
        assert app.focused is name  # the form focuses its first field

        await pilot.press("a", "b", "c")
        await pilot.press("left", "left")  # caret movement, not focus movement
        assert app.focused is name
        assert name.cursor_position == 1

        await pilot.press("down")
        assert app.focused is description
        await pilot.press("up")
        assert app.focused is name


@pytest.mark.unit
async def test_closed_select_moves_focus_open_select_keeps_arrows(
    db_manager: DatabaseManager,
):
    """↓ on a closed Select steps to the next field (skipping the hidden
    location widgets) instead of opening the menu; Enter still opens it, and
    the open dropdown keeps every arrow."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CreateCampaignScreen(db_manager))
        await pilot.pause()
        location = app.screen.query_one("#field-location", Select)
        network = app.screen.query_one("#field-network", Select)
        location.focus()
        await pilot.pause()

        await pilot.press("down")
        assert not location.expanded
        assert app.focused is network
        await pilot.press("up")
        assert app.focused is location

        await pilot.press("enter")  # Enter (not arrows) opens the dropdown
        assert location.expanded
        await pilot.press("down")  # arrows stay inside the open dropdown
        assert location.expanded
        assert location in app.focused.ancestors_with_self
        await pilot.press("escape")  # dismiss the overlay, not the screen
        await pilot.pause()
        assert not location.expanded
        assert isinstance(app.screen, CreateCampaignScreen)


@pytest.mark.unit
async def test_detail_left_right_switch_columns(db_manager: DatabaseManager):
    """←/→ hop between the detail body column and the ACTIONS list."""
    campaign = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignDetailScreen(db_manager, campaign.id))
        await wait_text(pilot, "#detail-status", "select an action")
        actions = app.screen.query_one("#detail-actions", ListView)
        body = app.screen.query_one("#detail-body")
        assert app.focused is actions

        await pilot.press("left")
        assert app.focused is body
        await pilot.press("right")
        assert app.focused is actions
        # ↑ on the first action also leaves the list (edge handoff).
        assert actions.index == 0
        await pilot.press("up")
        assert app.focused is body
