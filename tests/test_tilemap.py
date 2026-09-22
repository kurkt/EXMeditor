# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for level.tile — the terrain's ground textures."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from formats.exm.tilemap import TILE_GRID_SIDE, read_tilemap  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402

TILE_FILE = corpus("level.tile")


def _tilemap():
    return read_tilemap(TILE_FILE) if os.path.isfile(TILE_FILE) else None


def test_every_index_names_a_tile_that_exists() -> None:
    """The reading rests on this: 65536 bytes, all of them in 0..19.

    Twenty names and a maximum byte of nineteen, with nothing outside
    the range — a coincidence would have to land the top value exactly
    one below the name count.
    """
    tilemap = _tilemap()
    if tilemap is None:
        return

    assert len(tilemap.indices) == TILE_GRID_SIDE * TILE_GRID_SIDE
    assert max(tilemap.indices) == len(tilemap.tiles) - 1
    assert all(0 <= i < len(tilemap.tiles) for i in tilemap.indices)


def test_the_names_are_paths_under_a_shared_root() -> None:
    tilemap = _tilemap()
    if tilemap is None:
        return
    assert tilemap.root.lower().endswith("tiles")
    assert all(name.lower().endswith(".dds") for name in tilemap.tiles)
    assert tilemap.path_for(0).startswith(tilemap.root)


def test_the_distribution_looks_like_a_map_not_like_noise() -> None:
    """Two textures carry most of the ground, as a wasteland would.

    Not one: at the correct 128-cell grid, GROUND_WASTE holds 37% and
    the next tile 31%. The 84%-of-everything figure came from the
    wrong reading, where four cells were being folded into one.
    """
    tilemap = _tilemap()
    if tilemap is None:
        return
    counts = sorted(
        (tilemap.indices.count(i) for i in range(len(tilemap.tiles))),
        reverse=True,
    )
    assert sum(counts[:2]) > len(tilemap.indices) // 2


def test_lookup_agrees_with_the_raw_index() -> None:
    tilemap = _tilemap()
    if tilemap is None:
        return
    for x, y in ((0, 0), (64, 64), (127, 127)):
        raw = tilemap.indices[y * tilemap.side + x]
        assert tilemap.tile_at(x, y) == tilemap.tiles[raw]


def test_the_grid_side_is_read_from_the_file_not_assumed() -> None:
    """It is the block's first field.

    128 on the sample map, which is ``LEVELSIZE`` 32 times four — the
    same relation the watermap follows. The value had been assumed, and
    the assumption happened to be right here; a map of another size
    would have been read as noise.
    """
    tilemap = _tilemap()
    if tilemap is None:
        return
    assert tilemap.side == 128
    assert len(tilemap.indices) == 128 * 128


def test_the_table_ends_exactly_where_the_grid_begins() -> None:
    """The check that the header was read correctly.

    Names are length-prefixed and NUL-terminated, so a table walked
    field by field either lands on the grid or lands nowhere. On the
    sample map it ends at 622 of 66158, and the grid needs the
    remaining 65536 to the byte.
    """
    from formats.exm.gam import read_container
    from formats.exm.tilemap import TILE_CELL_SIZE, _read_table

    if not os.path.isfile(TILE_FILE):
        return

    block = max((c.data for c in read_container(TILE_FILE)[1]), key=len)
    root, names, side, offset = _read_table(block)

    assert root.lower().endswith("tiles")
    assert len(names) == 20
    assert offset == 622
    assert offset + side * side * TILE_CELL_SIZE == len(block)


def test_every_name_is_a_texture_under_the_root() -> None:
    tilemap = _tilemap()
    if tilemap is None:
        return
    for name in tilemap.tiles:
        assert name.lower().endswith(".dds"), name
        assert tilemap.path_for(0).startswith(tilemap.root)


# --- read on a file built here, so these run without the corpus --------


def _synthetic(side: int = 4, high_words=None, padding: bytes = b"") -> str:
    """A minimal ``level.tile`` on disk. Returns its path."""
    import struct
    import tempfile

    from formats.exm.gam import Chunk, write_container
    from formats.exm.tilemap import TILE_CHUNK_ID

    def string(text: str) -> bytes:
        raw = text.encode("cp1251")
        return struct.pack("<I", len(raw)) + raw + b"\x00"

    block = struct.pack("<I", side) + string("data\tiles")
    names = ["region1\sand.dds", "region1\rocks.dds"]
    block += struct.pack("<I", len(names))
    for name in names:
        block += string(name)
    block += padding

    for index in range(side * side):
        tile = 0 if index % side < side // 2 else 1
        word = 0 if high_words is None else high_words[index]
        block += bytes([tile, 0, word & 0xFF, (word >> 8) & 0xFF])

    path = os.path.join(tempfile.mkdtemp(), "level.tile")
    with open(path, "wb") as handle:
        handle.write(write_container(0x0B, [Chunk(TILE_CHUNK_ID, block)]))
    return path


def test_the_cell_grid_is_read_four_bytes_at_a_time() -> None:
    from formats.exm.tilemap import read_tilemap as read

    tilemap = read(_synthetic())
    assert tilemap.side == 4
    assert tilemap.root == "data\tiles"
    assert list(tilemap.indices) == [0, 0, 1, 1] * 4


def test_the_undecoded_bytes_of_each_cell_are_kept() -> None:
    """Never discard what has not been understood.

    The high half of a cell has been described as a mask set; nothing
    reads it yet, and dropping it at parse time would make finding out
    impossible without re-reading the file.
    """
    from formats.exm.tilemap import read_tilemap as read

    words = [i % 5 for i in range(16)]
    tilemap = read(_synthetic(high_words=words))

    assert len(tilemap.raw_cells) == 4 * len(tilemap.indices)
    recovered = [
        tilemap.raw_cells[i * 4 + 2] | (tilemap.raw_cells[i * 4 + 3] << 8)
        for i in range(len(tilemap.indices))
    ]
    assert recovered == words
    # And the first byte of each cell is still what indices holds.
    assert [tilemap.raw_cells[i * 4] for i in range(16)] == list(tilemap.indices)


def test_a_table_that_does_not_meet_its_grid_warns_instead_of_crashing() -> None:
    """The warning names the file — and did it through an ``os`` that
    was never imported, so the one path that reported a layout problem
    raised NameError instead of reporting it."""
    from formats.exm.tilemap import read_tilemap as read

    tilemap = read(_synthetic(padding=b"\x00\x00\x00\x00"))
    assert len(tilemap.indices) == 16
