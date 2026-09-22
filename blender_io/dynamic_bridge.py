# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""DynamicScene <-> Blender.

Builds the second placement layer: the thousands of breakables, trees,
cables and lamp posts that ``world.xml`` never mentions.

Objects go into their own collection rather than mixing with the scene
graph. They come from a different file, are identified differently
(``Prototype`` rather than a model id), and export back separately —
keeping them apart is what stops an edit to one layer being written to
the wrong file.

Objects whose prototype resolves to a model get real geometry; the
rest become Empties, exactly as in the other layer, so a gameplay-only
entity (a spawn zone, a team marker) still shows where it is.
"""

from __future__ import annotations

import json

import bpy

from blender_io.object_rotation import is_identity, object_rotation
from core.coordinates import CoordinateTransform
from core.dynamic_objects import DynamicObject, DynamicScene
from core.node_naming import NodeNameAllocator
from utils.errors import EXMeditorError
from utils.logging import get_logger
from core.terrain import HeightmapData
from utils.math import Vector3

PROTOTYPE_PROP = "exm_prototype"
logger = get_logger("blender_io.dynamic")

DYNAMIC_PROP = "exm_dynamic"          # marks an object as belonging to this layer
BELONG_PROP = "exm_belong"
MODEL_NAME_PROP = "exm_model_name"
#: On a child object that is a PART of a composite prototype — the gun
#: on a pillbox. Holds the part id. Such a child is not a placed object
#: of its own: it carries no DYNAMIC_PROP, so extraction never sees it,
#: and it follows its parent wherever the user moves that.
ATTACHMENT_PROP = "exm_attachment"
#: The identity an export matches a Blender object back to its record
#: by. The record's Name when it has one; otherwise its position in
#: the file's walk order, as ``#<index>``. MEASURED on r1m1: eleven
#: ``LightObject2`` and one ``Item`` (``BugForSale``) carry no Name.
#: Matching by name alone found no Blender object called None, took
#: that for a deletion, and dropped all twelve on every export — and
#: the editor died loading the map.
DYNAMIC_KEY_PROP = "exm_dynamic_key"


def dynamic_key(obj: DynamicObject, index: int) -> str:
    """What identifies a record across an import and an export."""
    return obj.name if obj.name else f"#{index}"
TAG_PROP = "exm_tag"
RAW_ATTRS_PROP = "exm_dynamic_raw"
HAD_ROTATION_PROP = "exm_dynamic_had_rot"
GROUND_OFFSET_PROP = "exm_dynamic_ground"
#: Set by the assign operator on an object created in Blender that is
#: to become a record of this layer; cleared by the export operator
#: once the file holding it is written (``confirm_written``). While it
#: is set, the object's stamped key is a name it ASKED for, not a
#: record it IS: an export that failed after stamping must not make
#: the next one take some other record of that name for this object.
NEW_DYNAMIC_PROP = "exm_dynamic_new"
#: Set alongside NEW_DYNAMIC_PROP and never cleared: this object was
#: created in Blender, not imported. Export rebuilds the dynamic layer
#: by UPDATING the source file's records, so an object whose record
#: the source does not hold is normally one the file lost and is left
#: alone. A created object is the exception: MEASURED on map ``t``,
#: exporting to a folder other than the source twice wrote the barrel
#: on the first pass and dropped it on the second — the source still
#: had no record for it. With this flag it is written again, under
#: its stamped name.
CREATED_PROP = "exm_dynamic_created"
#: The uniform scale the record was imported with — its ``NodeScale``,
#: or 1.0 when it had none — so export can tell an edited scale from
#: an untouched one and leave the file's own text alone otherwise.
SCALE_PROP = "exm_dynamic_scale"
#: MEASURED over the shipped maps: 11 112 records carry NodeScale, one
#: float, 0.2 .. 3.694, written "%.3f" (r2m1 2999, r3m1 2562, r4m1
#: 722 — trees, pallets, debris). It was neither applied on import
#: nor written on export before 0.49.0.
NODE_SCALE_ATTR = "NodeScale"
_SCALE_EPSILON = 5e-4


def build_dynamic_scene(
    scene: DynamicScene,
    collection: bpy.types.Collection,
    *,
    transform: CoordinateTransform | None = None,
    terrain: HeightmapData | None = None,
    resolve_model=None,
    mesh_provider=None,
    resolve_parts=None,
    assembler=None,
) -> list[bpy.types.Object]:
    """Create Blender objects for every placed dynamic object.

    ``resolve_parts`` maps an object to the parts mounted on its body
    (see ``formats/exm/dynamic_scene.resolve_parts``) — the gun on a
    turret. Each becomes a child object at the body's locator.

    ``assembler`` is the general form: an object with ``model_of(name)``,
    ``parts_of(name)`` and ``sub_objects_of(name)`` answering for a
    prototype NAME, so a prefab's contents — themselves possibly
    composite — can be built the same way, recursively.

    ``resolve_model`` maps an object to a model id (see
    ``formats/exm/dynamic_scene.resolve_model_id``); ``mesh_provider``
    turns that id into a mesh. Both optional — without them everything
    is built as an Empty, which still shows the layer's placement.

    Objects with no position are skipped: they are gameplay records
    (teams, quest state) with nothing to place.
    """
    transform = transform if transform is not None else CoordinateTransform()
    created: list[bpy.types.Object] = []

    for index, obj in enumerate(scene.walk()):
        if obj.position is None:
            continue
        built = _build_one(obj, collection, transform, terrain, resolve_model,
                           mesh_provider, resolve_parts, assembler)
        built[DYNAMIC_KEY_PROP] = dynamic_key(obj, index)
        created.append(built)
    return created


def _build_one(
    obj: DynamicObject,
    collection: bpy.types.Collection,
    transform: CoordinateTransform,
    terrain: HeightmapData | None,
    resolve_model,
    mesh_provider,
    resolve_parts=None,
    assembler=None,
) -> bpy.types.Object:
    mesh = None
    model_id = None
    if resolve_model is not None:
        model_id = resolve_model(obj)
    if model_id and mesh_provider is not None:
        mesh = mesh_provider.get_mesh(model_id)

    name = obj.name or (obj.prototype or "DynObject")
    blender_object = bpy.data.objects.new(name, mesh)
    if mesh is None:
        blender_object.empty_display_type = "PLAIN_AXES"
        blender_object.empty_display_size = 1.5

    position = obj.position
    # Heights in this layer are absolute — verified against terrain:
    # object Y values sit within the terrain's own height range rather
    # than clustering near zero, which is what a ground-relative offset
    # would look like.
    blender_object.location = transform.game_to_blender_position(position).as_tuple()

    if obj.raw_rotation is not None and len(obj.raw_rotation) == 4:
        blender_object.rotation_mode = "QUATERNION"
        blender_object.rotation_quaternion = transform.game_to_blender_rotation(
            obj.raw_rotation
        )

    scale = _node_scale(obj)
    if scale is not None:
        blender_object.scale = (scale, scale, scale)
    blender_object[SCALE_PROP] = scale if scale is not None else 1.0

    blender_object[DYNAMIC_PROP] = 1
    blender_object[TAG_PROP] = obj.tag
    if obj.prototype is not None:
        blender_object[PROTOTYPE_PROP] = obj.prototype
    if obj.belong is not None:
        blender_object[BELONG_PROP] = obj.belong
    if obj.model_name is not None:
        blender_object[MODEL_NAME_PROP] = obj.model_name
    if model_id:
        blender_object["exm_id"] = model_id
    if obj.raw_attrs:
        blender_object[RAW_ATTRS_PROP] = json.dumps(obj.raw_attrs)
    blender_object[HAD_ROTATION_PROP] = 1 if obj.raw_rotation is not None else 0

    collection.objects.link(blender_object)

    if mesh_provider is not None:
        if model_id and resolve_parts is not None:
            _mount_parts(blender_object, model_id, resolve_parts(obj), transform,
                         mesh_provider, collection)
        if assembler is not None and obj.prototype:
            _place_sub_objects(blender_object, obj.prototype, transform,
                               mesh_provider, collection, assembler, depth=0)
    return blender_object


#: On a child object that is one entry of a prefab's ObjInfos. Like
#: ATTACHMENT_PROP it carries no DYNAMIC_PROP, so it is never exported
#: as a placed object of its own — the game re-expands the prefab.
SUB_OBJECT_PROP = "exm_sub_object"

#: Prefabs nesting prefabs are not known to exist; the cap is against a
#: definition that names itself.
_MAX_PREFAB_DEPTH = 3


def _place_sub_objects(parent, prototype_name, transform, mesh_provider,
                       collection, assembler, depth):
    """Expand a prefab: one child per ObjInfo, at RelPos, turned by
    RelAngle, each assembled in turn (a barricade's turret gets its
    gun).

    MEASURED: barricade4_wGw is two Breakable_SackWall1 at x = -6 and
    +6 and a staticAutoGun08 at the origin. RelPos is in the prefab's
    own space, so it takes the OFFSET conversion; RelAngle is applied
    as degrees about the up axis — sign UNVERIFIED, see
    core.prototypes.Prototype.sub_objects.
    """
    import math

    if depth >= _MAX_PREFAB_DEPTH:
        return
    for index, sub in enumerate(assembler.sub_objects_of(prototype_name)):
        model_id = assembler.model_of(sub.prototype)
        mesh = mesh_provider.get_mesh(model_id) if model_id else None
        child = bpy.data.objects.new(f"{parent.name}:{sub.prototype}.{index}", mesh)
        if mesh is None:
            child.empty_display_type = "PLAIN_AXES"
            child.empty_display_size = 1.0
        child.location = transform.game_to_blender_offset(
            Vector3(*sub.rel_pos)).as_tuple()
        if sub.rel_angle:
            child.rotation_mode = "XYZ"
            child.rotation_euler = (0.0, 0.0, math.radians(sub.rel_angle))
        child.parent = parent
        child[SUB_OBJECT_PROP] = sub.prototype
        child[PROTOTYPE_PROP] = sub.prototype
        if model_id:
            child["exm_id"] = model_id
        collection.objects.link(child)

        if model_id:
            _mount_parts(child, model_id, assembler.parts_of(sub.prototype),
                         transform, mesh_provider, collection)
        _place_sub_objects(child, sub.prototype, transform, mesh_provider,
                           collection, assembler, depth + 1)


def _mount_parts(body, body_model_id, placements, transform, mesh_provider, collection):
    """Put each part on its locator, as a child of the body.

    MEASURED, the whole chain on r1m1's turrets::

        dynamicscene.xml   Prototype="staticAutoGun04"
        gameobjects.xml    StaticAutoGun: Parts DOT=heavy_dot4, CANNON=vulcan01
                           CANNON mounts on lpName="LP_CANNON01"
        heavy_dot4.gam     node LP_CANNON01 at (-0.19, 8.75, 0.03)
        vulcan01           -> data/models/guns/sml_vulcan01.gam, 0.54 high,
                              standing on its own Y=0

    The locator's position is in the body's model space, so it goes
    through the OFFSET conversion (no map centring), same as vertices.
    A part whose locator the body does not have is placed at the body's
    origin and said so, rather than dropped.
    """
    for placement in placements:
        mesh = mesh_provider.get_mesh(placement.model_id)
        if mesh is None:
            logger.debug("%s: no mesh for part %s (%s)",
                         body.name, placement.part_id, placement.model_id)
            continue
        child = bpy.data.objects.new(f"{body.name}:{placement.part_id}", mesh)
        node = mesh_provider.locator(body_model_id, placement.locator)
        if node is not None:
            child.location = transform.game_to_blender_offset(node.location).as_tuple()
            try:
                child.rotation_mode = "QUATERNION"
                child.rotation_quaternion = transform.game_to_blender_rotation(node.rotation)
            except (AttributeError, TypeError, ValueError):
                pass
        else:
            logger.warning(
                "%s: body model %s has no locator %s; %s placed at the body's "
                "origin", body.name, body_model_id, placement.locator, placement.part_id,
            )
        child.parent = body
        child[ATTACHMENT_PROP] = placement.part_id
        child["exm_id"] = placement.model_id
        collection.objects.link(child)


def extract_dynamic_scene(
    original: DynamicScene,
    collection: bpy.types.Collection,
    *,
    transform: CoordinateTransform | None = None,
    children_for=None,
) -> DynamicScene:
    """Read edited positions back into the original structure.

    ``children_for(prototype_name)`` names the child elements a record
    of that prototype carries — ``("Parts",)`` for a turret, see
    ``core.prototype_placement.record_children``. Without it new
    records get none.

    Deletions are applied first, then surviving objects have their
    positions updated. Deliberately updates the scene that was loaded
    rather than rebuilding one: this layer contains gameplay structures (patrol
    posts, area polygons, part lists) that have no Blender
    representation at all, and rebuilding from the viewport would
    discard every one of them. Only what Blender can express —
    position and rotation — is written back.
    """
    transform = transform if transform is not None else CoordinateTransform()

    edited: dict[str, bpy.types.Object] = {}
    for obj in _walk_collection(collection):
        if DYNAMIC_PROP in obj and not obj.get(NEW_DYNAMIC_PROP):
            edited[_key_of(obj)] = obj

    remove_deleted_objects(original, collection)

    for index, obj in enumerate(original.walk()):
        if obj.position is None:
            continue
        blender_object = edited.get(dynamic_key(obj, index))
        if blender_object is None:
            continue
        obj.position = transform.blender_to_game_position(
            Vector3(*tuple(blender_object.location))
        )
        rotation = object_rotation(blender_object)
        if blender_object.get(HAD_ROTATION_PROP, 0) or not is_identity(rotation):
            # A record that had no Rot and was turned in Blender gets
            # one: omitting it "because it wasn't there" would discard
            # the edit.
            obj.raw_rotation = transform.blender_to_game_rotation(rotation)
        scale = _uniform_scale(blender_object)
        if abs(scale - float(blender_object.get(SCALE_PROP, 1.0))) > _SCALE_EPSILON:
            obj.raw_attrs[NODE_SCALE_ATTR] = f"{scale:.3f}"
        _apply_reassignment(obj, blender_object, children_for)

    _append_new_objects(original, collection, transform, children_for)
    return original


def _apply_reassignment(obj: DynamicObject, blender_object, children_for) -> None:
    """A record re-assigned in Blender to another prototype or Belong.

    The case that produced it: two turrets exported as their pillbox
    part; the user re-assigns them to ``staticAutoGun02`` and exports
    again, and the RECORD has to follow. Only a difference is written,
    so an untouched record keeps its text.
    """
    wanted = blender_object.get(PROTOTYPE_PROP)
    if wanted and str(wanted) != (obj.prototype or ""):
        logger.info("%r: prototype %s -> %s", obj.name, obj.prototype, wanted)
        obj.prototype = str(wanted)
        _ensure_children(obj, children_for)
    belong = blender_object.get(BELONG_PROP)
    if belong is not None and str(belong) != (obj.belong or ""):
        obj.belong = str(belong)


def _ensure_children(obj: DynamicObject, children_for) -> None:
    """Give the record the child elements its prototype's records carry."""
    if children_for is None or not obj.prototype:
        return
    have = {child.tag for child in obj.children}
    for tag in children_for(obj.prototype) or ():
        if tag not in have:
            obj.children.append(DynamicObject(name="", tag=tag))


