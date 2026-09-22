# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""MapScene <-> Blender collection bridge.

Dispatches whichever fields of a ``MapScene`` are populated to the
matching per-type bridge, and organizes the result into named
sub-collections so a map with thousands of objects stays navigable in
the outliner.
"""

from __future__ import annotations

import dataclasses

import bpy

from blender_io.terrain_bridge import (
    apply_colormap,
    apply_landscape,
    apply_tilemap,
    build_mesh,
    build_water,
)
from blender_io.collision_bridge import build_obstacle_boxes, extract_obstacles
from blender_io.dynamic_bridge import build_dynamic_scene, extract_dynamic_scene
from blender_io.roads_bridge import (
    build_road_curves,
    build_road_surfaces,
    extract_road_network,
    place_road_models,
)
from blender_io.world_bridge import CLASS_PROP, build_object_tree, extract_object_tree
from blender_io.world_bridge import stamped_node_names as _stamped_node_names
from core.objects import ObjectInstance
from core.scene import MapScene
from core.collision import ObstacleSet
from core.roads import RoadNetwork
from core.terrain import HeightmapData
from core.coordinates import CoordinateTransform
from utils.errors import ValidationError
from utils.logging import get_logger

logger = get_logger("blender_io.scene_bridge")

#: Sub-collection names. Also used on export to find what to read back.
TERRAIN_COLLECTION = "Terrain"
OBJECTS_COLLECTION = "Objects"
ROADS_COLLECTION = "Roads"
COLLISION_COLLECTION = "Collision"
DYNAMIC_COLLECTION = "DynamicObjects"


@dataclasses.dataclass
class SceneBuildResult:
    """What ``build_scene`` actually built, so callers can report on it
    without searching the collection tree."""

    terrain_object: bpy.types.Object | None = None
    water_object: bpy.types.Object | None = None
    road_surfaces: int = 0
    sun_object: bpy.types.Object | None = None
    grass_count: int = 0
    object_roots: list[bpy.types.Object] = dataclasses.field(default_factory=list)
    road_curves: list[bpy.types.Object] = dataclasses.field(default_factory=list)
    obstacle_boxes: list[bpy.types.Object] = dataclasses.field(default_factory=list)
    dynamic_objects: list[bpy.types.Object] = dataclasses.field(default_factory=list)


def _expected_segments(curves) -> int:
    """How many segments a full placement would produce: one per node."""
    total = 0
    for obj in curves:
        splines = list(getattr(getattr(obj, "data", None), "splines", []) or [])
        if splines:
            total += len(splines[0].points)
    return total


def _sub_collection(parent: bpy.types.Collection, name: str) -> bpy.types.Collection:
    """Get or create a named child collection under ``parent``."""
    existing = parent.children.get(name) if hasattr(parent.children, "get") else None
    if existing is not None:
        return existing
    created = bpy.data.collections.new(name)
    parent.children.link(created)
    return created


def build_scene(
    map_scene: MapScene,
    collection: bpy.types.Collection,
    *,
    transform: CoordinateTransform | None = None,
    mesh_provider=None,
    resolve_dynamic_model=None,
    resolve_dynamic_parts=None,
    dynamic_assembler=None,
    naming_style: str = "ID_MODEL",
    game_root: str | None = None,
    build_water_surface: bool = True,
    lightmap_time: str = "daytime",
    terrain_detail: bool = True,
    blend_tiles: bool = True,
    textured_roads: bool = True,
    road_models: bool = False,
    import_lighting: bool = True,
    import_grass: bool = True,
) -> SceneBuildResult:
    """Link whatever parts of ``map_scene`` are populated into ``collection``.

    ``transform`` is the shared ``CoordinateTransform``, applied to
    terrain and object placement alike so both land in the same space.
    ``game_root`` is only needed to resolve the terrain's ground
    textures; object textures are resolved by the mesh provider. Anything not populated is skipped silently — the
    "why is this missing" messaging already lives in
    ``map_scene.warnings``.
    """
    transform = transform if transform is not None else CoordinateTransform()
    result = SceneBuildResult()

    if map_scene.terrain is not None:
        terrain_collection = _sub_collection(collection, TERRAIN_COLLECTION)
        terrain_obj = build_mesh(map_scene.terrain, name="ExM_Terrain", transform=transform)
        terrain_collection.objects.link(terrain_obj)
        result.terrain_object = terrain_obj

        if map_scene.colormap is not None:
            # A colormap that does not fit the terrain is reported, not
            # fatal: the geometry is still worth having, and a stretched
            # colour layer would look plausible and be wrong.
            try:
                apply_colormap(terrain_obj, map_scene.colormap)
            except ValidationError as exc:
                map_scene.warnings.append(f"Terrain colour not applied: {exc.message}")

        # The baked map first: it is what the original editor draws, and
        # the tile map on its own gives hard squares of one texture
        # where the reference has blended ground. Tiles remain the
        # fallback for a map that ships without it.
        if not apply_landscape(
            terrain_obj,
            map_scene.map_folder,
            map_scene.tilemap if terrain_detail else None,
            game_root,
            time_of_day=lightmap_time,
            blend=blend_tiles,
        ):
            if map_scene.tilemap is not None:
                apply_tilemap(
                    terrain_obj, map_scene.tilemap, game_root, blend=blend_tiles
                )

        # A map with no watermap has no water, whatever its ground
        # does below the water level.
        if (
            map_scene.water_level is not None
            and map_scene.water_map is not None
            and build_water_surface
        ):
            water = build_water(
                map_scene.terrain,
                map_scene.water_level,
                transform=transform,
                absorption=map_scene.water_absorption,
                water_mask=map_scene.water_map,
            )
            terrain_collection.objects.link(water)
            result.water_object = water

    if map_scene.objects:
        objects_collection = _sub_collection(collection, OBJECTS_COLLECTION)
        # Terrain is passed so object heights (which are stored relative
        # to the ground) resolve against the same surface the terrain
        # mesh was built from.
        result.object_roots = build_object_tree(
            map_scene.objects,
            objects_collection,
            transform=transform,
            terrain=map_scene.terrain,
            mesh_provider=mesh_provider,
            naming_style=naming_style,
        )

    if map_scene.roads is not None and map_scene.roads.chains:
        roads_collection = _sub_collection(collection, ROADS_COLLECTION)
        result.road_curves = build_road_curves(
            map_scene.roads,
            roads_collection,
            transform=transform,
            terrain=map_scene.terrain,
        )
        if textured_roads:
            from formats.exm.roadsets import find_road_sets

            sets = find_road_sets(game_root or "")

            # The road's own model, arrayed along its chain and bent to
            # follow it. Live, so editing a chain reshapes the road —
            # and textured by the model's own unwrap rather than by a
            # guess. The ribbon stands in only where the model cannot
            # be found.
            from blender_io.roads_bridge import build_roads_from_models

            live = build_roads_from_models(
                result.road_curves,
                game_root,
                collection=roads_collection,
                road_sets=sets,
                transform=transform,
            )
            if live:
                result.road_surfaces = live
                placed = _expected_segments(result.road_curves)
            else:
                placed = 0
            if road_models:
                placed = place_road_models(
                    result.road_curves,
                    game_root,
                    collection=roads_collection,
                    road_sets=sets,
                    transform=transform,
                )

            # The ribbon unless every chain got its models. Skipping it
            # on a partial result was worse than either: a map with a
            # handful of segments placed and the rest of its roads gone
            # altogether, which is what "roads are broken" was.
            if placed < _expected_segments(result.road_curves):
                if placed:
                    logger.warning(
                        "only %s of %s road segment(s) could be placed; "
                        "drawing the chains as surfaces instead",
                        placed, _expected_segments(result.road_curves),
                    )
                build_road_surfaces(
                    result.road_curves,
                    game_root,
                    collection=roads_collection,
                    road_sets=sets,
                    terrain=map_scene.terrain,
                    transform=transform,
                )

    if map_scene.grass is not None and import_grass:
        from blender_io.grass_bridge import GRASS_COLLECTION, build_grass

        grass_collection = _sub_collection(collection, GRASS_COLLECTION)
        result.grass_count = build_grass(
            map_scene.grass,
            grass_collection,
            game_root,
            transform=transform,
        )

    # Roads and grass are coloured by the terrain's lightmap at their
    # world position, times two — road.fx and grasstest_ps11.ps, not
    # the ILLUMINATION section. Done after both are built and after the
    # terrain has its lightmap, which is what the sampler reads.
    _tint_by_world_lightmap(result, collection)

    if map_scene.lighting is not None and import_lighting:
        result.sun_object = build_lighting(map_scene.lighting, collection)
        set_world_ambient(getattr(bpy.context, "scene", None), map_scene.lighting)

    if map_scene.obstacles is not None and map_scene.obstacles.boxes:
        collision_collection = _sub_collection(collection, COLLISION_COLLECTION)
        # No terrain passed: obstacle heights are absolute, not
        # terrain-relative (see core/collision.py).
        result.obstacle_boxes = build_obstacle_boxes(
            map_scene.obstacles, collision_collection, transform=transform,
        )

    if map_scene.dynamic_scene is not None and map_scene.dynamic_scene.objects:
        dynamic_collection = _sub_collection(collection, DYNAMIC_COLLECTION)
        result.dynamic_objects = build_dynamic_scene(
            map_scene.dynamic_scene,
            dynamic_collection,
            transform=transform,
            terrain=map_scene.terrain,
            resolve_model=resolve_dynamic_model,
            mesh_provider=mesh_provider,
            resolve_parts=resolve_dynamic_parts,
            assembler=dynamic_assembler,
        )

    return result


def _tint_by_world_lightmap(result, collection) -> None:
    """Apply the lightmap tint to everything in the roads and grass
    collections. A map with no lightmap, or no terrain, gets nothing."""
    from blender_io.grass_bridge import GRASS_COLLECTION
    from blender_io.world_lightmap import from_terrain, tint_objects

    sampler = from_terrain(result.terrain_object)
    if sampler is None:
        return
    for label in (ROADS_COLLECTION, GRASS_COLLECTION):
        for child in getattr(collection, "children", []) or []:
            if getattr(child, "name", "").startswith(label):
                tint_objects(list(_walk_objects(child)), sampler, label)


def _walk_objects(collection):
    for obj in getattr(collection, "objects", []) or []:
        yield obj
    for child in getattr(collection, "children", []) or []:
        yield from _walk_objects(child)


def extract_dynamic_from_collection(
    collection: bpy.types.Collection,
    original,
    *,
    transform: CoordinateTransform | None = None,
    children_for=None,
):
    """Read edited dynamic-object positions back into ``original``.

    Walks the WHOLE map collection, not only the Dynamic
    sub-collection: an object the user creates lands in whatever
    collection is active, and one assigned to this layer there must
    still be written (the world layer learnt the same lesson — see
    extract_objects_from_collection). Imported records live in the
    sub-collection and are reached through it.
    """
    if original is None:
        return None
    if any(child.name.startswith(DYNAMIC_COLLECTION) for child in collection.children):
        return extract_dynamic_scene(
            original, collection, transform=transform, children_for=children_for,
        )
    return original


def confirm_dynamic_written(collection: bpy.types.Collection) -> int:
    """See ``dynamic_bridge.confirm_written``."""
    from blender_io.dynamic_bridge import confirm_written

    return confirm_written(collection)


def extract_obstacles_from_collection(
    collection: bpy.types.Collection,
    *,
    transform: CoordinateTransform | None = None,
) -> ObstacleSet | None:
    """Read collision boxes back out of a built scene collection.

    Returns ``None`` when there's no Collision collection, so a caller
    can tell "this map has no obstacles" apart from "all were deleted".
    """
    for child in collection.children:
        if child.name.startswith(COLLISION_COLLECTION):
            return extract_obstacles(list(child.objects), transform=transform)
    return None


def extract_roads_from_collection(
    collection: bpy.types.Collection,
    *,
    transform: CoordinateTransform | None = None,
    terrain: HeightmapData | None = None,
) -> RoadNetwork | None:
    """Read the road curves back out of a built scene collection.

    Returns ``None`` when there's no Roads collection, so a caller can
    tell "this map has no roads" apart from "all roads were deleted".
    """
    for child in collection.children:
        if child.name.startswith(ROADS_COLLECTION):
            return extract_road_network(
                list(child.objects), transform=transform, terrain=terrain,
            )
    return None


def stamped_node_names(collection: bpy.types.Collection) -> set[str]:
    """See ``world_bridge.stamped_node_names``."""
    return _stamped_node_names(collection)


def extract_objects_from_collection(
    collection: bpy.types.Collection,
    *,
    transform: CoordinateTransform | None = None,
    terrain: HeightmapData | None = None,
    allocator=None,
) -> list[ObjectInstance]:
    """Read the object hierarchy back out of a built scene.

    Walks the WHOLE collection tree rather than only the ``Objects``
    sub-collection. An earlier version scanned just that one, which
    silently lost every object the user created: Blender links a new
    mesh into the *active* collection, not into the add-on's, so an
    object with perfectly correct node properties was never visited,
    never queued and never written — while the assign operator
    reported success.

    Objects from the other sub-collections are not a concern here:
    roads are curves, obstacles and dynamic objects carry their own
    marker properties and none of them carry ``CLASS_PROP``, so
    ``extract_object_tree`` ignores them.

    Only roots are collected — an object whose parent is also a node is
    reached through that parent, and gathering it separately would
    export it twice.
    """
    assigned: list[bpy.types.Object] = []
    for obj in _walk_objects(collection):
        if CLASS_PROP not in obj:
            continue
        # A child of another assigned node is exported via its parent.
        parent = getattr(obj, "parent", None)
        if parent is not None and CLASS_PROP in parent:
            continue
        assigned.append(obj)

    if not assigned:
        return []

    return extract_object_tree(
        assigned, transform=transform, terrain=terrain, allocator=allocator,
    )


def _walk_objects(collection: bpy.types.Collection):
    """Every object in a collection tree, depth first."""
    for obj in collection.objects:
        yield obj
    for child in collection.children:
        yield from _walk_objects(child)


# --- how the scene is displayed ---------------------------------------

#: Blender 3.6 ships with the Filmic view transform, which is built for
#: photographic renders with a wide dynamic range. Game textures are
#: authored in display space and already carry their own contrast, so
#: Filmic pushes them through a second tone curve: brown rock comes out
#: near-white, ground goes muddy, and everything loses saturation. That
#: is the whole difference between the two views this was compared
#: against, and it is a scene setting rather than anything in the data.
DISPLAY_VIEW_TRANSFORM = "Standard"
DISPLAY_LOOK = "None"


def use_display_referred_colours(scene) -> bool:
    """Show textures as authored rather than through a film curve.

    Returns whether the setting was changed, so the caller can say so —
    silently rewriting a colour management setting somebody chose on
    purpose would be worse than leaving it alone.
    """
    settings = getattr(scene, "view_settings", None)
    if settings is None:
        return False

    current = getattr(settings, "view_transform", "")
    if current == DISPLAY_VIEW_TRANSFORM:
        return False

    try:
        settings.view_transform = DISPLAY_VIEW_TRANSFORM
    except (AttributeError, TypeError) as exc:
        logger.debug("could not set the view transform: %s", exc)
        return False

    try:
        settings.look = DISPLAY_LOOK
    except (AttributeError, TypeError):
        pass

    logger.info(
        "view transform %r -> %r: game textures are display-referred and "
        "Filmic washes them out", current or "?", DISPLAY_VIEW_TRANSFORM,
    )
    return True


# --- the map's own lighting -------------------------------------------

SUN_OBJECT_NAME = "ExM_Sun"


def build_lighting(lighting, collection) -> "bpy.types.Object | None":
    """Put the map's sun in the scene and set the ambient from it.

    Every level states where its sun is and what colour its light is,
    and none of it was being read — the scene was lit by whatever
    Blender defaults to, which is why the two views differed in warmth
    long after the textures matched.

    Models and the landscape are given different colours by the map,
    and that is deliberate: the ground carries a baked lightmap
    already, so its share of the sun has been taken out of ``LS_*``.
    The lamp uses the model figures, which are the ones describing
    light that has not been baked in anywhere.
    """
    if lighting is None:
        return None

    existing = bpy.data.objects.get(SUN_OBJECT_NAME)
    if existing is not None:
        return existing

    try:
        lamp = bpy.data.lights.new(SUN_OBJECT_NAME, type="SUN")
        lamp.color = lighting.model_diffuse
        lamp.energy = max(0.05, lighting.sun_strength()) * 4.0
        # The sun in these maps sits high and the shadows in the
        # reference are soft-edged, not pinpoint.
        lamp.angle = 0.05
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.debug("could not create the sun: %s", exc)
        return None

    obj = bpy.data.objects.new(SUN_OBJECT_NAME, lamp)
    obj.rotation_euler = lighting.sun_rotation()

    if collection is not None:
        try:
            collection.objects.link(obj)
        except (AttributeError, RuntimeError, TypeError) as exc:
            logger.debug("could not link the sun: %s", exc)

    logger.info(
        "sun: azimuth %.0f, %.0f above the horizon, colour %.2f %.2f %.2f "
        "(MODEL_DIFFUSE)",
        lighting.azimuth, lighting.ascension, *lighting.model_diffuse,
    )
    if max(lighting.model_diffuse) < 0.05:
        # Said out loud rather than quietly corrected. Several shipped
        # maps declare MODEL_DIFFUSE as 0 0 0 — the shipped r1m1 among
        # them — and the map means it: the engine lights those scenes
        # from the baked lightmap instead. A black sun in Blender is
        # then faithful and looks exactly like a broken import, so the
        # line that tells them apart has to be here.
        logger.warning(
            "MODEL_DIFFUSE is 0 0 0 on this map: the sun contributes no "
            "light and the ground is lit by its baked lightmap. That is "
            "what the map says, not a failed read."
        )
    return obj


def set_world_ambient(scene, lighting) -> bool:
    """Take the world's background from the map's ambient colour."""
    if lighting is None or scene is None:
        return False

    world = getattr(scene, "world", None)
    if world is None:
        try:
            world = bpy.data.worlds.new("ExM_World")
            scene.world = world
        except (AttributeError, RuntimeError, TypeError) as exc:
            logger.debug("could not make a world: %s", exc)
            return False

    try:
        world.use_nodes = True
        for node in world.node_tree.nodes:
            if getattr(node, "type", "") == "BACKGROUND":
                node.inputs["Color"].default_value = (*lighting.model_ambient, 1.0)
                node.inputs["Strength"].default_value = 1.0
                logger.info(
                    "ambient: %.2f %.2f %.2f", *lighting.model_ambient
                )
                return True
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not set the ambient: %s", exc)

    return False
