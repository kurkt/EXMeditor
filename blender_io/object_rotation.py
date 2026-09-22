# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The rotation an object ACTUALLY has, whatever its rotation mode.

Both bridges used to read ``obj.rotation_quaternion``. That property
is only the object's rotation while ``rotation_mode == "QUATERNION"``
— the mode the importer sets on what it builds. Every object created
in Blender, and every asset dropped from the palette, is in ``XYZ``
Euler mode, and in that mode Blender leaves ``rotation_quaternion``
at identity no matter how the object is turned. MEASURED (Goo Engine
3.6, background)::

    rotation_mode = "XYZ", rotation_euler = (0, 0, 90°)
    rotation_quaternion            -> (1.0, 0.0, 0.0, 0.0)
    matrix_world.to_quaternion()   -> (0.7071, 0.0, 0.0, 0.7071)
    rotation_euler.to_quaternion() -> (0.7071, 0.0, 0.0, 0.7071)

So a new object turned in the viewport exported unturned, in both
world.xml and dynamicscene.xml. This reads the mode and converts.
"""

from __future__ import annotations

import math

IDENTITY_EPSILON = 1e-6


def object_rotation(obj) -> tuple[float, float, float, float]:
    """The object's local rotation as ``(w, x, y, z)``."""
    mode = getattr(obj, "rotation_mode", "QUATERNION")
    if mode == "QUATERNION":
        return tuple(float(v) for v in obj.rotation_quaternion)
    if mode == "AXIS_ANGLE":
        angle, x, y, z = tuple(obj.rotation_axis_angle)
        return _axis_angle_to_quaternion(angle, (x, y, z))
    euler = obj.rotation_euler
    to_quaternion = getattr(euler, "to_quaternion", None)
    if to_quaternion is not None:
        # mathutils.Euler knows its own order; let it do the work.
        return tuple(float(v) for v in to_quaternion())
    return euler_to_quaternion(tuple(float(v) for v in euler), mode)


def is_identity(quaternion) -> bool:
    w, x, y, z = quaternion
    return (
        abs(abs(w) - 1.0) < IDENTITY_EPSILON
        and abs(x) < IDENTITY_EPSILON
        and abs(y) < IDENTITY_EPSILON
        and abs(z) < IDENTITY_EPSILON
    )


def euler_to_quaternion(
    euler: tuple[float, float, float], order: str = "XYZ",
) -> tuple[float, float, float, float]:
    """Blender's Euler-to-quaternion, in pure Python, for the test
    double. Blender applies the axes in the order named, so ``XYZ`` is
    a rotation about X, then Y, then Z: ``q = qz * qy * qx``.

    Checked against mathutils: ``Euler((30°, -45°, 120°), 'XYZ')`` gives
    ``(0.360423, 0.43968, 0.02226, 0.822363)``.
    """
    axes = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
    angle_of = dict(zip("XYZ", euler))
    result = (1.0, 0.0, 0.0, 0.0)
    for axis in order:
        result = _multiply(_axis_angle_to_quaternion(angle_of[axis], axes[axis]), result)
    return result


def _axis_angle_to_quaternion(angle: float, axis) -> tuple[float, float, float, float]:
    x, y, z = axis
    length = math.sqrt(x * x + y * y + z * z)
    if length < IDENTITY_EPSILON:
        return (1.0, 0.0, 0.0, 0.0)
    s = math.sin(angle / 2.0) / length
    return (math.cos(angle / 2.0), x * s, y * s, z * s)


def _multiply(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )
