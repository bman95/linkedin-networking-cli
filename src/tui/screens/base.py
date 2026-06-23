"""Shared chrome and bindings for every TUI screen (issue #24).

``BaseScreen`` gives the app a consistent frame so the experience feels curated
rather than assembled per screen: a pinned ``Header``, a pinned ``Footer`` whose
key hints are always discoverable, and a uniform bold context title. Sub-screens
inherit a single ``Back``/``Quit`` binding pair so navigation is identical
everywhere; the main menu overrides ``escape`` since it has nowhere to pop to.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Static


class BaseScreen(Screen):
    """A screen with the shared app frame: header, titled body hook, footer.

    Subclasses set ``SCREEN_TITLE`` and implement :meth:`compose_body` to yield
    their content; the header, title, and footer are added automatically so the
    chrome stays consistent and in one place.
    """

    # Back to the previous screen, then Quit — shown in the footer everywhere.
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("q", "app.quit", "Quit"),
    ]

    # Bold context title rendered under the header. Subclasses override.
    SCREEN_TITLE = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        if self.SCREEN_TITLE:
            yield Static(self.SCREEN_TITLE, classes="screen-title")
        yield from self.compose_body()
        yield Footer()

    def compose_body(self) -> ComposeResult:
        """Yield the screen's content widgets. Overridden by subclasses."""
        return iter(())