def _append_new_objects(original: DynamicScene, collection, transform,
                        children_for=None) -> list[str]:
    """Write records for objects created in Blender. Returns their names.

    A barrel dragged from the palette and assigned to THIS layer is a
    ``<Object Prototype="Breakable_Barrel1" …>`` — the prototype is
    what carries the physics, the damage model and the explosion. The
    same barrel assigned as a world.xml node is a static decoration
    that nothing can hit, which is exactly what the user saw.

    Names: ``Object<N>`` above both the file's ``LastId`` and the
    highest such number in use, unique against every name in the
    file. MEASURED: the editor's own new dynamic objects are named
    that way on the maps it made (``t``: 43, ``mainmenu``: 110, ``zoo``:
    345); shipped maps also carry hand-typed names (``barrel12782``,
    ``tree21693``) that bear no relation to ``LastId``. Uniqueness is
    what matters; the scheme is the editor's.
    """
    existing_keys = {
        dynamic_key(obj, index) for index, obj in enumerate(original.walk())
    }
    pending = [
        obj for obj in _walk_collection(collection)
        if DYNAMIC_PROP in obj and (
            obj.get(NEW_DYNAMIC_PROP)
            or (obj.get(CREATED_PROP) and _key_of(obj) not in existing_keys)
        )
    ]
    if not pending:
        return []

    allocator = _name_allocator(original)
    written: list[str] = []
    for blender_object in pending:
        prototype = blender_object.get(PROTOTYPE_PROP)
        if not prototype:
            logger.warning(
                "%s is marked as a new dynamic object but names no prototype; "
                "skipped", blender_object.name,
            )
            continue
        stamped = blender_object.get(DYNAMIC_KEY_PROP)
        name = allocator.reuse(str(stamped)) if stamped else None
        if name is None:
            name = allocator.allocate()

        rotation = object_rotation(blender_object)
        raw_rotation = None
        if not is_identity(rotation):
            raw_rotation = transform.blender_to_game_rotation(rotation)
        raw_attrs: dict[str, str] = {}
        scale = _uniform_scale(blender_object)
        if abs(scale - 1.0) > _SCALE_EPSILON:
            raw_attrs[NODE_SCALE_ATTR] = f"{scale:.3f}"

        belong = blender_object.get(BELONG_PROP)
        record = DynamicObject(
            name=name,
            prototype=str(prototype),
            position=transform.blender_to_game_position(
                Vector3(*tuple(blender_object.location))
            ),
            raw_rotation=raw_rotation,
            belong=str(belong) if belong is not None else None,
            raw_attrs=raw_attrs,
            tag="Object",
        )
        _ensure_children(record, children_for)
        original.objects.append(record)

        # Stamp the record's identity so the next export updates this
        # record instead of writing another.
        blender_object[DYNAMIC_KEY_PROP] = name
        blender_object[CREATED_PROP] = 1
        blender_object[HAD_ROTATION_PROP] = 1 if raw_rotation is not None else 0
        blender_object[SCALE_PROP] = scale
        written.append(name)

    if written and "LastId" in original.root_attributes:
        original.root_attributes["LastId"] = str(
            max(allocator.last_id, _int_or_zero(original.root_attributes["LastId"]))
        )
    if written:
        logger.info(
            "Added %d new dynamic object(s): %s", len(written), ", ".join(written[:5]),
        )
    return written


