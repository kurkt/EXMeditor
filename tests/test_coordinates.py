# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `core/coordinates.py` — the single shared spatial transform.

These pin down the invariant that was violated in real use: terrain and
world.xml objects landing on different planes because each bridge did
its own conversion. The bug had two independent causes, both covered
here:

1. **Axis swap.** Ex Machina is Y-up, Blender is Z-up. Terrain put
   height in Blender Z; objects put it in Blender Y.
2. **Scale.** Terrain used a placeholder cell size of 1.0, covering
   0..511 game units, while objects spanned 0..3878 — an ~8x
   mismatch. The confirmed cell size is 8.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.coordinates import (  # noqa: E402
    DEFAULT_HEIGHT_SCALE,
    DEFAULT_XY_SCALE,
    TERRAIN_CELL_SIZE,
    CoordinateTransform,
)
from utils.errors import ValidationError  # noqa: E402
from utils.math import Vector3  # noqa: E402


def test_confirmed_terrain_cell_size() -> None:
    """Three independent measurements agree on 8: object span (0..3878),
    .ssl MAXSAFEX (4056), and 512 grid * 8 = 4088."""
    assert TERRAIN_CELL_SIZE == 8.0


def test_game_y_becomes_blender_z() -> None:
    """The core axis fix: game Y is height, Blender Z is height."""
    t = CoordinateTransform()
    result = t.game_to_blender_position(Vector3(0.0, 100.0, 0.0))
    assert result.z == 100.0
    assert result.y == 0.0


def test_game_z_becomes_blender_y() -> None:
    """Game Z is horizontal, and must land on Blender's horizontal Y —
    not on Blender Z, which would put it in the height axis."""
    t = CoordinateTransform()
    result = t.game_to_blender_position(Vector3(0.0, 0.0, 250.0))
    assert result.y == 250.0
    assert result.z == 0.0


def test_game_x_stays_blender_x() -> None:
    t = CoordinateTransform()
    assert t.game_to_blender_position(Vector3(42.0, 0.0, 0.0)).x == 42.0


def test_horizontal_axes_use_xy_scale_and_height_uses_height_scale() -> None:
    t = CoordinateTransform(xy_scale=10.0, height_scale=1.5)
    result = t.game_to_blender_position(Vector3(1.0, 1.0, 1.0))
    assert result.x == 10.0   # game X, horizontal
    assert result.y == 10.0   # game Z, horizontal
    assert result.z == 1.5    # game Y, height


def test_position_round_trip() -> None:
    t = CoordinateTransform(xy_scale=10.0, height_scale=1.5)
    original = Vector3(3317.794, 12.5, 3364.272)
    back = t.blender_to_game_position(t.game_to_blender_position(original))
    for a, b in zip(original.as_tuple(), back.as_tuple()):
        assert abs(a - b) < 1e-6


def test_offset_round_trip() -> None:
    """Parent-relative offsets convert the same way as points (no
    translation component), but through their own named methods."""
    t = CoordinateTransform(xy_scale=10.0, height_scale=1.5)
    original = Vector3(-3.523, 0.006, 10.524)
    back = t.blender_to_game_offset(t.game_to_blender_offset(original))
    for a, b in zip(original.as_tuple(), back.as_tuple()):
        assert abs(a - b) < 1e-6


def test_height_helpers_round_trip() -> None:
    t = CoordinateTransform(height_scale=1.5)
    assert abs(t.blender_height_to_game(t.game_height_to_blender(286.44)) - 286.44) < 1e-6


def test_terrain_grid_step_uses_xy_scale() -> None:
    """Terrain is placed by grid index, not by a game position, but must
    still use the same xy_scale — otherwise it desyncs from objects."""
    t = CoordinateTransform(xy_scale=10.0, height_scale=1.5)
    assert t.terrain_grid_step(cell_size=8.0) == 80.0
    assert t.terrain_grid_step() == TERRAIN_CELL_SIZE * 10.0


