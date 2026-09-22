# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""ObjectInstance tree <-> Blender Empty hierarchy.

Objects become Empties rather than meshes: resolving an ``id`` to real
geometry needs a solved ``.gam`` vertex format, which is deliberately
out of scope (see the v0.2 plan). Empties carry the full placement
data, so moving/rotating/deleting objects — the actual point of a map
editor — works today without any mesh support.

Blender's parenting maps onto ``world.xml``'s nesting directly: a
child object's transform in Blender is already parent-relative, which
is exactly what ``orgRel="0"`` means (spec §5), so nested nodes need
no coordinate conversion of their own.

Every SDK-owned attribute is stashed as a custom property prefixed
``exm_``, so export can reproduce the source exactly — including
knowing which attributes were *absent* originally, which matters for
lossless write-back (spec §11).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import bpy

from core.objects import ObjectInstance
from blender_io.object_rotation import object_rotation
from core.coordinates import CoordinateTransform
from core.naming import STYLE_ID_MODEL, object_name
from core.terrain import HeightmapData
from utils.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from blender_io.mesh_provider import MeshProvider
from utils.math import Vector3

# Custom-property names. All prefixed `exm_`, per the v0.2 plan's
# naming-convention item — one shared prefix across every bridge
# module rather than each inventing its own scheme.
CLASS_PROP = "exm_class"

#: Node classes whose ``id`` names something other than a model.
#:
#: ``SgSoundSourceNode`` carries a sound — ``S_WIND_GRASS`` and the
#: like, 84 of them on the sample map. Looking those up in the model
#: catalogue finds nothing, which is correct, and then reports 84
#: models as missing, which is not: it buries the models that really
#: are missing under noise.
CLASSES_WITHOUT_MODELS = frozenset({"SgSoundSourceNode"})
ASSET_ID_PROP = "exm_id"
ORG_REL_PROP = "exm_org_rel"
SKIN_PROP = "exm_skin"
CAST_SHADOW_PROP = "exm_cast_shadow"
NDM_ACTION_PROP = "exm_ndm_action"
RAW_ATTRS_PROP = "exm_raw_attrs"
NEW_OBJECT_PROP = "exm_new"
#: The name a node had when imported. Kept so re-assigning an imported
#: object doesn't renumber it — the map's other files and scripts refer
#: to nodes by name.
ORIGINAL_NAME_PROP = "exm_original_name"
HAD_ORG_PROP = "exm_had_org"
#: The original terrain-relative Y offset of a top-level object, kept
#: so export can restore it without re-deriving it from the terrain
#: (which the user may have edited in the meantime).
GROUND_OFFSET_PROP = "exm_ground_offset"
HAD_ROTATION_PROP = "exm_had_rotation"
HAD_SCALE_PROP = "exm_had_scale"

# Empty display style per node class — purely cosmetic, to make the
# outliner/viewport readable at a glance. Sound sources and gameplay
# units look different from plain decoration; nothing depends on this.
_DISPLAY_TYPE = {
    "SgNode": "PLAIN_AXES",
    "SgAnimatedModelNode": "CUBE",
    "SgSoundSourceNode": "SPHERE",
    "SgGameUnitNode": "ARROWS",
}


#: How far a value may drift from the identity before it counts as an
#: edit. Blender stores transforms as float32, so a value the user
#: never touched can differ from exactly 1.0 in the last bits;
#: anything smaller than this is treated as untouched so that
#: re-exporting an unmodified map doesn't start adding attributes.
_IDENTITY_EPSILON = 1e-6


def _is_identity_scale(scale) -> bool:
    return all(abs(component - 1.0) < _IDENTITY_EPSILON for component in scale)


def _is_identity_rotation(quaternion) -> bool:
    w, x, y, z = quaternion
    return (
        abs(abs(w) - 1.0) < _IDENTITY_EPSILON
        and abs(x) < _IDENTITY_EPSILON
        and abs(y) < _IDENTITY_EPSILON
        and abs(z) < _IDENTITY_EPSILON
    )


def build_object_tree(
    objects: list[ObjectInstance],
    collection: bpy.types.Collection,
    *,
    transform: CoordinateTransform | None = None,
    terrain: HeightmapData | None = None,
    mesh_provider: "MeshProvider | None" = None,
    naming_style: str = STYLE_ID_MODEL,
) -> list[bpy.types.Object]:
    """Create a Blender Empty hierarchy mirroring ``objects``.

    ``transform`` is the shared ``CoordinateTransform`` — the same one
    terrain import uses, so objects land in the same space rather than
    on a different plane.

    ``terrain``, when given, resolves top-level object heights: their
    stored Y is an offset ABOVE the ground surface, not an absolute
    height, so without the terrain they would all sit near Z=0 while
    the terrain sits at its real elevation.

    Returns the top-level Blender objects created.
    """
    transform = transform if transform is not None else CoordinateTransform()
    created: list[bpy.types.Object] = []
    for instance in objects:
        created.append(
            _build_one(
                instance, collection, transform, terrain, mesh_provider,
                parent=None, is_top_level=True, naming_style=naming_style,
            )
        )
    return created


