"""Full-screen main menu (issue #24).

The keyboard-first entry point: a focused list rendered in place, no
scroll-behind. Selecting an item pushes the matching screen. The command palette
(ctrl+p) offers the same destinations for power users.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Center, Middle
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView

from utils.logging import get_logger

from .campaigns import CampaignsScreen
from .dashboard import DashboardScreen
from .settings_view import SettingsScreen

logger = get_logger(__name__)


class MainMenuScreen(Screen):
    """Full-screen main menu rendered in place.

    The menu has nowhere to pop back to, so (unlike the other screens) it does
    not inherit ``BaseScreen``'s ``escape`` binding; ``q`` quits the app.
    """

    BINDINGS = [("q", "app.quit", "Quit")]

    # Each entry: (item id, label). The read-only screens are wired now; the
    # write/automation flows are migrated in later PRs of issue #24.
    MENU_ITEMS = (
        ("dashboard", "Dashboard"),
        ("campaigns", "Campaigns"),
        ("settings", "Settings"),
        ("quit", "Quit"),
    )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Middle():
            with Center():
                yield Label("LinkedIn Networking CLI", id="menu-title")
            with Center():
                yield Label("A calm, keyboard-first console", id="menu-tagline")
            with Center():
                yield ListView(
                    *(
                        ListItem(Label(label), id=f"menu-{item_id}")
                        for item_id, label in self.MENU_ITEMS
                    ),
                    id="main-menu",
                )
        yield Footer()

    def on_mount(self) -> None:
        # Focus the menu so the highlighted first item responds to Enter on the
        # very first launch (without it, a keyboard user pressing Enter sees
        # nothing happen).
        self.query_one("#main-menu", ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        db = self.app.db_manager
        if item_id == "menu-dashboard":
            self.app.push_screen(DashboardScreen(db))
        elif item_id == "menu-campaigns":
            self.app.push_screen(CampaignsScreen(db))
        elif item_id == "menu-settings":
            self.app.push_screen(SettingsScreen(db))
        elif item_id == "menu-quit":
            self.app.exit()
