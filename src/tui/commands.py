"""Command-palette navigation for the TUI (issue #24).

Textual's command palette (ctrl+p) is enabled by default; this provider adds the
app's destinations so power users can jump anywhere without walking the menu —
the calm, Claude-Code-style quick-nav. It *extends* the default system commands
(``App.COMMANDS = App.COMMANDS | {NavCommands}``) rather than replacing them, so
the built-in commands (theme, quit, …) stay available.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from textual.command import DiscoveryHit, Hit, Hits, Provider

from .nav import NAV_ITEMS


class NavCommands(Provider):
    """Exposes the app's screens as palette commands."""

    def _targets(self) -> list[tuple[str, str, Callable[[], None]]]:
        """(name, help, action) for each navigable destination.

        Derived from the shared ``tui.nav`` registry (which defers the screen
        imports, preserving the package's lazy-import bootstrap discipline),
        plus the palette-only Quit command. Resolved against the live app each
        call so it reflects current state.
        """
        app = self.app
        targets: list[tuple[str, str, Callable[[], None]]] = [
            (item.title, item.description, partial(item.push, app))
            for item in NAV_ITEMS
        ]
        targets.append(("Quit", "Exit the application", app.exit))
        return targets

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
