# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The single source of truth for game <-> Blender spatial conversion.

**Every** spatial format must convert positions through this module —
terrain, world objects, roads, obstacles, paths, and anything added
later. Two formats each doing "obviously correct" conversion inline is
exactly how terrain and ``world.xml`` ended up on different planes:
both looked right in isolation, and nothing compared them.

Axis convention
---------------
Ex Machina uses a **Y-up** convention; Blender is **Z-up**. Confirmed
from real map data rather than assumed:

- ``world.xml`` object positions (``org="X Y Z"``) have X and Z
  spanning 0..~3878 game units, while Y spans only ±82. Two large
  horizontal axes and one small vertical one: **Y is height**.
- The terrain grid indexes the same two horizontal axes (column, row),
  with the sample value as height.

So the mapping is::

    game (X, Y, Z)  ->  blender (X, Z, Y)
    game X  ->  blender X   (horizontal)
    game Y  ->  blender Z   (height, Y-up -> Z-up)
    game Z  ->  blender Y   (horizontal)

and the inverse swaps the same two components back.

Scale
-----
Both are **1.0**, and that is measured rather than tuned.

A tile cell is 32 units. Taken across seven maps at three different
grid sizes, the span of every object in ``world.xml`` divided by the
grid size lands on it::

    r1m1   6605 objects   grid 128   4106 x 3902   32.1 units per cell
    r1m2  10416 objects   grid 256   7942 x 10542  31.0
    r2m1   9308 objects   grid 256   8228 x 8081   32.1
    r0m0   2746 objects   grid  64   1997 x 1656   31.2

and no object crosses the edge: r1m1 reaches X 4010 against a limit of
128 x 32 = 4096.

Heights are absolute world Y in float32, spaced 8 units apart — a
quarter of a cell, which is why ``displace.bin`` is ``4G x 4G``.
Checked against 4951 objects carrying an absolute Y in
``dynamicscene.xml``: sampling ``H[z * N + x]`` gives a median error of
**0.00** and 99.2% of objects within 5 units, while ``H[x * N + z]``
gives −12.49 and 13.2%. A median of exactly zero confirms the height
format, the 8-unit step and the 32-unit cell at once.

So nothing needs scaling: read the height, use it. Earlier versions
carried 1.25 and 1.5, arrived at by adjusting values until the result
looked right in game. Those numbers described nothing.

They remain parameters rather than constants because a mod may change
the grid, not because the engine's own figures are in doubt.

