# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `core/terrain_transform.py`.

Each transform is tested in isolation (matching the requirement that
they be independently toggleable/verifiable), plus involution
properties (flip twice = identity, transpose twice = identity) and the
composed `apply_orientation` path.
"""

from __future__ import annotations

import array
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.terrain import HeightmapData  # noqa: E402
from core.terrain_transform import (  # noqa: E402
    TerrainOrientation,
    apply_orientation,
    flip_x,
    flip_y,
    offset_height,
    scale_height,
    scale_xy,
    transpose,
)


def _make_grid() -> HeightmapData:
    """A 2x3 grid (width=2, height=3) with every cell a unique value,
    so any axis mix-up is immediately visible in the output.

    row0: [ 0,  1]
    row1: [10, 11]
    row2: [20, 21]
    """
    return HeightmapData(width=2, height=3, cell_size=1.0, values=array.array("f", [0, 1, 10, 11, 20, 21]))


def _grid(h: HeightmapData) -> list[list[float]]:
    return [[h.get_height(x, y) for x in range(h.width)] for y in range(h.height)]


def test_transpose_swaps_dimensions_and_values() -> None:
    t = transpose(_make_grid())
    assert (t.width, t.height) == (3, 2)
    assert _grid(t) == [[0.0, 10.0, 20.0], [1.0, 11.0, 21.0]]


def test_transpose_is_an_involution() -> None:
    original = _make_grid()
    assert _grid(transpose(transpose(original))) == _grid(original)


def test_flip_x_reverses_each_row() -> None:
    f = flip_x(_make_grid())
    assert _grid(f) == [[1.0, 0.0], [11.0, 10.0], [21.0, 20.0]]


def test_flip_x_is_an_involution() -> None:
    original = _make_grid()
    assert _grid(flip_x(flip_x(original))) == _grid(original)


def test_flip_y_reverses_row_order() -> None:
    f = flip_y(_make_grid())
    assert _grid(f) == [[20.0, 21.0], [10.0, 11.0], [0.0, 1.0]]


def test_flip_y_is_an_involution() -> None:
    original = _make_grid()
    assert _grid(flip_y(flip_y(original))) == _grid(original)


def test_scale_height_multiplies_every_sample() -> None:
    scaled = scale_height(_make_grid(), 2.0)
    assert _grid(scaled) == [[0.0, 2.0], [20.0, 22.0], [40.0, 42.0]]
    assert scaled.width == 2 and scaled.height == 3  # dimensions untouched


def test_offset_height_adds_to_every_sample() -> None:
    offset = offset_height(_make_grid(), 100.0)
    assert _grid(offset) == [[100.0, 101.0], [110.0, 111.0], [120.0, 121.0]]


def test_scale_xy_only_changes_cell_size() -> None:
    original = _make_grid()
    scaled = scale_xy(original, 0.5)
    assert scaled.cell_size == 0.5
    assert _grid(scaled) == _grid(original)  # heights untouched


def test_apply_orientation_default_is_a_true_no_op() -> None:
    original = _make_grid()
    result = apply_orientation(original, TerrainOrientation())
    assert _grid(result) == _grid(original)
    assert result.cell_size == original.cell_size
    assert (result.width, result.height) == (original.width, original.height)


def test_apply_orientation_composes_in_documented_order() -> None:
    """flip_x, then *2 height scale, then +5 height offset."""
    result = apply_orientation(
        _make_grid(),
        TerrainOrientation(flip_x=True, height_scale=2.0, height_offset=5.0),
    )
    assert _grid(result) == [[7.0, 5.0], [27.0, 25.0], [47.0, 45.0]]


def test_apply_orientation_transpose_runs_before_flips() -> None:
    """Documented order: transpose first, then flip_x/flip_y — verified
    by checking transpose+flip_x differs from flip_x+transpose would."""
    grid = _make_grid()
    # transpose first: (3,2) grid [[0,10,20],[1,11,21]]; then flip_x
    # reverses each of THOSE rows -> [[20,10,0],[21,11,1]]
    result = apply_orientation(grid, TerrainOrientation(transpose=True, flip_x=True))
    assert (result.width, result.height) == (3, 2)
    assert _grid(result) == [[20.0, 10.0, 0.0], [21.0, 11.0, 1.0]]


_ALL_TESTS = (
    test_transpose_swaps_dimensions_and_values,
    test_transpose_is_an_involution,
    test_flip_x_reverses_each_row,
    test_flip_x_is_an_involution,
    test_flip_y_reverses_row_order,
    test_flip_y_is_an_involution,
    test_scale_height_multiplies_every_sample,
    test_offset_height_adds_to_every_sample,
    test_scale_xy_only_changes_cell_size,
    test_apply_orientation_default_is_a_true_no_op,
    test_apply_orientation_composes_in_documented_order,
    test_apply_orientation_transpose_runs_before_flips,
)


if __name__ == "__main__":
    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            test_fn()
        except Exception as exc:  # noqa: BLE001 - test runner, want to catch everything
            failures += 1
            print(f"FAIL: {test_fn.__name__}: {exc}")
        else:
            print(f"PASS: {test_fn.__name__}")
    print(f"\n{len(_ALL_TESTS) - failures}/{len(_ALL_TESTS)} passed")
    sys.exit(1 if failures else 0)
