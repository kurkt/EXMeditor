# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Static collision obstacles (``static_obstacles.xml``).

Confirmed structure from real map data: a flat list of ``<Box>``
elements, each an oriented bounding box given by ``min``/``max``
(extents in the box's own local space), ``origin`` (its centre in
world space) and ``rotation`` (a unit quaternion).

Height note — this differs from ``world.xml`` and ``LevelRoads.xml``:
obstacle ``origin`` Y is an **absolute** world height, not an offset
above the terrain. Verified against real data by sampling terrain
height under each of the 165 boxes: ground-level obstacles came out
within ±0.2 of the terrain surface, which only holds if the stored
value is already absolute. Applying terrain-relative resolution here
would shift every box by the local ground height.
"""

from __future__ import annotations

import dataclasses

from utils.math import Vector3


@dataclasses.dataclass
class ObstacleBox:
    """One oriented bounding box.

    ``min``/``max`` are extents in the box's own local space (mostly
    symmetric about the origin — 159 of 165 boxes on the reference map
    — but not always, so both corners are kept rather than collapsing
    them to a half-size).

    ``raw_rotation`` stays an opaque 4-float tuple rather than a
    ``Quaternion``, matching how ``world.xml`` rotations are handled:
    the component order is the same (x, y, z, w) reading that's rated
    *Вероятно* in the world.xml spec, and keeping it opaque means one
    place to correct if that turns out wrong.
    """

    min_corner: Vector3
    max_corner: Vector3
    origin: Vector3
    raw_rotation: tuple[float, ...]
    raw_attrs: dict[str, str] = dataclasses.field(default_factory=dict)

    def size(self) -> Vector3:
        """Full extent along each local axis."""
        return Vector3(
            self.max_corner.x - self.min_corner.x,
            self.max_corner.y - self.min_corner.y,
            self.max_corner.z - self.min_corner.z,
        )

    def local_centre(self) -> Vector3:
        """Centre of the box within its own local space.

        Usually (0, 0, 0) since most boxes are symmetric, but not
        guaranteed — an asymmetric box's visual centre is offset from
        its origin, and ignoring that would draw it in the wrong place.
        """
        return Vector3(
            (self.min_corner.x + self.max_corner.x) * 0.5,
            (self.min_corner.y + self.max_corner.y) * 0.5,
            (self.min_corner.z + self.max_corner.z) * 0.5,
        )


@dataclasses.dataclass
class ObstacleSet:
    """Every static collision box on a map."""

    boxes: list[ObstacleBox] = dataclasses.field(default_factory=list)

    def __len__(self) -> int:
        return len(self.boxes)
