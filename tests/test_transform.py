# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `utils/math.py`, focused on `Transform`/`CoordinateSystem`.

These formalize the manual smoke tests from earlier development —
including the two real bugs found by hand (a transposed change-of-basis
matrix, and an unhandled handedness mirror) — as permanent regression
tests, per the "don't lose ad hoc testing" note in the architecture
doc. No `bpy` dependency; see `test_displace.py`'s docstring for why
these can run directly via `python3` in this sandbox.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.math import (  # noqa: E402
    AABB,
    BLENDER_COORDS,
    IDENTITY,
    CoordinateSystem,
    Handedness,
    Quaternion,
    Transform,
    Vector3,
)


def _assert_vec_close(a: Vector3, b: Vector3, tol: float = 1e-6) -> None:
    assert abs(a.x - b.x) < tol and abs(a.y - b.y) < tol and abs(a.z - b.z) < tol, f"{a} != {b}"


# --- Vector3 / Quaternion basics ---


def test_vector3_dot_and_cross() -> None:
    a, b = Vector3(1, 2, 3), Vector3(4, 5, 6)
    assert a.dot(b) == 32
    assert a.cross(b) == Vector3(-3, 6, -3)


def test_vector3_normalized() -> None:
    v = Vector3(3, 4, 0).normalized()
    assert abs(v.length() - 1.0) < 1e-9


def test_quaternion_identity_rotates_nothing() -> None:
    v = Vector3(1, 2, 3)
    assert Quaternion.identity().rotate_vector(v) == v


def test_quaternion_90_degrees_around_z() -> None:
    q = Quaternion.from_axis_angle(Vector3(0, 0, 1), math.pi / 2)
    _assert_vec_close(q.rotate_vector(Vector3(1, 0, 0)), Vector3(0, 1, 0))


# --- Matrix4 ---


def test_matrix4_trs_inverse_round_trip() -> None:
    from utils.math import Matrix4

    q = Quaternion.from_axis_angle(Vector3(0, 0, 1), math.pi / 2)
    mat = Matrix4.from_trs(Vector3(10, 0, 0), q, Vector3(2, 2, 2))
    point = Vector3(1, 0, 0)
    transformed = mat.transform_point(point)
    back = mat.inverse().transform_point(transformed)
    _assert_vec_close(back, point)


# --- AABB ---


def test_aabb_from_empty_points_is_none() -> None:
    assert AABB.from_points([]) is None


def test_aabb_union() -> None:
    a = AABB(Vector3(0, 0, 0), Vector3(1, 1, 1))
    b = AABB(Vector3(-1, 5, 0), Vector3(2, 6, 0))
    u = a.union(b)
    assert u.min_corner == Vector3(-1, 0, 0)
    assert u.max_corner == Vector3(2, 6, 1)


# --- CoordinateSystem validation ---


def test_coordinate_system_rejects_same_base_axis() -> None:
    raised = False
    try:
        CoordinateSystem(up_axis="Z", forward_axis="-Z")
    except ValueError:
        raised = True
    assert raised, "up_axis and forward_axis sharing a base axis must be rejected"


# --- Transform / coordinate conversion: the two previously-found bugs ---


def test_identity_transform_is_a_no_op() -> None:
    """DEFAULT_COORDINATE_SYSTEM (IDENTITY) must convert to itself as a no-op —
    the SDK's 'no unverified default hypothesis' guarantee."""
    t = Transform.from_coordinate_systems(IDENTITY, IDENTITY)
    p = Vector3(1, 2, 3)
    _assert_vec_close(t.apply(p), p)


def test_axis_remap_y_up_to_blender_z_up() -> None:
    """Regression test for the transposed-matrix bug: converting a
    Y-up/Z-forward system into Blender's Z-up/-Y-forward convention must
    map 'up' to 'up' and 'forward' to 'forward', not some other axis."""
    y_up = CoordinateSystem(up_axis="Y", forward_axis="Z", scale=1.0)
    t = Transform.from_coordinate_systems(y_up, BLENDER_COORDS)

    # The y_up system's "up" vector (0,1,0) must land on Blender's "up" (0,0,1).
    _assert_vec_close(t.apply(Vector3(0, 1, 0)), Vector3(0, 0, 1))
    # The y_up system's "forward" vector (0,0,1) must land on Blender's
    # "forward" (0,-1,0).
    _assert_vec_close(t.apply(Vector3(0, 0, 1)), Vector3(0, -1, 0))


def test_transform_inverse_round_trip() -> None:
    y_up = CoordinateSystem(up_axis="Y", forward_axis="Z", scale=1.0)
    t = Transform.from_coordinate_systems(y_up, BLENDER_COORDS)
    p = Vector3(5, 7, 9)
    _assert_vec_close(t.inverse().apply(t.apply(p)), p)


def test_scale_only_conversion() -> None:
    """centimeters -> meters: a pure scale change with no axis remap."""
    cm_system = CoordinateSystem(up_axis="Z", forward_axis="-Y", scale=0.01)
    t = Transform.from_coordinate_systems(cm_system, BLENDER_COORDS)
    _assert_vec_close(t.apply(Vector3(0, 0, 100)), Vector3(0, 0, 1))


def test_handedness_mismatch_preserves_length() -> None:
    """Regression test for the mirror/handedness bug: converting between
    coordinate systems of different handedness must still produce a
    length-preserving result (a quaternion alone cannot represent a
    mirror — the fix factors it into a signed scale component)."""
    left_handed = CoordinateSystem(
        up_axis="Y", forward_axis="Z", handedness=Handedness.LEFT_HANDED,
    )
    t = Transform.from_coordinate_systems(left_handed, BLENDER_COORDS)

    for v in (Vector3(1, 0, 0), Vector3(0, 1, 0), Vector3(0, 0, 1)):
        result = t.apply_vector(v)
        assert abs(result.length() - 1.0) < 1e-6, (
            f"handedness-mismatched conversion did not preserve length for {v}: "
            f"got {result} (length={result.length()})"
        )


def test_handedness_mismatch_inverse_round_trip() -> None:
    left_handed = CoordinateSystem(
        up_axis="Y", forward_axis="Z", handedness=Handedness.LEFT_HANDED,
    )
    t = Transform.from_coordinate_systems(left_handed, BLENDER_COORDS)
    p = Vector3(3, -2, 7)
    _assert_vec_close(t.inverse().apply(t.apply(p)), p)


_ALL_TESTS = (
    test_vector3_dot_and_cross,
    test_vector3_normalized,
    test_quaternion_identity_rotates_nothing,
    test_quaternion_90_degrees_around_z,
    test_matrix4_trs_inverse_round_trip,
    test_aabb_from_empty_points_is_none,
    test_aabb_union,
    test_coordinate_system_rejects_same_base_axis,
    test_identity_transform_is_a_no_op,
    test_axis_remap_y_up_to_blender_z_up,
    test_transform_inverse_round_trip,
    test_scale_only_conversion,
    test_handedness_mismatch_preserves_length,
    test_handedness_mismatch_inverse_round_trip,
)


if __name__ == "__main__":
    # See test_displace.py's `if __name__` block for why this manual
    # runner exists alongside normal pytest-style test functions.
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