def confirm_written(collection) -> int:
    """Called by the export operator after the file is on disk: the new
    records now exist, so their objects stop being new. Returns how
    many were confirmed."""
    confirmed = 0
    for obj in _walk_collection(collection):
        if DYNAMIC_PROP in obj and obj.get(NEW_DYNAMIC_PROP) and obj.get(DYNAMIC_KEY_PROP):
            del obj[NEW_DYNAMIC_PROP]
            confirmed += 1
    return confirmed


def _name_allocator(scene: DynamicScene) -> "_DynamicNameAllocator":
    return _DynamicNameAllocator.for_scene(scene)


class _DynamicNameAllocator(NodeNameAllocator):
    """``Object<N>`` names for this layer, unique against every record."""

    @classmethod
    def for_scene(cls, scene: DynamicScene) -> "_DynamicNameAllocator":
        import re

        used = {obj.name for obj in scene.walk() if obj.name}
        highest = _int_or_zero(scene.root_attributes.get("LastId", "0"))
        pattern = re.compile(r"^Object(\d+)$")
        for name in used:
            match = pattern.match(name)
            if match:
                highest = max(highest, int(match.group(1)))
        return cls(last_id=highest, used_names=used)

    def reuse(self, name: str) -> str | None:
        """Take ``name`` if nothing in the file has it — a stamp left by
        an export that failed before writing. None when it is taken."""
        if not name or name.startswith("#") or name in self._used:
            return None
        self._used.add(name)
        self._issued.append(name)
        return name


