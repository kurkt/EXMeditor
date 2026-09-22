# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""ObstacleSet <-> Blender collision visualization.

Each box becomes an Empty with ``empty_display_type='CUBE'``, scaled to
the box's half-extents. An Empty rather than a real mesh because these
are collision volumes, not renderable geometry — a mesh per box would
generate throwaway vertex data for hundreds of boxes and invite
accidental editing of geometry that has no meaning in the format.

Blender's CUBE-display Empty draws a cube spanning ±``empty_display_size``
along each axis, scaled by the object's scale — so setting scale to the
box's half-extents reproduces its true dimensions.

Height note: obstacle origins are **absolute** world heights, unlike
``world.xml`` objects and road nodes whose Y is terrain-relative
(confirmed — see ``core/collision.py``). No terrain is needed or
accepted here.
"""

from __future__ import annotations

import json

import bpy

from core.collision import ObstacleBox, ObstacleSet
from core.coordinates import CoordinateTransform
from utils.math import Vector3

OBSTACLE_PROP = "exm_obstacle"           # marks an object as an imported obstacle
MIN_CORNER_PROP = "exm_obstacle_min"     # JSON [x, y, z], local-space extents
MAX_CORNER_PROP = "exm_obstacle_max"     # JSON [x, y, z]
RAW_ATTRS_PROP = "exm_obstacle_raw"      # JSON dict of unmodeled attributes

# Blender's CUBE empty is drawn at ±display_size; using 1.0 makes the
# object's scale equal the box's half-extents directly, so the numbers
# in the N-panel match the format's own values.
_DISPLAY_SIZE = 1.0


def build_obstacle_boxes(
    obstacles: ObstacleSet,
    collection: bpy.types.Collection,
    *,
    transform: CoordinateTransform | None = None,
) -> list[bpy.types.Object]:
    """Create one Empty per collision box."""
    transform = transform if transform is not None else CoordinateTransform()
    created: list[bpy.types.Object] = []

    for index, box in enumerate(obstacles.boxes):
        created.append(_build_box(box, index, collection, transform))

    return created


def _build_box(
    box: ObstacleBox,
    index: int,
    collection: bpy.types.Collection,
    transform: CoordinateTransform,
) -> bpy.types.Object:
    obj = bpy.data.objects.new(f"ExM_Obstacle_{index:03d}", None)
    obj.empty_display_type = "CUBE"
    obj.empty_display_size = _DISPLAY_SIZE

    # The box's visual centre is its origin plus its local centre —
    # zero for the symmetric majority, but not for all of them.
    local_centre = box.local_centre()
    world_centre = Vector3(
        box.origin.x + local_centre.x,
        box.origin.y + local_centre.y,
        box.origin.z + local_centre.z,
    )
    position = transform.game_to_blender_position(world_centre)
    obj.location = position.as_tuple()

    if len(box.raw_rotation) == 4:
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = transform.game_to_blender_rotation(box.raw_rotation)

    # Half-extents, axis-swapped to match the position convention
    # (game Y is height -> Blender Z) and scaled the same way.
    size = box.size()
    obj.scale = (
        size.x * 0.5 * transform.xy_scale,
        size.z * 0.5 * transform.xy_scale,
        size.y * 0.5 * transform.height_scale,
    )

    obj[OBSTACLE_PROP] = 1
    obj[MIN_CORNER_PROP] = json.dumps(list(box.min_corner.as_tuple()))
    obj[MAX_CORNER_PROP] = json.dumps(list(box.max_corner.as_tuple()))
    if box.raw_attrs:
        obj[RAW_ATTRS_PROP] = json.dumps(box.raw_attrs)

    collection.objects.link(obj)
    return obj


def extract_obstacles(
    objects: list[bpy.types.Object],
    *,
    transform: CoordinateTransform | None = None,
) -> ObstacleSet:
    """Read collision boxes back out of Blender.

    Objects without ``OBSTACLE_PROP`` are skipped — they weren't
    imported as obstacles.

    Local ``min``/``max`` extents are restored from the stored custom
    properties rather than re-derived from the object's scale: the two
    agree for an untouched box, but the stored values also carry the
    asymmetry of the 6-in-165 boxes whose extents aren't centred, which
    scale alone cannot express.
    """
    transform = transform if transform is not None else CoordinateTransform()
    obstacles = ObstacleSet()

    for obj in objects:
        if OBSTACLE_PROP not in obj:
            continue
        obstacles.boxes.append(_extract_box(obj, transform))

    return obstacles


def _extract_box(obj: bpy.types.Object, transform: CoordinateTransform) -> ObstacleBox:
    min_values = json.loads(obj.get(MIN_CORNER_PROP) or "[0,0,0]")
    max_values = json.loads(obj.get(MAX_CORNER_PROP) or "[0,0,0]")
    raw_attrs_json = obj.get(RAW_ATTRS_PROP)

    min_corner = Vector3(*min_values)
    max_corner = Vector3(*max_values)

    world_centre = transform.blender_to_game_position(Vector3(*obj.location))
    # Undo the local-centre offset applied on import, so `origin` means
    # the same thing it did in the source file.
    local_centre = Vector3(
        (min_corner.x + max_corner.x) * 0.5,
        (min_corner.y + max_corner.y) * 0.5,
        (min_corner.z + max_corner.z) * 0.5,
    )
    origin = Vector3(
        world_centre.x - local_centre.x,
        world_centre.y - local_centre.y,
        world_centre.z - local_centre.z,
    )

    raw_rotation = transform.blender_to_game_rotation(tuple(obj.rotation_quaternion))

    return ObstacleBox(
        min_corner=min_corner,
        max_corner=max_corner,
        origin=origin,
        raw_rotation=raw_rotation,
        raw_attrs=json.loads(raw_attrs_json) if raw_attrs_json else {},
    )
