"""Tests for the curated TUI shell, Dashboard and Settings screens (issue #24).

Uses Textual's headless harness (``App.run_test()`` + ``Pilot``), matching the
approach the #37 slice established in ``tests/test_tui.py``. Data is seeded
through the real ``DatabaseManager`` so the screens exercise the actual
data-flow path, not mocks.
"""

import threading

import pytest
from textual.widgets import DataTable, ListView, Static

from database.operations import DatabaseManager
from tui.app import DashboardScreen, HomeScreen, LinkedInTUI, SettingsScreen
from tui.commands import NavCommands
from tui.screens.settings_view import mask_email

# ── helpers ────────────────────────────────────────────────────────────────


async def open_menu_item(pilot, item_id: str) -> None:
    """Drive the real keyboard-first home navigation to a specific entry."""
    menu = pilot.app.screen.query_one("#home-nav", ListView)
    item_ids = [item.id for item in menu.query("ListItem")]
    target = item_ids.index(f"nav-{item_id}")
    while menu.index != target:
        await pilot.press("down")
    await pilot.press("enter")
    await pilot.pause()


async def wait_for_status(pilot, screen, status_id: str, needle: str) -> str:
    """Poll a status line (populated by a threaded worker) until it contains text."""
    status = screen.query_one(status_id, Static)
    await pilot.wait_for_scheduled_animations()
    for _ in range(50):
        if needle in str(status.render()):
            break
        await pilot.pause()
    return str(status.render())


def card_value(screen, card_id: str) -> str:
    return str(screen.query_one(f"#value-{card_id}", Static).render())


@pytest.fixture
def seeded_db_manager(db_manager: DatabaseManager) -> DatabaseManager:
    """Two campaigns and contacts so dashboard stats are non-trivial.

    ``get_dashboard_stats`` aggregates over *contacts* (not campaign columns),
    so contacts are seeded to make sent/accepted/rate meaningful.
    """
    c1 = db_manager.create_campaign(
        {"name": "Tech Professionals", "daily_limit": 20, "active": True}
    )
    db_manager.create_campaign(
        {"name": "Marketing Leads", "daily_limit": 15, "active": False}
    )
    # Five contacts on campaign 1: 4 accepted, 1 sent (pending) -> 5 sent total,
    # acceptance rate 4/5 = 80.0%.
    for i in range(4):
        db_manager.create_contact(
            {
                "campaign_id": c1.id,
                "name": f"Accepted {i}",
                "profile_url": f"https://example.com/a{i}",
                "status": "accepted",
            }
        )
    db_manager.create_contact(
        {
            "campaign_id": c1.id,
            "name": "Pending 0",
            "profile_url": "https://example.com/p0",
            "status": "sent",
        }
    )
    return db_manager


# ── shell / navigation ───────────────────────────────────────────────────────


@pytest.mark.unit
async def test_home_has_all_entries(db_manager: DatabaseManager):
    """The home exposes every home destination, ordered (issue #42: the
    automation flows moved onto the campaign detail; Extract is palette-only)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, HomeScreen)
        menu = app.screen.query_one("#home-nav", ListView)
        item_ids = [item.id for item in menu.query("ListItem")]
        assert item_ids == [
            "nav-dashboard",
            "nav-campaigns",
            "nav-create",
            "nav-settings",
        ]
        assert menu.has_focus and menu.index == 0


@pytest.mark.unit
async def test_brand_theme_is_active(db_manager: DatabaseManager):
    """The app registers and selects the cohesive 'linkedin' theme on mount."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "linkedin"
        assert app.get_theme("linkedin").primary == "#0A66C2"


@pytest.mark.unit
async def test_navigate_to_dashboard_and_back(db_manager: DatabaseManager):
    """Menu -> Dashboard, then Esc returns to the menu (shared Back binding)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "dashboard")
        assert isinstance(app.screen, DashboardScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, HomeScreen)


@pytest.mark.unit
async def test_navigate_to_settings(db_manager: DatabaseManager):
    """Menu -> Settings opens the read-only settings screen."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "settings")
        assert isinstance(app.screen, SettingsScreen)


