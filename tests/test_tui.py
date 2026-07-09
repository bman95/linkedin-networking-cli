"""Tests for the Textual TUI vertical slice (issue #24).

Uses Textual's headless test harness (``App.run_test()`` + ``Pilot``) to prove:
- the main menu mounts in the full-screen app, and
- selecting the menu entry navigates to the read-only Campaigns screen, which
  renders data read from the real ``DatabaseManager``.

The DB is seeded through ``DatabaseManager.create_campaign`` (the real business
logic) so the slice exercises the actual data-flow path, not a mock.
"""

import subprocess
import sys
import threading
from pathlib import Path

import pytest
from textual.widgets import DataTable, ListView, Static

from database.operations import DatabaseManager
from tui.app import CampaignsScreen, HomeScreen, LinkedInTUI


async def open_menu_item(pilot, item_id: str) -> None:
    """Drive the real keyboard-first home navigation to a specific entry.

    The home is a focused nav list; this walks the highlight to the target row
    and activates it, exercising the same path a user takes (rather than calling
    ``push_screen`` directly).
    """
    menu = pilot.app.screen.query_one("#home-nav", ListView)
    item_ids = [item.id for item in menu.query("ListItem")]
    target = item_ids.index(f"nav-{item_id}")
    while menu.index != target:
        await pilot.press("down")
    await pilot.press("enter")
    await pilot.pause()


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
async def test_home_mounts(db_manager: DatabaseManager):
    """The app boots into the focused home launcher with selectable items."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, HomeScreen)
        menu = app.screen.query_one("#home-nav", ListView)
        item_ids = [item.id for item in menu.query("ListItem")]
        assert "nav-campaigns" in item_ids
        assert "nav-dashboard" in item_ids
        # First item is highlighted and the nav is focused, so Enter works on
        # first launch with no manual selection.
        assert menu.index == 0
        assert menu.has_focus


@pytest.mark.unit
async def test_navigate_to_campaigns_screen_renders_db_data(
    seeded_db_manager: DatabaseManager,
):
    """Selecting 'Campaigns' opens the read-only screen and shows DB rows."""
    app = LinkedInTUI(db_manager=seeded_db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "campaigns")

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
    """An empty DB renders the screen with zero rows and a friendly message.

    Drives the real menu navigation path (not a direct push_screen) so the
    empty-DB branch is exercised exactly as a user would reach it.
    """
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "campaigns")

        assert isinstance(app.screen, CampaignsScreen)
        table = app.screen.query_one("#campaigns-table", DataTable)
        status = app.screen.query_one("#campaigns-status", Static)
        for _ in range(50):
            if "No campaigns" in str(status.render()):
                break
            await pilot.pause()
        assert table.row_count == 0
        assert "No campaigns" in str(status.render())


@pytest.mark.unit
async def test_campaign_name_with_markup_does_not_crash(db_manager: DatabaseManager):
    """A campaign name with Rich markup must render literally, not crash.

    Regression: ``DataTable`` parses string cells as Rich markup, so a user-typed
    name like ``Q4 [/] Outreach`` would raise ``MarkupError`` and tear down the
    UI. Names must be rendered as literal text.
    """
    db_manager.create_campaign({"name": "Q4 [/] Outreach", "daily_limit": 10})
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "campaigns")
        table = app.screen.query_one("#campaigns-table", DataTable)
        for _ in range(50):
            if table.row_count == 1:
                break
            await pilot.pause()
        assert table.row_count == 1
        assert "Q4 [/] Outreach" in str(table.get_row_at(0)[0])


@pytest.mark.unit
async def test_quit_while_load_in_flight_does_not_error(db_manager: DatabaseManager):
    """Quitting before a slow DB read returns must not error on shutdown.

    Regression for the threaded-worker race: the worker can't be interrupted, so
    it finishes after the app has exited. Marshalling back into the dead event
    loop would raise RuntimeError (and could hang the process); the
    ``app.is_running`` guard must make the late callback a no-op instead.
    """
    release = threading.Event()

    class _SlowDB:
        def get_campaigns(self, active_only=False):
            release.wait(timeout=5)  # block the worker until the test releases it
            return []

    app = LinkedInTUI(db_manager=_SlowDB())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "campaigns")
        assert isinstance(app.screen, CampaignsScreen)

        screen = app.screen
        app.exit()  # quit while the worker is still blocked in get_campaigns

    # Loop is now torn down. Simulate the in-flight worker reaching its marshal
    # step after exit (it captured the app reference before quitting); the guard
    # must skip call_from_thread rather than raise/hang.
    assert not app.is_running
    screen.marshal_load(app, screen._load_generation, screen._populate, [], None)  # silent no-op
    release.set()


@pytest.mark.unit
async def test_app_ref_captured_on_ui_thread_at_schedule_time(
    db_manager: DatabaseManager,
):
    """The App ref must be captured on the UI thread when the load is scheduled.

    Regression for the deferred-worker race: ``@work(thread=True)`` runs the
    worker body later on a worker thread, so resolving ``self.app`` inside it
    would raise if the screen was popped/quit first (before the shutdown guards
    can run). ``load_campaigns`` must therefore capture ``self.app`` on the UI
    thread and hand it to the worker. Assert the scheduled worker receives the
    live app instance as its first argument.
    """
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "campaigns")
        screen = app.screen
        assert isinstance(screen, CampaignsScreen)

        captured = {}

        def _spy(passed_app, generation):
            captured["app"] = passed_app
            captured["generation"] = generation

        # Replace the worker with a synchronous spy so we observe exactly what
        # ``load_campaigns`` passes at schedule time (no deferred thread body).
        screen._run_load = _spy
        screen.load_campaigns()

        # The app handed to the worker is the live app, resolved on the UI thread
        # — not looked up lazily inside the (deferred) worker body.
        assert captured["app"] is app
        assert captured["generation"] == screen._load_generation


@pytest.mark.unit
async def test_campaigns_screen_handles_missing_db_manager(db_manager: DatabaseManager):
    """A degraded app (no DB) renders 'Database unavailable.' instead of crashing.

    Mirrors the classic CLI's demo-mode fallback when DB/settings init fails.
    """
    # Inject a temp manager (avoids touching the real home dir), then drop it to
    # simulate the degraded startup state.
    app = LinkedInTUI(db_manager=db_manager)
    app.db_manager = None
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "campaigns")
        assert isinstance(app.screen, CampaignsScreen)
        status = app.screen.query_one("#campaigns-status", Static)
        table = app.screen.query_one("#campaigns-table", DataTable)
        for _ in range(50):
            if "Database unavailable" in str(status.render()):
                break
            await pilot.pause()
        assert table.row_count == 0
        assert "Database unavailable" in str(status.render())


@pytest.mark.unit
async def test_stale_load_result_is_dropped(seeded_db_manager: DatabaseManager):
    """A superseded (slower, older) load must not overwrite a newer one.

    Drives the generation-token invariant deterministically: a result tagged
    with an older generation is ignored, while the current generation applies.
    """
    app = LinkedInTUI(db_manager=seeded_db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_menu_item(pilot, "campaigns")
        screen = app.screen
        assert isinstance(screen, CampaignsScreen)
        table = screen.query_one("#campaigns-table", DataTable)
        for _ in range(50):
            if table.row_count == 2:
                break
            await pilot.pause()
        assert table.row_count == 2

        current = screen._load_generation
        # A late result from a superseded load (older token) is dropped by the
        # mixin's generation gate before _populate runs.
        screen._apply_if_current(current - 1, screen._populate, [], None)
        assert table.row_count == 2
        # The current generation applies normally.
        screen._apply_if_current(current, screen._populate, [], "Database unavailable.")
        assert table.row_count == 0
        assert "Database unavailable" in str(
            screen.query_one("#campaigns-status", Static).render()
        )


@pytest.mark.unit
def test_importing_package_does_not_eagerly_load_app():
    """Importing the ``tui`` package must not eagerly import ``tui.app``.

    Regression for the bootstrap-order bug: ``app`` calls ``get_logger`` at
    module scope, which runs ``LoggerSetup.setup()`` with production defaults
    (creating ``$HOME/.linkedin-networking-cli/logs``) the first time. The
    entry points (``linkedin_tui.py`` / ``python -m tui``) deliberately
    initialize logging *before* importing the app, so the package init must NOT
    pull in ``app`` (which would defeat that order and crash on a read-only
    home). Runs in a fresh subprocess so the import state and ``LoggerSetup``
    guard are pristine; uses an isolated HOME so a real setup would be visible.
    """
    src = str(Path(__file__).parent.parent / "src")
    probe = "\n".join(
        [
            "import os, sys",
            "from pathlib import Path",
            "import tui",
            # Importing the package must not drag in the app module.
            "assert 'tui.app' not in sys.modules, 'tui.app imported eagerly'",
            # Nor the screen modules (each calls get_logger at module scope).
            "eager = [m for m in sys.modules if m.startswith('tui.screens.')]",
            "assert not eager, f'screen modules imported eagerly: {eager}'",
            # Default-setup side effect (log dir under HOME) must not happen.
            "logdir = Path(os.environ['HOME']) / '.linkedin-networking-cli' / 'logs'",
            "assert not logdir.exists(), 'LoggerSetup ran with defaults at import'",
            # The public attribute resolves lazily, only now loading the app.
            "assert tui.LinkedInTUI is not None",
            "assert 'tui.app' in sys.modules, 'lazy access should load tui.app'",
            "print('OK')",
        ]
    )
    import os
    import tempfile

    fake_home = tempfile.mkdtemp(prefix="tui-import-probe-home-")
    env = dict(os.environ, PYTHONPATH=src, HOME=fake_home)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"probe failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "OK" in result.stdout