def _int_or_zero(text) -> int:
    try:
        return int(str(text))
    except (TypeError, ValueError):
        return 0


def _node_scale(obj: DynamicObject) -> float | None:
    """The record's NodeScale as a float, or None when absent/unreadable."""
    raw = obj.raw_attrs.get(NODE_SCALE_ATTR)
    if raw is None:
        return None
    try:
        return float(str(raw).split()[0])
    except (TypeError, ValueError, IndexError):
        logger.warning("%r: NodeScale %r is not a number; ignored", obj.name, raw)
        return None


def _uniform_scale(blender_object) -> float:
    """The one scale the format can hold. A non-uniform Blender scale
    is averaged and reported — the record cannot express it."""
    sx, sy, sz = (float(v) for v in tuple(blender_object.scale))
    if abs(sx - sy) > _SCALE_EPSILON or abs(sx - sz) > _SCALE_EPSILON:
        logger.warning(
            "%s is scaled non-uniformly (%.3f, %.3f, %.3f); the dynamic layer "
            "holds one NodeScale, so the average is written",
            blender_object.name, sx, sy, sz,
        )
    return (sx + sy + sz) / 3.0


#: Refuse rather than obey when this share of the placed layer has
#: vanished. Deleting a few hundred fences is ordinary work; losing
#: most of the layer at once is a scene that came from somewhere else,
#: and the file it would overwrite is the user's map.
MAX_DELETION_SHARE = 0.5