@pytest.mark.unit
async def test_command_palette_navigates(db_manager: DatabaseManager):
    """The nav command provider discovers destinations and routes to them."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = NavCommands(app.screen)
        discovered = [hit async for hit in provider.discover()]
        names = [str(hit.display) for hit in discovered]
        assert names == [
            "Dashboard",
            "Campaigns",
            "New Campaign",
            "Extract Profile Data",
            "Settings",
            "Quit",
        ]

        # Fuzzy search narrows to Dashboard.
        searched = [hit async for hit in provider.search("dash")]
        assert len(searched) == 1
        assert "Dashboard" in str(searched[0].match_display)

        # Invoking the command routes to the screen.
        next(h for h in discovered if str(h.display) == "Dashboard").command()
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


@pytest.mark.unit
async def test_command_palette_extends_system_commands():
    """COMMANDS must extend, not replace, the built-in palette providers."""
    assert NavCommands in LinkedInTUI.COMMANDS
    # The default App command provider(s) are still present.
    assert LinkedInTUI.COMMANDS >= LinkedInTUI.__mro__[1].COMMANDS


# ── dashboard rendering & states ─────────────────────────────────────────────


@pytest.mark.unit
async def test_dashboard_renders_stats(seeded_db_manager: DatabaseManager):
    """The dashboard cards reflect real DatabaseManager stats."""
    app = LinkedInTUI(db_manager=seeded_db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "dashboard")
        screen = app.screen
        assert isinstance(screen, DashboardScreen)
        await wait_for_status(pilot, screen, "#dashboard-status", "Updated")

        assert card_value(screen, "active-campaigns") == "1/2"
        assert card_value(screen, "total-connections") == "5 sent / 4 accepted"
        # 4 accepted / 5 sent -> 80.0%. Label parity: the card is "Success Rate".
        assert "80.0%" in card_value(screen, "success-rate")
        assert card_value(screen, "total-contacts") == "5"
        assert card_value(screen, "pending") == "1"
        labels = [str(s.render()) for s in screen.query(".stat-label")]
        assert "Success Rate" in labels


@pytest.mark.unit
async def test_dashboard_recent_campaigns_table(seeded_db_manager: DatabaseManager):
    """The mini-table renders DB-backed rows whose stats come from live contacts.

    The seeded campaigns have their stats only on the *contacts* (created via
    create_contact), not the denormalized Campaign.total_sent columns. The recent
    table must compute sent/accepted/rate from the same live source as the cards,
    so it shows Tech Professionals as 5 sent / 4 accepted / 80.0%, never 0/0.
    """
    app = LinkedInTUI(db_manager=seeded_db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "dashboard")
        screen = app.screen
        await wait_for_status(pilot, screen, "#dashboard-status", "Updated")
        table = screen.query_one("#dashboard-recent", DataTable)
        assert table.row_count == 2

        rows = {
            str(table.get_row_at(i)[0]): [str(c) for c in table.get_row_at(i)]
            for i in range(table.row_count)
        }
        assert "Tech Professionals" in rows
        assert "Marketing Leads" in rows
        # Live-contact stats, consistent with the cards (not the stale 0 columns).
        tech = rows["Tech Professionals"]
        assert tech[2] == "5"  # sent
        assert tech[3] == "4"  # accepted
        assert tech[4] == "80.0%"  # rate
        # A campaign with no contacts reads 0/0/0.0%, not a crash.
        assert rows["Marketing Leads"][2] == "0"
        assert rows["Marketing Leads"][4] == "0.0%"


@pytest.mark.unit
async def test_dashboard_recent_orders_and_truncates(db_manager: DatabaseManager):
    """Recent table is newest-first by last_run/created_at and capped at 5."""
    from datetime import datetime, timedelta

    base = datetime(2026, 1, 1, 12, 0, 0)
    # Seven campaigns with increasing last_run; only the newest five should show,
    # newest first.
    for i in range(7):
        db_manager.create_campaign(
            {"name": f"Camp {i}", "daily_limit": 10, "last_run": base + timedelta(days=i)}
        )
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "dashboard")
        screen = app.screen
        await wait_for_status(pilot, screen, "#dashboard-status", "Updated")
        table = screen.query_one("#dashboard-recent", DataTable)
        assert table.row_count == 5  # RECENT_LIMIT
        names = [str(table.get_row_at(i)[0]) for i in range(table.row_count)]
        # Newest (Camp 6) first, oldest shown (Camp 2) last; Camp 0/1 truncated.
        assert names == ["Camp 6", "Camp 5", "Camp 4", "Camp 3", "Camp 2"]


@pytest.mark.unit
async def test_dashboard_week_usage_states(db_manager: DatabaseManager, monkeypatch):
    """The 'Used This Week' card colours by the weekly budget and degrades to —.

    The tile tracks the weekly invitation budget — LinkedIn's actually-binding
    constraint — not the DAILY_CONNECTION_LIMIT env fallback (issue #46).
    """
    from textual.widgets import Static

    monkeypatch.delenv("WEEKLY_INVITATION_LIMIT", raising=False)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "dashboard")
        screen = app.screen
        await wait_for_status(pilot, screen, "#dashboard-status", "Updated")
        used = screen.query_one("#value-week-usage", Static)
        # Default weekly budget is 100 and usage is 0 -> "0/100", not warned.
        assert card_value(screen, "week-usage") == "0/100"
        assert "-warn" not in used.classes

        # Simulate at-cap: re-populate with used == budget and assert the warn class.
        from tui.screens.dashboard import DashboardData

        screen._populate(
            DashboardData(stats={"total_campaigns": 1}, recent=[], used_week=100, weekly_limit=100),
            None,
        )
        assert card_value(screen, "week-usage") == "100/100"
        assert "-warn" in used.classes

        # A non-positive budget blocks every run (enforcement is used >= limit),
        # so it must warn rather than render neutral.
        screen._populate(
            DashboardData(stats={"total_campaigns": 1}, recent=[], used_week=0, weekly_limit=0),
            None,
        )
        assert card_value(screen, "week-usage") == "0/0"
        assert "-warn" in used.classes

        # Budget unavailable -> blank dash, neutral.
        screen._populate(
            DashboardData(stats={"total_campaigns": 1}, recent=[], used_week=None, weekly_limit=None),
            None,
        )
        assert card_value(screen, "week-usage") == "—"
        assert "-warn" not in used.classes


@pytest.mark.unit
async def test_dashboard_campaign_name_with_markup_does_not_crash(
    db_manager: DatabaseManager,
):
    """A campaign name containing Rich markup must render literally, not crash.

    Regression: ``Static``/``DataTable`` parse cell strings as Rich markup, so a
    user-typed name like ``Q4 [/] Outreach`` would raise ``MarkupError`` and tear
    down the TUI. Names must be rendered as literal text.
    """
    db_manager.create_campaign({"name": "Q4 [/] Outreach", "daily_limit": 10})
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "dashboard")
        screen = app.screen
        await wait_for_status(pilot, screen, "#dashboard-status", "Updated")
        table = screen.query_one("#dashboard-recent", DataTable)
        assert table.row_count == 1
        assert "Q4 [/] Outreach" in str(table.get_row_at(0)[0])


@pytest.mark.unit
async def test_dashboard_empty_db(db_manager: DatabaseManager):
    """An empty DB renders zeros and a friendly empty message, not blanks."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "dashboard")
        screen = app.screen
        text = await wait_for_status(pilot, screen, "#dashboard-status", "No campaigns")
        assert "No campaigns" in text
        assert card_value(screen, "active-campaigns") == "0/0"
        assert card_value(screen, "total-contacts") == "0"


@pytest.mark.unit
async def test_dashboard_degraded_no_db(db_manager: DatabaseManager):
    """A degraded app (no DB) shows 'Database unavailable.', not a crash."""
    app = LinkedInTUI(db_manager=db_manager)
    app.db_manager = None
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "dashboard")
        screen = app.screen
        text = await wait_for_status(pilot, screen, "#dashboard-status", "Database unavailable")
        assert "Database unavailable" in text
        assert card_value(screen, "active-campaigns") == "—"


