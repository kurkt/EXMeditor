# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""How the engine blends one ground tile into the next.

Twenty hard-edged textures on a 128x128 grid would draw the terrain as
a chessboard. The game's does not look like that, and this is why.

The algorithm
-------------

MEASURED, by reading the engine's terrain renderer:

- The blend patch is not a tile cell. It is centred on a **grid vertex**
  of the tile map, and its four corners are the four cells that meet
  there. The blend grid is therefore the dual of the tile grid — offset
  by half a cell — which is what puts a gradient across a cell boundary
  instead of on it.
- Those four cells are grouped **by equal tile index**. Each group
  becomes one render pass carrying a 4-bit corner mask, bits
  ``1 / 2 / 4 / 8`` for top-left / top-right / bottom-left /
  bottom-right.
- Passes are drawn in ascending tile index, and the **first covers the
  whole patch with mask 15** — it is the opaque base the rest blend
  over.
- Every pass but the base samples its alpha from one shared atlas,
  ``data/tiles/mask.dds``: 512x128 DXT5, an 8x2 grid of 64x64 cells.
  Which cell a mask uses, and at what rotation, is
  :data:`MASK_ATLAS_CELLS` — read off the shipped file, see there for
  how.
- Blending is the normal case, not an edge: on ``r1m2`` 42046 of 66049
  patches have more than one tile at their corners, and 948 have four.

What the shader uses
--------------------

The atlas, through :func:`atlas_uv`: each pass gets a texture
coordinate into ``mask.dds`` naming its cell and rotation, and the
alpha it reads there is the game's own blend shape.

Verified end to end on ``r1m2``: 42046 patches blend, and at all
168184 of their corners, compositing the passes by the alpha the atlas
actually returns shows that corner's own tile. No mismatches.

:func:`pass_alphas` remains as the fallback for an install without the
atlas on disk. It interpolates the corner bits bilinearly — a DECLARED
APPROXIMATION, exact at the corners and a plain slope in between.

Sign convention
---------------

