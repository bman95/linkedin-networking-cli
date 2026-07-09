"""The single source of colour truth for the Textual TUI (issue #24).

The TUI's look is split deliberately:

- **This module** owns the *semantic palette* as a registered Textual
  :class:`~textual.theme.Theme`. Screens reference resolved tokens (``$primary``,
  ``$panel``, ``$text-muted`` …) in CSS, never raw hex — so colour lives in one
  place and contrast is derived consistently.
- **``app.tcss``** owns *layout*: spacing, borders, focus styling, grids. It
  reads the tokens defined here.

**Palette direction.** Anchored on LinkedIn's brand blue (kept identical to the
classic CLI for cross-surface coherence), but built out into a calm, modern
terminal palette in the spirit of the dark schemes that define 2025/2026
terminal aesthetics (Tokyo Night, Catppuccin): a deep, slightly *cool* neutral
base layered by elevation, soft pastel-leaning accents used with restraint, and
desaturated semantic colours that harmonise rather than shout. The brand blue
stays the identity (solid fills, focus, active); a brighter blue carries text
accents where the deep brand blue would read low-contrast on dark.
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
    foreground="#E4E9F0",      # body text — a touch cool, not stark white
    background="#11151A",      # app background — deep, cool, calm
    surface="#161B22",         # screen surface (one step up from background)
    panel="#1C222B",           # elevated cards / panels (one more step up)
    dark=True,
    variables={
        # Muted captions and status lines (the ``$text-muted`` token). The
        # dimmest tier (eyebrows) uses Textual's built-in ``$text-disabled``, and
        # raised hover surfaces use the auto-generated ``$surface-lighten-1`` —
        # both resolvable at first CSS parse, unlike brand-new custom variables.
        "text-muted": "#8A95A6",
        "block-cursor-foreground": "#FFFFFF",
    },
)
