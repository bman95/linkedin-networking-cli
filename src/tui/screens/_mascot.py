"""Home mascot art — Bit, the LinkedIn-blue robot (issue #24).

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

Rendered at 30 cells wide x 14 tall (minimal unit P=3.6 source px,
offset (2.0, 2.0)). Regenerate with:
    uv run --with pillow python tools/mascot/generate.py --module
"""

MASCOT = (
    '     [#161B22 on #042781]▀[/][#161B22 on #4bb4fc]▀[/][#161B22 on #ebf7ff]▀[/]              [#161B22 on #4bb4fc]▀[/][#161B22 on #4bb4fc]▀[/]\n'
    '     [#4bb4fc on #161B22]▀[/][#4bb4fc]█[/][#4bb4fc]█[/][#4bb4fc on #0a51d3]▀[/][#161B22 on #042781]▀[/]          [#161B22 on #4bb4fc]▀[/]  [#161B22 on #0a51d3]▀[/] [#4bb4fc on #161B22]▀[/]\n'
    '         [#0a46b7 on #161B22]▀[/][#0a46b7]█[/][#161B22 on #0a46b7]▀[/]        [#4bb4fc on #161B22]▀[/]\n'
    '       [#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#0a46b7]█[/][#0a46b7]█[/][#161B22 on #0a51d3]▀[/][#161B22 on #0a51d3]▀[/][#161B22 on #0a51d3]▀[/][#161B22 on #0a51d3]▀[/][#161B22 on #0a51d3]▀[/][#161B22 on #0a51d3]▀[/][#161B22 on #0a51d3]▀[/][#161B22 on #4bb4fc]▀[/][#161B22 on #4bb4fc]▀[/][#161B22 on #ebf7ff]▀[/][#161B22 on #ebf7ff]▀[/]\n'
    '    [#161B22 on #042781]▀[/][#0a46b7 on #0a51d3]▀[/][#0a51d3]█[/][#0a51d3]█[/][#0a51d3]█[/][#0a51d3]█[/][#0a51d3]█[/][#0a51d3]█[/][#0a51d3]█[/][#0a51d3]█[/][#0a51d3]█[/][#0a51d3]█[/][#0a51d3]█[/][#0a51d3]█[/][#0a51d3]█[/][#0a51d3]█[/][#0a51d3]█[/][#4bb4fc on #0a51d3]▀[/][#4bb4fc on #0a51d3]▀[/][#ebf7ff on #0a51d3]▀[/][#ebf7ff on #1c6bd3]▀[/][#042781 on #0a51d3]▀[/]\n'
    '    [#042781]█[/][#0a51d3]█[/][#0a51d3 on #042781]▀[/][#0a51d3 on #161B22]▀[/]              [#0a51d3 on #161B22]▀[/][#0a51d3]█[/][#0a51d3]█[/][#094ac9 on #0a51d3]▀[/]\n'
    ' [#161B22 on #1c6bd3]▀[/][#0a46b7 on #0a51d3]▀[/] [#042781]█[/][#0b4fcd]█[/]  [#161B22 on #4bb4fc]▀[/][#4bb4fc on #ebf7ff]▀[/][#4bb4fc]█[/][#4bb4fc on #ebf7ff]▀[/][#161B22 on #4bb4fc]▀[/]    [#161B22 on #4bb4fc]▀[/][#4bb4fc]█[/][#4bb4fc]█[/]    [#0a51d3]█[/][#0a46b7]█[/] [#0a46b7 on #4bb4fc]▀[/][#161B22 on #4bb4fc]▀[/]\n'
    ' [#094ac9]█[/][#094ac9]█[/] [#042781]█[/][#0b4fcd]█[/]  [#4bb4fc]█[/][#4bb4fc]█[/] [#4bb4fc]█[/][#4bb4fc]█[/]     [#4bb4fc]█[/][#4bb4fc]█[/]    [#0a51d3]█[/][#0a46b7]█[/] [#0b4fcd on #094ac9]▀[/][#094ac9]█[/]\n'
    ' [#0a46b7 on #042781]▀[/][#0a46b7 on #042781]▀[/] [#042781]█[/][#0b4fcd]█[/]  [#4bb4fc on #161B22]▀[/][#ebf7ff on #4bb4fc]▀[/][#4bb4fc]█[/][#ebf7ff on #4bb4fc]▀[/][#4bb4fc on #161B22]▀[/]     [#4bb4fc]█[/][#4bb4fc]█[/][#4bb4fc]█[/]   [#0a51d3 on #0b4fcd]▀[/][#0a46b7]█[/] [#094ac9 on #042781]▀[/][#0a46b7 on #042781]▀[/]\n'
    '    [#042781]█[/][#094ac9]█[/][#042781 on #094ac9]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#042781 on #0b4fcd]▀[/][#0b4fcd]█[/][#0a46b7]█[/]\n'
    '    [#042781 on #161B22]▀[/][#094ac9]█[/][#094ac9]█[/][#094ac9]█[/][#0b4fcd on #094ac9]▀[/][#0b4fcd on #094ac9]▀[/][#1c6bd3 on #094ac9]▀[/][#1c6bd3 on #094ac9]▀[/][#0b4fcd on #4bb4fc]▀[/][#0b4fcd on #094ac9]▀[/][#0b4fcd on #094ac9]▀[/][#094ac9]█[/][#0b4fcd on #094ac9]▀[/][#0b4fcd on #1c6bd3]▀[/][#1c6bd3 on #094ac9]▀[/][#1c6bd3 on #094ac9]▀[/][#0b4fcd on #094ac9]▀[/][#0b4fcd on #094ac9]▀[/][#0b4fcd on #094ac9]▀[/][#0b4fcd on #094ac9]▀[/][#094ac9]█[/][#0a46b7 on #042781]▀[/]\n'
    '      [#094ac9 on #161B22]▀[/][#094ac9]█[/][#094ac9]█[/][#094ac9]█[/][#094ac9]█[/][#094ac9]█[/][#094ac9]█[/][#4bb4fc on #094ac9]▀[/][#4bb4fc on #094ac9]▀[/][#4bb4fc on #094ac9]▀[/][#4bb4fc on #094ac9]▀[/][#094ac9]█[/][#094ac9]█[/][#094ac9 on #042781]▀[/][#094ac9 on #042781]▀[/][#094ac9 on #042781]▀[/][#094ac9 on #042781]▀[/][#094ac9 on #042781]▀[/][#042781 on #161B22]▀[/][#042781 on #161B22]▀[/]\n'
    '       [#161B22 on #0a46b7]▀[/][#161B22 on #094ac9]▀[/][#161B22 on #094ac9]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/]     [#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #0a46b7]▀[/][#161B22 on #094ac9]▀[/][#161B22 on #094ac9]▀[/]\n'
    '       [#042781 on #161B22]▀[/][#0a46b7 on #161B22]▀[/][#1c6bd3 on #161B22]▀[/][#0a51d3 on #161B22]▀[/][#0a51d3 on #161B22]▀[/][#0a46b7 on #161B22]▀[/]    [#0a46b7 on #161B22]▀[/][#0a46b7 on #161B22]▀[/][#0a51d3 on #161B22]▀[/][#0a51d3 on #161B22]▀[/][#4bb4fc on #161B22]▀[/][#0a51d3 on #161B22]▀[/]'
)