def test_terrain_and_objects_share_one_footprint() -> None:
    """The regression this module exists for: a terrain grid and an
    object at the far corner of the same map must land at the same
    Blender coordinates.

    A 512-cell grid at cell size 8 spans 0..4088 game units. An object
    at game (4088, 0, 4088) must therefore land at the terrain's far
    corner vertex, not somewhere else entirely.
    """
    t = CoordinateTransform(xy_scale=10.0, height_scale=1.5)

    grid_side = 512
    step = t.terrain_grid_step()
    terrain_far_corner_x = (grid_side - 1) * step
    terrain_far_corner_y = (grid_side - 1) * step

    far_game_coord = (grid_side - 1) * TERRAIN_CELL_SIZE
    obj = t.game_to_blender_position(Vector3(far_game_coord, 0.0, far_game_coord))

    assert abs(obj.x - terrain_far_corner_x) < 1e-6
    assert abs(obj.y - terrain_far_corner_y) < 1e-6


def test_the_defaults_leave_the_engine_figures_alone() -> None:
    """Both scales are 1.0, and that is measured rather than tuned.

    A tile cell is 32 units and the heightfield is sampled every 8 —
    a quarter of a cell. Checked against 4951 objects carrying an
    absolute Y, sampling ``H[z * N + x]`` gives a median error of 0.00
    and 99.2% within 5 units. A median of exactly zero confirms the
    height format, the 8-unit step and the 32-unit cell at once, and
    leaves nothing for a scale factor to do.

    Earlier versions carried 1.25 and 1.5, arrived at by adjusting
    values until the result looked right in game. This is the test that
    used to pin them, and it pinned a number that described nothing.
    """
    transform = CoordinateTransform(
        xy_scale=DEFAULT_XY_SCALE, height_scale=DEFAULT_HEIGHT_SCALE,
    )
    assert DEFAULT_XY_SCALE == 1.0
    assert DEFAULT_HEIGHT_SCALE == 1.0

    # One grid cell spans its own 8 units, untouched.
    assert abs(transform.terrain_grid_step() - 8.0) < 1e-6

    # And a height passes through as the world Y it already is.
    assert abs(transform.game_height_to_blender(286.44) - 286.44) < 1e-6


def test_rotation_conversion_matches_position_conversion() -> None:
    """Regression for a bug that only became visible once real geometry
    loaded: rotations were passed through with no axis change while
    positions were swapped, so a turn about the game's vertical axis
    became a turn about a horizontal one and every rotated object tipped
    out of place.

    The check: rotating a point in game space and then converting must
    equal converting the point and rotating it with the converted
    quaternion.
    """
    import math
    import random

    from utils.math import Quaternion

    transform = CoordinateTransform(xy_scale=1.25, height_scale=1.25)
    random.seed(11)

    for _ in range(100):
        axis = [random.uniform(-1, 1) for _ in range(3)]
        length = math.sqrt(sum(v * v for v in axis))
        axis = [v / length for v in axis]
        angle = random.uniform(-math.pi, math.pi)
        s, c = math.sin(angle / 2), math.cos(angle / 2)
        game_rotation = (axis[0] * s, axis[1] * s, axis[2] * s, c)
        point = Vector3(
            random.uniform(-50, 50), random.uniform(-50, 50), random.uniform(-50, 50)
        )

        game_q = Quaternion(w=game_rotation[3], x=game_rotation[0],
                            y=game_rotation[1], z=game_rotation[2])
        expected = transform.game_to_blender_position(game_q.rotate_vector(point))

        w, x, y, z = transform.game_to_blender_rotation(game_rotation)
        actual = Quaternion(w=w, x=x, y=y, z=z).rotate_vector(
            transform.game_to_blender_position(point)
        )

        for a, b in zip(expected.as_tuple(), actual.as_tuple()):
            assert abs(a - b) < 1e-6


def test_rotation_round_trip() -> None:
    transform = CoordinateTransform(xy_scale=1.25, height_scale=1.25)
    original = (0.1, 0.2, 0.3, 0.927)
    back = transform.blender_to_game_rotation(
        transform.game_to_blender_rotation(original)
    )
    for a, b in zip(original, back):
        assert abs(a - b) < 1e-9


def test_is_uniform_flags_a_scale_that_shears_rotations() -> None:
    """A non-uniform scale makes an exact rotation conversion
    impossible — rotated geometry is sheared. Callers need to know."""
    assert CoordinateTransform(xy_scale=1.25, height_scale=1.25).is_uniform
    assert not CoordinateTransform(xy_scale=1.25, height_scale=1.5).is_uniform


