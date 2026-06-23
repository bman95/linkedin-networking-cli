"""Command-palette navigation for the TUI (issue #24).

Textual's command palette (ctrl+p) is enabled by default; this provider adds the
app's destinations so power users can jump anywhere without walking the menu —
the calm, Claude-Code-style quick-nav. It *extends* the default system commands
(``App.COMMANDS = App.COMMANDS | {NavCommands}``) rather than replacing them, so
the built-in commands (theme, quit, …) stay available.
"""

from __future__ import annotations

from typing import Callable, List, Tuple

from textual.command import DiscoveryHit, Hit, Hits, Provider


class NavCommands(Provider):
    """Exposes the app's screens as palette commands."""

    def _targets(self) -> List[Tuple[str, str, Callable[[], None]]]:
        """(name, help, action) for each navigable destination.

        Resolved against the live app each call so it reflects current state.
        Imports are local to keep the command module free of eager screen
        imports (preserving the package's lazy-import bootstrap discipline).
        """
        from .screens.campaigns import CampaignsScreen
        from .screens.dashboard import DashboardScreen
        from .screens.settings_view import SettingsScreen

        app = self.app
        db = app.db_manager
        return [
            ("Dashboard", "Open the overview dashboard",
             lambda: app.push_screen(DashboardScreen(db))),
            ("Campaigns", "List campaigns",
             lambda: app.push_screen(CampaignsScreen(db))),
            ("Settings", "View configuration",
             lambda: app.push_screen(SettingsScreen(db))),
            ("Quit", "Exit the application", app.exit),
        ]

    async def discover(self) -> Hits:
        """Commands shown before the user types anything."""
        for name, help_text, action in self._targets():
            yield DiscoveryHit(name, action, help=help_text)

    async def search(self, query: str) -> Hits:
        """Fuzzy-match the destinations against the typed query."""
        matcher = self.matcher(query)
        for name, help_text, action in self._targets():
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), action, help=help_text)
