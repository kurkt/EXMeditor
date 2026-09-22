# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Turns a Blender mesh into a usable game model, end to end.

The gap this closes: *Assign ExMachina Node* asked for a model id,
which only exists for models the game already ships. Naming a mesh you
just modelled was impossible — the field demanded an answer that could
not exist yet. This asks for a NAME and produces the id itself.

Three things must all happen, and doing any one alone appears to do
nothing:

1. write the geometry to a ``.gam`` file;
2. register that file in ``AnimModels.xml`` under a new id — the game
   resolves models by id through the catalogue and never scans the
   disk, so an unregistered file is invisible;
3. assign the id to the object so it reaches ``world.xml``.

Writing the ``.gam`` uses HTAToolchain's parser, which round-trips a
real model with only its own version stamp differing. This SDK's own
parser reads ``.gam`` but cannot write one: the material, shadow and
tool-metadata chunks are understood well enough to read past, not well
enough to generate.
"""

from __future__ import annotations

import os
import re

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from addon.preferences import get_game_root
from blender_io.world_bridge import (
    ASSET_ID_PROP,
    CLASS_PROP,
    HAD_ORG_PROP,
    HAD_ROTATION_PROP,
    HAD_SCALE_PROP,
    NDM_ACTION_PROP,
    NEW_OBJECT_PROP,
    ORG_REL_PROP,
)
from utils.errors import EXMeditorError
from utils.logging import get_logger

logger = get_logger("addon.create_model")

#: Where new models are written, relative to the game root. A folder of
#: its own so a user's work is never confused with shipped assets, and
#: so it can be deleted wholesale without touching the game.
CUSTOM_MODEL_DIR = os.path.join("data", "models", "custom")

#: Model ids appear in XML attributes and file paths, so the safe set
#: is deliberately narrow.
#: Characters that survive into an XML attribute and a filename.
#: Spaces are allowed — the editor uses them.
_SAFE_ID = re.compile(r'[^A-Za-z0-9_ .-]+')


def make_model_id(name: str) -> str:
    """Turn a user-typed name into an id the format accepts.

    Spaces are kept: the game's own editor created a working model with
    ``id="MSCV NOD"``, so replacing them was an invented restriction
    that made ids differ from what the user typed for no reason.

    Characters that would break the XML or a path are still replaced.
    """
    cleaned = _SAFE_ID.sub("_", name.strip()).strip("_ ")
    return cleaned or "model"



def _catalogue_reference(servers_path: str, catalogue_path: str) -> str:
    """How servers.xml should refer to the catalogue.

    Copied from the file's own entries rather than computed. Existing
    <Item> lines already name the catalogue, and matching them exactly
    avoids inventing a second spelling of the same path in a file the
    game ships.
    """
    name = os.path.basename(catalogue_path)
    try:
        with open(servers_path, "r", encoding="cp1251", errors="replace") as handle:
            text = handle.read()
    except OSError:
        text = ""

    match = re.search(
        rf'file\s*=\s*"([^"]*{re.escape(name)})"', text, re.IGNORECASE
    )
    if match is not None:
        return match.group(1)
    return os.path.join("data", "models", name).replace(os.sep, "\\")


class EXM_OT_create_model(bpy.types.Operator):
    """Export the selected mesh as a new game model and place it."""

    bl_idname = "exmachina.create_model"
    bl_label = "Create Model from Mesh"
    bl_description = (
        "Give the selected mesh a name; it is exported as a .gam, registered "
        "in the game's model catalogue, and assigned to the object so it "
        "appears in the map"
    )
    bl_options = {"REGISTER", "UNDO"}

    model_name: StringProperty(
        name="Model Name",
        description=(
            "A name of your choosing, e.g. 'My Watchtower'. The id the game "
            "uses is derived from it automatically"
        ),
        default="",
    )

    node_class: StringProperty(
        name="Node Class",
        description="Which kind of map node to create",
        default="SgAnimatedModelNode",
    )

    overwrite: BoolProperty(
        name="Replace If It Exists",
        description=(
            "Re-export the mesh even if a .gam of this name already exists. "
            "Off reuses the existing file, which is what you want after "
            "exporting it by hand"
        ),
        default=False,
    )

    vertex_format: EnumProperty(
        name="Vertex Format",
        description=(
            "Which vertex layout HTAToolchain writes. 15 is what the one "
            "model measured to render correctly in the game's editor uses; "
            "9 is what shipped map decorations were believed to use"
        ),
        items=[
            ("15", "15 — pos, normal, uv, tangent (stride 48)",
             "Measured on a model the game's editor renders"),
            ("9", "9 — pos, normal, colour, 2x uv (stride 44)",
             "Believed to be used by shipped decorations; unconfirmed"),
        ],
        default="15",
    )

    place_on_terrain: BoolProperty(
        name="Place On Terrain",
        description="Drop the object onto the terrain surface (heights are stored relative to it)",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return any(
            getattr(o, "type", None) == "MESH" for o in context.selected_objects
        )

    def invoke(self, context, event):
        if not self.model_name and context.active_object is not None:
            self.model_name = context.active_object.name
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context) -> None:
        layout = self.layout
        layout.prop(self, "model_name")
        if self.model_name:
            layout.label(text=f"Game id: {make_model_id(self.model_name)}", icon="INFO")
        layout.prop(self, "vertex_format")
        layout.prop(self, "place_on_terrain")
        layout.prop(self, "overwrite")

    def execute(self, context):
        meshes = [o for o in context.selected_objects if getattr(o, "type", None) == "MESH"]
        if not meshes:
            self.report({"ERROR"}, "Select a mesh object")
            return {"CANCELLED"}
        if not self.model_name.strip():
            self.report({"ERROR"}, "Enter a name for the model")
            return {"CANCELLED"}

        game_root = self._game_root(context)
        if game_root is None:
            return {"CANCELLED"}

        catalogue_path = self._catalogue_path(context, game_root)
        if catalogue_path is None:
            return {"CANCELLED"}

        model_id = make_model_id(self.model_name)
        relative = os.path.join(CUSTOM_MODEL_DIR, f"{model_id}.gam")
        target = os.path.join(game_root, relative)

        # An existing .gam is USED, not refused. HTAToolchain's exporter
        # expects a scene containing only the model, so on a loaded map
        # it fails on the first object without a material — the terrain.
        # Exporting once by hand and letting this step register the
        # result is therefore the working path, and blocking it because
        # the file already exists defeated the whole point.
        reuse = os.path.isfile(target) and not self.overwrite
        # Whether a USABLE file predates this run. A file this run wrote
        # and then failed to verify is not a fallback: registering it
        # put a truncated .gam into the catalogue and reported success,
        # so the failure only surfaced later as a model the game could
        # not load.
        pre_existing = os.path.isfile(target)
        try:
            if not reuse:
                self._write_gam(context, meshes, target)
                # The file now holds the rotation and scale; the object
                # must stop holding them too, or the node gets them a
                # second time. See gam_export.apply_baked_transform.
                from blender_io.gam_export import apply_baked_transform

                apply_baked_transform(meshes)
        except EXMeditorError as exc:
            if pre_existing and not self.overwrite:
                # A file from an earlier, successful run; registering it
                # is still useful.
                self.report({"WARNING"}, f"{exc.message} Using the existing file.")
                reuse = True
            else:
                self.report(
                    {"ERROR"},
                    f"{exc.message} Export the mesh with File > Export > HTA "
                    f"to {target}, then run this again.",
                )
                return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001 - surface the real cause
            self.report({"ERROR"}, f"Could not write the model: {exc}")
            return {"CANCELLED"}

        # Textures before registration: a model listed in the catalogue
        # whose texture is not where the engine looks shows whatever
        # happens to be bound, which reads as a random texture and sent
        # this investigation after the model format for several rounds.
        if not reuse:
            from blender_io.texture_export import (
                export_textures_beside_model,
                retarget_model_textures,
            )

            textures = export_textures_beside_model(meshes, target)

            # The .gam names its textures in the skin chunk, and the
            # exporter that wrote it took those names from whatever the
            # material was called. Swapping an image in Blender changes
            # neither, so without this the game reloads the old file and
            # the change appears to have done nothing.
            retargeted = retarget_model_textures(target, meshes)
            if retargeted:
                self.report(
                    {"INFO"},
                    f"Retargeted {len(retargeted)} texture reference(s) — "
                    "see the system console",
                )
            if textures.written:
                self.report(
                    {"INFO"}, f"Textures: {textures.summary()}",
                )
            if textures.skipped:
                self.report(
                    {"WARNING"},
                    f"{len(textures.skipped)} texture(s) could not be written "
                    "— the game will show a default for them",
                )

        from formats.exm.model_catalog import register_in_servers, register_model

        try:
            register_model(
                catalogue_path,
                model_id,
                relative.replace(os.sep, "\\"),
                replace=True,
            )
        except EXMeditorError as exc:
            self.report(
                {"ERROR"},
                f"Model written but not registered: {exc.message}. It will not "
                "appear in-game until the catalogue lists it.",
            )
            return {"CANCELLED"}

        # servers.xml as well, and this was the missing half.
        #
        # register_in_servers() was written, tested and documented —
        # "every one of the 114 models the reference map places appears
        # there, without exception" — and then never called from
        # anywhere. The catalogue is the full list of models that
        # exist; servers.xml is the shorter list a given map loads. A
        # model in the first and not the second is catalogued
        # perfectly and never offered, which is exactly the reported
        # symptom: the .gam is written, the entry is correct, and the
        # editor's model list does not show it.
        servers_path = self._servers_index(context, game_root, catalogue_path)
        if servers_path is None:
            self.report(
                {"WARNING"},
                "No servers.xml found for this map, so the model is catalogued "
                "but not listed for the map to load. It may not appear in the "
                "editor's model list.",
            )
        else:
            try:
                # Referenced the way the file's existing entries do.
                # game_root may itself be data\models — the audit ran
                # with it set there — so a path relative to it would
                # read "animmodels.xml" while every neighbouring entry
                # reads "data\models\AnimModels.xml".
                catalogue_ref = _catalogue_reference(servers_path, catalogue_path)
                added = register_in_servers(servers_path, model_id, catalogue_ref)
            except EXMeditorError as exc:
                self.report(
                    {"ERROR"},
                    f"Catalogued, but servers.xml was not updated: {exc.message}. "
                    "The map will not load the model until it is listed there.",
                )
                return {"CANCELLED"}
            if added:
                logger.info("Listed %r in %s", model_id, servers_path)

        # servers.xml is deliberately NOT touched.
        #
        # An earlier version registered there too, reasoning that all
        # 114 models a reference map places appear in it. That was
        # correlation: those 114 ship with the map. Watching the game's
        # own editor integrate a model settled it — the editor adds
        # nothing to servers.xml, and the model works. Writing to a
        # shipped file the editor leaves alone is a risk taken for no
        # benefit.

        # One node for the whole model. The .gam holds every mesh (a
        # vehicle cab ships as 28), so a multi-part object is one model
        # and one map object, not several.
        self._assign(context, meshes[0], model_id)
        for extra in meshes[1:]:
            # The other parts are geometry inside that model, not nodes
            # of their own; parenting keeps them together in Blender
            # without exporting them twice.
            if extra.parent is None:
                extra.parent = meshes[0]

        for mesh_object in meshes:
            self._warn_about_material(mesh_object)

        source = "existing file" if reuse else "newly exported"
        # The catalogue path is NAMED, not just "the model catalogue".
        # Which of several AnimModels.xml files a map resolves through
        # is worked out at run time, and a model registered in one the
        # editor does not read is indistinguishable from one that was
        # never registered — both simply fail to appear in its list.
        self.report(
            {"INFO"},
            f"'{model_id}' ({len(meshes)} mesh part(s)) registered from the "
            f"{source} at {relative}, listed in {catalogue_path}, and assigned "
            "— it will be in world.xml on export",
        )
        return {"FINISHED"}

    # --- steps ---

    def _game_root(self, context) -> str | None:
        from formats.exm.model_catalog import normalise_game_root

        configured = get_game_root(context)
        if not configured:
            self.report(
                {"ERROR"},
                "Set the Game Folder in Preferences > Add-ons > EXMeditor "
                "— a model has to be written into the game to be usable",
            )
            return None
        root = normalise_game_root(configured)
        if root is None:
            self.report({"ERROR"}, f"No 'data' folder found at or above {configured}")
            return None
        return root

    def _servers_index(self, context, game_root: str, catalogue_path: str = "") -> str | None:
        """The servers.xml a model has to be listed in.

        The MAP's own, and this is now read off the editor's log rather
        than reasoned about. M3DEditor announces exactly what it opens::

            DataServer.cpp Loading Servers: data\\maps\\r1m1\\servers.xml
            DataServer.cpp Loading Servers: data\\models\\commonservers.xml

        Two files, and ``data\\models\\servers.xml`` is not one of them.
        An earlier version of this method preferred that file because a
        registration audit found a gap there — a real gap, in a file
        nothing reads, which is why closing it changed nothing and
        writing to it only added entries the game will never look at.

        So the map folder comes first. The manifest route is kept after
        it, and the catalogue's neighbour is gone.
        """
        from formats.exm.model_catalog import (
            _resolve_case_insensitive,
            resolve_game_relative_path,
        )
        from formats.exm.ssl import read_manifest, resolve_in_folder
        from formats.exm.plugin import find_manifest

        source_dir = context.scene.get("exm_source_dir", "") if context else ""
        if source_dir:
            own = _resolve_case_insensitive(os.path.join(source_dir, "servers.xml"))
            if own is not None:
                return own

        if not source_dir:
            return None
        try:
            manifest_path = find_manifest(source_dir)
            if manifest_path is None:
                return None
            reference = read_manifest(manifest_path).file_ref("SERVERS")
            if not reference:
                return None
            return resolve_game_relative_path(
                reference, game_root,
            ) or resolve_in_folder(
                source_dir, os.path.basename(reference.replace("\\", "/")),
            )
        except EXMeditorError:
            return None

    def _catalogue_path(self, context, game_root: str) -> str | None:
        """The AnimModels.xml the current map resolves models through.

        Found through the map's own manifest rather than assumed, so a
        mod with its own catalogue is registered in the right one.
        """
        from formats.exm.model_catalog import (
            _resolve_case_insensitive,
            resolve_game_relative_path,
        )
        from formats.exm.ssl import read_manifest, resolve_in_folder
        from formats.exm.plugin import find_manifest

        source_dir = context.scene.get("exm_source_dir", "")
        candidates: list[str] = []

        if source_dir:
            try:
                manifest_path = find_manifest(source_dir)
                if manifest_path is not None:
                    manifest = read_manifest(manifest_path)
                    reference = manifest.file_ref("SERVERS")
                    if reference:
                        index = resolve_game_relative_path(
                            reference, game_root,
                        ) or resolve_in_folder(
                            source_dir, os.path.basename(reference.replace("\\", "/")),
                        )
                        if index is not None:
                            candidates.extend(self._catalogues_from(index, game_root))
            except EXMeditorError:
                pass

        fallback = _resolve_case_insensitive(
            os.path.join(game_root, "data", "models", "AnimModels.xml")
        )
        if fallback is not None:
            candidates.append(fallback)

        if not candidates:
            self.report(
                {"ERROR"},
                "Could not find AnimModels.xml under the Game Folder — there is "
                "nowhere to register the model",
            )
            return None
        return candidates[0]

    @staticmethod
    def _catalogues_from(index_path: str, game_root: str) -> list[str]:
        """Catalogue files a servers index points at."""
        from formats.exm.model_catalog import (
            _game_path_to_local,
            _read_text,
            _resolve_case_insensitive,
        )

        found: list[str] = []
        try:
            text = _read_text(index_path)
        except OSError:
            return found
        for match in re.finditer(r'\bfile\s*=\s*"([^"]+\.xml)"', text, re.IGNORECASE):
            local = _resolve_case_insensitive(
                _game_path_to_local(match.group(1), game_root)
            )
            if local is not None and local not in found:
                found.append(local)
        return found

    def _write_gam(self, context, objects, target: str) -> None:
        """Write the selected meshes out as one .gam via HTAToolchain."""
        from blender_io.gam_export import export_meshes_to_gam

        os.makedirs(os.path.dirname(target), exist_ok=True)
        export_meshes_to_gam(
            objects, target, context=context, vertex_type=self.vertex_format,
        )

    def _servers_path(self, context, game_root: str) -> str | None:
        """The map's servers.xml, through its own manifest."""
        from formats.exm.model_catalog import resolve_game_relative_path
        from formats.exm.plugin import find_manifest
        from formats.exm.ssl import read_manifest, resolve_in_folder

        source_dir = context.scene.get("exm_source_dir", "")
        if not source_dir:
            return None
        try:
            manifest_path = find_manifest(source_dir)
            if manifest_path is None:
                return None
            reference = read_manifest(manifest_path).file_ref("SERVERS")
        except EXMeditorError:
            return None
        if not reference:
            return None
        return resolve_game_relative_path(reference, game_root) or resolve_in_folder(
            source_dir, os.path.basename(reference.replace("\\", "/")),
        )

    def _warn_about_material(self, obj) -> None:
        """Say plainly when the texture will not survive export.

        HTAToolchain finds textures by NODE NAME, so an Image Texture
        node left with Blender's default name is invisible to it and
        the model arrives untextured — which looks like the material
        being replaced by a random one.
        """
        from blender_io.gam_export import TEXTURE_NODE_NAMES

        mesh = getattr(obj, "data", None)
        materials = [m for m in getattr(mesh, "materials", []) or [] if m is not None]
        for material in materials:
            tree = getattr(material, "node_tree", None)
            nodes = getattr(tree, "nodes", None)
            if nodes is None:
                continue
            try:
                names = set(nodes.keys())
            except (AttributeError, TypeError):
                continue
            if not names & set(TEXTURE_NODE_NAMES):
                self.report(
                    {"WARNING"},
                    f"Material '{material.name}' has no node named 'Diffuse', so "
                    "no texture is written and the game shows a default. Rename "
                    "the Image Texture node to 'Diffuse' in the Shader Editor.",
                )
                return

    def _assign(self, context, obj, model_id: str) -> None:
        obj[CLASS_PROP] = self.node_class
        obj[ASSET_ID_PROP] = model_id
        obj[ORG_REL_PROP] = 1
        obj[HAD_ORG_PROP] = 1
        obj[HAD_ROTATION_PROP] = 0
        obj[HAD_SCALE_PROP] = 0
        obj[NDM_ACTION_PROP] = "0"
        if not obj.get("exm_original_name"):
            obj[NEW_OBJECT_PROP] = 1

        if self.place_on_terrain:
            from addon.assign_operator import _find_terrain, _sample_terrain_height

            terrain = _find_terrain(context.collection)
            if terrain is not None:
                height = _sample_terrain_height(
                    terrain, obj.location[0], obj.location[1],
                )
                if height is not None:
                    x, y, _z = obj.location
                    obj.location = (x, y, height)


_CLASSES = (EXM_OT_create_model,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
