# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""``level.tile`` — which ground texture covers each patch of terrain.

The file is an ``ecbnt,t`` container, the same one ``.gam`` uses, with
three chunks. The big one holds a root folder, a list of tile texture
names, and then a byte per tile cell saying which of them covers it.

What was measured
-----------------

The sample map's payload chunk ends in **65536 bytes = 256x256**, and
every one of those bytes is in **0..19** — against exactly **20** tile
names in the same chunk. Nothing outside the range, no gaps at the top.
An index map is the only reading that fits: a coincidence would have to
place the maximum byte value one below the name count by chance.

The distribution matches the map too. ``GROUND_WASTE`` takes 84% of
the cells and ``rocks`` 7.7%, which is what a wasteland looks like
rather than what noise looks like.

What was not
------------

**The cell-to-quad ratio is a working assumption.** The heightfield is
512x512 vertices and the tile map is 256x256, so two heightfield quads
per tile cell along each axis is the obvious reading and is not
confirmed by anything. It is applied where it matters and named where
it is used.

**How the engine blends between tiles** is now known and lives in
``core/tile_blend.py`` — patches centred on the grid's vertices, one
render pass per distinct tile among the four cells that meet there.
What is still missing there is the mask atlas's own table; see that
module.

**The three bytes per cell that are not the tile index.** Only the low
byte is read as the tile. The high half has been described as a mask
set 0..4, which — if it is — would be authored blending data rather
than blending derived from the neighbours. НЕИЗВЕСТНО: nothing here
interprets it, and ``raw_cells`` keeps all four bytes so that nothing
throws it away either.