def _build_one(
    instance: ObjectInstance,
    collection: bpy.types.Collection,
    transform: CoordinateTransform,
    terrain: HeightmapData | None,
    mesh_provider: "MeshProvider | None",
    parent: bpy.types.Object | None,
    is_top_level: bool,
    naming_style: str = STYLE_ID_MODEL,
) -> bpy.types.Object:
    # Real geometry when the model resolves; an Empty otherwise, so an
    # unresolvable id still shows the object's placement rather than
    # dropping it from the scene entirely.
    mesh = None
    if (
        mesh_provider is not None
        and instance.asset_id
        and instance.node_class not in CLASSES_WITHOUT_MODELS
    ):
        mesh = mesh_provider.get_mesh(instance.asset_id)

    # A readable name carrying the node id and the model it draws.
    # Only for the user's benefit — export reads the real node name
    # from ORIGINAL_NAME_PROP, since Blender renames duplicates and
    # users rename things.
    display_name = object_name(
        instance.name, instance.node_class, instance.asset_id, naming_style,
    )
    obj = bpy.data.objects.new(display_name, mesh)
    if mesh is None:
        obj.empty_display_type = _DISPLAY_TYPE.get(instance.node_class, "PLAIN_AXES")
        obj.empty_display_size = 2.0

    if instance.org is not None:
        if is_top_level:
            # Top-level positions are world-space (orgRel=1), and their
            # Y is a height OFFSET above the terrain surface, not an
            # absolute height — resolve it against the terrain so
            # objects sit on the ground rather than at height ~0 while
            # the terrain is at 227..541.
            org = instance.org
            if terrain is not None:
                ground = terrain.sample_at_world(org.x, org.z)
                org = Vector3(org.x, ground + org.y, org.z)
                obj[GROUND_OFFSET_PROP] = instance.org.y
            position = transform.game_to_blender_position(org)
        else:
            # Nested positions are parent-relative (orgRel=0): a delta,
            # not a point, and Blender applies the parent transform
            # itself — so only the axis/scale conversion applies.
            position = transform.game_to_blender_offset(instance.org)
        obj.location = position.as_tuple()

    if instance.raw_rotation is not None and len(instance.raw_rotation) == 4:
        # Through the shared transform: rotations need the same axis
        # change as positions, or a vertical-axis turn becomes a
        # horizontal one and the object tips over.
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = transform.game_to_blender_rotation(instance.raw_rotation)

    if instance.scale is not None:
        obj.scale = instance.scale.as_tuple()

    # --- provenance, for lossless export ---
    obj[CLASS_PROP] = instance.node_class
    if instance.asset_id is not None:
        obj[ASSET_ID_PROP] = instance.asset_id
    if instance.org_rel is not None:
        obj[ORG_REL_PROP] = 1 if instance.org_rel else 0
    if instance.skin is not None:
        obj[SKIN_PROP] = instance.skin
    if instance.cast_shadow is not None:
        obj[CAST_SHADOW_PROP] = 1 if instance.cast_shadow else 0
    if instance.ndm_action is not None:
        obj[NDM_ACTION_PROP] = instance.ndm_action
    if instance.raw_attrs:
        obj[RAW_ATTRS_PROP] = json.dumps(instance.raw_attrs)
    # Blender always has a location, a rotation and a scale, so "was
    # this attribute present in the source?" can't be recovered from the
    # object alone — record it explicitly, or export would add
    # attributes that weren't there originally. Confirmed to matter:
    # 84 SgSoundSourceNode nodes in a real map carry no `org` at all.
    obj[ORIGINAL_NAME_PROP] = instance.name
    obj[HAD_ORG_PROP] = 1 if instance.org is not None else 0
    obj[HAD_ROTATION_PROP] = 1 if instance.raw_rotation is not None else 0
    obj[HAD_SCALE_PROP] = 1 if instance.scale is not None else 0

    collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent

    for child in instance.children:
        _build_one(
            child, collection, transform, terrain, mesh_provider,
            parent=obj, is_top_level=False, naming_style=naming_style,
        )

    return obj