def test_rotation_rejects_wrong_component_count() -> None:
    transform = CoordinateTransform()
    try:
        transform.game_to_blender_rotation((0.0, 0.0, 1.0))
        raise AssertionError("expected ValidationError")
    except ValidationError:
        pass


def test_origin_offset_shifts_positions() -> None:
    """A map is thousands of units across; without centring it all sits
    in the +X/+Y quadrant with its middle far from the origin."""
    offset = Vector3(100.0, 0.0, 200.0)
    transform = CoordinateTransform(origin_offset=offset)
    result = transform.game_to_blender_position(Vector3(100.0, 5.0, 200.0))
    assert result.x == 0.0
    assert result.y == 0.0
    assert result.z == 5.0  # height is never shifted


def test_origin_offset_round_trips_exactly() -> None:
    """The shift must never reach the map files."""
    transform = CoordinateTransform(
        xy_scale=1.25, height_scale=1.25, origin_offset=Vector3(2044.0, 0.0, 2044.0),
    )
    original = Vector3(3317.794, 12.5, 3364.272)
    back = transform.blender_to_game_position(
        transform.game_to_blender_position(original)
    )
    for a, b in zip(original.as_tuple(), back.as_tuple()):
        assert abs(a - b) < 1e-6


def test_origin_offset_does_not_apply_to_deltas() -> None:
    """Nested children store parent-relative offsets. Shifting the map
    moves points, not the distances between them — applying the offset
    to a delta would displace every child by the centring amount."""
    transform = CoordinateTransform(origin_offset=Vector3(2044.0, 0.0, 2044.0))
    delta = Vector3(3.0, 1.0, 4.0)
    result = transform.game_to_blender_offset(delta)
    assert result.x == 3.0
    assert result.y == 4.0   # game Z -> Blender Y, unshifted
    assert result.z == 1.0


def test_centre_offset_puts_a_grid_middle_on_the_origin() -> None:
    offset = CoordinateTransform.centre_offset_for_grid(512, cell_size=8.0)
    assert offset.x == (512 - 1) * 8.0 * 0.5
    assert offset.z == offset.x
    assert offset.y == 0.0, "height must not be shifted"


def test_rejects_zero_scales() -> None:
    for kwargs in ({"xy_scale": 0.0}, {"height_scale": 0.0}):
        try:
            CoordinateTransform(**kwargs)
            raise AssertionError(f"expected ValidationError for {kwargs}")
        except ValidationError:
            pass


def test_identity_transform_only_swaps_axes() -> None:
    """With scale 1.0 the transform is purely the Y-up -> Z-up swap."""
    t = CoordinateTransform()
    assert t.game_to_blender_position(Vector3(1.0, 2.0, 3.0)) == Vector3(1.0, 3.0, 2.0)


_ALL_TESTS = (
    test_confirmed_terrain_cell_size,
    test_game_y_becomes_blender_z,
    test_game_z_becomes_blender_y,
    test_game_x_stays_blender_x,
    test_horizontal_axes_use_xy_scale_and_height_uses_height_scale,
    test_position_round_trip,
    test_offset_round_trip,
    test_height_helpers_round_trip,
    test_terrain_grid_step_uses_xy_scale,
    test_terrain_and_objects_share_one_footprint,
    test_the_defaults_leave_the_engine_figures_alone,
    test_rotation_conversion_matches_position_conversion,
    test_rotation_round_trip,
    test_is_uniform_flags_a_scale_that_shears_rotations,
    test_rotation_rejects_wrong_component_count,
    test_origin_offset_shifts_positions,
    test_origin_offset_round_trips_exactly,
    test_origin_offset_does_not_apply_to_deltas,
    test_centre_offset_puts_a_grid_middle_on_the_origin,
    test_rejects_zero_scales,
    test_identity_transform_only_swaps_axes,
)


if __name__ == "__main__":
    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            test_fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL: {test_fn.__name__}: {exc}")
        else:
            print(f"PASS: {test_fn.__name__}")
    print(f"\n{len(_ALL_TESTS) - failures}/{len(_ALL_TESTS)} passed")
    sys.exit(1 if failures else 0)
