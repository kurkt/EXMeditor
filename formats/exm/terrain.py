# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""``displace.bin`` codec.

Format, as confirmed by direct experiment (see the architecture doc's
changelog): a square grid of little-endian float32 height samples,
whose side length scales with the map's declared size — NOT a fixed
512x512. Resolution is derived from the file's own byte size, not
assumed.
"""

from __future__ import annotations

from math import isqrt

from core.coordinates import TERRAIN_CELL_SIZE
from core.terrain import HeightmapData
from utils.binary import BinaryReader, BinaryWriter
from utils.errors import ErrorContext, ParsingError, ValidationError

# World-space distance between adjacent height samples. NOT YET
# CONFIRMED against real map data — 1.0 is a neutral placeholder (one
# unit per sample), not a guess at the real value, consistent with the
# SDK-wide "no unverified default" rule (see utils.math's
# DEFAULT_COORDINATE_SYSTEM note).
# World-space size of one grid cell, in game units. **Confirmed** by
# three independent measurements agreeing on 8 (see
# core/coordinates.py's TERRAIN_CELL_SIZE, which is the single source
# of truth this re-exports for convenience): world.xml object positions
# span 0..3878; .ssl's MAXSAFEX is 4056; a 512x512 grid at cell size 8
# spans 0..4088. The previous placeholder of 1.0 made the terrain cover
# only 1/8th of the map's actual footprint, putting it on a visibly
# different plane from the objects.
DEFAULT_CELL_SIZE = TERRAIN_CELL_SIZE

_FLOAT32_SIZE = 4


def _resolve_grid_side(file_size: int, *, source_file: str | None = None) -> int:
    """Derive the square grid side length from a file's byte size.

    Algorithm (confirmed against real displace.bin files of several
    different map sizes — see the architecture doc): the file is a
    flat array of float32 samples forming a square grid, so
    ``side = sqrt(file_size / 4)``. No fixed or listed size is assumed;
    any file whose sample count isn't a perfect square is rejected as
    not matching this format, rather than silently guessing a shape.
    """
    if file_size % _FLOAT32_SIZE != 0:
        raise ParsingError(
            "displace.bin size is not a multiple of 4 bytes (float32 samples)",
            context=ErrorContext(source_file=source_file, extra={"file_size": file_size}),
        )
    sample_count = file_size // _FLOAT32_SIZE
    side = isqrt(sample_count)
    if side * side != sample_count:
        raise ParsingError(
            "displace.bin sample count is not a perfect square "
            "(expected a square grid — this file may not be a displace.bin, "
            "or the format assumption is wrong)",
            context=ErrorContext(
                source_file=source_file,
                extra={"sample_count": sample_count, "nearest_side_guess": side},
            ),
        )
    return side


def read_displace(filepath: str, *, cell_size: float = DEFAULT_CELL_SIZE) -> HeightmapData:
    """Read a ``displace.bin`` file into a ``HeightmapData``.

    The grid's side length is derived from the file's byte size (see
    ``_resolve_grid_side``) — this function supports any square map
    size, not a fixed or enumerated list of sizes.

    Raises
    ------
    ParsingError
        If the file's size isn't a multiple of 4 bytes, or the
        resulting sample count isn't a perfect square, or the file
        can't be read at all (see ``BinaryReader.from_file``).
    """
    reader = BinaryReader.from_file(filepath)
    side = _resolve_grid_side(reader.remaining(), source_file=filepath)
    values = reader.read_float32_array(side * side)
    return HeightmapData(width=side, height=side, cell_size=cell_size, values=values)


def write_displace(filepath: str, heightmap: HeightmapData) -> None:
    """Write a ``HeightmapData`` back out as a ``displace.bin`` file.

    Raises
    ------
    ValidationError
        If ``heightmap`` isn't square (every confirmed displace.bin is
        square — see the architecture doc), or contains a non-finite
        value (see ``HeightmapData.validate()``). Raised *before*
        anything is written, so a bad export never touches disk.
    """
    if heightmap.width != heightmap.height:
        raise ValidationError(
            "displace.bin requires a square grid (width must equal height)",
            context=ErrorContext(extra={"width": heightmap.width, "height": heightmap.height}),
        )
    heightmap.validate()  # NaN/Infinity check only — no fixed size expected
    writer = BinaryWriter()
    writer.write_float32_array(heightmap.values)
    writer.write_to_file(filepath)
