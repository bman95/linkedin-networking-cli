"""Shared chrome and bindings for every TUI screen (issue #24).

``BaseScreen`` gives the app one consistent frame so every view reads as the same
product as the home launcher: the same brand lockup (the blue ``in`` badge), a
breadcrumb to the current location, and a **dim hint bar** at the foot —
Claude-Code-style ``key action`` hints in muted text with a ``·`` separator,
deliberately *not* Textual's generic ``Header`` bar or chunky key-cap ``Footer``
(both read dated). Sub-screens inherit a single ``Back``/``Quit`` binding pair so
navigation is identical everywhere; the home overrides them.

``BaseScreen`` also mixes in :class:`~tui.screens.workers.WorkerGuardMixin`, so
every screen shares the same threaded-worker race discipline.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static

from .workers import WorkerGuardMixin

# The app mark: a single LinkedIn-blue "in" chip. (An earlier two-tile variant
# appended a bright "01" bit-square; side by side the pair read as two stray
# boxes rather than one mark, so the chip is just the identity now — the 0/1
# motif lives on in the mascot's eyes.) One mark, shared by every sub-screen's
# masthead, so the whole app reads as one piece.
BADGE = "[$text on $primary] in [/]"


def masthead_markup(title: str) -> str:
    """Breadcrumb masthead: ``in  LinkedIn Networking  ·  <Title>``.

    Identity recedes (muted wordmark) so the current location — the accent
    title — is the line's focus. Strings are static, so the markup is safe.
    """
    lockup = f"{BADGE}  [$text-muted]LinkedIn Networking[/]"
    if title:
        return f"{lockup}  [$text-disabled]·[/]  [b $secondary]{title}[/]"
    return lockup


def hint_markup(hints: tuple[tuple[str, str], ...]) -> str:
    """Render ``(key, action)`` pairs as a dim hint bar: ``key action  ·  …``.

    Keys are bold; the whole line is muted so it recedes. The strings are static
    (never user input), so the markup carries no injection risk.
    """
    parts = [f"[b]{key}[/b] {action}" for key, action in hints]
    return "[$text-muted]" + "  ·  ".join(parts) + "[/]"


class BaseScreen(WorkerGuardMixin, Screen):
    """A screen with the shared app frame: masthead, body hook, hint bar.

    Subclasses set ``SCREEN_TITLE`` and implement :meth:`compose_body` to yield
    their content; the brand masthead and hint bar are added automatically so the
    chrome stays consistent and in one place. The mixin supplies the threaded-
    worker guards (``begin_load`` / ``marshal`` / ``marshal_load``).
    """

    # Back to the previous screen — surfaced in the hint bar. Quit lives only
    # on the home screen (owner rule, 2026-07-09: no letter/ctrl accelerators
    # app-wide); every other screen's Quit path is esc-back-to-home-then-esc.
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
    ]

    # Current location, shown in the masthead breadcrumb. Subclasses override.
    SCREEN_TITLE = ""

    # (key, action) pairs shown in the dim hint bar. Subclasses override with
    # their own actual verbs; this is the generic fallback.
    HINTS: tuple[tuple[str, str], ...] = (
        ("esc", "back"),
    )

    def compose(self) -> ComposeResult:
        yield Static(masthead_markup(self.SCREEN_TITLE), classes="masthead")
        yield from self.compose_body()
        yield Static(hint_markup(self.HINTS), classes="hint-bar")

    def compose_body(self) -> ComposeResult:
        """Yield the screen's content widgets. Overridden by subclasses."""
        return iter(())
