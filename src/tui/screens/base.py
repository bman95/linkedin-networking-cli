"""Shared chrome and bindings for every TUI screen (issue #24).

``BaseScreen`` gives the app one consistent frame so every view reads as the same
product as the home launcher: the same brand lockup (the blue ``in`` badge), a
breadcrumb to the current location, and a **dim hint bar** at the foot —
Claude-Code-style ``key action`` hints in muted text with a ``·`` separator,
deliberately *not* Textual's generic ``Header`` bar or chunky key-cap ``Footer``
(both read dated). Sub-screens inherit a single ``Back``/``Quit`` binding pair so
navigation is identical everywhere; the home overrides them.
"""

from __future__ import annotations

from typing import Tuple

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static

# The app mark: a two-tile chip — the LinkedIn "in" on the brand blue, joined to
# a "01" bit on the brighter accent. It reads as the LinkedIn identity given a
# binary/digital twist (a bit: 0/1). One mark, shared by the home masthead and
# every sub-screen, so the whole app reads as one piece.
BADGE = "[$text on $primary] in [/][$text on $secondary] 01 [/]"


def masthead_markup(title: str) -> str:
    """Breadcrumb masthead: ``in  LinkedIn Networking  ·  <Title>``.

    Identity recedes (muted wordmark) so the current location — the accent
    title — is the line's focus. Strings are static, so the markup is safe.
    """
    lockup = f"{BADGE}  [$text-muted]LinkedIn Networking[/]"
    if title:
        return f"{lockup}  [$text-disabled]·[/]  [b $secondary]{title}[/]"
    return lockup


def hint_markup(hints: Tuple[Tuple[str, str], ...]) -> str:
    """Render ``(key, action)`` pairs as a dim hint bar: ``key action  ·  …``.

    Keys are bold; the whole line is muted so it recedes. The strings are static
    (never user input), so the markup carries no injection risk.
    """
    parts = [f"[b]{key}[/b] {action}" for key, action in hints]
    return "[$text-muted]" + "  ·  ".join(parts) + "[/]"


class BaseScreen(Screen):
    """A screen with the shared app frame: masthead, body hook, hint bar.

    Subclasses set ``SCREEN_TITLE`` and implement :meth:`compose_body` to yield
    their content; the brand masthead and hint bar are added automatically so the
    chrome stays consistent and in one place.
    """

    # Back to the previous screen, then Quit — surfaced in the hint bar.
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("q", "app.quit", "Quit"),
    ]

    # Current location, shown in the masthead breadcrumb. Subclasses override.
    SCREEN_TITLE = ""

    # (key, action) pairs shown in the dim hint bar. The data screens add their
    # own refresh hint; this is the shared baseline.
    HINTS: Tuple[Tuple[str, str], ...] = (
        ("esc", "back"),
        ("r", "refresh"),
        ("q", "quit"),
        ("ctrl+p", "commands"),
    )

    def compose(self) -> ComposeResult:
        yield Static(masthead_markup(self.SCREEN_TITLE), classes="masthead")
        yield from self.compose_body()
        yield Static(hint_markup(self.HINTS), classes="hint-bar")

    def compose_body(self) -> ComposeResult:
        """Yield the screen's content widgets. Overridden by subclasses."""
        return iter(())
