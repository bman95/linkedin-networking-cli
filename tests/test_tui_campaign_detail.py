"""Tests for the campaign detail + edit flow (issue #24).

Drives the real navigation (Campaigns list -> Enter -> detail) and the manage
actions (toggle active, delete with confirmation, edit) against a real
``DatabaseManager`` on a temp SQLite path. No mocks, no browser.
"""

import pytest
from textual.widgets import Button, DataTable, Input, Label, ListView, Static

from database.operations import DatabaseManager
from tui.app import (
    CampaignDetailScreen,
    CampaignEditScreen,
    CampaignsScreen,
    HomeScreen,
    LinkedInTUI,
)
from tui.screens.run_panel import ConfirmBar


def make_campaign(db: DatabaseManager, name="Backend Engineers", **extra):
    data = {
        "name": name,
        "daily_limit": 20,
        "keywords": "python",
        "message_template": "Hi {name}, let's connect!",
    }
    data.update(extra)
    return db.create_campaign(data)


async def wait_text(pilot, status_id: str, needle: str, tries: int = 80) -> str:
    """Poll a status line until it contains text, tolerating a not-yet-mounted
    screen (a freshly pushed screen composes on the next message pump)."""
    last = ""
    for _ in range(tries):
        await pilot.pause()
        try:
            status = pilot.app.screen.query_one(status_id, Static)
        except Exception:
            continue
        last = str(status.render())
        if needle in last:
            return last
    return last


async def activate_action(pilot, screen, index: int) -> None:
    """Navigate the ACTIONS list to ``index`` and press Enter (arrows + Enter
    is the only path to any action — owner rule, 2026-07-10: no accelerators)."""
    actions = screen.query_one("#detail-actions", ListView)
    while actions.index != index:
        await pilot.press("down")
    await pilot.press("enter")


async def submit_form(pilot, screen) -> None:
    """Tab to the form's submit button and press Enter (the only submit path)."""
    button = screen.query_one("#form-submit", Button)
    while pilot.app.focused is not button:
        await pilot.press("tab")
    await pilot.press("enter")


@pytest.mark.unit
async def test_campaigns_enter_opens_detail(db_manager: DatabaseManager):
    c = make_campaign(db_manager)
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
        # Wait for the worker-populated row, then activate it.
        table = app.screen.query_one("#campaigns-table", DataTable)
        for _ in range(60):
            if table.row_count >= 1:
                break
            await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, CampaignDetailScreen)
        assert app.screen._campaign_id == c.id


@pytest.mark.unit
async def test_detail_renders_fields(db_manager: DatabaseManager):
    c = make_campaign(db_manager, name="Designers NYC")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignDetailScreen(db_manager, c.id))
        await wait_text(pilot, "#detail-status", "select an action")
        overview = str(app.screen.query_one("#detail-body-overview", Static).render())
        assert "Designers NYC" in overview
        assert "Active" in overview


@pytest.mark.unit
async def test_detail_performance_derives_from_contacts_not_stale_counters(
    db_manager: DatabaseManager,
):
    """Performance shows live counts from ``contacts``, not the denormalized
    ``Campaign.total_*`` columns.

    Those columns are only kept in sync by ``update_campaign_stats`` (called
    after a real automation run); anything that bypasses it (a crash mid-run,
    a manual edit, a seed script) leaves them stale. Before issue #66 this
    screen rendered the stale stored numbers, contradicting the Campaigns list
    and Dashboard, which already derived live from ``contacts``.
    """
    c = make_campaign(
        db_manager, name="Stale Counters", total_sent=12, total_accepted=5
    )
    db_manager.create_contact(
        {
            "campaign_id": c.id,
            "name": "Accepted 0",
            "profile_url": "https://example.com/detail-accepted-0",
            "status": "accepted",
        }
    )
    db_manager.create_contact(
        {
            "campaign_id": c.id,
            "name": "Sent 0",
            "profile_url": "https://example.com/detail-sent-0",
            "status": "sent",
        }
    )
    db_manager.create_contact(
        {
            "campaign_id": c.id,
            "name": "Sent 1",
            "profile_url": "https://example.com/detail-sent-1",
            "status": "sent",
        }
    )
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignDetailScreen(db_manager, c.id))
        await wait_text(pilot, "#detail-status", "select an action")
        performance = str(
            app.screen.query_one("#detail-body-performance", Static).render()
        )
        assert "Sent: 3" in performance
        assert "Accepted: 1" in performance
        assert "Pending: 2" in performance
        assert "Acceptance Rate: 33.3%" in performance
        # Not the stale stored counters.
        assert "Sent: 12" not in performance
        assert "Accepted: 5" not in performance


@pytest.mark.unit
async def test_detail_toggle_active(db_manager: DatabaseManager):
    c = make_campaign(db_manager)
    assert c.active is True
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignDetailScreen(db_manager, c.id))
        await wait_text(pilot, "#detail-status", "select an action")
        await activate_action(pilot, app.screen, 3)  # Toggle active
        for _ in range(60):
            if db_manager.get_campaign(c.id).active is False:
                break
            await pilot.pause()
    assert db_manager.get_campaign(c.id).active is False


