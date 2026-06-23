"""The single source of colour truth for the Textual TUI (issue #24).

The TUI's look is split deliberately:

- **This module** owns the *semantic palette* as a registered Textual
  :class:`~textual.theme.Theme`. Screens reference resolved tokens (``$primary``,
  ``$panel``, ``$text-muted`` …) in CSS, never raw hex — so colour lives in one
  place and contrast is derived consistently.
- **``app.tcss``** owns *layout*: spacing, borders, focus styling, grids. It
  reads the tokens defined here.

The palette is anchored on LinkedIn's brand blue so the new presentation layer
stays visually coherent with the classic CLI (which uses the same constant).
"""

from __future__ import annotations

from textual.theme import Theme

# LinkedIn brand blue — the same constant the classic InquirerPy CLI brands with
# (``BRAND_BLUE`` in ``linkedin_cli.py``). Kept identical for cross-surface
# coherence as the migration proceeds.
BRAND_BLUE = "#0A66C2"

# One calm, dark theme. A light variant is an explicit later decision (see
# docs/tui-migration.md), so only one theme is registered now — no speculative
# theming machinery.
LINKEDIN_THEME = Theme(
    name="linkedin",
    primary=BRAND_BLUE,        # brand accent: borders, focus, headings
    secondary="#378FE9",       # lighter blue: highlighted rows, secondary accents
    accent=BRAND_BLUE,         # aligned with primary for brand consistency
    success="#057642",         # LinkedIn green: healthy/active states, good rates
    warning="#B24020",         # quota approaching, caution states
    error="#CC1016",           # error / degraded states
    foreground="#E8E8E8",      # body text
    background="#1B1F23",      # app background — deep, calm
    surface="#22272E",         # screen surface
    panel="#2D333B",           # cards / panels
    dark=True,
    variables={
        # Muted captions and status lines (the ``$text-muted`` token).
        "text-muted": "#9AA5B1",
        "block-cursor-foreground": "#FFFFFF",
    },
)