def stamped_node_names(collection: bpy.types.Collection) -> set[str]:
    """Every node name stamped on an object under ``collection``.

    Handed to the allocator as reserved names: a stamp may name a node
    the file does not have yet, from an export that failed after
    allocating.
    """
    names: set[str] = set()
    pending = [collection]
    while pending:
        current = pending.pop()
        for obj in current.objects:
            name = obj.get(ORIGINAL_NAME_PROP)
            if name:
                names.add(str(name))
        pending.extend(current.children)
    return names


def extract_object_tree(
    roots: list[bpy.types.Object],
    *,
    transform: CoordinateTransform | None = None,
    terrain: HeightmapData | None = None,
    allocator=None,
) -> list[ObjectInstance]:
    """Read a Blender Empty hierarchy back into ObjectInstances.

    The exact inverse of ``build_object_tree``: attributes absent in
    the original (tracked via ``exm_had_*`` custom properties) stay
    absent, so the written file doesn't gain fields the source never
    had.

    Objects lacking ``exm_class`` are skipped: they are not map nodes.
    An object created in Blender becomes one through the Assign
    operator, which stamps the class and model id; without that,
    silently inventing a class would write something the game may not
    accept.
    """
    transform = transform if transform is not None else CoordinateTransform()
    instances = [
        _extract_one(obj, transform, terrain, True, allocator)
        for obj in roots if CLASS_PROP in obj
    ]
    _warn_about_buried_objects(instances)
    return instances


logger = get_logger("blender_io.world")

#: The scale a placed node may carry before it is almost certainly a
#: mistake. MEASURED over every shipped world.xml: 24 202 of 36 338
#: nodes carry a scale attribute, all of them uniform, and every value
#: lies in 0.6 .. 2.4. The SDK-written maps had 258, 345, 1890, 5352
#: and -831 — a Blender object scaled to size, exported as a node
#: scale on top of geometry that already had it baked in.
PLAUSIBLE_SCALE = (0.25, 4.0)

#: Beyond this from the terrain surface, a node is almost certainly a
#: mistake rather than a choice.
#:
#: MEASURED over every ``world.xml`` in the game — 34368 ground-relative
#: nodes across 28 maps::
#:
#:     exactly 0.0        28493   82.9%
#:     within -20 .. 200          98.8%
#:     below -50             73
#:     above 200              2
#:
#: 150 keeps every one of those 73 outliers reportable while never
#: firing on the 98.8%.
PLAUSIBLE_GROUND_OFFSET = 150.0


def _warn_about_buried_objects(instances) -> None:
    """Flag a node written far above or below the terrain.

    ``org`` Y on a top-level node is a height ABOVE THE GROUND, not an
    absolute height, and the terrain itself sits at 227..541 in Blender
    units. An object created in Blender at Z near zero is therefore
    some four hundred units UNDERGROUND, and the export records that
    faithfully.

    Measured, not guessed: an icosphere modelled at what looked like
    ground level exported as ``org="2044.000 -396.415 2044.000"`` and
    was nowhere to be found in the game's editor, while all 1626 nodes
    of the shipped map lie within 150 units of the surface. The file
    was correct, the geometry was correct, the registration was
    correct — the object was simply buried, and nothing said so.
    """
    buried = []
    for instance in instances:
        if instance.org is None:
            continue
        if abs(instance.org.y) <= PLAUSIBLE_GROUND_OFFSET:
            continue
        buried.append(instance)
        logger.warning(
            "%s (%s) is written %.0f units %s the terrain. Height in world.xml "
            "is measured FROM THE GROUND, and the terrain sits well above zero "
            "in Blender — an object placed near Z=0 ends up underground and "
            "will not be visible in the game or its editor. Move it onto the "
            "terrain mesh in Blender before exporting.",
            instance.name,
            instance.asset_id or "no model",
            abs(instance.org.y),
            "below" if instance.org.y < 0 else "above",
        )

    oversized = []
    for instance in instances:
        scale = instance.scale
        if scale is None:
            continue
        parts = scale.as_tuple()
        if all(PLAUSIBLE_SCALE[0] <= v <= PLAUSIBLE_SCALE[1] for v in parts):
            continue
        oversized.append(instance)
        logger.warning(
            "%s (%s) is written with scale %s. Shipped maps never leave "
            "0.6 .. 2.4; a model scaled to size in Blender and then written "
            "out with Create Model already has that scale in its geometry, "
            "and the game applies the node's scale ON TOP. Reset the "
            "object's scale (Alt+S) if the model itself was made at this "
            "size.",
            instance.name, instance.asset_id or "no model",
            " ".join(f"{v:.3f}" for v in parts),
        )

    # One line at the end, because a warning per object gets lost among
    # everything else an export reports — and this one is the difference
    # between a model that is in the map and a model that is not.
    if oversized:
        logger.warning(
            "%d object(s) carry a scale no shipped map uses: %s",
            len(oversized),
            ", ".join(
                f"{i.asset_id or i.name} (x{max(abs(v) for v in i.scale.as_tuple()):.0f})"
                for i in oversized[:6]
            ) + (f" and {len(oversized) - 6} more" if len(oversized) > 6 else ""),
        )
    if buried:
        logger.warning(
            "%d object(s) will not be visible: %s. Everything else about "
            "them is correct — they are simply buried. Select them, run "
            "Assign ExMachina Node again with 'Place On Terrain' on, and "
            "export.",
            len(buried),
            ", ".join(
                f"{i.asset_id or i.name} ({i.org.y:+.0f})" for i in buried[:6]
            ) + (f" and {len(buried) - 6} more" if len(buried) > 6 else ""),
        )