def remove_deleted_objects(
    original: DynamicScene, collection: bpy.types.Collection
) -> int:
    """Drop objects the user deleted in Blender. Returns how many.

    An object is considered deleted when it HAD a Blender
    representation and no longer has one. ``build_dynamic_scene``
    creates one for every object with a position — an Empty when the
    model cannot be resolved — and skips objects without one, so
    "was placed" and "should be in the scene" are the same question.
    That is what makes this safe: an object with no position never had
    a viewport presence, so its absence means nothing and it is left
    alone.

    A node whose subtree still has a survivor is KEPT even when the
    node itself is gone. This layer nests gameplay structures — patrol
    posts, area polygons, part lists — under placed objects, and
    removing a parent would take them with it. Losing records the user
    can neither see nor recover is worse than leaving one stale entry,
    so the case is reported instead of acted on.
    """
    present = {
        _key_of(obj) for obj in _walk_collection(collection)
        if DYNAMIC_PROP in obj and not obj.get(NEW_DYNAMIC_PROP)
    }

    keyed = [(dynamic_key(obj, index), obj) for index, obj in enumerate(original.walk())]
    # A record with no Name is never "missing": nothing in Blender can
    # be said to have been it, so nothing can be said to have deleted
    # it. It is left exactly as it was read — and left out of the
    # wholesale-loss share, which is a share of what CAN be deleted.
    # MEASURED on r1m1: 16 of 4459 placed records are nameless.
    deletable = [
        (key, obj) for key, obj in keyed if obj.position is not None and obj.name
    ]
    missing = [obj for key, obj in deletable if key not in present]
    if not missing:
        return 0

    if deletable and len(missing) / len(deletable) > MAX_DELETION_SHARE:
        raise EXMeditorError(
            f"{len(missing)} of {len(deletable)} placed dynamic objects are "
            "absent from the scene. Exporting would delete them all. That is "
            "almost certainly a scene imported from a different map, or one "
            "where the Dynamic collection was removed — re-import the map "
            "before exporting."
        )

    removed = _prune(original.objects, present)
    logger.info("Removed %d dynamic object(s) deleted in Blender", removed)
    return removed


