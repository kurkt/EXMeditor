# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generic, game-agnostic math primitives for the EXMeditor.

This module knows nothing about Ex Machina, ``world.xml``, or any other
game format. It provides the same kind of vector/quaternion/matrix
toolkit any 3D pipeline needs, plus a :class:`CoordinateSystem` /
:class:`Transform` pair for converting between axis conventions. Which
concrete coordinate system a given game uses is knowledge that belongs
in that game's plugin (e.g. ``formats/exm/``), not here — see
``formats/exm/`` (future) for where an Ex-Machina-specific
``CoordinateSystem`` instance will eventually live, once verified
against real in-game reference data.

Design notes
------------
- No ``bpy`` dependency: every type here is a plain Python
  ``dataclass`` built on ``float``/``tuple``. ``blender_io/*_bridge.py``
  is responsible for converting to/from ``mathutils.Vector`` /
  ``mathutils.Quaternion`` at the point where ``bpy`` is already
  imported — this module must stay importable in a plain interpreter.
- No default axis-conversion hypothesis. ``DEFAULT_COORDINATE_SYSTEM``
  is an alias for ``BLENDER_COORDS`` — i.e. "assume the source data is
  already in Blender's convention" — precisely so that an unconverted
  import is *visibly* wrong (rotated/mirrored) rather than *silently*
  wrong under an unverified guess. Per-game presets, once confirmed,
  are opt-in constants a plugin passes explicitly.
- Conversion is done through :class:`Transform`, not a bare
  ``convert_position(point, src, dst)`` function: ``Transform.apply()``
  composes with any other transform (object placement, parenting, ...)
  and ``Transform.inverse()`` makes export "free" once import works,
  which a one-off conversion function would not give us.
"""

from __future__ import annotations

import dataclasses
import math
from enum import Enum
from typing import Iterable, Literal

# ---------------------------------------------------------------------------
# Vector3
# ---------------------------------------------------------------------------

Axis = Literal["X", "Y", "Z", "-X", "-Y", "-Z"]


@dataclasses.dataclass(frozen=True)
class Vector3:
    """An immutable 3D vector / point."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    @staticmethod
    def zero() -> "Vector3":
        return Vector3(0.0, 0.0, 0.0)

    @staticmethod
    def one() -> "Vector3":
        return Vector3(1.0, 1.0, 1.0)

    def __add__(self, other: "Vector3") -> "Vector3":
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: "Vector3") -> "Vector3":
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __neg__(self) -> "Vector3":
        return Vector3(-self.x, -self.y, -self.z)

    def __mul__(self, scalar: float) -> "Vector3":
        return Vector3(self.x * scalar, self.y * scalar, self.z * scalar)

    __rmul__ = __mul__

    def component_mul(self, other: "Vector3") -> "Vector3":
        """Component-wise (Hadamard) product — used for applying scale."""
        return Vector3(self.x * other.x, self.y * other.y, self.z * other.z)

    def dot(self, other: "Vector3") -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: "Vector3") -> "Vector3":
        return Vector3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def length(self) -> float:
        return math.sqrt(self.dot(self))

    def normalized(self) -> "Vector3":
        length = self.length()
        if length == 0.0:
            raise ValueError("cannot normalize a zero-length Vector3")
        return self * (1.0 / length)

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    @staticmethod
    def lerp(a: "Vector3", b: "Vector3", t: float) -> "Vector3":
        return a + (b - a) * t


def _axis_unit_vector(axis: Axis) -> Vector3:
    """Return the unit vector for a named cardinal axis, e.g. ``"-Y"``."""
    mapping: dict[str, Vector3] = {
        "X": Vector3(1.0, 0.0, 0.0),
        "-X": Vector3(-1.0, 0.0, 0.0),
        "Y": Vector3(0.0, 1.0, 0.0),
        "-Y": Vector3(0.0, -1.0, 0.0),
        "Z": Vector3(0.0, 0.0, 1.0),
        "-Z": Vector3(0.0, 0.0, -1.0),
    }
    try:
        return mapping[axis]
    except KeyError as exc:
        raise ValueError(f"invalid axis: {axis!r}") from exc


def _axis_base(axis: Axis) -> str:
    """Return the base axis letter ('X'/'Y'/'Z') ignoring sign."""
    return axis[-1]


# ---------------------------------------------------------------------------
# Quaternion
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Quaternion:
    """An immutable rotation quaternion, scalar-first (w, x, y, z)."""

    w: float = 1.0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    @staticmethod
    def identity() -> "Quaternion":
        return Quaternion(1.0, 0.0, 0.0, 0.0)

    @staticmethod
    def from_axis_angle(axis: Vector3, angle_rad: float) -> "Quaternion":
        """Build a rotation of ``angle_rad`` radians around ``axis``.

        ``axis`` is normalized internally; it does not need to be a unit
        vector already.
        """
        unit_axis = axis.normalized()
        half = angle_rad * 0.5
        sin_half = math.sin(half)
        return Quaternion(
            w=math.cos(half),
            x=unit_axis.x * sin_half,
            y=unit_axis.y * sin_half,
            z=unit_axis.z * sin_half,
        )

    def __mul__(self, other: "Quaternion") -> "Quaternion":
        """Hamilton product: ``self * other`` applies ``other`` first."""
        return Quaternion(
            w=self.w * other.w - self.x * other.x - self.y * other.y - self.z * other.z,
            x=self.w * other.x + self.x * other.w + self.y * other.z - self.z * other.y,
            y=self.w * other.y - self.x * other.z + self.y * other.w + self.z * other.x,
            z=self.w * other.z + self.x * other.y - self.y * other.x + self.z * other.w,
        )

    def conjugate(self) -> "Quaternion":
        return Quaternion(self.w, -self.x, -self.y, -self.z)

    def length(self) -> float:
        return math.sqrt(self.w**2 + self.x**2 + self.y**2 + self.z**2)

    def normalized(self) -> "Quaternion":
        length = self.length()
        if length == 0.0:
            raise ValueError("cannot normalize a zero-length Quaternion")
        inv = 1.0 / length
        return Quaternion(self.w * inv, self.x * inv, self.y * inv, self.z * inv)

    def inverse(self) -> "Quaternion":
        """Inverse rotation. Equivalent to ``conjugate()`` for unit quaternions,
        but this also handles non-unit ones correctly."""
        length_sq = self.w**2 + self.x**2 + self.y**2 + self.z**2
        if length_sq == 0.0:
            raise ValueError("cannot invert a zero-length Quaternion")
        conj = self.conjugate()
        inv = 1.0 / length_sq
        return Quaternion(conj.w * inv, conj.x * inv, conj.y * inv, conj.z * inv)

    def rotate_vector(self, v: Vector3) -> Vector3:
        """Rotate ``v`` by this quaternion (assumed normalized for best results)."""
        qv = Quaternion(0.0, v.x, v.y, v.z)
        result = self * qv * self.conjugate()
        return Vector3(result.x, result.y, result.z)


# ---------------------------------------------------------------------------
# Matrix4 (row-major 4x4, affine or general)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Matrix4:
    """An immutable 4x4 matrix, stored row-major as a flat 16-tuple.

    Index ``rows[r * 4 + c]`` is row ``r``, column ``c``. Points are
    treated as column vectors: ``M @ [x, y, z, 1]``.
    """

    values: tuple[float, ...]  # length 16, row-major

    def __post_init__(self) -> None:
        if len(self.values) != 16:
            raise ValueError(f"Matrix4 requires exactly 16 values, got {len(self.values)}")

    @staticmethod
    def identity() -> "Matrix4":
        return Matrix4((
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ))

    def get(self, row: int, col: int) -> float:
        return self.values[row * 4 + col]

    @staticmethod
    def from_trs(translation: Vector3, rotation: Quaternion, scale: Vector3) -> "Matrix4":
        """Build a matrix from translation, rotation, and scale (TRS order:
        scale is applied first, then rotation, then translation)."""
        w, x, y, z = rotation.w, rotation.x, rotation.y, rotation.z
        # Standard quaternion-to-rotation-matrix conversion.
        r00 = 1 - 2 * (y * y + z * z)
        r01 = 2 * (x * y - z * w)
        r02 = 2 * (x * z + y * w)
        r10 = 2 * (x * y + z * w)
        r11 = 1 - 2 * (x * x + z * z)
        r12 = 2 * (y * z - x * w)
        r20 = 2 * (x * z - y * w)
        r21 = 2 * (y * z + x * w)
        r22 = 1 - 2 * (x * x + y * y)
        sx, sy, sz = scale.x, scale.y, scale.z
        return Matrix4((
            r00 * sx, r01 * sy, r02 * sz, translation.x,
            r10 * sx, r11 * sy, r12 * sz, translation.y,
            r20 * sx, r21 * sy, r22 * sz, translation.z,
            0.0, 0.0, 0.0, 1.0,
        ))

    def multiply(self, other: "Matrix4") -> "Matrix4":
        """Return ``self @ other`` (self's transform applied after other's)."""
        result = [0.0] * 16
        for r in range(4):
            for c in range(4):
                total = 0.0
                for k in range(4):
                    total += self.get(r, k) * other.get(k, c)
                result[r * 4 + c] = total
        return Matrix4(tuple(result))

    def __matmul__(self, other: "Matrix4") -> "Matrix4":
        return self.multiply(other)

    def transform_point(self, point: Vector3) -> Vector3:
        """Transform a point (implicit w=1 — translation applies)."""
        x, y, z = point.x, point.y, point.z
        v = self.values
        return Vector3(
            v[0] * x + v[1] * y + v[2] * z + v[3],
            v[4] * x + v[5] * y + v[6] * z + v[7],
            v[8] * x + v[9] * y + v[10] * z + v[11],
        )

    def transform_vector(self, vector: Vector3) -> Vector3:
        """Transform a direction (implicit w=0 — translation does not apply)."""
        x, y, z = vector.x, vector.y, vector.z
        v = self.values
        return Vector3(
            v[0] * x + v[1] * y + v[2] * z,
            v[4] * x + v[5] * y + v[6] * z,
            v[8] * x + v[9] * y + v[10] * z,
        )

    def transpose(self) -> "Matrix4":
        v = self.values
        return Matrix4((
            v[0], v[4], v[8], v[12],
            v[1], v[5], v[9], v[13],
            v[2], v[6], v[10], v[14],
            v[3], v[7], v[11], v[15],
        ))

    def determinant3(self) -> float:
        """Determinant of this matrix's upper-left 3x3 block.

        Used to detect whether a change-of-basis matrix is a proper
        rotation (``+1``) or includes a mirror (``-1``) — a quaternion
        can only represent the former, so ``Transform.from_coordinate_systems``
        uses this to decide whether a non-uniform (mirroring) scale
        component is needed in addition to the rotation.
        """
        m00, m01, m02 = self.values[0], self.values[1], self.values[2]
        m10, m11, m12 = self.values[4], self.values[5], self.values[6]
        m20, m21, m22 = self.values[8], self.values[9], self.values[10]
        return (
            m00 * (m11 * m22 - m12 * m21)
            - m01 * (m10 * m22 - m12 * m20)
            + m02 * (m10 * m21 - m11 * m20)
        )

    def inverse(self) -> "Matrix4":
        """General 4x4 inverse via Gauss-Jordan elimination.

        Works for any invertible matrix, not just affine TRS ones, so it
        stays correct if a future format needs a non-uniform shear or a
        projection matrix. Raises ``ValueError`` if the matrix is singular.
        """
        # Augment [self | identity] and row-reduce the left half to
        # identity; the right half becomes the inverse.
        a = [list(self.values[r * 4:r * 4 + 4]) for r in range(4)]
        identity = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        for col in range(4):
            pivot_row = max(range(col, 4), key=lambda r: abs(a[r][col]))
            if abs(a[pivot_row][col]) < 1e-12:
                raise ValueError("Matrix4 is singular and cannot be inverted")
            if pivot_row != col:
                a[col], a[pivot_row] = a[pivot_row], a[col]
                identity[col], identity[pivot_row] = identity[pivot_row], identity[col]
            pivot = a[col][col]
            a[col] = [v / pivot for v in a[col]]
            identity[col] = [v / pivot for v in identity[col]]
            for r in range(4):
                if r == col:
                    continue
                factor = a[r][col]
                if factor == 0.0:
                    continue
                a[r] = [v - factor * a[col][i] for i, v in enumerate(a[r])]
                identity[r] = [v - factor * identity[col][i] for i, v in enumerate(identity[r])]
        flat = tuple(value for row in identity for value in row)
        return Matrix4(flat)


# ---------------------------------------------------------------------------
# AABB
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AABB:
    """An axis-aligned bounding box."""

    min_corner: Vector3
    max_corner: Vector3

    @staticmethod
    def from_points(points: Iterable[Vector3]) -> "AABB | None":
        """Build the tightest AABB containing ``points``.

        Returns ``None`` for an empty iterable rather than raising, since
        callers such as ``MapScene.bounding_box()`` must handle an empty
        or partially-loaded scene without a special-case branch.
        """
        iterator = iter(points)
        try:
            first = next(iterator)
        except StopIteration:
            return None
        min_x = max_x = first.x
        min_y = max_y = first.y
        min_z = max_z = first.z
        for point in iterator:
            min_x, max_x = min(min_x, point.x), max(max_x, point.x)
            min_y, max_y = min(min_y, point.y), max(max_y, point.y)
            min_z, max_z = min(min_z, point.z), max(max_z, point.z)
        return AABB(Vector3(min_x, min_y, min_z), Vector3(max_x, max_y, max_z))

    def union(self, other: "AABB") -> "AABB":
        return AABB(
            Vector3(
                min(self.min_corner.x, other.min_corner.x),
                min(self.min_corner.y, other.min_corner.y),
                min(self.min_corner.z, other.min_corner.z),
            ),
            Vector3(
                max(self.max_corner.x, other.max_corner.x),
                max(self.max_corner.y, other.max_corner.y),
                max(self.max_corner.z, other.max_corner.z),
            ),
        )

    def center(self) -> Vector3:
        return Vector3.lerp(self.min_corner, self.max_corner, 0.5)

    def size(self) -> Vector3:
        return self.max_corner - self.min_corner


# ---------------------------------------------------------------------------
# CoordinateSystem
# ---------------------------------------------------------------------------


class Handedness(Enum):
    RIGHT_HANDED = "right"
    LEFT_HANDED = "left"


@dataclasses.dataclass(frozen=True)
class CoordinateSystem:
    """Describes a 3D axis convention relative to a common reference.

    Parameters
    ----------
    up_axis, forward_axis:
        Which cardinal axis (with sign) is "up" and "forward" in this
        convention. Must refer to two different base axes (e.g.
        ``up_axis="Z"`` and ``forward_axis="-Z"`` is invalid — that's
        the same base axis with opposite sign).
    scale:
        Multiply a value in this system by ``scale`` to get meters
        (Blender's unit). E.g. if this system uses centimeters,
        ``scale=0.01``.
    origin:
        World-space offset (in *this* system's units) of this system's
        origin relative to the destination system's origin — e.g. a
        game that places (0, 0, 0) at a map corner instead of its
        center would set this to the corner-to-center offset.
    handedness:
        Whether this convention is right- or left-handed. Needed to
        derive the third ("right") basis vector correctly, and to
        detect when a conversion requires a mirroring in addition to a
        rotation.
    """

    up_axis: Axis
    forward_axis: Axis
    scale: float = 1.0
    origin: Vector3 = dataclasses.field(default_factory=Vector3.zero)
    handedness: Handedness = Handedness.RIGHT_HANDED

    def __post_init__(self) -> None:
        if _axis_base(self.up_axis) == _axis_base(self.forward_axis):
            raise ValueError(
                f"up_axis ({self.up_axis!r}) and forward_axis "
                f"({self.forward_axis!r}) must not share the same base axis"
            )

    def basis_vectors(self) -> tuple[Vector3, Vector3, Vector3]:
        """Return this system's (right, up, forward) unit basis vectors."""
        up = _axis_unit_vector(self.up_axis)
        forward = _axis_unit_vector(self.forward_axis)
        if self.handedness is Handedness.RIGHT_HANDED:
            right = forward.cross(up).normalized()
        else:
            right = up.cross(forward).normalized()
        return right, up, forward

    def basis_matrix(self) -> Matrix4:
        """3x3 (embedded in a Matrix4) change-of-basis matrix ``B``.

        ``B`` maps a semantic (right, up, forward) coordinate triple to
        this system's raw (x, y, z) numbers: ``raw = B @ (r, u, f)``.
        Its columns are, in order, the right/up/forward unit vectors.
        """
        right, up, forward = self.basis_vectors()
        return Matrix4((
            right.x, up.x, forward.x, 0.0,
            right.y, up.y, forward.y, 0.0,
            right.z, up.z, forward.z, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ))


# Blender's own convention: Z up, -Y forward, right-handed, meters.
BLENDER_COORDS = CoordinateSystem(
    up_axis="Z", forward_axis="-Y", scale=1.0, origin=Vector3.zero(), handedness=Handedness.RIGHT_HANDED,
)

# No default hypothesis about any game's coordinate system. This equals
# BLENDER_COORDS on purpose: until a game plugin supplies a verified
# CoordinateSystem of its own, data is imported as-is, so a wrong
# assumption shows up immediately as a visibly rotated/mirrored import
# instead of silently baking an unverified guess into every map.
IDENTITY = BLENDER_COORDS
DEFAULT_COORDINATE_SYSTEM = IDENTITY


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Transform:
    """A translation + rotation + scale transform.

    This is the SDK's unit of "convert this data from one space to
    another" — callers do ``transform.apply(point)`` rather than calling
    a bare conversion function, so the same object can later be
    inverted (``transform.inverse()``, needed for export) or composed
    with another transform (e.g. an object's own placement) without a
    second code path.
    """

    translation: Vector3 = dataclasses.field(default_factory=Vector3.zero)
    rotation: Quaternion = dataclasses.field(default_factory=Quaternion.identity)
    scale: Vector3 = dataclasses.field(default_factory=Vector3.one)

    @staticmethod
    def identity() -> "Transform":
        return Transform(Vector3.zero(), Quaternion.identity(), Vector3.one())

    def apply(self, point: Vector3) -> Vector3:
        """Transform a point: scale, then rotate, then translate."""
        scaled = point.component_mul(self.scale)
        rotated = self.rotation.rotate_vector(scaled)
        return rotated + self.translation

    def apply_vector(self, vector: Vector3) -> Vector3:
        """Transform a direction: scale and rotate, but do not translate."""
        scaled = vector.component_mul(self.scale)
        return self.rotation.rotate_vector(scaled)

    def to_matrix(self) -> Matrix4:
        return Matrix4.from_trs(self.translation, self.rotation, self.scale)

    def inverse(self) -> "Transform":
        """Return the transform that undoes this one.

        Used to make export (Blender -> game space) reuse the same
        conversion that import (game space -> Blender) was built from,
        instead of writing a second, independently-maintained formula.
        """
        if self.scale.x == 0.0 or self.scale.y == 0.0 or self.scale.z == 0.0:
            raise ValueError("cannot invert a Transform with a zero scale component")
        inv_scale = Vector3(1.0 / self.scale.x, 1.0 / self.scale.y, 1.0 / self.scale.z)
        inv_rotation = self.rotation.inverse()
        # Undo translation first, in the original (rotated+scaled) space,
        # then undo rotation, then undo scale.
        inv_translation = inv_rotation.rotate_vector(-self.translation).component_mul(inv_scale)
        return Transform(inv_translation, inv_rotation, inv_scale)

    def compose(self, other: "Transform") -> "Transform":
        """Return a transform equivalent to applying ``other`` then ``self``."""
        combined_rotation = self.rotation * other.rotation
        combined_scale = self.scale.component_mul(other.scale)
        combined_translation = self.apply(other.translation) - self.translation + self.translation
        # (self.apply(other.translation) already scales+rotates+translates
        # other.translation by self; that *is* the composed translation.)
        combined_translation = self.apply(other.translation)
        return Transform(combined_translation, combined_rotation, combined_scale)

    @staticmethod
    def from_coordinate_systems(src: CoordinateSystem, dst: CoordinateSystem) -> "Transform":
        """Build the transform that converts a point from ``src`` into ``dst``.

        Derived purely from each system's basis vectors, scale, and
        origin — no game-specific knowledge. If ``src == dst`` this
        returns an identity transform (no rotation, no rescale).

        Derivation: a raw point in ``src`` first gets reinterpreted as
        semantic (right, up, forward) coordinates via ``src``'s inverse
        basis matrix (its transpose, since the basis is orthonormal),
        then re-expressed as ``dst`` raw numbers via ``dst``'s basis
        matrix: ``M = B_dst @ B_src^T``.
        """
        if dst.scale == 0.0:
            raise ValueError("destination CoordinateSystem.scale must not be 0")

        change_of_basis = dst.basis_matrix().multiply(src.basis_matrix().transpose())

        uniform_factor = src.scale / dst.scale
        scale_vector = Vector3(uniform_factor, uniform_factor, uniform_factor)

        # A quaternion can only represent a proper rotation (determinant
        # +1). If src and dst differ in handedness, change_of_basis has
        # determinant -1 (a mirror is baked in) and would produce a
        # non-length-preserving result if converted to a quaternion
        # directly. Factor the mirror out as a sign flip on one local
        # axis (applied as part of `scale`, before rotation — see
        # Transform.apply's order), leaving a proper rotation behind.
        _MIRROR_X = Matrix4((
            -1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ))
        if change_of_basis.determinant3() < 0.0:
            rotation_matrix = change_of_basis.multiply(_MIRROR_X)
            scale_vector = Vector3(-scale_vector.x, scale_vector.y, scale_vector.z)
        else:
            rotation_matrix = change_of_basis

        rotation = _quaternion_from_rotation_matrix(rotation_matrix)

        # Origin offset: src.origin is expressed in src's own raw units;
        # apply the same scale (including any mirror) + rotation before
        # subtracting dst's own origin offset.
        transformed_origin = rotation.rotate_vector(src.origin.component_mul(scale_vector))
        translation = transformed_origin - dst.origin

        return Transform(translation=translation, rotation=rotation, scale=scale_vector)


def _quaternion_from_rotation_matrix(m: Matrix4) -> Quaternion:
    """Standard matrix-to-quaternion conversion (Shepperd's method)."""
    v = m.values
    m00, m01, m02 = v[0], v[1], v[2]
    m10, m11, m12 = v[4], v[5], v[6]
    m20, m21, m22 = v[8], v[9], v[10]
    trace = m00 + m11 + m22
    if trace > 0.0:
        s = 0.5 / math.sqrt(trace + 1.0)
        return Quaternion(
            w=0.25 / s,
            x=(m21 - m12) * s,
            y=(m02 - m20) * s,
            z=(m10 - m01) * s,
        )
    if m00 > m11 and m00 > m22:
        s = 2.0 * math.sqrt(1.0 + m00 - m11 - m22)
        return Quaternion(
            w=(m21 - m12) / s,
            x=0.25 * s,
            y=(m01 + m10) / s,
            z=(m02 + m20) / s,
        )
    if m11 > m22:
        s = 2.0 * math.sqrt(1.0 + m11 - m00 - m22)
        return Quaternion(
            w=(m02 - m20) / s,
            x=(m01 + m10) / s,
            y=0.25 * s,
            z=(m12 + m21) / s,
        )
    s = 2.0 * math.sqrt(1.0 + m22 - m00 - m11)
    return Quaternion(
        w=(m10 - m01) / s,
        x=(m02 + m20) / s,
        y=(m12 + m21) / s,
        z=0.25 * s,
    )
