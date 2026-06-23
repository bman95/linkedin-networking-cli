"""Textual application shell for the LinkedIn networking CLI (issue #24).

This is the app frame: it registers the design-system theme, loads the external
stylesheet, wires the command palette, and routes to the screens. The screens
themselves live under ``tui.screens``; the design tokens live in ``tui.theme``
and the layout in ``app.tcss`` — one source of truth each.

The app reuses the existing business logic untouched: ``AppSettings`` for the DB
path and ``DatabaseManager`` for reads. Every shipped screen is read-only, so the
TUI needs no LinkedIn credentials and no browser, and stays driveable in the
Textual headless harness.

``MainMenuScreen`` and ``CampaignsScreen`` are re-exported here for backwards
compatibility with importers (and tests) that predate the screens split.
"""

from __future__ import annotations

from typing import Optional

from textual.app import App

from config.settings import AppSettings
from database.operations import DatabaseManager
from utils.logging import get_logger

from .commands import NavCommands
from .screens.campaigns import CampaignsScreen
from .screens.dashboard import DashboardScreen
from .screens.main_menu import MainMenuScreen
from .screens.settings_view import SettingsScreen
from .theme import LINKEDIN_THEME

logger = get_logger(__name__)

__all__ = [
    "LinkedInTUI",
    "MainMenuScreen",
    "CampaignsScreen",
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

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        super().__init__()
        if db_manager is None:
            # Build the default manager, but degrade gracefully on a startup
            # failure (e.g. read-only home, bad DB path) the same way the classic
            # CLI does, rather than crashing before any screen is shown. A None
            # manager surfaces "Database unavailable." in the data screens.
            try:
                settings = AppSettings()
                db_manager = DatabaseManager(str(settings.db_path))
            except Exception:
                logger.exception("Failed to initialize database; running degraded")
                db_manager = None
        self.db_manager = db_manager

    def on_mount(self) -> None:
        # Register and select the design-system theme before the first screen
        # mounts so every screen renders against the brand palette.
        self.register_theme(LINKEDIN_THEME)
        self.theme = "linkedin"
        self.push_screen(MainMenuScreen())


def run() -> None:
    """Launch the Textual TUI."""
    LinkedInTUI().run()
