#!/usr/bin/env python3
"""Generate the home wordmark ("NETWORKING") as half-block pixel type.

The hero mascot is chunky half-block pixel art; thin figlet ASCII next to it
reads scratchy and mismatched. So the wordmark uses the same visual language:
a 5px-tall pixel font rendered with the same half-block technique (``▀``/
``▄``/``█``, 1 pixel = 1 col x half a row), solid brand light blue.

Emits WORDMARK_TALL — each pixel row a full terminal row (``█``): 5 rows tall,
vertically stretched (terminal cells are ~1:2), reads as condensed type. (The
home screen uses only the tall variant.)

Regenerate the baked module with:
    uv run python tools/mascot/generate_wordmark.py
"""
from __future__ import annotations

from pathlib import Path

LIGHT = "4bb4fc"          # brand light blue (same as the mascot highlights)

# 5px-tall proportional pixel glyphs.
GLYPHS = {
    "N": (
        "#...#",
        "##..#",
        "#.#.#",
        "#..##",
        "#...#",
    ),
    "E": (
        "####",
        "#...",
        "###.",
        "#...",
        "####",
    ),
    "T": (
        "#####",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
    ),
    "W": (
        "#...#",
        "#...#",
        "#.#.#",
        "##.##",
        "#...#",
    ),
    "O": (
        ".###.",
        "#...#",
        "#...#",
        "#...#",
        ".###.",
    ),
    "R": (
        "###.",
        "#..#",
        "###.",
        "#.#.",
        "#..#",
    ),
    "K": (
        "#..#",
        "#.#.",
        "##..",
        "#.#.",
        "#..#",
    ),
    "I": (
        "###",
        ".#.",
        ".#.",
        ".#.",
        "###",
    ),
    "G": (
        ".###.",
        "#....",
        "#..##",
        "#...#",
        ".####",
    ),
}


def word_bitmap(word: str) -> list[str]:
    rows = ["" for _ in range(5)]
    for i, ch in enumerate(word):
        g = GLYPHS[ch]
        for r in range(5):
            rows[r] += g[r] + ("." if i < len(word) - 1 else "")
    return rows


def to_halfblocks(rows: list[str]) -> str:
    """Square pixels: pair bitmap rows into half-block terminal rows."""
    if len(rows) % 2:
        rows = rows + ["." * len(rows[0])]
    lines = []
    for r in range(0, len(rows), 2):
        top, bot = rows[r], rows[r + 1]
        run = ""
        for t, b in zip(top, bot):
            on_t, on_b = t == "#", b == "#"
            if on_t and on_b:
                ch = "█"
            elif on_t:
                ch = "▀"
            elif on_b:
                ch = "▄"
            else:
                ch = " "
            run += ch
        # one colour for the whole line; blanks stay transparent
        lines.append(f"[#{LIGHT}]{run}[/]".rstrip())
    return "\n".join(lines)


def to_tall(rows: list[str]) -> str:
    """Condensed type: every bitmap row is one full terminal row of ``█``.

    ON runs paint background AND foreground the same colour: a bare ``█``
    relies on the glyph covering its whole cell, and any renderer whose block
    glyph leaves hairline gaps (SVG export, some fonts) shows the backdrop
    through them; with the background painted the cell is solid everywhere.
    """
    lines = []
    for row in rows:
        parts = []
        run_on = None
        buf = ""

        def flush():
            nonlocal buf
            if buf:
                parts.append(
                    f"[#{LIGHT} on #{LIGHT}]{buf}[/]" if run_on else buf
                )
                buf = ""

        for c in row:
            on = c == "#"
            if on != run_on and buf:
                flush()
            run_on = on
            buf += "█" if on else " "
        flush()
        lines.append("".join(parts).rstrip())
    return "\n".join(lines)


def main() -> None:
    rows = word_bitmap("NETWORKING")
    w = len(rows[0])
    tall = to_tall(rows)

    def as_body(markup: str) -> str:
        lines = markup.split("\n")
        return "\n".join(
            "    " + repr(line + ("\n" if i < len(lines) - 1 else ""))
            for i, line in enumerate(lines)
        )

    module = f'''"""Home wordmark art — "NETWORKING" as half-block pixel type (issue #24).

Same visual language as the mascot: a 5px pixel font rendered as a stretched
full-block variant, {w} cols wide, brand light blue via inline markup.
Regenerate with:
    uv run python tools/mascot/generate_wordmark.py
"""

WORDMARK_TALL = (
{as_body(tall)}
)

__all__ = ["WORDMARK_TALL"]
'''
    out = Path(__file__).resolve().parents[2] / "src/tui/screens/_wordmark.py"
    out.write_text(module, encoding="utf-8")
    print(f"wrote {out} ({w} cols)")


if __name__ == "__main__":
    main()