"Top" here means the lower row index, matching the row-major order the
rest of the SDK reads grids in. Whether the engine's bit 1 is the same
corner is UNCONFIRMED, and it matters only once the atlas is sampled
for real: it would change which rotation a mask picks, never which tile
lands where. The analytic ramp is derived from the same convention it
is consumed by, so it cannot be affected either way.
"""

from __future__ import annotations

import dataclasses

#: Corner bits, as the engine numbers them.
CORNER_TOP_LEFT = 1
CORNER_TOP_RIGHT = 2
CORNER_BOTTOM_LEFT = 4
CORNER_BOTTOM_RIGHT = 8

#: In the order :func:`bilinear_weights` returns weights in.
CORNER_BITS = (
    CORNER_TOP_LEFT,
    CORNER_TOP_RIGHT,
    CORNER_BOTTOM_LEFT,
    CORNER_BOTTOM_RIGHT,
)

#: All four corners — the mask the base pass is drawn with.
FULL_COVERAGE = 15

#: The alpha atlas every tile type shares, relative to the game root.
MASK_ATLAS_FILE = "data/tiles/mask.dds"

#: Its shape: 512x128, an 8x2 grid of 64x64 cells. Sixteen cells for
#: sixteen possible corner masks, which is the arithmetic that makes
#: the reading of the atlas certain even without the table below.
MASK_ATLAS_SIZE = (512, 128)
MASK_ATLAS_CELL_SIZE = 64
MASK_ATLAS_GRID = (8, 2)

#: mask -> (atlas cell, quarter turns clockwise). MEASURED, off the
#: shipped ``mask.dds`` rather than copied from anywhere.
#:
#: How it was read, so it can be checked rather than trusted:
#:
#: 1. The atlas decodes as 512x128 DXT5, 10 mip levels. Cut into 64x64
#:    cells it holds 16, of which **13 carry anything** — cells 13, 14
#:    and 15 are alpha 0 throughout.
#: 2. Sampling each cell's four corners (a 6x6 patch inset 2px, so
#:    dither cannot decide it) gives a corner mask per cell::
#:
#:        cell  0                       15  (opaque everywhere)
#:        cells 1..4    5, 7, 1, 9
#:        cells 5..8    5, 7, 1, 9
#:        cells 9..12   5, 7, 1, 9
#:
#:    Three groups of the same four shapes, and one full cover. The
#:    groups differ only in how soft the ramp is; which one a map uses
#:    is what ``tileinfo.xml``'s ``alphaset`` and its ``mask1..mask4``
#:    entries appear to select, and that is NOT yet established — see
#:    :data:`MASK_ATLAS_VARIANTS`.
#: 3. Those five shapes, closed under 90-degree rotation, generate
#:    every mask exactly once::
#:
#:        1  -> 2, 8, 4          (one corner,      4 masks)
#:        5  -> 3, 10, 12        (one edge,        4 masks)
#:        7  -> 11, 14, 13       (three corners,   4 masks)
#:        9  -> 6                (two diagonal,    2 masks)
#:        15                     (everything,      1 mask)
#:
#:    Fifteen masks, plus mask 0 which draws nothing: sixteen. No mask
#:    is missing and none is claimed twice. A guessed table would have
#:    no reason to come out exact, which is what makes this a
#:    measurement rather than a fit.
#:
#: Rotation is clockwise, taking TL->TR->BR->BL, in the corner order
#: this module uses throughout.
MASK_ATLAS_CELLS: dict[int, tuple[int, int]] = {
    1: (3, 0),
    2: (3, 1),
    3: (1, 1),
    4: (3, 3),
    5: (1, 0),
    6: (4, 1),
    7: (2, 0),
    8: (3, 2),
    9: (4, 0),
    10: (1, 2),
    11: (2, 1),
    12: (1, 3),
    13: (2, 3),
    14: (2, 2),
    15: (0, 0),
}

#: The three groups of four shapes, by the cell each group starts at.
#: They are the same four masks at different ramp softness.
#:
#: Which one applies is still НЕИЗВЕСТНО, but the likeliest answer has
#: been ОПРОВЕРГНУТО: the high word of a ``level.tile`` cell was
#: described as a mask set 0..4, and on ``r1m2`` it reads **0 on 65532
#: of 65536 cells and 1 on the other four**. That is not per-cell
#: authored blending data. Group 1 is used for everything, and the
#: other two are unreached until something says otherwise.
MASK_ATLAS_VARIANTS = (1, 5, 9)

#: Mask 0 covers no corner, so its pass is not drawn at all.
MASK_DRAWS_NOTHING = 0


def atlas_is_available() -> bool:
    """Whether the measured mask atlas table is complete.

    Fifteen entries, not sixteen: mask 0 is the pass that covers no
    corner and is never drawn, so it has no cell.
    """
    return len(MASK_ATLAS_CELLS) == 15


def rotate_mask(mask: int, turns: int = 1) -> int:
    """Turn a corner mask clockwise, TL->TR->BR->BL.

    The atlas carries five shapes and reaches the other ten masks by
    rotating them, so this is how the table above was derived and how
    it can be checked.
    """
    for _ in range(turns % 4):
        turned = 0
        for source, target in ((CORNER_TOP_LEFT, CORNER_TOP_RIGHT),
                               (CORNER_TOP_RIGHT, CORNER_BOTTOM_RIGHT),
                               (CORNER_BOTTOM_RIGHT, CORNER_BOTTOM_LEFT),
                               (CORNER_BOTTOM_LEFT, CORNER_TOP_LEFT)):
            if mask & source:
                turned |= target
        mask = turned
    return mask


def atlas_cell_uv(cell: int) -> tuple[float, float, float, float]:
    """Where a cell sits in the atlas, as ``(u0, v0, u1, v1)``."""
    columns, rows = MASK_ATLAS_GRID
    return (
        (cell % columns) / columns,
        (cell // columns) / rows,
        (cell % columns + 1) / columns,
        (cell // columns + 1) / rows,
    )


def rotate_uv(u: float, v: float, turns: int) -> tuple[float, float]:
    """Where to read a cell that has been turned ``turns`` times clockwise.

    The table says a mask is its cell's shape rotated. To *sample* that
    rotated shape at patch position ``(u, v)`` the lookup goes the other
    way, and this is that inverse.

    Checked against the corners: one clockwise turn takes the shape's
    top-left to the top-right, so the patch's top-right must read the
    cell's top-left — ``rotate_uv(1, 0, 1) == (0, 0)``.
    """
    turns %= 4
    if turns == 0:
        return (u, v)
    if turns == 1:
        return (v, 1.0 - u)
    if turns == 2:
        return (1.0 - u, 1.0 - v)
    return (1.0 - v, u)


def atlas_uv(mask: int, u: float, v: float) -> tuple[float, float]:
    """Texture coordinate in ``mask.dds`` for a point in a blend patch.

    ``(u, v)`` is the position inside the patch, u left to right and v
    top to bottom. The result is a Blender texture coordinate, so its V
    is measured from the BOTTOM of the image while the atlas's rows are
    counted from the top — the flip at the end is that, and getting it
    wrong mirrors every blend vertically.

    Inset by half a texel. The cells sit edge to edge in one image and
    bilinear filtering at a cell boundary otherwise reaches into the
    next cell, which puts a thin wrong-shaped fringe around every
    blended patch.

    Mask 0 covers nothing and is never drawn; it returns the corner of
    the atlas rather than raising, since a caller that asks has already
    decided to draw something.
    """
    entry = MASK_ATLAS_CELLS.get(mask)
    if entry is None:
        return (0.0, 0.0)
    cell, turns = entry

    source_u, source_v = rotate_uv(u, v, turns)
    source_u = min(max(source_u, 0.0), 1.0)
    source_v = min(max(source_v, 0.0), 1.0)

    columns, rows = MASK_ATLAS_GRID
    column, row = cell % columns, cell // columns
    width, height = MASK_ATLAS_SIZE
    inset_u, inset_v = 0.5 / width, 0.5 / height

    across = (column + source_u) / columns
    down = (row + source_v) / rows
    across = min(max(across, column / columns + inset_u),
                 (column + 1) / columns - inset_u)
    down = min(max(down, row / rows + inset_v),
               (row + 1) / rows - inset_v)

    return (across, 1.0 - down)


@dataclasses.dataclass(frozen=True)
class BlendPass:
    """One render pass over a blend patch: a tile and the corners it covers."""

    tile: int
    mask: int

    def covers(self, corner_bit: int) -> bool:
        return bool(self.mask & corner_bit)


def corner_cells(
    blend_x: int, blend_y: int, side: int
) -> tuple[tuple[int, int], ...]:
    """The four tile cells meeting at blend vertex ``(blend_x, blend_y)``.

    Returned top-left, top-right, bottom-left, bottom-right — the order
    :data:`CORNER_BITS` and :func:`bilinear_weights` both use.

    Off the edge of the map there is no cell, and the edge cell is
    repeated. That keeps the border a solid tile rather than blending
    it towards whatever a wrapped or zeroed index would name — an
    ASSUMPTION about the engine, visible only in the outermost half
    cell of the map.
    """
    left = min(max(blend_x - 1, 0), side - 1)
    right = min(max(blend_x, 0), side - 1)
    top = min(max(blend_y - 1, 0), side - 1)
    bottom = min(max(blend_y, 0), side - 1)
    return ((left, top), (right, top), (left, bottom), (right, bottom))


def corner_tiles(
    indices, side: int, blend_x: int, blend_y: int
) -> tuple[int, int, int, int]:
    """The tile index at each of the four corners of a blend patch."""
    cells = corner_cells(blend_x, blend_y, side)
    return tuple(indices[y * side + x] for x, y in cells)  # type: ignore[return-value]


def passes_for(corners) -> list[BlendPass]:
    """Group four corner tiles into render passes, in draw order.

    One pass per distinct tile, ascending by tile index, and the first
    one widened to :data:`FULL_COVERAGE` because it is the opaque base.

    A patch whose four corners agree yields a single pass — the common
    case by far, and the one that costs nothing.
    """
    masks: dict[int, int] = {}
    for bit, tile in zip(CORNER_BITS, corners):
        masks[tile] = masks.get(tile, 0) | bit

    ordered = [BlendPass(tile=tile, mask=masks[tile]) for tile in sorted(masks)]
    ordered[0] = BlendPass(tile=ordered[0].tile, mask=FULL_COVERAGE)
    return ordered


def bilinear_weights(u: float, v: float) -> tuple[float, float, float, float]:
    """Corner weights at ``(u, v)`` inside a patch, in :data:`CORNER_BITS` order.

    ``u`` runs left to right, ``v`` top to bottom, both 0..1. The four
    weights sum to 1 everywhere, which is what makes the base pass's
    alpha come out at exactly 1 for free.
    """
    return (
        (1.0 - u) * (1.0 - v),
        u * (1.0 - v),
        (1.0 - u) * v,
        u * v,
    )


def pass_alphas(passes, u: float, v: float) -> list[float]:
    """Alpha of each pass at ``(u, v)``, in draw order.

    The declared approximation described in the module docstring: a
    pass's alpha is how much of the point belongs to the corners it
    covers. The base pass carries mask 15 and therefore alpha 1, which
    is right — it is opaque.
    """
    weights = bilinear_weights(u, v)
    alphas = []
    for render_pass in passes:
        total = 0.0
        for bit, weight in zip(CORNER_BITS, weights):
            if render_pass.mask & bit:
                total += weight
        alphas.append(total)
    return alphas


def blend_cell_of_quad(quad: int, quads_per_cell: int, side: int) -> int:
    """Which blend patch a terrain quad falls in, along one axis.

    The blend grid is offset half a tile cell from the tile grid, so
    the patch is found from the quad's own centre rather than from its
    corner — otherwise a quad straddles two patches and its four
    corners disagree about which one they are in.
    """
    if quads_per_cell <= 0:
        return 0
    centre = quad + 0.5
    cell = int((centre + quads_per_cell * 0.5) // quads_per_cell)
    return min(max(cell, 0), side)


def offset_in_blend_cell(sample: float, cell: int, quads_per_cell: int) -> float:
    """Where a terrain vertex sits inside its blend patch, 0..1 along one axis.

    ``sample`` is the vertex's grid coordinate. Clamped, because the
    patches at the map's edge are half outside it.
    """
    if quads_per_cell <= 0:
        return 0.0
    position = (sample + quads_per_cell * 0.5) / quads_per_cell - cell
    return min(max(position, 0.0), 1.0)


def plan_quad(
    quad_x: int,
    quad_y: int,
    indices,
    side: int,
    quads_per_cell: int,
) -> tuple[tuple[int, int], list[BlendPass]]:
    """The blend patch a quad belongs to, and the passes drawn over it."""
    blend_x = blend_cell_of_quad(quad_x, quads_per_cell, side)
    blend_y = blend_cell_of_quad(quad_y, quads_per_cell, side)
    corners = corner_tiles(indices, side, blend_x, blend_y)
    return (blend_x, blend_y), passes_for(corners)


def combination_of(passes) -> tuple[int, ...]:
    """The key identifying the material a pass list needs.

    Two patches drawn with the same tiles in the same order can share
    one material, and on a real map most patches are one tile.
    """
    return tuple(render_pass.tile for render_pass in passes)


def blend_statistics(indices, side: int) -> dict[str, int]:
    """How much of a map actually blends.

    Reported rather than assumed: if nearly every patch comes out with
    a single pass, the blending is doing nothing and the tile map — or
    this reading of it — is the thing to question, not the shader.
    """
    counts = {"patches": 0, "single": 0, "two": 0, "three": 0, "four": 0}
    names = {1: "single", 2: "two", 3: "three", 4: "four"}
    for blend_y in range(side + 1):
        for blend_x in range(side + 1):
            corners = corner_tiles(indices, side, blend_x, blend_y)
            counts["patches"] += 1
            counts[names[len(set(corners))]] += 1
    return counts