def _extract_one(
    obj: bpy.types.Object,
    transform: CoordinateTransform,
    terrain: HeightmapData | None,
    is_top_level: bool,
    allocator=None,
) -> ObjectInstance:
    node_class = obj[CLASS_PROP]

    # An imported node keeps the name it came in with: other map files
    # and scripts refer to nodes by name, so renaming one on export
    # would break those references. Objects created in Blender get a
    # name from the map's own LastId sequence instead — a Blender
    # object name like "Cube.003" is not a valid node name.
    name = obj.get(ORIGINAL_NAME_PROP)
    if allocator is not None:
        if name and not allocator.claim(name):
            # A duplicate: Shift+D copied the stamp along with the
            # rest of the properties. The copy is a new node.
            logger.info(
                "%s carries the node name %s already exported by another "
                "object; it is a duplicate and gets a name of its own",
                obj.name, name,
            )
            name = None
        if not name:
            name = allocator.allocate()
            allocator.claim(name)
            # Stamp it, so the next export writes the SAME node rather
            # than allocating again. MEASURED on r1m1: the barrel went
            # out as Object76476763 and, on the next export, as
            # Object76476764 — a fresh node each time, while LastId
            # climbed by one per export.
            obj[ORIGINAL_NAME_PROP] = name
    elif not name:
        name = obj.name

    location = None
    if obj.get(HAD_ORG_PROP, 1):
        blender_position = Vector3(*obj.location)
        if is_top_level:
            location = transform.blender_to_game_position(blender_position)
            # Undo the terrain-relative resolution done on import: the
            # file stores height ABOVE the ground, not absolute height.
            if terrain is not None:
                ground = terrain.sample_at_world(location.x, location.z)
                location = Vector3(location.x, location.y - ground, location.z)
        else:
            location = transform.blender_to_game_offset(blender_position)

    # The rotation the object actually has — NOT rotation_quaternion,
    # which is identity for every object in Euler mode (every new one
    # and every dropped asset) however it was turned. See
    # blender_io/object_rotation.py for the measurement.
    rotation = object_rotation(obj)
    raw_rotation = None
    if obj.get(HAD_ROTATION_PROP, 0):
        raw_rotation = transform.blender_to_game_rotation(rotation)
    elif not _is_identity_rotation(rotation):
        # The source had no rotation attribute, but the object has been
        # rotated in Blender. Omitting it because "it wasn't there
        # originally" would silently discard the user's edit — losslessness
        # means reproducing what wasn't touched, not refusing to record
        # what was.
        raw_rotation = transform.blender_to_game_rotation(rotation)

    if obj.get(HAD_SCALE_PROP, 0):
        scale = Vector3(*obj.scale)
    elif not _is_identity_scale(obj.scale):
        scale = Vector3(*obj.scale)  # newly scaled in Blender — see above
    else:
        scale = None

    org_rel_prop = obj.get(ORG_REL_PROP)
    cast_shadow_prop = obj.get(CAST_SHADOW_PROP)
    raw_attrs_json = obj.get(RAW_ATTRS_PROP)

    instance = ObjectInstance(
        name=name,
        node_class=node_class,
        org=location,
        org_rel=None if org_rel_prop is None else bool(org_rel_prop),
        raw_rotation=raw_rotation,
        scale=scale,
        asset_id=obj.get(ASSET_ID_PROP),
        skin=obj.get(SKIN_PROP),
        cast_shadow=None if cast_shadow_prop is None else bool(cast_shadow_prop),
        ndm_action=obj.get(NDM_ACTION_PROP),
        raw_attrs=json.loads(raw_attrs_json) if raw_attrs_json else {},
    )

    for child in obj.children:
        if CLASS_PROP in child:
            instance.children.append(
                _extract_one(child, transform, terrain, False, allocator)
            )

    return instance