@pytest.mark.unit
async def test_dashboard_error_state(db_manager: DatabaseManager):
    """A read that raises is surfaced in-place, never as a traceback."""

    class _BoomDB:
        def get_dashboard_stats(self):
            raise RuntimeError("boom")

        def get_campaigns(self, active_only=False):
            return []

        def get_daily_connection_count(self, date_str):
            return 0

    app = LinkedInTUI(db_manager=_BoomDB())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "dashboard")
        screen = app.screen
        text = await wait_for_status(pilot, screen, "#dashboard-status", "Error loading dashboard")
        assert "Error loading dashboard" in text
        assert "boom" in text


@pytest.mark.unit
async def test_dashboard_stale_load_is_dropped(seeded_db_manager: DatabaseManager):
    """A superseded (older-generation) result must not overwrite a newer one."""
    app = LinkedInTUI(db_manager=seeded_db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "dashboard")
        screen = app.screen
        await wait_for_status(pilot, screen, "#dashboard-status", "Updated")

        current = screen._load_generation
        # An older-generation error result is ignored (the mixin's generation
        # gate drops it before _populate runs).
        screen._apply_if_current(current - 1, screen._populate, None, "Database unavailable.")
        assert card_value(screen, "active-campaigns") == "1/2"
        # The current generation applies.
        screen._apply_if_current(current, screen._populate, None, "Database unavailable.")
        assert card_value(screen, "active-campaigns") == "—"


@pytest.mark.unit
async def test_dashboard_quit_while_load_in_flight_does_not_error(
    db_manager: DatabaseManager,
):
    """Quitting before a slow read returns must not error/hang on shutdown."""
    release = threading.Event()

    class _SlowDB:
        def get_dashboard_stats(self):
            release.wait(timeout=5)
            return {}

        def get_campaigns(self, active_only=False):
            return []

        def get_daily_connection_count(self, date_str):
            return 0

    app = LinkedInTUI(db_manager=_SlowDB())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "dashboard")
        screen = app.screen
        assert isinstance(screen, DashboardScreen)
        app.exit()

    assert not app.is_running
    # The in-flight worker reaches its marshal step after exit: a silent no-op.
    screen.marshal_load(app, screen._load_generation, screen._populate, None, None)
    release.set()


