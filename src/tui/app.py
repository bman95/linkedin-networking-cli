"""Textual application shell for the LinkedIn networking CLI (issue #24).

This is the app frame: it registers the design-system theme, loads the external
stylesheet, wires the command palette, and routes to the screens. The screens
themselves live under ``tui.screens``; the design tokens live in ``tui.theme``
and the layout in ``app.tcss`` — one source of truth each.

The app reuses the existing business logic untouched: ``AppSettings`` for the DB
path and ``DatabaseManager`` for reads. Every shipped screen is read-only, so the
TUI needs no LinkedIn credentials and no browser, and stays driveable in the
Textual headless harness.

``HomeScreen`` (the launcher) and ``CampaignsScreen`` are re-exported here so
importers (and tests) have a single stable entry point.
"""

from __future__ import annotations

from textual.app import App

from config.settings import AppSettings
from database.operations import DatabaseManager
from utils.logging import get_logger

from .commands import NavCommands
from .screens.campaign_detail import CampaignDetailScreen
from .screens.campaign_edit import CampaignEditScreen
from .screens.campaigns import CampaignsScreen
from .screens.check_connections import CheckConnectionsScreen
from .screens.create_campaign import CreateCampaignScreen
from .screens.dashboard import DashboardScreen
from .screens.execute_campaign import ExecuteCampaignScreen
from .screens.extract_profiles import ExtractProfilesScreen
from .screens.home import HomeScreen
from .screens.settings_view import SettingsScreen
from .theme import LINKEDIN_THEME

logger = get_logger(__name__)

__all__ = [
    "LinkedInTUI",
    "HomeScreen",
    "CampaignsScreen",
    "CampaignDetailScreen",
    "CampaignEditScreen",
    "CreateCampaignScreen",
    "ExecuteCampaignScreen",
    "CheckConnectionsScreen",
    "ExtractProfilesScreen",
    "DashboardScreen",
    "SettingsScreen",
    "run",
]


class LinkedInTUI(App):
    """Full-screen Textual front end for issue #24."""

    TITLE = "LinkedIn Networking CLI"

    # External stylesheet (resolved relative to this module): the single source
    # of layout truth. Colour comes from the registered theme below.
    CSS_PATH = "app.tcss"

    # Extend the built-in palette commands with the app's navigation, rather
    # than replacing them, so theme/quit/etc. stay available.
    COMMANDS = App.COMMANDS | {NavCommands}

    def __init__(
        self,
        db_manager: DatabaseManager | None = None,
        settings: AppSettings | None = None,
    ) -> None:
        super().__init__()
        # AppSettings() touches the filesystem (it creates the app dir) and the
        # automation flows need it (LinkedInAutomation(db_manager, settings)), so
        # build and keep it here. Degrade to None on failure so the app still
        # runs and screens render an "unavailable" state instead of crashing.
        if settings is None:
            try:
                settings = AppSettings()
            except Exception:
                logger.exception("Failed to initialize settings; running degraded")
                settings = None
        if db_manager is None and settings is not None:
            # Build the default manager, but degrade gracefully on a startup
            # failure (e.g. read-only home, bad DB path) the same way the classic
            # CLI does. A None manager surfaces "Database unavailable." in screens.
            try:
                db_manager = DatabaseManager(str(settings.db_path))
            except Exception:
                logger.exception("Failed to initialize database; running degraded")
                db_manager = None
        self.settings = settings
        self.db_manager = db_manager

    def on_mount(self) -> None:
        # Register and select the design-system theme before the first screen
        # mounts so every screen renders against the brand palette.
        self.register_theme(LINKEDIN_THEME)
        self.theme = "linkedin"
        self.push_screen(HomeScreen())


def run() -> None:
    """Launch the Textual TUI."""
    LinkedInTUI().run()