def _key_of(blender_object) -> str:
    """The key a built object carries; its name for one built before
    the key existed."""
    key = blender_object.get(DYNAMIC_KEY_PROP)
    return str(key) if key else blender_object.name


def _prune(objects: list[DynamicObject], present: set[str]) -> int:
    """Remove deleted nodes in place, depth first. Returns the count."""
    removed = 0
    survivors: list[DynamicObject] = []

    for obj in objects:
        removed += _prune(obj.children, present)

        # Nameless records are kept regardless: see remove_deleted_objects.
        deleted = obj.position is not None and bool(obj.name) and obj.name not in present
        if deleted and _subtree_is_gone(obj, present):
            removed += 1
            continue
        if deleted:
            logger.warning(
                "%r was deleted in Blender but still holds records that were "
                "not, so it has been kept. Delete its children too if it "
                "should go.",
                obj.name,
            )
        survivors.append(obj)

    objects[:] = survivors
    return removed


def _subtree_is_gone(obj: DynamicObject, present: set[str]) -> bool:
    """True when nothing under this node survives in the scene."""
    return not any(child.name in present for child in obj.walk())


def _walk_collection(collection: bpy.types.Collection):
    for obj in collection.objects:
        yield obj
    for child in collection.children:
        yield from _walk_collection(child)
