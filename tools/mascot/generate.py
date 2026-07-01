#!/usr/bin/env python3
"""Generate the home mascot ("Bit") as Rich half-block markup.

Reproducible source of truth: the 160x160 RGB sprite embedded in
``bit_sprite_source.py`` (no PNG, no glow/halo). The sprite is anti-aliased
(~2000 colours) but the robot is really small pixel art — a handful of flat
colours on a single logical grid (the "minimal unit"). Point-sampling that with
nearest-neighbour lands on the anti-alias fringe and smears the structure (the
"0" loses its four white squares); interpolation (Lanczos) blurs it worse. So we
RECOVER the logical grid instead:

1. **Palette-snap** the crop — every source pixel maps to the nearest true
   ``PALETTE`` colour; pixels darker than ``$surface`` map to background.
2. **Detect the minimal unit** — find the native pixel pitch ``P`` and grid
   offset by scoring how strongly real colour edges land on grid lines, gated by
   the ground-truth anchor that the "0" must contain exactly four white squares.
3. **Mode-downsample** — each native cell takes the MAJORITY snapped colour of
   its source pixels, collapsing the anti-alias smear into the flat logical robot.
4. **Render** to half-block Rich markup (``▀``: top pixel = fg, bottom = bg;
   only ``▀`` / ``█`` / space — universal glyphs).

Regenerate the baked module with:
    uv run --with pillow python tools/mascot/generate.py --module
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bit_sprite_source as src  # noqa: E402

# The robot's true palette (a few blues + navy + white). The anti-aliased sprite
# has ~2000 colours; every visible pixel is snapped to one of these.
PALETTE = [
    (0x04, 0x27, 0x81),  # 0 deep navy — body shadow / dark outline
    (0x0a, 0x46, 0xb7),  # 1 body blues …
    (0x09, 0x4a, 0xc9),  # 2
    (0x0b, 0x4f, 0xcd),  # 3
    (0x0a, 0x51, 0xd3),  # 4
    (0x1c, 0x6b, 0xd3),  # 5 brighter blue
    (0x4b, 0xb4, 0xfc),  # 6 light blue — the "0 1" outline / highlights
    (0xeb, 0xf7, 0xff),  # 7 white      — the four "0" squares
]
WHITE = 7
SURFACE = (0x16, 0x1B, 0x22)
SURFACE_HEX = "161B22"
CROP = (24, 23, 135, 125)  # x0, y0, x1, y1 inclusive — robot bbox


def luma(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


SURF_L = luma(SURFACE)


def snap(c):
    """Nearest palette index, or -1 (background) for sub-surface pixels."""
    if luma(c) < SURF_L:
        return -1
    return min(
        range(len(PALETTE)),
        key=lambda i: (PALETTE[i][0] - c[0]) ** 2
        + (PALETTE[i][1] - c[1]) ** 2
        + (PALETTE[i][2] - c[2]) ** 2,
    )


def load_snapped():
    pixels = src.load_pixels()
    x0, y0, x1, y1 = CROP
    W, H = x1 - x0 + 1, y1 - y0 + 1
    G = [[snap(pixels[y0 + y][x0 + x]) for x in range(W)] for y in range(H)]
    return G, W, H


# ── minimal-unit grid detection ────────────────────────────────────────────
def edge_signals(G, W, H):
    vedge = [0.0] * W
    for x in range(1, W):
        vedge[x] = sum(1 for y in range(H) if G[y][x] != G[y][x - 1])
    hedge = [0.0] * H
    for y in range(1, H):
        hedge[y] = sum(1 for x in range(W) if G[y][x] != G[y - 1][x])
    return vedge, hedge


def on_grid_contrast(edge, N, P, off):
    """avg edge ON grid lines  -  avg edge OFF grid lines (higher = aligned)."""
    on, off_w = [], []
    lines = set()
    k = 0
    while off + k * P < N:
        lines.add(int(round(off + k * P)))
        k += 1
    for x in range(1, N):
        (on if x in lines else off_w).append(edge[x])
    if not on or not off_w:
        return -1.0
    return sum(on) / len(on) - sum(off_w) / len(off_w)


def mode_grid(G, W, H, P, ox, oy):
    Lw = int((W - ox) / P)
    Lh = int((H - oy) / P)
    out = [[None] * Lw for _ in range(Lh)]
    for cy in range(Lh):
        ya, yb = int(round(oy + cy * P)), int(round(oy + (cy + 1) * P))
        for cx in range(Lw):
            xa, xb = int(round(ox + cx * P)), int(round(ox + (cx + 1) * P))
            cnt = {}
            for y in range(ya, min(yb, H)):
                row = G[y]
                for x in range(xa, min(xb, W)):
                    v = row[x]
                    cnt[v] = cnt.get(v, 0) + 1
            # majority; on a tie prefer a real colour over background
            out[cy][cx] = max(cnt, key=lambda k: (cnt[k], 0 if k == -1 else 1))
    return out, Lw, Lh


def white_squares_in_zero(grid, P, ox, oy):
    """Count distinct white components whose centre lands in the '0' box."""
    Lh, Lw = len(grid), len(grid[0])
    seen = [[False] * Lw for _ in range(Lh)]
    n = 0
    for sy in range(Lh):
        for sx in range(Lw):
            if grid[sy][sx] == WHITE and not seen[sy][sx]:
                cells = [(sy, sx)]
                seen[sy][sx] = True
                st = [(sy, sx)]
                while st:
                    y, x = st.pop()
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < Lh and 0 <= nx < Lw and grid[ny][nx] == WHITE and not seen[ny][nx]:
                            seen[ny][nx] = True
                            cells.append((ny, nx))
                            st.append((ny, nx))
                mx = sum(c[1] for c in cells) / len(cells)
                my = sum(c[0] for c in cells) / len(cells)
                sxs = ox + (mx + 0.5) * P
                sys_ = oy + (my + 0.5) * P
                if 30 <= sxs <= 47 and 44 <= sys_ <= 66:  # the "0" box, crop coords
                    n += 1
    return n


# An unconstrained edge search collapses onto the smallest unit (~3 px -> 37
# cols), which overflows a TUI panel. So we render that unit scaled to a sensible
# terminal canvas: the grid in the 24-32 col band with the best edge alignment
# that still reproduces the four-white-square anchor.
TARGET_COLS = (24, 32)


def detect(G, W, H):
    """Find (P, ox, oy): max edge-on-grid contrast within the target col band,
    gated by the ground-truth four-white-square anchor in the '0'."""
    vedge, hedge = edge_signals(G, W, H)
    best = None
    for P10 in range(30, 56):          # P from 3.0 to 5.5 source px
        P = P10 / 10
        if not (TARGET_COLS[0] <= int(W / P) <= TARGET_COLS[1]):
            continue
        offs = [o / 2 for o in range(0, int(P * 2))]
        for ox in offs:
            cx = on_grid_contrast(vedge, W, P, ox)
            for oy in offs:
                cy = on_grid_contrast(hedge, H, P, oy)
                grid, _, Lh = mode_grid(G, W, H, P, ox, oy)
                if Lh % 2:             # half-block needs an even row count
                    continue
                if white_squares_in_zero(grid, P, ox, oy) != 4:
                    continue           # anchor: the "0" must show exactly four
                score = cx + cy
                if best is None or score > best[0]:
                    best = (score, P, ox, oy)
    return best


# ── rendering ──────────────────────────────────────────────────────────────
def _hex(c):
    return f"{c[0]:02x}{c[1]:02x}{c[2]:02x}"


def to_markup(grid):
    """Half-block Rich markup; grid rows paired top/bottom into one cell each."""
    Lh, Lw = len(grid), len(grid[0])
    lines = []
    for r in range(Lh // 2):
        parts = []
        for c in range(Lw):
            ti, bi = grid[2 * r][c], grid[2 * r + 1][c]
            tb = SURFACE_HEX if ti == -1 else _hex(PALETTE[ti])
            bb = SURFACE_HEX if bi == -1 else _hex(PALETTE[bi])
            if ti == -1 and bi == -1:
                parts.append(" ")
            elif tb == bb:
                parts.append(f"[#{tb}]█[/]")
            else:
                parts.append(f"[#{tb} on #{bb}]▀[/]")
        lines.append("".join(parts).rstrip())
    return "\n".join(lines)


def build():
    """Recover the grid and return (markup, cols, rows, P, ox, oy)."""
    G, W, H = load_snapped()
    best = detect(G, W, H)
    if best is None:
        raise SystemExit("grid detection failed")
    _, P, ox, oy = best
    grid, Lw, Lh = mode_grid(G, W, H, P, ox, oy)
    return to_markup(grid), Lw, Lh // 2, P, ox, oy


_MODULE_TEMPLATE = '''"""Home mascot art — Bit, the LinkedIn-blue robot (issue #24).

A faithful rendering of the reference robot, generated from the 160x160 RGB
sprite embedded in ``tools/mascot/bit_sprite_source.py`` (no PNG, no glow/halo).
The sprite is anti-aliased, but the robot is really small pixel art on a single
logical grid; the generator recovers that grid — detects the minimal unit (native
pixel pitch), then takes the majority palette colour per cell — so the figure is
crisp and flat (the "0" keeps its four white squares) rather than the speckle a
point-sampled downscale produces. Rendered as half blocks: each cell is ``▀``
(top pixel = foreground, bottom = background); only ``▀`` / ``█`` / space appear.
Pixels darker than ``$surface`` (#161B22) map to surface so the backdrop
dissolves into the home.

Rendered at {cols} cells wide x {rows} tall (minimal unit P={P} source px,
offset ({ox}, {oy})). Regenerate with:
    uv run --with pillow python tools/mascot/generate.py --module
"""

MASCOT = (
{body}
)
'''


def module_source() -> str:
    markup, cols, rows, P, ox, oy = build()
    lines = markup.split("\n")
    body = "\n".join(
        "    " + repr(line + ("\n" if i < len(lines) - 1 else ""))
        for i, line in enumerate(lines)
    )
    return _MODULE_TEMPLATE.format(cols=cols, rows=rows, P=P, ox=ox, oy=oy, body=body)


if __name__ == "__main__":
    if "--module" in sys.argv:
        out = Path(__file__).resolve().parents[2] / "src/tui/screens/_mascot.py"
        out.write_text(module_source(), encoding="utf-8")
        markup, cols, rows, P, ox, oy = build()
        print(f"wrote {out} ({cols}x{rows}, unit P={P}px, offset ({ox},{oy}))")
    else:
        markup, cols, rows, P, ox, oy = build()
        print(f"# {cols}x{rows}  unit P={P}px  offset ({ox},{oy})")
        print(markup)