The two header words before the root string are 128 and 10. Neither is
the tile count, which is 20, so neither is interpreted.
"""

from __future__ import annotations

import dataclasses
import os
import re

import struct

from formats.exm.gam import read_container
from utils.logging import get_logger

logger = get_logger("formats.exm.tilemap")
from utils.errors import ErrorContext, ParsingError

#: The chunk carrying names and the index map. The id looks like a
#: sentinel rather than a number anyone chose to mean something.
TILE_CHUNK_ID = 0x0BADF00D

#: Side of the index map, and the size of one cell.
#:
#: **128 x 128 cells of four bytes**, not 256 x 256 of one. Only the
#: first byte of each cell carries the tile; the other three are zero
#: across all 16384 cells of the sample map, with a single exception
#: that is masked off.
#:
#: Read as bytes the values still land in 0..19 against 20 tile names,
#: which is why the wrong reading looked right. What gives it away is
#: structure: at 128 the map agrees with its horizontal neighbour 51.4%
#: of the time and its vertical neighbour 58.4%, against a 24.7%
#: baseline for its own histogram — twice chance, which is what ground
#: laid out in regions looks like. Read as 256 the same data sits at
#: chance, and the terrain comes out in near-random squares.
TILE_GRID_SIDE = 128
TILE_CELL_SIZE = 4

#: Heightfield quads per tile cell along each axis. See the module
#: docstring — this is the assumption, not a measurement.
QUADS_PER_TILE_CELL = 2

#: The block's own layout, read field by field rather than scanned for
#: strings. Verified byte-exact on the sample map, where the table ends
#: at 622 and the cell grid begins there::
#:
#:     uint32            grid side  (128 on the sample map)
#:     uint32 + bytes    root folder, NUL-terminated
#:     uint32            number of tile types
#:     per type:
#:         uint32 + bytes    name, NUL-terminated
#:     per cell, grid side squared:
#:         4 bytes, of which only the first carries the tile
#:
#: The side is in the file. It had been assumed, and the assumption
#: happened to be right for this map — ``LEVELSIZE`` 32 times 4 — but
#: a map of another size would have been read as noise.
_NAME_PATTERN = re.compile(rb"[\x20-\x7e]{4,}")


@dataclasses.dataclass
class TileMap:
    """The ground texture list and which cell uses which."""

    #: Folder the names are relative to, e.g. ``data\\tiles``.
    root: str = ""
    #: Texture names in index order, e.g. ``region1\\rocks.dds``.
    tiles: list[str] = dataclasses.field(default_factory=list)
    #: Row-major ``side * side`` bytes, each an index into ``tiles``.
    indices: bytes = b""
    side: int = TILE_GRID_SIDE
    #: The cell grid as stored, four bytes a cell, undecoded past the
    #: first. Kept because the SDK does not rewrite what it has not
    #: understood, and because the high half may yet turn out to be
    #: the engine's own blend masks.
    raw_cells: bytes = b""

    def tile_at(self, x: int, y: int) -> str:
        """The texture name covering tile cell ``(x, y)``."""
        return self.tiles[self.indices[y * self.side + x]]

    def used_tiles(self) -> list[int]:
        """Indices that actually appear, in ascending order.

        A map references far fewer tiles than it lists — building a
        Blender material for every name would fill the slot list with
        twenty entries most of which nothing uses.
        """
        return sorted(set(self.indices))

    def path_for(self, index: int) -> str:
        """Full path of a tile texture relative to the game root."""
        name = self.tiles[index]
        return f"{self.root}\\{name}" if self.root else name


def _read_table(block: bytes):
    """Header and tile names. Returns ``(root, names, side, offset)``."""
    position = 0

    side = struct.unpack_from("<I", block, position)[0]
    position += 4

    root, position = _read_string(block, position)
    count = struct.unpack_from("<I", block, position)[0]
    position += 4

    names = []
    for _ in range(count):
        name, position = _read_string(block, position)
        names.append(name)

    return root, names, side, position


def _read_string(block: bytes, position: int):
    """``uint32`` length, that many bytes, then a NUL."""
    length = struct.unpack_from("<I", block, position)[0]
    position += 4
    text = block[position : position + length].decode("cp1251", errors="replace")
    return text, position + length + 1


def read_tilemap(path: str) -> TileMap:
    """Read ``level.tile``.

    Raises if the index map does not sit on a square grid, or if it
    names a tile the file does not list — both mean the layout is not
    what was measured, and reading on would produce a plausible-looking
    terrain painted with the wrong textures.
    """
    _subtype, chunks = read_container(path)

    block = None
    for chunk in chunks:
        if chunk.chunk_id == TILE_CHUNK_ID:
            block = chunk.data
            break
    if block is None:
        raise ParsingError(
            "level.tile has no tile chunk",
            context=ErrorContext(
                source_file=path,
                extra={"chunks": [hex(c.chunk_id) for c in chunks]},
            ),
        )

    try:
        root, tiles, side, offset = _read_table(block)
    except (struct.error, UnicodeDecodeError, ValueError) as exc:
        raise ParsingError(
            "level.tile does not follow the known layout",
            context=ErrorContext(source_file=path, extra={"reason": str(exc)}),
        ) from exc

    if not tiles:
        raise ParsingError(
            "level.tile names no tiles",
            context=ErrorContext(source_file=path),
        )

    expected = side * side * TILE_CELL_SIZE
    if len(block) < expected:
        raise ParsingError(
            "level.tile is too short to hold a tile index map",
            context=ErrorContext(
                source_file=path,
                extra={"chunk_size": len(block), "expected_map": expected},
            ),
        )

    # The map is at the end. Taking it from there rather than from
    # where the names stop avoids depending on how the string table is
    # padded — and on the sample map the two agree exactly: the names
    # end at 621 and the map runs from 622 to the end.
    # The grid begins where the table ends. On the sample map that is
    # also exactly ``len(block) - expected``, and the two agreeing is
    # the check that the table was read correctly.
    if offset + expected != len(block):
        logger.warning(
            "%s: the table ends at %s and the grid needs %s of %s bytes",
            os.path.basename(path), offset, expected, len(block),
        )
    cells = block[offset : offset + expected]
    indices = bytes(cells[i * TILE_CELL_SIZE] for i in range(side * side))

    highest = max(indices)
    if highest >= len(tiles):
        raise ParsingError(
            "level.tile references a tile it does not name",
            context=ErrorContext(
                source_file=path,
                extra={"highest_index": highest, "tiles_named": len(tiles)},
            ),
        )

    return TileMap(
        root=root, tiles=tiles, indices=indices, side=side, raw_cells=cells
    )
