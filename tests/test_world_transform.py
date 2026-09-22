# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `core/world_transform.py`.

No `bpy` dependency — this is a pure numeric layer, tested in complete
isolation from both the codec (`formats/exm/terrain.py`) and Blender
(`blender_io/terrain_bridge.py`), which is the entire point of pulling
it out into its own module.
"""

from __future__ import annotations

import array
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.terrain import HeightmapData  # noqa: E402
from core.world_transform import WorldTransform  # noqa: E402
from utils.errors import ValidationError  # noqa: E402


def _grid(cell_size: float = 1.0) -> HeightmapData:
    return HeightmapData(width=3, height=3, cell_size=cell_size, values=array.array("f", [10, 20, 30, 40, 50, 60, 70, 80, 90]))


def test_identity_transform_is_a_true_no_op() -> None:
    original = _grid()
    result = WorldTransform().to_world(original)
    assert result.cell_size == original.cell_size
    assert list(result.values) == list(original.values)


def test_to_world_scales_xy_via_cell_size() -> None:
    result = WorldTransform(xy_scale=2.5).to_world(_grid(cell_size=1.0))
    assert result.cell_size == 2.5


def test_to_world_scales_height() -> None:
    result = WorldTransform(height_scale=0.1).to_world(_grid())
    assert list(result.values) == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]


def test_to_world_composes_xy_scale_with_existing_cell_size() -> None:
    """A format-confirmed cell_size and a manual xy_scale override must
    both apply — neither silently overwrites the other."""
    result = WorldTransform(xy_scale=2.0).to_world(_grid(cell_size=3.0))
    assert result.cell_size == 6.0


def test_to_game_is_the_exact_inverse_of_to_world() -> None:
    original = _grid(cell_size=1.0)
    transform = WorldTransform(xy_scale=2.5, height_scale=0.1)
    world = transform.to_world(original)
    back = transform.to_game(world)
    assert back.cell_size == original.cell_size
    for a, b in zip(back.values, original.values):
        assert abs(a - b) < 1e-4


def test_to_game_rejects_zero_xy_scale() -> None:
    try:
        WorldTransform(xy_scale=0.0).to_game(_grid())
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "xy_scale" in e.message


def test_to_game_rejects_zero_height_scale() -> None:
    try:
        WorldTransform(height_scale=0.0).to_game(_grid())
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "height_scale" in e.message


def test_to_world_does_not_mutate_the_input() -> None:
    original = _grid(cell_size=1.0)
    original_values_copy = list(original.values)
    WorldTransform(xy_scale=5.0, height_scale=5.0).to_world(original)
    assert original.cell_size == 1.0
    assert list(original.values) == original_values_copy


_ALL_TESTS = (
    test_identity_transform_is_a_true_no_op,
    test_to_world_scales_xy_via_cell_size,
    test_to_world_scales_height,
    test_to_world_composes_xy_scale_with_existing_cell_size,
    test_to_game_is_the_exact_inverse_of_to_world,
    test_to_game_rejects_zero_xy_scale,
    test_to_game_rejects_zero_height_scale,
    test_to_world_does_not_mutate_the_input,
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
