"""Single-source registry of the app's navigable destinations (issue #24).

The home screen's navigation list, its number-key bindings, and the command
palette all enumerate the same destinations. Before this module they were four
hand-synced parallel lists (``home.py`` ``NAV_ITEMS``, its bindings, the screen
imports, and ``commands.py`` ``_targets``); now each derives from ``NAV_ITEMS``
here, so adding a destination is a one-place change.

Screen imports stay **inside** each ``push`` function: this module is imported
by ``tui.commands`` (loaded with the app shell), and the package's bootstrap
discipline requires screen modules to load lazily (see ``tui.screens.__init__``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.app import App


@dataclass(frozen=True)
class NavItem:
    """One navigable destination.

    ``key`` doubles as the home nav item's id suffix (``nav-<key>``); ``title``
    is the visible name in both the home list and the palette; ``description``
    is the home sub-line and the palette help text; ``push`` builds the screen
    from the live app's ``db_manager``/``settings`` and pushes it.
    """

    key: str
    title: str
    description: str
    push: Callable[[App], None]


def _dashboard(app: App) -> None:
    from .screens.dashboard import DashboardScreen

    app.push_screen(DashboardScreen(app.db_manager))


def _campaigns(app: App) -> None:
    from .screens.campaigns import CampaignsScreen

    app.push_screen(CampaignsScreen(app.db_manager))


def _create(app: App) -> None:
    from .screens.create_campaign import CreateCampaignScreen

    app.push_screen(CreateCampaignScreen(app.db_manager))


def _execute(app: App) -> None:
    from .screens.execute_campaign import ExecuteCampaignScreen

    app.push_screen(ExecuteCampaignScreen(app.db_manager, app.settings))


def _check(app: App) -> None:
    from .screens.check_connections import CheckConnectionsScreen

    app.push_screen(CheckConnectionsScreen(app.db_manager, app.settings))


def _extract(app: App) -> None:
    from .screens.extract_profiles import ExtractProfilesScreen

    app.push_screen(ExtractProfilesScreen(app.db_manager, app.settings))


def _settings(app: App) -> None:
    from .screens.settings_view import SettingsScreen

    app.push_screen(SettingsScreen(app.db_manager))


NAV_ITEMS: tuple[NavItem, ...] = (
    NavItem("dashboard", "Dashboard",
            "Campaign overview, connection stats, recent activity", _dashboard),
    NavItem("campaigns", "Campaigns",
            "Browse, open and manage your outreach campaigns", _campaigns),
    NavItem("create", "Create Campaign",
            "Set up a new outreach campaign", _create),
    NavItem("execute", "Execute Campaign",
            "Run automation: search and send connection requests", _execute),
    NavItem("check", "Check Connections",
            "Reconcile which pending invites were accepted", _check),
    NavItem("extract", "Extract Profile Data",
            "Pull detailed public data from profiles", _extract),
    NavItem("settings", "Settings",
            "Credentials, browser, rate limits, data locations", _settings),
)
