# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""What a new map is made of, before anything is written.

MEASURED, off the six size templates the game ships in ``data/maps``
(``8x8`` through ``256x256``) and the two shipped levels::

    folder     LEVELSIZE   G   displace   colormap   passmap   water
    8x8            2       8      4096       4096       256     128
    16x16          4      16     16384      16384      1024     512
    32x32          8      32     65536      65536      4096    2048
    64x64         16      64    262144     262144     16384    8192
    r1m1          32     128   1048576    1048576     65536   32768
    r1m2 /256x256 64     256   4194304    4194304    262144  131072

Two things fall out and both hold on every row:

* **G = LEVELSIZE x 4.** The folder is named for G, the tile grid, and
  the manifest stores LEVELSIZE. They are not the same number, and
  confusing them makes a map a quarter of the size asked for.
* the four rasters are ``64G^2``, ``64G^2``, ``4G^2`` and ``2G^2``
  bytes — exactly the formulas the format notes already carried,
  confirmed here across six sizes rather than one.

The terrain is ``4G x 4G`` vertices at 8 units apart, so the world
spans ``32G`` units. The playable area the shipped templates declare is
inset 40 units on every side, which on ``r1m1`` gives the 40..4056 that
was measured from the other direction long ago.
"""

from __future__ import annotations

#: Tile-grid sizes a map can have, smallest first. The five with a
#: shipped template, plus 128 — which has no template folder and is the
#: size of ``r1m1``, so it is as real as the rest.
GRID_SIZES = (8, 16, 32, 64, 128, 256)

#: Units between terrain vertices, and vertices per tile cell.
VERTEX_SPACING = 8.0
VERTICES_PER_TILE = 4

#: How far the playable area sits inside the terrain, per side.
SAFE_INSET = 40.0

#: The height a fresh terrain is flat at. Not zero: the shipped
#: templates write 255.0 into every sample of ``displace.bin``, which
#: leaves room to carve downwards as well as up.
FLAT_HEIGHT = 255.0

#: A fresh colour map is neutral grey, opaque — 127 is the neutral of
#: the modulate2x the terrain shader applies, so a new map's colour
#: layer changes nothing until it is painted.
NEUTRAL_COLOUR = (127, 127, 127, 255)

#: The one ground texture a new map starts with, and the folder its
#: names are relative to. Both taken from the shipped templates.
DEFAULT_TILE_ROOT = "data\\tiles"
DEFAULT_TILE = "region1\\GROUND_WASTE.dds"


def level_size(grid: int) -> int:
    """The ``LEVELSIZE`` a manifest stores for a tile grid of ``grid``."""
    return grid // VERTICES_PER_TILE


def grid_for_level_size(size: int) -> int:
    """The tile grid a ``LEVELSIZE`` means."""
    return size * VERTICES_PER_TILE


def terrain_vertices(grid: int) -> int:
    """Terrain samples per side: four per tile cell."""
    return grid * VERTICES_PER_TILE


def world_span(grid: int) -> float:
    """How many units across the map is."""
    return terrain_vertices(grid) * VERTEX_SPACING


def safe_bounds(grid: int) -> tuple[float, float, float, float]:
    """``(min_x, min_y, max_x, max_y)`` of the playable area."""
    far = world_span(grid) - SAFE_INSET
    return (SAFE_INSET, SAFE_INSET, far, far)


def camera_start(grid: int) -> tuple[float, float, float]:
    """Where the editor's camera opens: over the middle, looking down.

    The height is the templates' own: 555 above a terrain flat at 255,
    so 300 units up. Scaled with the map rather than fixed, since the
    same 300 units on a 256-grid map shows a corner of one tile.
    """
    middle = world_span(grid) / 2.0
    return (middle, FLAT_HEIGHT + 300.0, middle)


def raster_sizes(grid: int) -> dict:
    """Bytes each raster takes, by filename."""
    return {
        "displace.bin": 64 * grid * grid,
        "colormap.raw": 64 * grid * grid,
        "passmap.raw": 4 * grid * grid,
        "water.raw": 2 * grid * grid,
    }


#: The files every shipped map folder has — measured across ten of
#: them, where these eighteen are the only names present in all.
#:
#: ``NormalMap.bin`` and ``ShoreLine.bin`` are not in all ten (9 and 7
#: of them), and a map was first created without either on the strength
#: of that. **ОПРОВЕРГНУТО.** The original editor opened it, logged
#: ``Couldn't load normal map from file ...\NormalMap.bin`` and died on
#: an assert — exception 0x80000003, BREAKPOINT, which is an internal
#: assertion and not a memory fault.
#:
#: The mistake is worth naming: presence in nine folders out of ten is
#: a STATISTIC ABOUT SHIPPED DATA, and requirement is a property of the
#: LOADER. One says nothing about the other, and reading the first as
#: the second is the same error as reading a value range as a
#: structure. Both files are written now — see
#: ``formats/exm/new_map.py`` — and this list stays as what it always
#: was: the names common to every shipped map, no more.
REQUIRED_FILES = (
    "camera_paths.xml",
    "cinemaTriggers.xml",
    "colormap.raw",
    "displace.bin",
    "dynamicscene.xml",
    "external_paths.xml",
    "grass.xml",
    "level.tile",
    "levelroads.xml",
    "object_names.xml",
    "passmap.raw",
    "QuestStates.xml",
    "roads.xml",
    "static_obstacles.xml",
    "strings.xml",
    "triggers.xml",
    "water.raw",
    "world.xml",
)


#: Files the editor needs that are NOT common to every shipped map.
#: Kept apart from :data:`REQUIRED_FILES` because they were arrived at
#: differently: that list is a census, this one is what a crash said.
EDITOR_REQUIRED_FILES = ("NormalMap.bin", "ShoreLine.bin")

#: Samples per side of the normal map, MEASURED across five sizes::
#:
#:     G=8    N=4356     side  66 = 8*8+2
#:     G=16   N=16900    side 130 = 8*16+2
#:     G=32   N=66564    side 258 = 8*32+2
#:     G=64   N=264196   side 514 = 8*64+2
#:     G=256  N=4202500  side 2050 = 8*256+2
#:
#: Exact on every one, so the payload is ``(8G+2)^2`` bytes behind a
#: ``uint32`` count. That is twice the terrain's own 4G resolution plus
#: a one-sample border on each side.
def normal_map_side(grid: int) -> int:
    """Samples per side of ``NormalMap.bin`` for a tile grid of ``grid``."""
    return 8 * grid + 2


def normal_map_samples(grid: int) -> int:
    """Total samples, which is the count the payload opens with."""
    side = normal_map_side(grid)
    return side * side


def describe(grid: int) -> dict:
    """Every number a new map of this size needs, in one place."""
    if grid not in GRID_SIZES:
        raise ValueError(
            f"{grid} is not a map size; the shipped ones are {GRID_SIZES}"
        )
    min_x, min_y, max_x, max_y = safe_bounds(grid)
    return {
        "grid": grid,
        "level_size": level_size(grid),
        "vertices": terrain_vertices(grid),
        "span": world_span(grid),
        "safe": (min_x, min_y, max_x, max_y),
        "camera": camera_start(grid),
        "rasters": raster_sizes(grid),
    }