@pytest.mark.unit
async def test_detail_actions_list_enter_opens_edit(db_manager: DatabaseManager):
    """The ACTIONS list is the primary path (owner rule): arrows + Enter reach
    every manage action — here, Edit (the third item)."""
    c = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignDetailScreen(db_manager, c.id))
        await wait_text(pilot, "#detail-status", "select an action")
        actions = app.screen.query_one("#detail-actions", ListView)
        assert app.focused is actions
        await pilot.press("down", "down")  # Run now → Check acceptances → Edit
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, CampaignEditScreen)


@pytest.mark.unit
async def test_detail_toggle_item_names_the_transition(db_manager: DatabaseManager):
    """The toggle action reads 'Deactivate' while active and 'Activate' after."""
    c = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignDetailScreen(db_manager, c.id))
        await wait_text(pilot, "#detail-status", "select an action")
        label = app.screen.query_one("#action-toggle .nav-title", Label)
        assert str(label.render()) == "Deactivate"
        await activate_action(pilot, app.screen, 3)  # Toggle active
        for _ in range(60):
            await pilot.pause()
            if str(label.render()) == "Activate":
                break
        assert str(label.render()) == "Activate"
        assert db_manager.get_campaign(c.id).active is False


@pytest.mark.unit
async def test_detail_actions_list_dispatches_toggle_export_delete(
    db_manager: DatabaseManager,
):
    """Every remaining ACTIONS item dispatches from the list via Enter alone
    (run/check/edit are pinned elsewhere): Toggle active, Export CSV, Delete."""
    c = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignDetailScreen(db_manager, c.id))
        await wait_text(pilot, "#detail-status", "select an action")
        actions = app.screen.query_one("#detail-actions", ListView)

        await pilot.press("down", "down", "down")  # index 3: Toggle active
        assert actions.index == 3
        await pilot.press("enter")
        for _ in range(60):
            await pilot.pause()
            if db_manager.get_campaign(c.id).active is False:
                break
        assert db_manager.get_campaign(c.id).active is False

        await pilot.press("down")  # index 4: Export CSV (no contacts yet)
        assert actions.index == 4
        await pilot.press("enter")
        await wait_text(pilot, "#detail-status", "No contacts to export")

        await pilot.press("down")  # index 5: Delete — arms the confirm
        assert actions.index == 5
        await pilot.press("enter")
        await wait_text(pilot, "#detail-status", "Enter to confirm")
        assert app.screen.query_one("#detail-delete-confirm", ConfirmBar).armed
        assert db_manager.get_campaign(c.id) is not None  # armed, not deleted


@pytest.mark.unit
async def test_detail_delete_via_focused_confirm_button(db_manager: DatabaseManager):
    """Activating Delete arms a focused inline confirm (owner rule: Enter
    confirms, esc cancels — no chord-twice requirement, and — 2026-07-10 — no
    accelerator-repeat either)."""
    c = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignDetailScreen(db_manager, c.id))
        await wait_text(pilot, "#detail-status", "select an action")
        await activate_action(pilot, app.screen, 5)  # Delete: arms the confirm bar
        await wait_text(pilot, "#detail-status", "Enter to confirm")
        bar = app.screen.query_one("#detail-delete-confirm", ConfirmBar)
        assert bar.armed
        assert app.focused is bar.query_one(".confirm-yes", Button)
        assert db_manager.get_campaign(c.id) is not None  # not deleted yet
        await pilot.press("enter")
        for _ in range(60):
            if db_manager.get_campaign(c.id) is None:
                break
            await pilot.pause()
    assert db_manager.get_campaign(c.id) is None


@pytest.mark.unit
async def test_edit_prefills_and_saves(db_manager: DatabaseManager):
    c = make_campaign(db_manager, name="Old Name", daily_limit=10)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignEditScreen(db_manager, c.id))
        await wait_text(pilot, "#edit-status", "Edit the fields")
        # Prefilled from the existing campaign.
        assert app.screen.query_one("#field-name", Input).value == "Old Name"
        assert app.screen.query_one("#field-daily", Input).value == "10"
        # Change and save.
        app.screen.query_one("#field-name", Input).value = "New Name"
        app.screen.query_one("#field-daily", Input).value = "30"
        await submit_form(pilot, app.screen)
        await wait_text(pilot, "#edit-status", "updated")

    updated = db_manager.get_campaign(c.id)
    assert updated.name == "New Name"
    assert updated.daily_limit == 30


@pytest.mark.unit
async def test_detail_export_csv(db_manager: DatabaseManager, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # export writes under ~/.linkedin-networking-cli
    c = make_campaign(db_manager)
    db_manager.create_contact({
        "campaign_id": c.id,
        "name": "Jane Doe",
        "profile_url": "https://www.linkedin.com/in/jane",
        "status": "sent",
    })
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignDetailScreen(db_manager, c.id))
        await wait_text(pilot, "#detail-status", "select an action")
        await activate_action(pilot, app.screen, 4)  # Export CSV
        text = await wait_text(pilot, "#detail-status", "Exported")
        assert "Exported" in text

    files = list((tmp_path / ".linkedin-networking-cli" / "exports").glob("*.csv"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "profile_url" in content  # header row
    assert "Jane Doe" in content


@pytest.mark.unit
async def test_detail_degraded_without_db(db_manager: DatabaseManager):
    c = make_campaign(db_manager)
    app = LinkedInTUI(db_manager=db_manager)
    app.db_manager = None
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(CampaignDetailScreen(None, c.id))
        text = await wait_text(pilot, "#detail-status", "unavailable")
        assert "unavailable" in text
