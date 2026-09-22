# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Independent terrain orientation/scale transforms.

Deliberately separate from both `formats/exm/terrain.py` (raw file
reading) and `blender_io/terrain_bridge.py` (Blender mesh building):
this module only ever takes a `HeightmapData` and returns a new,
transformed `HeightmapData`. No file I/O, no `bpy`.

None of these are applied by default anywhere in the SDK yet — the
correct orientation/scale for Ex Machina terrain is NOT confirmed
(the one real `displace.bin` seen so far was a uniform placeholder,
uninformative for this question). Each transform is a standalone,
independently toggleable function so a real varied-terrain file can
be tested against every combination without editing any loader code —
see `TerrainOrientation` / `apply_orientation` below for the combined
form once you're ready to sweep combinations.
"""

from __future__ import annotations

import array
import dataclasses

from core.terrain import HeightmapData


def transpose(heightmap: HeightmapData) -> HeightmapData:
    """Swap X and Y: cell (x, y) becomes cell (y, x).

    Returns a new `HeightmapData` with `width`/`height` swapped
    (identity only when the grid is square, which — per the confirmed
    displace.bin format — it always currently is, but this function
    doesn't assume that).
    """
    new_width, new_height = heightmap.height, heightmap.width
    values = array.array("f", [0.0]) * (new_width * new_height)
    for y in range(heightmap.height):
        for x in range(heightmap.width):
            # Original (x, y) -> transposed (y, x).
            values[x * new_width + y] = heightmap.values[y * heightmap.width + x]
    return HeightmapData(width=new_width, height=new_height, cell_size=heightmap.cell_size, values=values)


def flip_x(heightmap: HeightmapData) -> HeightmapData:
    """Mirror the grid along X: column ``x`` becomes column ``width-1-x``."""
    width, height = heightmap.width, heightmap.height
    values = array.array("f", [0.0]) * (width * height)
    for y in range(height):
        row_start = y * width
        for x in range(width):
            values[row_start + (width - 1 - x)] = heightmap.values[row_start + x]
    return HeightmapData(width=width, height=height, cell_size=heightmap.cell_size, values=values)


def flip_y(heightmap: HeightmapData) -> HeightmapData:
    """Mirror the grid along Y: row ``y`` becomes row ``height-1-y``."""
    width, height = heightmap.width, heightmap.height
    values = array.array("f", [0.0]) * (width * height)
    for y in range(height):
        src_row = heightmap.values[y * width:(y + 1) * width]
        dst_y = height - 1 - y
        values[dst_y * width:(dst_y + 1) * width] = src_row
    return HeightmapData(width=width, height=height, cell_size=heightmap.cell_size, values=values)


def scale_height(heightmap: HeightmapData, factor: float) -> HeightmapData:
    """Multiply every height sample by ``factor`` (no change to X/Y)."""
    values = array.array("f", (v * factor for v in heightmap.values))
    return HeightmapData(width=heightmap.width, height=heightmap.height, cell_size=heightmap.cell_size, values=values)


def offset_height(heightmap: HeightmapData, offset: float) -> HeightmapData:
    """Add ``offset`` to every height sample.

    Candidate use: the map editor's default "Ground Level" was observed
    to be 255 — if raw height samples turn out to be encoded relative
    to that baseline (e.g. "255 means sea level"), the real height
    would be ``raw - 255``, i.e. ``offset_height(heightmap, -255)``.
    NOT confirmed — this is exactly the kind of hypothesis this
    function exists to let you test in isolation, not a default this
    module applies on its own.
    """
    values = array.array("f", (v + offset for v in heightmap.values))
    return HeightmapData(width=heightmap.width, height=heightmap.height, cell_size=heightmap.cell_size, values=values)


def scale_xy(heightmap: HeightmapData, factor: float) -> HeightmapData:
    """Multiply the grid's ``cell_size`` by ``factor`` (X/Y spacing only,
    heights untouched). Adjusts world-space spacing between samples
    without resampling the grid itself."""
    return HeightmapData(
        width=heightmap.width,
        height=heightmap.height,
        cell_size=heightmap.cell_size * factor,
        values=heightmap.values,
    )


@dataclasses.dataclass(frozen=True)
class TerrainOrientation:
    """A named combination of the toggles above, for sweeping options.

    Every field defaults to a no-op — constructing
    ``TerrainOrientation()`` and applying it via ``apply_orientation``
    changes nothing, consistent with the SDK-wide rule of never
    defaulting to an unverified guess (see `utils.math`'s
    `DEFAULT_COORDINATE_SYSTEM` note for the same principle applied to
    axis conversion).
    """

    transpose: bool = False
    flip_x: bool = False
    flip_y: bool = False
    height_scale: float = 1.0
    height_offset: float = 0.0
    xy_scale: float = 1.0


def apply_orientation(heightmap: HeightmapData, orientation: TerrainOrientation) -> HeightmapData:
    """Apply every toggle in ``orientation`` to ``heightmap``, in a fixed order.

    Order (documented since these operations don't all commute):
    transpose, then flip_x, then flip_y, then xy_scale, then
    height_scale, then height_offset. Transpose is applied first
    because it changes which axis is which — doing a flip before a
    transpose flips the *other* axis than doing it after.
    """
    result = heightmap
    if orientation.transpose:
        result = transpose(result)
    if orientation.flip_x:
        result = flip_x(result)
    if orientation.flip_y:
        result = flip_y(result)
    if orientation.xy_scale != 1.0:
        result = scale_xy(result, orientation.xy_scale)
    if orientation.height_scale != 1.0:
        result = scale_height(result, orientation.height_scale)
    if orientation.height_offset != 0.0:
        result = offset_height(result, orientation.height_offset)
    return result
