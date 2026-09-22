# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Turns a Blender object into an Ex Machina map node.

Until now the SDK could only export objects it had imported: anything
created in Blender was skipped, because export had no way to know what
class it should be or which model it draws. This supplies exactly that
missing information, and nothing more — the object keeps its Blender
position, rotation and scale, which is what makes placing something new
feel like ordinary modelling rather than data entry.

The node name is deliberately NOT assigned here. Names come from the
map's ``LastId`` sequence and can only be allocated safely against a
complete picture of what is already in use, which exists at export
time. Assigning one now would risk two objects claiming the same name
if the user assigns, undoes, and assigns again.

Two layers
----------

A map places things in two files, and they are not interchangeable.
``world.xml`` holds static nodes: a model at a position, drawn and
nothing more. ``dynamicscene.xml`` holds PROTOTYPE instances: a
``Breakable_Barrel1`` has mass, a damage model and an explosion, all
defined on the prototype, and the record only says where it stands.
MEASURED on r1m1: every barrel is a dynamic record; not one is a
world node. A barrel assigned as a world node is a decoration nothing
can hit — which is exactly what the user reported ("the barrel lost
its properties"). So when the model is one a prototype draws, this
operator defaults to the dynamic layer and offers the prototypes.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from blender_io import dynamic_bridge
from blender_io.world_bridge import (
    ASSET_ID_PROP,
    CLASS_PROP,
    NDM_ACTION_PROP,
    HAD_ORG_PROP,
    HAD_ROTATION_PROP,
    HAD_SCALE_PROP,
    NEW_OBJECT_PROP,
    ORG_REL_PROP,
)
from core.objects import KNOWN_NODE_CLASSES
from core.prototype_placement import default_belong, is_placeable, prototypes_for_model
from utils.logging import get_logger

logger = get_logger("addon.assign")

#: What the asset palette stamps on its objects. Deliberately not the
#: same name as ``ASSET_ID_PROP``: a palette object is not a placed
#: node, and marking it as one would put a thousand models into the
#: export. Read here so a dragged asset arrives with its model id
#: already filled in.
PALETTE_ID_PROP = "exm_asset_id"

_LAYER_ITEMS = [
    ("DYNAMIC", "Game Object (dynamicscene.xml)",
     "A prototype instance: physics, damage, explosion — what a barrel, "
     "a fence or a turret is in the game. Choose the prototype below"),
    ("WORLD", "Static Node (world.xml)",
     "A model at a position and nothing more: buildings, rocks, decoration. "
     "Nothing in the game can hit or break it"),
]

#: The dynamic-layer properties this operator writes or clears.
_DYNAMIC_PROPERTIES = (
    dynamic_bridge.DYNAMIC_PROP, dynamic_bridge.PROTOTYPE_PROP,
    dynamic_bridge.BELONG_PROP, dynamic_bridge.TAG_PROP,
    dynamic_bridge.NEW_DYNAMIC_PROP, dynamic_bridge.DYNAMIC_KEY_PROP,
    dynamic_bridge.CREATED_PROP,
    dynamic_bridge.HAD_ROTATION_PROP, dynamic_bridge.SCALE_PROP,
)
_WORLD_PROPERTIES = (
    CLASS_PROP, ORG_REL_PROP, NEW_OBJECT_PROP, NDM_ACTION_PROP,
    HAD_ORG_PROP, HAD_ROTATION_PROP, HAD_SCALE_PROP,
)

#: Prototype catalogues by game root, so the dialog does not re-read
#: ~30 XML files on every invocation. 986 definitions; cheap to hold.
_CATALOG_CACHE: dict = {}
#: Blender requires the strings behind a dynamic EnumProperty to stay
#: alive; this holds the last item list built.
_PROTOTYPE_ITEMS: list = []


def _prototype_catalog(context):
    """The game's prototype catalogue, or None without a game folder."""
    from addon.preferences import get_game_root
    from core.prototypes import read_prototype_catalog

    root = (get_game_root(context) or "").strip()
    if not root:
        return None
    if root not in _CATALOG_CACHE:
        try:
            _CATALOG_CACHE[root] = read_prototype_catalog(root)
        except Exception as exc:  # noqa: BLE001 - a missing catalogue must not block assign
            logger.warning("could not read the prototype catalogue: %s", exc)
            _CATALOG_CACHE[root] = None
    return _CATALOG_CACHE[root]


def _prototype_items(self, context):
    """Candidates for the model id, best first, for the dropdown."""
    global _PROTOTYPE_ITEMS
    catalog = _prototype_catalog(context)
    items = []
    if catalog is not None:
        for prototype in prototypes_for_model(catalog, self.model_id):
            items.append((
                prototype.name, prototype.name,
                f"{prototype.prototype_class or '?'} — {os.path.basename(prototype.source_file or '')}",
            ))
    if not items:
        items.append(("NONE", "(no prototype draws this model — type one)", ""))
    _PROTOTYPE_ITEMS = items
    return _PROTOTYPE_ITEMS


def _on_prototype_choice(self, context):
    if self.prototype_choice and self.prototype_choice != "NONE":
        self.prototype = self.prototype_choice


def _same_class(catalog, prototype_name: str) -> tuple[str, ...]:
    """Other prototypes of the same class — the turrets a new turret
    takes its faction from."""
    if catalog is None:
        return ()
    chosen = catalog.get(prototype_name)
    if chosen is None or not chosen.prototype_class:
        return ()
    return tuple(
        p.name for p in catalog.entries()
        if p.prototype_class == chosen.prototype_class and p.name != prototype_name
    )


def _belongs_on_map(collection) -> dict[str, list[str]]:
    """Belong values already on the map, by prototype, from the built
    objects — the imported scene is not at hand here, they are."""
    found: dict[str, list[str]] = {}
    pending = [collection]
    while pending:
        current = pending.pop()
        for obj in current.objects:
            if dynamic_bridge.DYNAMIC_PROP not in obj:
                continue
            prototype = obj.get(dynamic_bridge.PROTOTYPE_PROP)
            belong = obj.get(dynamic_bridge.BELONG_PROP)
            if prototype and belong is not None:
                found.setdefault(str(prototype), []).append(str(belong))
        pending.extend(current.children)
    return found


def _dynamic_collection(collection):
    """The map's Dynamic sub-collection, or None."""
    from blender_io.scene_bridge import DYNAMIC_COLLECTION

    pending = [collection]
    while pending:
        current = pending.pop()
        for child in current.children:
            if child.name.startswith(DYNAMIC_COLLECTION):
                return child
            pending.append(child)
    return None


#: Classes worth offering. ``SgNode`` is a container and
#: ``SgSoundSourceNode`` needs a sound id rather than a model, so the
#: two that actually carry geometry come first.
_CLASS_ITEMS = [
    ("SgAnimatedModelNode", "Animated Model", "Ordinary visual object — the usual choice"),
    ("SgGameUnitNode", "Game Unit", "Building or gameplay entity with logic attached"),
    ("SgNode", "Group", "Container with no geometry of its own"),
    ("SgSoundSourceNode", "Sound Source", "Point sound emitter (id names a sound, not a model)"),
]


def _find_terrain(collection):
    """The imported terrain object, or None."""
    for obj in collection.objects:
        if "exm_cell_size" in obj:
            return obj
    for child in collection.children:
        found = _find_terrain(child)
        if found is not None:
            return found
    return None


def _sample_terrain_height(terrain, x: float, y: float) -> float | None:
    """Terrain height in Blender space at (x, y), or None.

    Bilinear across the four surrounding grid vertices. Nearest-vertex
    was tried first and left a residual offset of a few units — the
    grid step is 10 Blender units, so a point between vertices can sit
    that far from the nearest one, and export writes the difference as
    a visible height offset. Real nodes sit at exactly 0.
    """
    mesh = getattr(terrain, "data", None)
    vertices = list(getattr(mesh, "vertices", []) or [])
    if not vertices:
        return None

    # The grid is axis-aligned and regular, so the four neighbours can
    # be found by distance without reconstructing the topology.
    below_left = below_right = above_left = above_right = None
    best = [float("inf")] * 4

    for vertex in vertices:
        vx, vy, vz = vertex.co[0], vertex.co[1], vertex.co[2]
        dx, dy = vx - x, vy - y
        distance = dx * dx + dy * dy
        quadrant = (0 if dx <= 0 else 1) + (0 if dy <= 0 else 2)
        if distance < best[quadrant]:
            best[quadrant] = distance
            if quadrant == 0:
                below_left = (vx, vy, vz)
            elif quadrant == 1:
                below_right = (vx, vy, vz)
            elif quadrant == 2:
                above_left = (vx, vy, vz)
            else:
                above_right = (vx, vy, vz)

    corners = [c for c in (below_left, below_right, above_left, above_right) if c]
    if not corners:
        return None
    if len(corners) < 4:
        # Outside the grid, or on its edge: the nearest corner is the
        # best available answer.
        return min(
            corners, key=lambda c: (c[0] - x) ** 2 + (c[1] - y) ** 2,
        )[2]

    x0, x1 = below_left[0], below_right[0]
    y0, y1 = below_left[1], above_left[1]
    tx = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
    ty = 0.0 if y1 == y0 else (y - y0) / (y1 - y0)

    bottom = below_left[2] + (below_right[2] - below_left[2]) * tx
    top = above_left[2] + (above_right[2] - above_left[2]) * tx
    return bottom + (top - bottom) * ty


class EXM_OT_assign_node(bpy.types.Operator):
    """Mark selected objects as Ex Machina map nodes so they export."""

    bl_idname = "exmachina.assign_node"
    bl_label = "Assign Map Object"
    bl_description = (
        "Make the selected object(s) part of the map: a game object with a "
        "prototype (dynamicscene.xml) or a static node with a model "
        "(world.xml). Written on export"
    )
    bl_options = {"REGISTER", "UNDO"}

    layer: EnumProperty(
        name="Layer",
        description="Which map file the object is written to",
        items=_LAYER_ITEMS,
        default="WORLD",
    )

    prototype_choice: EnumProperty(
        name="Prototype",
        description="Prototypes in the game that draw this model",
        items=_prototype_items,
        update=_on_prototype_choice,
    )

    prototype: StringProperty(
        name="Prototype Name",
        description=(
            "The prototype the record instantiates, e.g. 'Breakable_Barrel1'. "
            "Must exist in the game's prototype files"
        ),
    )

    belong: StringProperty(
        name="Belong",
        description=(
            "Faction/ownership id the record carries. Pre-filled from the "
            "records of the same prototype already on this map; -1 is what "
            "nearly every breakable on the shipped maps uses"
        ),
        default="-1",
    )

    node_class: EnumProperty(
        name="Class",
        description="Which kind of map node this becomes",
        items=_CLASS_ITEMS,
        default="SgAnimatedModelNode",
    )

    model_id: StringProperty(
        name="Model",
        description=(
            "The model id the game should draw, e.g. 'house3'. Must exist in "
            "the game's model catalogue — use Validate to check"
        ),
    )

    top_level: BoolProperty(
        name="World Space Position",
        description=(
            "On for an object placed directly on the map (its position is "
            "world-space, height measured from the terrain). Off for a child "
            "of another node, whose position is relative to its parent"
        ),
        default=True,
    )

    snap_to_ground: BoolProperty(
        name="Place On Terrain",
        description=(
            "Put the object on the terrain surface. Node heights are stored as "
            "an offset ABOVE the ground, and 1438 of 1610 objects on a real map "
            "sit at exactly 0 — an object left at an arbitrary Blender height "
            "exports as a large offset and ends up buried or floating"
        ),
        default=True,
    )

    validate_model: BoolProperty(
        name="Check Model Exists",
        description=(
            "Look the model id up in the game catalogue and refuse ids that "
            "are not there — a typo would otherwise only surface in-game"
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def invoke(self, context, event):
        # Pre-fill from an already-assigned object, so re-running on a
        # node to change one field doesn't clear the others.
        active = context.active_object
        if active is not None:
            if CLASS_PROP in active:
                self.node_class = active[CLASS_PROP]
            # ...or from an object dragged out of the Asset Browser,
            # which carries the model id under a DIFFERENT property.
            # The asset module set exm_asset_id and this read exm_id,
            # so the field came up empty and the model had to be typed
            # in — or, worse, exported as a brand new model, which
            # writes a new .gam whose shader is not the original's.
            # A dragged asset IS a catalogue model; it needs placing,
            # not creating.
            existing = active.get(ASSET_ID_PROP) or active.get(PALETTE_ID_PROP)
            if existing:
                self.model_id = existing

            # Which layer. An object already in the dynamic layer stays
            # there; otherwise, a model some prototype draws is offered
            # as that prototype — a barrel is a Breakable_Barrel1, not
            # a static node. Only a model no prototype names defaults
            # to world.xml.
            known_prototype = active.get(dynamic_bridge.PROTOTYPE_PROP)
            catalog = _prototype_catalog(context)
            candidates = (
                prototypes_for_model(catalog, self.model_id) if catalog else []
            )
            if known_prototype:
                self.layer = "DYNAMIC"
                self.prototype = str(known_prototype)
            elif candidates and CLASS_PROP not in active:
                self.layer = "DYNAMIC"
                self.prototype = candidates[0].name
            if self.layer == "DYNAMIC" and self.prototype:
                self.belong = default_belong(
                    _belongs_on_map(context.scene.collection)
                    if getattr(context.scene, "collection", None) is not None
                    else {},
                    self.prototype,
                    siblings=_same_class(catalog, self.prototype),
                )
                if active.get(dynamic_bridge.BELONG_PROP) is not None:
                    self.belong = str(active[dynamic_bridge.BELONG_PROP])
                if any(c.name == self.prototype for c in candidates):
                    self.prototype_choice = self.prototype
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context) -> None:
        layout = self.layout
        layout.prop(self, "layer", expand=True)

        if self.layer == "DYNAMIC":
            layout.prop(self, "model_id")
            layout.prop(self, "prototype_choice")
            layout.prop(self, "prototype")
            layout.prop(self, "belong")
            layout.prop(self, "snap_to_ground")
            if not self.prototype.strip():
                layout.label(text="A prototype name is required", icon="ERROR")
            else:
                layout.label(
                    text="Physics, damage and effects come from the prototype",
                    icon="INFO",
                )
            return

        layout.prop(self, "node_class")

        needs_model = self.node_class in ("SgAnimatedModelNode", "SgGameUnitNode")
        row = layout.row()
        row.enabled = needs_model or self.node_class == "SgSoundSourceNode"
        row.prop(self, "model_id")

        layout.prop(self, "top_level")
        layout.prop(self, "snap_to_ground")
        layout.prop(self, "validate_model")

        if needs_model and not self.model_id:
            layout.label(text="A model id is required for this class", icon="ERROR")
        if self.model_id:
            catalog = _prototype_catalog(context)
            candidates = prototypes_for_model(catalog, self.model_id) if catalog else []
            if candidates:
                layout.label(
                    text=f"{candidates[0].name} draws this model — as a static node "
                    "it will have no physics or damage",
                    icon="ERROR",
                )

    def execute(self, context):
        selected = list(context.selected_objects)
        if not selected:
            self.report({"ERROR"}, "Nothing selected")
            return {"CANCELLED"}

        if self.layer == "DYNAMIC":
            return self._execute_dynamic(context, selected)

        # A group node draws nothing. Assigning one to a mesh is almost
        # always a mistake — the object exports, occupies an id, and is
        # invisible in the game, which looks exactly like a broken
        # export rather than the wrong class.
        if self.node_class == "SgNode":
            meshes = [o for o in selected if getattr(o, "type", None) == "MESH"]
            if meshes:
                self.report(
                    {"ERROR"},
                    f"{len(meshes)} of the selected objects have geometry, but "
                    "SgNode is a container that never draws anything. Use "
                    "Animated Model with a model id from the game catalogue.",
                )
                return {"CANCELLED"}

        needs_model = self.node_class in (
            "SgAnimatedModelNode", "SgGameUnitNode", "SgSoundSourceNode",
        )
        if needs_model and not self.model_id.strip():
            self.report({"ERROR"}, f"{self.node_class} needs a model id")
            return {"CANCELLED"}

        if needs_model:
            problem, checked = self._validate(context, self.model_id.strip())
            if problem is not None and self.validate_model:
                self.report({"ERROR"}, problem)
                return {"CANCELLED"}
            if problem is not None:
                self.report({"WARNING"}, problem + " (check disabled)")
            elif not checked:
                # Silence here once let a non-existent id through to a
                # map that then showed nothing.
                self.report(
                    {"WARNING"},
                    f"Could not verify '{self.model_id.strip()}' against the game "
                    "catalogue — set the Game Folder in Preferences to enable "
                    "the check",
                )

        snapped = 0
        for obj in selected:
            self._assign(obj)
            if self.snap_to_ground and self._snap(context, obj):
                snapped += 1

        note = f", {snapped} placed on the terrain" if snapped else ""
        self.report(
            {"INFO"},
            f"{len(selected)} object(s) assigned as {self.node_class}"
            + (f" ({self.model_id})" if self.model_id else "")
            + note
            + " — they will be written to world.xml on export",
        )
        return {"FINISHED"}

    def _execute_dynamic(self, context, selected):
        prototype = self.prototype.strip()
        if not prototype:
            self.report({"ERROR"}, "A game object needs a prototype name")
            return {"CANCELLED"}

        catalog = _prototype_catalog(context)
        if catalog is None:
            self.report(
                {"WARNING"},
                f"Could not verify '{prototype}' against the game's prototypes — "
                "set the Game Folder in Preferences to enable the check",
            )
        elif prototype not in catalog:
            near = [
                p.name for p in catalog.entries()
                if prototype.lower() in p.name.lower()
            ][:5]
            suggestion = f" Did you mean: {', '.join(near)}?" if near else ""
            self.report(
                {"ERROR"},
                f"'{prototype}' is not a prototype the game defines, so the "
                f"object would never appear.{suggestion}",
            )
            return {"CANCELLED"}
        elif not is_placeable(catalog.get(prototype)):
            chosen = catalog.get(prototype)
            wholes = [
                p.name for p in catalog.entries()
                if p.is_composite and p.main_part_prototype == prototype
                and is_placeable(p)
            ][:5]
            suggestion = (
                f" It is the body of: {', '.join(wholes)} — place one of those."
                if wholes else ""
            )
            self.report(
                {"ERROR"},
                f"'{prototype}' is a {chosen.prototype_class}: a part of "
                f"something, not a thing the map can place — the game shows "
                f"nothing for it.{suggestion}",
            )
            return {"CANCELLED"}

        target = _dynamic_collection(context.scene.collection) if getattr(
            context.scene, "collection", None,
        ) is not None else None
        belong = self.belong.strip() or None
        snapped = moved = 0
        for obj in selected:
            self._assign_dynamic(obj, prototype, belong)
            if target is not None and self._move_into(obj, target):
                moved += 1
            if self.snap_to_ground and self._snap(context, obj):
                snapped += 1

        notes = []
        if snapped:
            notes.append(f"{snapped} placed on the terrain")
        if moved:
            notes.append(f"{moved} moved into {target.name}")
        self.report(
            {"INFO"},
            f"{len(selected)} object(s) assigned as {prototype}"
            + (f" ({', '.join(notes)})" if notes else "")
            + " — they will be written to dynamicscene.xml on export",
        )
        return {"FINISHED"}

    def _assign_dynamic(self, obj, prototype: str, belong: str | None) -> None:
        """Make the object a new record of the dynamic layer.

        An object that was a world node stops being one: the two layers
        are different files, and a barrel cannot be both a static node
        and a breakable. Its world.xml node, if it had one, is deleted
        on export like any other removed node.
        """
        for name in _WORLD_PROPERTIES:
            if name in obj:
                del obj[name]
        obj[dynamic_bridge.DYNAMIC_PROP] = 1
        obj[dynamic_bridge.TAG_PROP] = "Object"
        obj[dynamic_bridge.PROTOTYPE_PROP] = prototype
        if belong is not None:
            obj[dynamic_bridge.BELONG_PROP] = belong
        elif dynamic_bridge.BELONG_PROP in obj:
            del obj[dynamic_bridge.BELONG_PROP]
        # Only a record that has none yet is new; re-assigning an
        # imported one (to change its prototype, say) keeps its key.
        if not obj.get(dynamic_bridge.DYNAMIC_KEY_PROP):
            obj[dynamic_bridge.NEW_DYNAMIC_PROP] = 1
            obj[dynamic_bridge.CREATED_PROP] = 1
            obj[dynamic_bridge.HAD_ROTATION_PROP] = 0
            obj[dynamic_bridge.SCALE_PROP] = 1.0
        model_id = self.model_id.strip()
        if model_id:
            obj[ASSET_ID_PROP] = model_id

    @staticmethod
    def _move_into(obj, target) -> bool:
        """Link the object into the Dynamic collection and out of the
        others, so the outliner shows which layer it belongs to."""
        try:
            already = any(o is obj for o in target.objects)
        except TypeError:
            already = False
        if already:
            return False
        try:
            target.objects.link(obj)
        except (RuntimeError, TypeError, AttributeError):
            return False
        for collection in list(getattr(obj, "users_collection", ()) or ()):
            if collection is target:
                continue
            try:
                collection.objects.unlink(obj)
            except (RuntimeError, TypeError, AttributeError):
                pass
        return True

    @staticmethod
    def _snap(context, obj) -> bool:
        """Move the object down onto the terrain mesh.

        Node heights are stored as an offset above the ground, so an
        object left at whatever height it was modelled at exports as a
        large offset — the reported symptom was a node that appeared in
        world.xml with org.y = -137 and was invisible in the editor,
        because it was underground.

        Snapping uses the imported terrain mesh rather than re-reading
        displace.bin: the mesh is already in Blender space, so no
        assumption about scale or centring is needed here.
        """
        terrain = _find_terrain(context.collection)
        if terrain is None:
            return False

        height = _sample_terrain_height(terrain, obj.location[0], obj.location[1])
        if height is None:
            return False

        x, y, _z = obj.location
        obj.location = (x, y, height)
        return True

    def _assign(self, obj: bpy.types.Object) -> None:
        # Leaving the dynamic layer, if it was there: one file per object.
        for name in _DYNAMIC_PROPERTIES:
            if name in obj:
                del obj[name]
        obj[CLASS_PROP] = self.node_class
        # 1561 of 1734 nodes on a real map carry ndmAction; the editor
        # writes it on every model node it creates. A node without it
        # is not obviously broken, but matching what the editor
        # produces avoids finding out the hard way.
        if self.node_class in ("SgAnimatedModelNode", "SgGameUnitNode"):
            obj[NDM_ACTION_PROP] = "0"
        if self.model_id.strip():
            obj[ASSET_ID_PROP] = self.model_id.strip()
        elif ASSET_ID_PROP in obj:
            del obj[ASSET_ID_PROP]

        obj[ORG_REL_PROP] = 1 if self.top_level else 0

        # A new node always carries a position; rotation and scale are
        # written only when they differ from the identity, matching how
        # imported nodes behave (see blender_io/world_bridge.py).
        obj[HAD_ORG_PROP] = 1
        obj[HAD_ROTATION_PROP] = 0
        obj[HAD_SCALE_PROP] = 0

        # Marks the object as needing a name from the map's LastId
        # sequence at export. Only set when it isn't already a known
        # node, so re-assigning an imported object doesn't renumber it.
        if not obj.get("exm_original_name"):
            obj[NEW_OBJECT_PROP] = 1

    def _validate(self, context, model_id: str) -> tuple[str | None, bool]:
        """Check the id against the game catalogue.

        Returns ``(problem, checked)``. The second value matters: an
        unchecked id used to look identical to a valid one, so a
        non-existent model reached the map and simply never appeared.
        The caller reports the difference.
        """
        from addon.preferences import get_game_root
        from formats.exm.model_catalog import normalise_game_root

        configured = get_game_root(context)
        if not configured:
            return None, False
        game_root = normalise_game_root(configured)
        if game_root is None:
            return None, False

        source_dir = context.scene.get("exm_source_dir")
        if not source_dir:
            return None, False

        try:
            from formats.exm.model_trace import build_catalogue_with_sources
            from formats.exm.plugin import find_manifest
            from formats.exm.ssl import read_manifest

            manifest_path = find_manifest(source_dir)
            if manifest_path is None:
                return None, False
            manifest = read_manifest(manifest_path)
            catalog, _sources, _problems = build_catalogue_with_sources(
                manifest, source_dir, game_root,
            )
        except Exception as exc:  # noqa: BLE001 - validation must not block work
            logger.warning("could not check model id: %s", exc)
            return None, False

        if len(catalog) == 0:
            return None, False
        if catalog.get(model_id) is not None:
            return None, True

        near = [
            entry.model_id for entry in catalog.entries()
            if model_id.lower() in entry.model_id.lower()
        ][:5]
        suggestion = f" Did you mean: {', '.join(near)}?" if near else ""
        return (
            f"'{model_id}' is not in the game's model catalogue, so nothing "
            f"would be drawn.{suggestion} To use geometry you modelled "
            "yourself, run 'Create Model from Mesh' instead — it exports the "
            "mesh and registers it under a name of your choosing.",
            True,
        )


class EXM_OT_clear_node(bpy.types.Operator):
    """Remove Ex Machina node data, so an object stops being exported."""

    bl_idname = "exmachina.clear_node"
    bl_label = "Clear Map Object"
    bl_description = (
        "Strip the map data from the selected object(s). They stay in the "
        "Blender scene but are no longer written to the map"
    )
    bl_options = {"REGISTER", "UNDO"}

    _PROPERTIES = (
        CLASS_PROP, ASSET_ID_PROP, ORG_REL_PROP, NEW_OBJECT_PROP, NDM_ACTION_PROP,
        HAD_ORG_PROP, HAD_ROTATION_PROP, HAD_SCALE_PROP,
    ) + _DYNAMIC_PROPERTIES

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        cleared = 0
        for obj in context.selected_objects:
            if CLASS_PROP not in obj and dynamic_bridge.DYNAMIC_PROP not in obj:
                continue
            for name in self._PROPERTIES:
                if name in obj:
                    del obj[name]
            cleared += 1

        self.report({"INFO"}, f"{cleared} object(s) will no longer be exported")
        return {"FINISHED"}


_CLASSES = (EXM_OT_assign_node, EXM_OT_clear_node)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
