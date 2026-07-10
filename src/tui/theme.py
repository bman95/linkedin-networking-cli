"""The single source of colour truth for the Textual TUI (issue #24).

The TUI's look is split deliberately:

- **This module** owns the *semantic palette* as a registered Textual
  :class:`~textual.theme.Theme`. Screens reference resolved tokens (``$primary``,
  ``$panel``, ``$text-muted`` …) in CSS, never raw hex — so colour lives in one
  place and contrast is derived consistently.
- **``app.tcss``** owns *layout*: spacing, borders, focus styling, grids. It
  reads the tokens defined here.

**Palette direction.** Anchored on LinkedIn's brand blue (kept identical to the
classic CLI for cross-surface coherence), but built out into a true-black
terminal palette: an OLED-black base (background/surface sit at or near
``#000000``), with elevation carried by near-neutral dark panels rather than
by lightening a cool grey-blue. The brand blue stays the identity (solid
fills, focus, active); a brighter blue carries text accents where the deep
brand blue would read low-contrast on black.
"""

from __future__ import annotations

from textual.theme import Theme

# LinkedIn brand blue — the same constant the classic InquirerPy CLI (retired,
# issue #47) branded with (``BRAND_BLUE`` in the removed ``linkedin_cli.py``).
# Kept identical for continuity, and pinned by ``test_brand_theme_is_active``.
BRAND_BLUE = "#0A66C2"

# One calm, dark theme. A light variant is an explicit later decision (see
# docs/tui-migration.md), so only one theme is registered now — no speculative
# theming machinery.
LINKEDIN_THEME = Theme(
    name="linkedin",
    primary=BRAND_BLUE,        # brand identity: solid fills, focus, active state
    secondary="#4D9FFF",       # brighter blue: text accents/links on dark
    accent="#4D9FFF",          # bright accent for headings/marks (legible on dark)
    success="#4FB477",         # calm green: healthy/active states, good rates
    warning="#E0A65B",         # amber: quota approaching, caution states
    error="#E5615B",           # soft red: error / degraded states
    foreground="#E6E8EB",      # body text — neutral, not stark white
    background="#000000",      # app background — true black
    surface="#050506",         # screen surface — effectively true black
    panel="#101114",           # elevated cards / panels — near-neutral dark
    dark=True,
    variables={
        # Muted captions and status lines (the ``$text-muted`` token). The
        # dimmest tier (eyebrows) uses Textual's built-in ``$text-disabled``, and
        # raised hover surfaces use the auto-generated ``$surface-lighten-1`` —
        # both resolvable at first CSS parse, unlike brand-new custom variables.
        "text-muted": "#7E858F",
        "block-cursor-foreground": "#FFFFFF",
    },
)
