# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build the model palette Blender's Asset Browser draws.

One operator, because the window that shows the result is Blender's
own. See ``blender_io/asset_library.py`` for why the assets go in the
current file rather than a library on disk.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from addon.asset_operator import SOURCE_DIR_PROP, get_library
from addon.preferences import get_game_root
from blender_io.asset_library import (
    ASSET_COLLECTION,
    build_asset_previews,
    clear_asset_previews,
)
from blender_io.world_bridge import ASSET_ID_PROP
from core.asset_previews import DEFAULT_LIMIT, plan_previews, unresolved
from utils.logging import OperatorReportHandler, get_logger

logger = get_logger("addon.asset_previews")

SCOPE_MAP = "MAP"
SCOPE_CATEGORY = "CATEGORY"
SCOPE_ALL = "ALL"


def _map_name(context) -> str:
    """The map the open scene came from, for the catalogue tree.

    Taken from the folder the import remembered on the scene. Falls
    back to a fixed name rather than an empty one: a branch called
    nothing is worse than a branch called "Unsorted".
    """
    scene = getattr(context, "scene", None)
    folder = ""
    if scene is not None:
        try:
            folder = scene.get(SOURCE_DIR_PROP) or ""
        except (AttributeError, TypeError):
            folder = ""
    name = os.path.basename(os.path.normpath(folder)) if folder else ""
    return name or "Unsorted"


def _ids_in_scene(context) -> set:
    """Every model id the open scene already places."""
    found = set()
    for obj in getattr(bpy.data, "objects", []) or []:
        asset_id = obj.get(ASSET_ID_PROP)
        if asset_id:
            found.add(asset_id)
    return found


class EXM_OT_build_asset_previews(bpy.types.Operator):
    """Import models and mark them as assets, so the Asset Browser can
    show them as thumbnails to drag into the scene."""

    bl_idname = "exmachina.build_asset_previews"
    bl_label = "Build Asset Previews"
    bl_description = (
        "Import game models into a hidden collection and mark them as "
        "assets with rendered thumbnails. Open an Asset Browser and set "
        "its library to 'Current File' to see them as a grid"
    )
    bl_options = {"REGISTER", "UNDO"}

    scope: EnumProperty(
        name="Models",
        description="Which models to build previews for",
        items=[
            (SCOPE_MAP, "Used by This Map",
             "Only the models the open scene already places — the map's "
             "own vocabulary, usually a few dozen"),
            (SCOPE_CATEGORY, "One Category",
             "Every model in a single category"),
            (SCOPE_ALL, "Everything",
             "The whole catalogue. Over a thousand models on a full "
             "install, so mind the limit"),
        ],
        default=SCOPE_MAP,
    )
    category: StringProperty(
        name="Category",
        description="Category to build, when the scope is one category",
        default="buildings",
    )
    limit: IntProperty(
        name="Limit",
        description=(
            "Stop after this many, most-used first. 0 builds all of them"
        ),
        default=DEFAULT_LIMIT, min=0, max=5000,
    )
    generate_previews: BoolProperty(
        name="Render Thumbnails",
        description=(
            "Render a preview image per model. Off marks the assets "
            "without pictures, which is much faster and much less useful"
        ),
        default=True,
    )

    def execute(self, context):
        handler = OperatorReportHandler(self)
        logger.addHandler(handler)
        try:
            return self._run(context)
        finally:
            logger.removeHandler(handler)

    def _run(self, context):
        library = get_library(context)
        if library is None:
            self.report(
                {"ERROR"},
                "Set the Game Folder in Preferences > Add-ons > ExMachina "
                "SDK — the model catalogue lives under it",
            )
            return {"CANCELLED"}
        if not len(library):
            self.report({"WARNING"}, "The model catalogue is empty")
            return {"CANCELLED"}

        asset_ids = None
        category = None
        if self.scope == SCOPE_MAP:
            asset_ids = _ids_in_scene(context)
            if not asset_ids:
                self.report(
                    {"WARNING"},
                    "No imported objects in this file to take a model list "
                    "from. Import a map first, or choose another scope",
                )
                return {"CANCELLED"}
        elif self.scope == SCOPE_CATEGORY:
            category = self.category

        chosen = plan_previews(
            library, category=category, asset_ids=asset_ids, limit=self.limit,
        )
        if not chosen:
            self.report(
                {"WARNING"},
                f"Nothing to build: no model in {category or 'that selection'} "
                "resolved to a file on disk",
            )
            return {"CANCELLED"}

        provider = self._mesh_provider(context, library)
        if provider is None:
            return {"CANCELLED"}

        result = build_asset_previews(
            chosen, provider,
            scene=getattr(context, "scene", None),
            generate_previews=self.generate_previews,
            map_name=_map_name(context),
        )

        gone = unresolved(library, category=category)
        if gone:
            logger.info(
                "%s catalogued model(s) have no file on disk and were "
                "skipped", len(gone),
            )

        if not result.catalog_file:
            self.report(
                {"WARNING"},
                "Save this .blend and build again to get the Ex Machina > "
                "map > category tree — Blender keeps catalogues in a file "
                "beside the .blend. Tags work without saving",
            )
        self.report(
            {"INFO"},
            f"{result.built} model(s) added to the asset browser"
            f"{f', {result.reused} already there' if result.reused else ''}"
            f"{f', {len(result.failed)} could not be read' if result.failed else ''}"
            ". Open an Asset Browser and set its library to 'Current File'",
        )
        return {"FINISHED"}

    def _mesh_provider(self, context, library):
        from blender_io.mesh_provider import MeshProvider
        from formats.exm.model_catalog import (
            normalise_game_root,
            read_catalogs_from_servers,
        )

        root = normalise_game_root(get_game_root(context))
        if not root:
            self.report({"ERROR"}, "The Game Folder is not set")
            return None
        try:
            catalog = read_catalogs_from_servers(
                os.path.join(root, "data", "models", "servers.xml"), root,
            )
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            self.report({"ERROR"}, f"Could not read the model catalogue: {exc}")
            return None
        return MeshProvider(catalog, root)


class EXM_OT_clear_asset_previews(bpy.types.Operator):
    """Remove every built preview from the file."""

    bl_idname = "exmachina.clear_asset_previews"
    bl_label = "Clear Asset Previews"
    bl_description = (
        "Unmark and delete the imported preview models. The palette is "
        "derived from the game folder, so it should not outlive a change "
        "of folder"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = clear_asset_previews()
        self.report(
            {"INFO"},
            f"{removed} preview model(s) removed"
            if removed else f"Nothing to remove — no {ASSET_COLLECTION}",
        )
        return {"FINISHED"}


_CLASSES = (EXM_OT_build_asset_previews, EXM_OT_clear_asset_previews)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
