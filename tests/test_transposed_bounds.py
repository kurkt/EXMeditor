# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""A model whose bounding box was written on the wrong axes.

Found on ``road_country.gam`` in a real install: the vertices span
Y 0.002..0.190 and Z -7.223..7.223, and the stored box says
min(-9.885, -7.223, 0.000) max(9.885, 7.223, 0.190) — the same six
numbers with Y and Z exchanged. The mesh name is ``mesh0.007``, so the
file came back out of Blender.

The reader used to refuse it, and a refused road model is a road that
does not appear at all. The vertices were never in doubt: every number
of the box is present and correct, in the wrong slot. That is a broken
writer, not a vertex layout this reader misunderstands, and the two
have to be told apart rather than lumped together.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from formats.exm.gam import (  # noqa: E402
    MESH_CHUNK_ID,
    Chunk,
    read_model,
    write_container,
)
from utils.errors import ParsingError  # noqa: E402

#: A flat quad, deliberately asymmetric in every axis so a swap shows.
VERTICES = [
    (-9.0, 0.05, -7.0),
    (9.0, 0.05, -7.0),
    (9.0, 0.20, 7.0),
    (-9.0, 0.20, 7.0),
]
TRIANGLES = [(0, 1, 2), (0, 2, 3)]


def _mesh_chunk(bounds) -> bytes:
    """One vertex-type-7 mesh, followed by the bounding box given."""
    block = bytearray()
    header = bytearray(72)
    header[0:5] = b"mesh0"
    struct.pack_into("<I", header, 0x28, 4)          # streamMask: static
    struct.pack_into("<I", header, 0x2C, 0)          # meshIndex
    struct.pack_into("<I", header, 0x38, 32)         # stride
    struct.pack_into("<I", header, 0x3C, 7)          # vertexType 7
    struct.pack_into("<I", header, 0x40, len(VERTICES))
    struct.pack_into("<I", header, 0x44, len(TRIANGLES))
    block += header

    for x, y, z in VERTICES:
        block += struct.pack("<3f", x, y, z)         # position
        block += struct.pack("<3f", 0.0, 1.0, 0.0)   # normal
        block += struct.pack("<2f", 0.0, 0.0)        # uv
    for tri in TRIANGLES:
        block += struct.pack("<3H", *tri)
    block += struct.pack("<6f", *bounds)
    return bytes(block)


def _model(bounds) -> str:
    path = os.path.join(tempfile.mkdtemp(), "piece.gam")
    with open(path, "wb") as handle:
        handle.write(write_container(0, [Chunk(MESH_CHUNK_ID, _mesh_chunk(bounds))]))
    return path


def _true_bounds():
    xs = [v[0] for v in VERTICES]
    ys = [v[1] for v in VERTICES]
    zs = [v[2] for v in VERTICES]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def test_a_correct_box_is_read_as_it_always_was() -> None:
    model = read_model(_model(_true_bounds()))
    assert len(model.meshes) == 1
    assert len(model.meshes[0].positions) == 4


def test_a_box_with_y_and_z_swapped_is_read_and_reported() -> None:
    """The regression: this used to lose the whole model."""
    x0, y0, z0, x1, y1, z1 = _true_bounds()
    model = read_model(_model((x0, z0, y0, x1, z1, y1)))

    assert len(model.meshes) == 1
    assert len(model.meshes[0].positions) == 4


def test_the_recomputed_box_describes_the_vertices() -> None:
    """Keeping the stored one would hand on a box with min above max."""
    x0, y0, z0, x1, y1, z1 = _true_bounds()
    model = read_model(_model((x0, z0, y0, x1, z1, y1)))

    bounds = model.meshes[-1].stored_bounds
    assert bounds is not None
    assert abs(bounds.min_corner.y - y0) < 1e-6
    assert abs(bounds.max_corner.y - y1) < 1e-6
    assert abs(bounds.min_corner.z - z0) < 1e-6
    assert abs(bounds.max_corner.z - z1) < 1e-6
    for axis in ("x", "y", "z"):
        assert getattr(bounds.min_corner, axis) <= getattr(bounds.max_corner, axis)


def test_a_box_that_no_permutation_explains_is_still_refused() -> None:
    """The check earns its keep only if it still catches the real thing.

    A box that is not the vertices in any order means the positions
    were read from the wrong offsets, and reading on would produce a
    plausible model made of nothing.
    """
    try:
        read_model(_model((-1.0, -1.0, -1.0, 1.0, 1.0, 1.0)))
    except ParsingError:
        return
    raise AssertionError("a box unrelated to the vertices was accepted")


def test_every_axis_swap_is_recognised_not_just_the_one_seen() -> None:
    """One file showed Y/Z. Nothing says the next writer breaks the same
    way, and the arithmetic covers the rest for free."""
    true = _true_bounds()
    for order in ((0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        stored = tuple(true[i] for i in order) + tuple(true[3 + i] for i in order)
        model = read_model(_model(stored))
        assert len(model.meshes[0].positions) == 4, order
