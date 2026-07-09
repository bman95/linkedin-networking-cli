#!/usr/bin/env python3
"""
Native-cell recovery of the "Bit" robot at full fidelity (45x41 logical grid).

Running this file directly bakes ``MASCOT_LARGE`` into
``src/tui/screens/_mascot_large.py`` (the home hero) and renders the full-size
mascot to ``mascot.txt`` / ``result.png`` / ``compare.png`` for inspection.

Approach
--------
The sprite is anti-aliased pixel art whose logical pixel boundaries are NOT on
a uniform lattice: the art was resized to 160px by a non-integer factor
(~2.5x), so logical pixels are 2-3 source px wide in an irregular rhythm, and
any fixed-unit grid drifts against them (cells straddle two logical pixels and
edges wobble by one cell — visibly "unsharp").

So instead of assuming a unit, we MEASURE the lattice:

1. Gradient profiles along each axis (within the robot bbox crop): logical
   pixel boundaries show up as columns/rows of high colour change.
2. Non-max suppression + centroid refinement gives subpixel boundary
   positions; peaks closer than 2.0px are one anti-aliased boundary split in
   two (the minimum logical unit is ~2.2px), so they are merged.
3. The measured boundaries become the grid lines directly. Gaps with no edges
   are flat colour, so they are subdivided evenly at the median unit (2.5px)
   — harmless there — to keep cells one-logical-pixel sized.
4. Each cell takes a weighted majority over its full pixel block. Two weights
   multiply: boundary distance (the centre weighs most, the ~1px AA band on
   each boundary weighs ~nothing) and PURITY (closeness to a true palette
   colour — AA blends between body-blue and the dark backdrop land nearest to
   navy and would otherwise paint false navy specks). A white-salience rule
   lets the tiny white highlight dots (on the "0" ring, antenna ball, ears)
   win their cell even against a light-blue majority.
5. Cells still ambiguous after that (majority colour explains <60% of the
   cell's weight) sit on locally-wobbly edges — the art's logical pixels are
   hand-drawn and drift ~1px around the global lattice line, most visibly on
   rounded corners and the thin smile stroke. Those cells are REFINED: their
   own boundaries jitter up to +-0.8px (less than half a unit, so a thin
   stroke cannot be double-counted into two cells) to the position that makes
   the cell maximally single-coloured, and are resampled there.

The native cells are then rendered as Rich half-block markup ("▀": each cell
is 1 col wide x 2 native cells tall; fg = top cell, bg = bottom cell, dark
snapped to the surface background).

Run:  uv run --with pillow python generate_large.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "tools/mascot")
import bit_sprite_source as s  # noqa: E402

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
CROP = (24, 23, 135, 125)          # inclusive bbox of the robot (112 x 103 px)
MEDIAN_UNIT = 2.5                  # only used to subdivide flat (edge-free) gaps
MERGE_DIST = 2.0                   # peaks closer than this are one AA boundary
PEAK_THR = 0.03                    # min peak energy, fraction of profile max

# ---------------------------------------------------------------------------
# Palette (snap every visible pixel to nearest; dark -> surface background)
# ---------------------------------------------------------------------------
SURFACE = (22, 27, 34)
NAVY = (4, 39, 129)
WHITE = (235, 247, 255)
LIGHT = (75, 180, 252)
PALETTE = {
    "navy": NAVY,
    "b1": (10, 70, 183),
    "b2": (9, 74, 201),
    "b3": (11, 79, 205),
    "b4": (10, 81, 211),
    "b5": (28, 107, 211),
    "light": LIGHT,
    "white": WHITE,
}
# Colours whose luma is at/under this are treated as background (surface).
# navy (luma ~ 41) is a real robot colour and must survive; surface is ~26.
BG_LUMA = 33


def luma(c):
    r, g, b = c
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def dist2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def snap(c):
    """Snap a raw pixel to the nearest palette entry, or SURFACE if too dark."""
    name, col = min(PALETTE.items(), key=lambda kv: dist2(c, kv[1]))
    if name == "navy" and luma(c) < BG_LUMA:
        return "surface", SURFACE
    return name, col


def is_white_core(c):
    """A coherent white pixel: bright and close to pure white."""
    r, g, b = c
    return r >= 205 and g >= 230 and b >= 245


# ---------------------------------------------------------------------------
# Measured lattice: gradient peaks -> grid lines
# ---------------------------------------------------------------------------
def _profile(px, axis):
    """Total colour change across each candidate boundary line in the crop."""
    x0, y0, x1, y1 = CROP
    g = []
    if axis == "x":
        for x in range(x0, x1):
            e = sum(
                abs(px[y][x][c] - px[y][x + 1][c])
                for y in range(y0, y1 + 1) for c in range(3)
            )
            g.append((x + 1.0, e))     # boundary sits between x and x+1
    else:
        for y in range(y0, y1):
            e = sum(
                abs(px[y][x][c] - px[y + 1][x][c])
                for x in range(x0, x1 + 1) for c in range(3)
            )
            g.append((y + 1.0, e))
    return g


def _grid_lines(g, lo, hi):
    """Subpixel gradient peaks (merged) plus even subdivision of flat gaps."""
    vals = [e for _, e in g]
    thr = max(vals) * PEAK_THR
    raw = []
    for i, (pos, e) in enumerate(g):
        if e < thr:
            continue
        prev_e = g[i - 1][1] if i > 0 else 0
        next_e = g[i + 1][1] if i < len(g) - 1 else 0
        if e >= prev_e and e > next_e:
            # centroid over the 3-neighbourhood for a subpixel position
            wsum = prev_e + e + next_e
            cpos = (
                (g[i - 1][0] if i > 0 else pos) * prev_e
                + pos * e
                + (g[i + 1][0] if i < len(g) - 1 else pos) * next_e
            ) / wsum
            raw.append((cpos, e))
    merged = []
    for pos, e in raw:
        if merged and pos - merged[-1][0] < MERGE_DIST:
            p0, e0 = merged[-1]
            merged[-1] = ((p0 * e0 + pos * e) / (e0 + e), e0 + e)
        else:
            merged.append((pos, e))
    lines = [lo] + [p for p, _ in merged if lo + 0.8 < p < hi - 0.8] + [hi]
    full = []
    for a, b in zip(lines, lines[1:]):
        full.append(a)
        gap = b - a
        n = max(1, round(gap / MEDIAN_UNIT))
        for k in range(1, n):
            full.append(a + gap * k / n)
    full.append(lines[-1])
    return full


# ---------------------------------------------------------------------------
# Build the native grid on the measured lattice
# ---------------------------------------------------------------------------
def _cell_weights(a0, a1, b0, b1):
    """(x, y, weight) for every pixel of cell [a0,a1)x[b0,b1). Weight is the
    product of each axis' distance from the pixel centre to the nearest cell
    boundary, so the centre dominates and the ~1px AA band counts ~nothing."""
    x0, y0, x1, y1 = CROP

    def axis(lo, hi, amin, amax):
        out = []
        for v in range(max(amin, int(lo)), min(amax, int(hi)) + 1):
            t = v + 0.5
            if not (lo <= t <= hi):
                continue
            d = min(t - lo, hi - t)
            out.append((v, max(d - 0.25, 0.05)))
        return out or [(min(amax, max(amin, int((lo + hi) / 2))), 1.0)]

    return [
        (x, y, wx * wy)
        for y, wy in axis(b0, b1, y0, y1)
        for x, wx in axis(a0, a1, x0, x1)
    ]


def _purity(c):
    """1.0 for a pixel sitting ON a palette colour, ->0 for an AA blend."""
    d = min(dist2(c, col) for col in list(PALETTE.values()) + [SURFACE])
    return 1.0 / (1.0 + d / 800.0)


def _sample_cell(px, a0, a1, b0, b1):
    """(chosen name, explained fraction) for cell [a0,a1)x[b0,b1)."""
    counts = {}
    white_w = total_w = 0.0
    for x, y, w in _cell_weights(a0, a1, b0, b1):
        col = px[y][x]
        w *= _purity(col)
        total_w += w
        if is_white_core(col):
            white_w += w
        name, _ = snap(col)
        counts[name] = counts.get(name, 0.0) + w

    # Salience rule: a coherent white core (>=25% of the cell weight) wins the
    # cell so the tiny white highlight dots survive against a light-blue
    # majority.
    if white_w * 4 >= total_w:
        return "white", white_w / total_w
    best, best_w = max(counts.items(), key=lambda kv: kv[1])
    return best, best_w / total_w


REFINE_BELOW = 0.60      # refine cells whose colour explains less than this
JITTER = 0.8             # max local boundary shift, px (< half a 2.5px unit)


def build_native_grid():
    px = s.load_pixels()
    x0, y0, x1, y1 = CROP
    gx = _grid_lines(_profile(px, "x"), float(x0), float(x1 + 1))
    gy = _grid_lines(_profile(px, "y"), float(y0), float(y1 + 1))

    grid = []                              # rows of (name, rgb)
    refined = 0
    for r in range(len(gy) - 1):
        row = []
        for c in range(len(gx) - 1):
            name, frac = _sample_cell(px, gx[c], gx[c + 1], gy[r], gy[r + 1])

            if frac < REFINE_BELOW:
                # Locally-wobbly edge: jitter this cell's own boundaries to
                # the most single-coloured position and resample. Prefer the
                # smallest shift on ties so unambiguous strokes stay put.
                best = (frac, 0.0, name)
                for da0 in (-JITTER, 0.0, JITTER):
                    for da1 in (-JITTER, 0.0, JITTER):
                        for db0 in (-JITTER, 0.0, JITTER):
                            for db1 in (-JITTER, 0.0, JITTER):
                                shift = abs(da0) + abs(da1) + abs(db0) + abs(db1)
                                if shift == 0.0:
                                    continue
                                n2, f2 = _sample_cell(
                                    px,
                                    gx[c] + da0, gx[c + 1] + da1,
                                    gy[r] + db0, gy[r + 1] + db1,
                                )
                                if (f2, -shift) > (best[0], -best[1]):
                                    best = (f2, shift, n2)
                if best[2] != name:
                    refined += 1
                name = best[2]

            row.append((name, SURFACE if name == "surface" else PALETTE[name]))
        grid.append(row)
    print(f"refined {refined} locally-wobbly cells")
    return grid


# ---------------------------------------------------------------------------
# Rich half-block markup
# ---------------------------------------------------------------------------
def rgb_hex(c):
    return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"


def render_markup(grid):
    rows = len(grid)
    cols = len(grid[0])
    lines = []
    for ry in range(0, rows, 2):
        top_row = grid[ry]
        bot_row = grid[ry + 1] if ry + 1 < rows else [("surface", SURFACE)] * cols
        parts = []
        run_fg = run_bg = None
        buf = ""
        for cx in range(cols):
            top = top_row[cx][1]
            bot = bot_row[cx][1]
            if top == run_fg and bot == run_bg:
                buf += "▀"
            else:
                if buf:
                    parts.append(_span(run_fg, run_bg, buf))
                run_fg, run_bg = top, bot
                buf = "▀"
        if buf:
            parts.append(_span(run_fg, run_bg, buf))
        lines.append("".join(parts))
    return "\n".join(lines)


def _span(fg, bg, text):
    return f"[{rgb_hex(fg)} on {rgb_hex(bg)}]{text}[/]"


# ---------------------------------------------------------------------------
# PNG renderers (font-free) via Pillow
# ---------------------------------------------------------------------------
def render_result_png(grid, path, scale=10):
    from PIL import Image

    rows = len(grid)
    cols = len(grid[0])
    img = Image.new("RGB", (cols * scale, rows * scale), SURFACE)
    px = img.load()
    for cy in range(rows):
        for cx in range(cols):
            c = grid[cy][cx][1]
            for yy in range(cy * scale, (cy + 1) * scale):
                for xx in range(cx * scale, (cx + 1) * scale):
                    px[xx, yy] = c
    img.save(path)
    return img.size


def render_compare_png(grid, path, scale=10):
    from PIL import Image

    px_src = s.load_pixels()
    x0, y0, x1, y1 = CROP
    cw = x1 - x0 + 1
    ch = y1 - y0 + 1

    rows = len(grid)
    cols = len(grid[0])
    result_h = rows * scale
    result_w = cols * scale

    # Original crop nearest-upscaled to the SAME height as the result panel.
    up = result_h / ch
    src_w = int(round(cw * up))
    src_h = result_h
    left = Image.new("RGB", (src_w, src_h), SURFACE)
    lpx = left.load()
    for yy in range(src_h):
        sy = min(ch - 1, int(yy / up))
        for xx in range(src_w):
            sx = min(cw - 1, int(xx / up))
            c = px_src[y0 + sy][x0 + sx]
            # dark -> surface, so the black outside becomes the surface bg
            lpx[xx, yy] = SURFACE if luma(c) < BG_LUMA and c != NAVY else c

    right = Image.new("RGB", (result_w, result_h), SURFACE)
    rpx = right.load()
    for cy in range(rows):
        for cx in range(cols):
            c = grid[cy][cx][1]
            for yy in range(cy * scale, (cy + 1) * scale):
                for xx in range(cx * scale, (cx + 1) * scale):
                    rpx[xx, yy] = c

    gap = 24
    total = Image.new("RGB", (src_w + gap + result_w, result_h), SURFACE)
    total.paste(left, (0, 0))
    total.paste(right, (src_w + gap, 0))
    total.save(path)
    return total.size


_MODULE_TEMPLATE = '''"""Large home-hero mascot — Bit at full fidelity (issue #24).

Recovered from the 160x160 sprite by MEASURING the logical pixel lattice
(gradient peaks; the art's boundaries are non-uniform, 2-3px apart, so no
fixed-unit grid fits) and sampling each cell with boundary-distance x purity
weighting plus local jitter refinement — see ``tools/mascot/generate_large.py``.
Rendered as half blocks at {cols} cols x {rows} rows. Regenerate with:
    uv run --with pillow python tools/mascot/generate_large.py --module
"""

MASCOT_LARGE = (
{body}
)

__all__ = ["MASCOT_LARGE"]
'''


def module_source() -> str:
    grid = build_native_grid()
    markup = render_markup(grid)
    lines = markup.split("\n")
    body = "\n".join(
        "    " + repr(line + ("\n" if i < len(lines) - 1 else ""))
        for i, line in enumerate(lines)
    )
    return _MODULE_TEMPLATE.format(
        cols=len(grid[0]), rows=(len(grid) + 1) // 2, body=body
    )


# ---------------------------------------------------------------------------
def main():
    if "--module" in sys.argv:
        out = Path(__file__).resolve().parents[2] / "src/tui/screens/_mascot_large.py"
        out.write_text(module_source(), encoding="utf-8")
        print(f"wrote {out}")
        return

    out = Path(__file__).resolve().parent
    grid = build_native_grid()
    rows = len(grid)
    cols = len(grid[0])
    term_rows = (rows + 1) // 2

    markup = render_markup(grid)
    (out / "mascot.txt").write_text(markup + "\n", encoding="utf-8")

    res_size = render_result_png(grid, out / "result.png")
    cmp_size = render_compare_png(grid, out / "compare.png")

    print(f"measured grid: {cols} cols x {rows} rows")
    print(f"terminal half-block render: {cols} cols x {term_rows} rows")
    print(f"result.png  {res_size[0]}x{res_size[1]}")
    print(f"compare.png {cmp_size[0]}x{cmp_size[1]}")
    print(f"mascot.txt  {out / 'mascot.txt'}")


if __name__ == "__main__":
    main()
