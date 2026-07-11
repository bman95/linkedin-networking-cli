"""Arrow-key focus movement across a screen — Tab is never required.

The TUI's design language is arrows + Enter first (owner rule, 2026-07-09):
every action must be reachable with the arrow keys and activated with Enter,
with Tab kept only as an accelerator. Until this module, moving between a
screen's widget *groups* (table → buttons, field → submit) still required Tab.
Now a bare arrow key moves focus whenever the focused widget has no further
use for that key itself:

- Widgets with a vertical cursor (``ListView``, ``DataTable``, ``OptionList``,
  ``TextArea``, scrollables) keep ``up``/``down`` until the cursor sits on the
  relevant edge; one more press then moves focus to the previous/next widget.
- A single-line ``Input`` keeps ``left``/``right`` for the caret but has no
  vertical use, so ``up``/``down`` always move focus. Buttons use no arrows,
  so all four move focus (``left``/``right`` walk side-by-side button rows).
- A *closed* ``Select`` no longer opens on ``up``/``down`` — Enter/space still
  open it — so the arrows move focus like on any other field. An *open* Select
  focuses its internal ``SelectOverlay`` option list, which keeps every arrow:
  yanking focus out of a dropdown mid-choice would be hostile.

Textual's key-dispatch order makes the screen a safe interception point: a
``Key`` event bubbles through ``on_key`` handlers from the focused widget up
to the app *before* any widget's ``BINDINGS`` are checked (``App._on_key``
runs ``_check_bindings`` only once the event has bubbled unstopped). The
screen therefore sees every arrow first, and stopping the event is exactly
what suppresses the focused widget's own binding. Handlers closer to the leaf
(e.g. ``ConfirmBar.on_key``, which owns the arrows while armed) still win by
stopping the event before it reaches the screen.

``Screen.focus_next``/``focus_previous`` walk the focus chain in DOM order,
which matches the visual top-to-bottom (and left-to-right within rows) layout
of every screen here, and skip hidden widgets (``display: none`` children are
excluded from the chain). The chain wraps, so ``down`` on the last widget
returns to the first — arrows alone can always reach everything.
"""

from __future__ import annotations

from textual import events
from textual.containers import ScrollableContainer
from textual.widget import Widget
from textual.widgets import DataTable, Input, ListView, OptionList, Select, TextArea
from textual.widgets._select import SelectOverlay

_ARROWS = ("up", "down", "left", "right")
_BACKWARD = ("up", "left")


def widget_wants_arrow(widget: Widget, key: str) -> bool:
    """True while the focused widget still has its own use for this arrow.

    The subclass checks run most-derived first: ``SelectOverlay`` is an
    ``OptionList``; ``ListView``, ``DataTable``, ``OptionList`` and
    ``TextArea`` are all scrollables.
    """
    if isinstance(widget, SelectOverlay):
        return True  # an open dropdown keeps every arrow until dismissed
    if isinstance(widget, Select):
        return False  # closed Select: Enter/space open it, arrows move focus
    if isinstance(widget, Input):
        return key in ("left", "right")  # caret only; no vertical use
    if isinstance(widget, TextArea):
        if key in ("left", "right"):
            return True
        row = widget.cursor_location[0]
        return row > 0 if key == "up" else row < widget.document.line_count - 1
    if isinstance(widget, ListView):
        if key in ("left", "right"):
            return False
        index = widget.index
        if index is None:  # empty list must not trap focus
            return False
        return index > 0 if key == "up" else index < len(widget.children) - 1
    if isinstance(widget, OptionList):
        if key in ("left", "right"):
            return False
        highlighted = widget.highlighted
        if highlighted is None:
            return False
        return highlighted > 0 if key == "up" else highlighted < widget.option_count - 1
    if isinstance(widget, DataTable):
        if widget.row_count == 0:
            return False
        row, column = widget.cursor_coordinate
        if key == "up":
            return row > 0
        if key == "down":
            return row < widget.row_count - 1
        if widget.cursor_type not in ("cell", "column"):
            return False  # row cursor: left/right have no visible effect
        return column > 0 if key == "left" else column < len(widget.columns) - 1
    if isinstance(widget, ScrollableContainer):
        offset = widget.scroll_offset
        if key == "up":
            return offset.y > 0
        if key == "down":
            return offset.y < widget.max_scroll_y
        if key == "left":
            return offset.x > 0
        return offset.x < widget.max_scroll_x
    return False


class ArrowFocusMixin:
    """Screen mixin: bare arrows move focus once the focused widget is done.

    Mix into a ``Screen`` subclass (it relies on ``self.focused`` and
    ``focus_next``/``focus_previous``). ``down``/``right`` advance,
    ``up``/``left`` go back.
    """

    def on_key(self, event: events.Key) -> None:
        if event.key not in _ARROWS:
            return
        focused = self.focused
        # A hidden widget can hold focus for the instant before its host
        # refocuses (e.g. a just-disarmed ConfirmBar button); leave it alone.
        if focused is None or not focused.focusable:
            return
        if widget_wants_arrow(focused, event.key):
            return
        event.stop()
        if event.key in _BACKWARD:
            self.focus_previous()
        else:
            self.focus_next()