No ``bpy`` import — usable from a CLI, tests, or a converter.
"""

from __future__ import annotations

import dataclasses

from utils.errors import ValidationError
from utils.math import Vector3

#: World-space size of one terrain grid cell, in game units.
#:
#: **Confirmed** by three independent measurements agreeing on 8:
#:
#: * ``world.xml`` object positions span 0..3878 game units;
#: * ``.ssl``'s ``MAXSAFEX``/``MAXSAFEY`` are 4056;
#: * a 512x512 terrain grid at cell size 8 spans 0..4088.
#:
#: With the previous placeholder of 1.0 the terrain covered only
#: 0..511 game units while objects covered 0..3878 — an ~8x mismatch
#: that put terrain and objects on visibly different planes.
TERRAIN_CELL_SIZE = 8.0

#: Calibrated game -> Blender scale, verified by eye in-game.
#:
#: These belong here rather than in the add-on because they are tied to
#: ``TERRAIN_CELL_SIZE``: the two must change together. XY was
#: originally calibrated as 10.0 while cell size was still a
#: placeholder 1.0 (so one grid cell spanned 10 Blender units). When
#: cell size was corrected to 8.0, leaving XY at 10.0 made the terrain
#: 8x wider with unchanged heights — the map imported almost flat.
#:
#: 1.25 = 10.0 / 8.0 restores exactly the proportions already verified
#: in-game, while keeping terrain aligned with object and road
#: coordinates. The invariant to preserve is
#: ``TERRAIN_CELL_SIZE * DEFAULT_XY_SCALE == 10.0`` — one grid cell per
#: 10 Blender units.
#: Measured, not tuned. See the module docstring.
DEFAULT_XY_SCALE = 1.0
DEFAULT_HEIGHT_SCALE = 1.0

#: Units across one tile cell, and the spacing of the heightfield.
UNITS_PER_TILE_CELL = 32.0
UNITS_PER_HEIGHT_SAMPLE = 8.0


@dataclasses.dataclass(frozen=True)
class CoordinateTransform:
    """Converts positions between game space and Blender space.

    Combines the fixed axis convention (Y-up -> Z-up), the calibrated
    scale factors, and an optional origin offset. Construct one per
    import/export and pass it to every bridge, so all formats share
    identical placement.

    ``origin_offset`` is subtracted from game positions before scaling,
    which is what lets the map sit around Blender's origin instead of
    entirely in the +X/+Y quadrant (a 4096-unit map otherwise has its
    centre thousands of units from the origin, which makes orbiting,
    clipping and precision all worse). It is applied in GAME units and
    reversed exactly on export, so it never reaches the file.
    """

    xy_scale: float = 1.0
    height_scale: float = 1.0
    origin_offset: Vector3 = dataclasses.field(default_factory=Vector3.zero)

    def __post_init__(self) -> None:
        if self.xy_scale == 0:
            raise ValidationError("xy_scale must not be 0")
        if self.height_scale == 0:
            raise ValidationError("height_scale must not be 0")

    def game_to_blender_position(self, position: Vector3) -> Vector3:
        """Convert a game-space position to Blender space.

        Applies the origin offset, the axis swap (game Y-up -> Blender
        Z-up) and the scale factors.
        """
        x = position.x - self.origin_offset.x
        y = position.y - self.origin_offset.y
        z = position.z - self.origin_offset.z
        return Vector3(
            x * self.xy_scale,
            z * self.xy_scale,       # game Z (horizontal) -> Blender Y
            y * self.height_scale,   # game Y (height)     -> Blender Z
        )

    def blender_to_game_position(self, position: Vector3) -> Vector3:
        """Convert a Blender-space position back to game space.

        The exact inverse of :meth:`game_to_blender_position`.
        """
        return Vector3(
            position.x / self.xy_scale + self.origin_offset.x,
            position.z / self.height_scale + self.origin_offset.y,  # Blender Z -> game Y
            position.y / self.xy_scale + self.origin_offset.z,      # Blender Y -> game Z
        )

    def game_to_blender_offset(self, offset: Vector3) -> Vector3:
        """Convert a parent-relative offset (a delta, not a point).

        Unlike a position, a delta must NOT have the origin offset
        applied — shifting the whole map moves points, not the
        distances between them. Getting this wrong would displace every
        nested child by the centring amount.
        """
        return Vector3(
            offset.x * self.xy_scale,
            offset.z * self.xy_scale,
            offset.y * self.height_scale,
        )

    def blender_to_game_offset(self, offset: Vector3) -> Vector3:
        """Inverse of :meth:`game_to_blender_offset`."""
        return Vector3(
            offset.x / self.xy_scale,
            offset.z / self.height_scale,
            offset.y / self.xy_scale,
        )

    @staticmethod
    def centre_offset_for_grid(
        grid_side: int, cell_size: float = TERRAIN_CELL_SIZE,
    ) -> Vector3:
        """The offset that puts a terrain grid's centre on the origin.

        Height is deliberately left at 0: shifting the map vertically
        would separate it from the water level and from object heights,
        which are meaningful absolute values, whereas the horizontal
        position of the map as a whole is arbitrary.
        """
        half = (grid_side - 1) * cell_size * 0.5
        return Vector3(half, 0.0, half)

    @property
    def is_uniform(self) -> bool:
        """True when horizontal and vertical scale match.

        Only then is a game rotation expressible as a Blender rotation.
        With different factors the space is scaled non-uniformly, which
        shears rotated geometry — harmless for terrain (never rotated)
        but visible on models.
        """
        return abs(self.xy_scale - self.height_scale) < 1e-9

    def game_to_blender_rotation(
        self, raw_rotation: tuple[float, ...],
    ) -> tuple[float, float, float, float]:
        """Convert a game rotation quaternion to Blender's, ``(w, x, y, z)``.

        Rotations need the SAME axis change as positions, and getting
        this wrong is not subtle: a rotation about the game's vertical
        axis becomes a rotation about a horizontal one, so every
        rotated object tips over instead of turning. That stayed
        invisible while objects were imported as Empties and became
        obvious the moment real geometry appeared.

        The conversion: the axis vector is swapped like any vector
        (game Y and Z trade places) and negated, because swapping two
        axes mirrors the space and so reverses the rotation direction.
        Found by searching every permutation and sign combination
        against rotations applied in game space and then converted —
        not derived on paper, because the paper derivation gave a
        formula that was right for vertical-axis turns and wrong for
        the other two.

        NOTE: an exact conversion exists only when ``xy_scale`` and
        ``height_scale`` are equal. Under a non-uniform scale a
        rotation is not a rotation any more — the geometry is sheared —
        and no quaternion can express it. See ``is_uniform``.

        Input is ``(x, y, z, w)`` — the order used by both ``world.xml``
        and the engine's own script calls. Output is ``(w, x, y, z)``,
        Blender's order.
        """
        if len(raw_rotation) != 4:
            raise ValidationError(
                f"rotation must have 4 components, got {len(raw_rotation)}"
            )
        x, y, z, w = raw_rotation
        return (w, -x, -z, -y)

    def blender_to_game_rotation(
        self, quaternion: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        """Convert a Blender ``(w, x, y, z)`` quaternion back to game
        ``(x, y, z, w)``. Exact inverse of :meth:`game_to_blender_rotation`."""
        if len(quaternion) != 4:
            raise ValidationError(
                f"quaternion must have 4 components, got {len(quaternion)}"
            )
        w, x, y, z = quaternion
        return (-x, -z, -y, w)

    def terrain_grid_step(self, cell_size: float = TERRAIN_CELL_SIZE) -> float:
        """Blender-space distance between adjacent terrain grid samples.

        Terrain is placed by grid index rather than by a game-space
        position, so it needs this instead of a position conversion —
        but it must use the same ``xy_scale``, which is why it lives
        here rather than in the terrain bridge.
        """
        return cell_size * self.xy_scale

    def game_height_to_blender(self, height: float) -> float:
        """Convert a bare height value (e.g. a terrain sample) to Blender Z."""
        return height * self.height_scale

    def blender_height_to_game(self, height: float) -> float:
        """Inverse of :meth:`game_height_to_blender`."""
        return height / self.height_scale