@pytest.mark.unit
async def test_dashboard_app_ref_captured_on_ui_thread(db_manager: DatabaseManager):
    """The load must capture the live app on the UI thread at schedule time."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "dashboard")
        screen = app.screen

        captured = {}

        def _spy(passed_app, generation):
            captured["app"] = passed_app
            captured["generation"] = generation

        screen._run_load = _spy
        screen.load_dashboard()
        assert captured["app"] is app
        assert captured["generation"] == screen._load_generation


# ── settings masking & states ────────────────────────────────────────────────


def test_mask_email_algorithm():
    """Email masking matches the classic CLI: prefix[:3] + '***@' + domain."""
    assert mask_email("johndoe@example.com") == "joh***@example.com"
    assert mask_email("ab@example.com") == "ab***@example.com"
    assert mask_email("noatsign") == "noa***"
    assert mask_email(None) == "Not set"
    assert mask_email("") == "Not set"


@pytest.mark.unit
async def test_settings_masks_email_and_hides_password(
    db_manager: DatabaseManager, monkeypatch
):
    """The settings screen masks the email and never prints the password value."""
    monkeypatch.setenv("LINKEDIN_EMAIL", "johndoe@example.com")
    monkeypatch.setenv("LINKEDIN_PASSWORD", "sup3r-secret-value")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "settings")
        screen = app.screen
        await wait_for_status(pilot, screen, "#settings-status", "Read-only")
        body = str(screen.query_one("#body-credentials", Static).render())
        assert "joh***@example.com" in body
        assert "johndoe@example.com" not in body  # never the raw email
        assert "sup3r-secret-value" not in body  # never the raw password
        assert "Password: Set" in body
        assert "Status: Configured" in body


@pytest.mark.unit
async def test_settings_status_not_configured(db_manager: DatabaseManager, monkeypatch):
    """Without credentials the status reads 'Not configured'."""
    monkeypatch.delenv("LINKEDIN_EMAIL", raising=False)
    monkeypatch.delenv("LINKEDIN_PASSWORD", raising=False)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "settings")
        screen = app.screen
        await wait_for_status(pilot, screen, "#settings-status", "Read-only")
        body = str(screen.query_one("#body-credentials", Static).render())
        assert "Status: Not configured" in body
        assert "Password: Not set" in body


@pytest.mark.unit
async def test_settings_renders_rate_limiting_parity(
    db_manager: DatabaseManager, monkeypatch
):
    """Rate-limiting labels match the classic CLI vocabulary."""
    monkeypatch.delenv("WEEKLY_INVITATION_LIMIT", raising=False)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "settings")
        screen = app.screen
        await wait_for_status(pilot, screen, "#settings-status", "Read-only")
        limits = str(screen.query_one("#body-limits", Static).render())
        assert "Connection Delay:" in limits
        # The env value is only the per-campaign fallback and must be labelled
        # as such; the weekly budget is the binding constraint (issue #46).
        assert "Default Daily Limit (fallback when a campaign sets none):" in limits
        assert "Daily Connection Limit:" not in limits
        assert "Weekly Invitation Limit: 100" in limits
        assert "Used Today: 0" in limits  # db_manager present -> usage shown
        assert "Used This Week: 0/100" in limits
        assert "Used Today: 0/" not in limits  # never "used/env-fallback"
        assert "Search Limit:" in limits


@pytest.mark.unit
async def test_settings_error_state(db_manager: DatabaseManager, monkeypatch):
    """A failure while gathering settings shows 'Settings unavailable.'."""
    import config.settings as settings_module

    def _boom(*args, **kwargs):
        raise RuntimeError("no settings")

    monkeypatch.setattr(settings_module, "AppSettings", _boom)
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "settings")
        screen = app.screen
        text = await wait_for_status(pilot, screen, "#settings-status", "unavailable")
        assert "Settings unavailable" in text


@pytest.mark.unit
async def test_settings_path_with_markup_does_not_crash(
    db_manager: DatabaseManager, monkeypatch
):
    """Env-derived values with Rich markup must render literally, not crash.

    The browser executable / user-agent come from env and flow into the Settings
    body; a value like ``/opt/[/]chrome`` would otherwise raise MarkupError.
    """
    monkeypatch.setenv("PLAYWRIGHT_BROWSER_EXECUTABLE", "/opt/[/]chrome")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "settings")
        screen = app.screen
        await wait_for_status(pilot, screen, "#settings-status", "Read-only")
        body = str(screen.query_one("#body-browser", Static).render())
        assert "/opt/[/]chrome" in body
