"""Tests for the Textual TUI vertical slice (issue #24).

Uses Textual's headless test harness (``App.run_test()`` + ``Pilot``) to prove:
- the main menu mounts in the full-screen app, and
- selecting the menu entry navigates to the read-only Campaigns screen, which
  renders data read from the real ``DatabaseManager``.

The DB is seeded through ``DatabaseManager.create_campaign`` (the real business
logic) so the slice exercises the actual data-flow path, not a mock.
"""

import pytest

from textual.widgets import DataTable, ListView, Static

from database.operations import DatabaseManager
from tui.app import CampaignsScreen, LinkedInTUI, MainMenuScreen


@pytest.fixture
def seeded_db_manager(db_manager: DatabaseManager) -> DatabaseManager:
    """A DatabaseManager with two campaigns, one active and one inactive."""
    db_manager.create_campaign(
        {
            "name": "Tech Professionals",
            "daily_limit": 20,
            "active": True,
            "total_sent": 10,
            "total_accepted": 4,
        }
    )
    db_manager.create_campaign(
        {
            "name": "Marketing Leads",
            "daily_limit": 15,
            "active": False,
            "total_sent": 0,
            "total_accepted": 0,
        }
    )
    return db_manager


@pytest.mark.unit
async def test_main_menu_mounts(db_manager: DatabaseManager):
    """The app boots into the full-screen main menu with selectable items."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, MainMenuScreen)
        menu = app.screen.query_one("#main-menu", ListView)
        item_ids = [item.id for item in menu.query("ListItem")]
        assert "menu-campaigns" in item_ids
        assert "menu-quit" in item_ids


@pytest.mark.unit
async def test_navigate_to_campaigns_screen_renders_db_data(
    seeded_db_manager: DatabaseManager,
):
    """Selecting 'Campaigns' opens the read-only screen and shows DB rows."""
    app = LinkedInTUI(db_manager=seeded_db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Select the first menu item ("Campaigns") and activate it.
        menu = app.screen.query_one("#main-menu", ListView)
        menu.index = 0
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, CampaignsScreen)

        table = app.screen.query_one("#campaigns-table", DataTable)
        # Threaded worker loads rows; wait until populated.
        await pilot.wait_for_scheduled_animations()
        for _ in range(50):
            if table.row_count == 2:
                break
            await pilot.pause()
        assert table.row_count == 2

        # Column headers match the documented schema.
        labels = [str(col.label) for col in table.columns.values()]
        assert labels == ["Name", "Status", "Sent", "Accepted", "Rate", "Daily Limit"]

        # Row content reflects the seeded campaigns (real DB-backed data).
        rendered = "\n".join(
            " ".join(str(cell) for cell in table.get_row_at(i))
            for i in range(table.row_count)
        )
        assert "Tech Professionals" in rendered
        assert "Marketing Leads" in rendered
        assert "Active" in rendered
        assert "Inactive" in rendered
        # Acceptance rate for the active campaign: 4/10 -> 40.0%.
        assert "40.0%" in rendered


@pytest.mark.unit
async def test_campaigns_screen_handles_empty_db(db_manager: DatabaseManager):
    """An empty DB renders the screen with zero rows and a friendly message."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignsScreen(db_manager))
        await pilot.pause()
        table = app.screen.query_one("#campaigns-table", DataTable)
        status = app.screen.query_one("#campaigns-status", Static)
        for _ in range(50):
            if "No campaigns" in str(status.render()):
                break
            await pilot.pause()
        assert table.row_count == 0
        assert "No campaigns" in str(status.render())
