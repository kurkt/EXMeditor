# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Asset browsing, model replacement and map validation.

Three operators sharing one dependency — the asset library — which is
why they live together rather than in three modules that would each
build their own half-catalogue.

The library is cached per game folder. Building it reads a
thousand-entry catalogue, which is fast enough once and wasteful on
every keystroke of a search box.
"""

from __future__ import annotations

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty

from addon.preferences import get_game_root
from blender_io.world_bridge import ASSET_ID_PROP, CLASS_PROP
from core.assets import build_library, measure_asset
from core.map_validation import validate_map
from utils.errors import EXMeditorError
from utils.logging import get_logger

logger = get_logger("addon.assets")

SOURCE_DIR_PROP = "exm_source_dir"

#: Cached library, keyed by (game folder, map folder). Rebuilding it on
#: every search keystroke would re-read a thousand catalogue entries.
_LIBRARY_CACHE: dict[tuple[str, str], object] = {}


def get_library(context, *, refresh: bool = False):
    """The asset library for the current map, or ``None``.

    Returns ``None`` rather than an empty library when the game folder
    is unset: "no assets" and "cannot look" need different messages,
    and a caller that cannot tell them apart gives misleading advice.
    """
    from formats.exm.model_catalog import normalise_game_root
    from formats.exm.model_trace import build_catalogue_with_sources
    from formats.exm.plugin import find_manifest
    from formats.exm.ssl import read_manifest
    from formats.exm.world import read_world
    from formats.exm.ssl import resolve_in_folder

    configured = get_game_root(context)
    source_dir = context.scene.get(SOURCE_DIR_PROP, "")
    if not configured or not source_dir:
        return None

    game_root = normalise_game_root(configured)
    if game_root is None:
        return None

    key = (game_root, source_dir)
    if not refresh and key in _LIBRARY_CACHE:
        return _LIBRARY_CACHE[key]

    manifest_path = find_manifest(source_dir)
    if manifest_path is None:
        return None

    try:
        manifest = read_manifest(manifest_path)
        catalog, _sources, _problems = build_catalogue_with_sources(
            manifest, source_dir, game_root,
        )
    except EXMeditorError as exc:
        logger.warning("could not build the asset library: %s", exc)
        return None

    if len(catalog) == 0:
        return None

    # Usage counts come from the map itself, so the browser can put the
    # asset placed 219 times above the one placed once.
    usage: dict[str, int] = {}
    world_path = resolve_in_folder(source_dir, "world.xml")
    if world_path is not None:
        try:
            objects, _root = read_world(world_path)
        except EXMeditorError:
            objects = []
        for instance in _walk(objects):
            if instance.asset_id:
                usage[instance.asset_id] = usage.get(instance.asset_id, 0) + 1

    library = build_library(catalog, game_root=game_root, usage_counts=usage)
    _LIBRARY_CACHE[key] = library
    return library


def _walk(objects):
    for instance in objects:
        yield instance
        yield from _walk(instance.children)


class EXM_OT_browse_assets(bpy.types.Operator):
    """List available models, by category or search."""

    bl_idname = "exmachina.browse_assets"
    bl_label = "Browse Assets"
    bl_description = (
        "List the models available to place, with their category, size and "
        "how often this map already uses them"
    )
    bl_options = {"REGISTER"}

    search: StringProperty(
        name="Search",
        description="Filter by id, name or category. Leave empty to list categories",
    )

    category: StringProperty(
        name="Category",
        description="Show only this category, e.g. 'Buildings'",
    )

    limit: IntProperty(
        name="Limit",
        description="How many assets to list",
        default=40,
        min=1,
        max=1000,
    )

    def execute(self, context):
        library = get_library(context)
        if library is None:
            self.report(
                {"ERROR"},
                "No asset library — import a map and set the Game Folder in "
                "Preferences > Add-ons > EXMeditor",
            )
            return {"CANCELLED"}

        if self.search:
            self._print_search(library)
        elif self.category:
            self._print_category(library)
        else:
            self._print_overview(library)

        self.report(
            {"INFO"}, f"{len(library)} asset(s) — listing in the console",
        )
        return {"FINISHED"}

    def _print_overview(self, library) -> None:
        print("")
        print("=== Asset Library ===")
        print(f"{len(library)} model(s) available")
        print("")
        print("Categories:")
        for name, count in library.categories().items():
            print(f"  {name:<22} {count:5d}")
        print("")
        print("Most used on this map:")
        for asset in library.most_used(15):
            print(f"  {asset.asset_id:<26} {asset.usage_count:5d}  {asset.full_category}")
        print("")
        print("Set Category or Search to list individual assets.")

    def _print_category(self, library) -> None:
        assets = library.in_category(self.category)
        print("")
        print(f"=== {self.category} ({len(assets)}) ===")
        subs = library.subcategories(self.category)
        if subs:
            print("Sub-categories: " + ", ".join(f"{k} ({v})" for k, v in subs.items()))
            print("")
        for asset in assets[: self.limit]:
            self._print_asset(asset)
        if len(assets) > self.limit:
            print(f"  ... and {len(assets) - self.limit} more")

    def _print_search(self, library) -> None:
        results = library.search(self.search, limit=self.limit)
        print("")
        print(f"=== Search '{self.search}' ({len(results)} shown) ===")
        for asset in results:
            self._print_asset(asset)

    @staticmethod
    def _print_asset(asset) -> None:
        used = f"used {asset.usage_count}x" if asset.usage_count else "unused here"
        print(f"  {asset.asset_id:<26} {asset.full_category:<26} {used}")


class EXM_OT_replace_model(bpy.types.Operator):
    """Swap the model on the selected map objects."""

    bl_idname = "exmachina.replace_model"
    bl_label = "Replace Model"
    bl_description = (
        "Change which model the selected object(s) draw, keeping their "
        "position, rotation, scale and node id"
    )
    bl_options = {"REGISTER", "UNDO"}

    model_id: StringProperty(
        name="New Model",
        description="Model id to switch to, e.g. 'house3'",
    )

    reload_mesh: EnumProperty(
        name="Geometry",
        description="Whether to load the new model's mesh now",
        items=[
            ("RELOAD", "Load New Mesh", "Replace the displayed geometry as well"),
            ("KEEP", "Keep Current Mesh", "Change the id only; geometry updates on re-import"),
        ],
        default="RELOAD",
    )

    @classmethod
    def poll(cls, context):
        return any(CLASS_PROP in obj for obj in context.selected_objects)

    def invoke(self, context, event):
        active = context.active_object
        if active is not None and ASSET_ID_PROP in active:
            self.model_id = active[ASSET_ID_PROP]
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        model_id = self.model_id.strip()
        if not model_id:
            self.report({"ERROR"}, "Enter a model id")
            return {"CANCELLED"}

        targets = [obj for obj in context.selected_objects if CLASS_PROP in obj]
        if not targets:
            self.report({"ERROR"}, "No map objects selected")
            return {"CANCELLED"}

        library = get_library(context)
        if library is not None and model_id not in library:
            near = [a.asset_id for a in library.search(model_id, limit=5)]
            suggestion = f" Did you mean: {', '.join(near)}?" if near else ""
            self.report({"ERROR"}, f"'{model_id}' is not in the catalogue.{suggestion}")
            return {"CANCELLED"}

        mesh = None
        if self.reload_mesh == "RELOAD" and library is not None:
            mesh = self._load_mesh(context, library, model_id)

        for obj in targets:
            obj[ASSET_ID_PROP] = model_id
            if mesh is not None and obj.type == "MESH":
                obj.data = mesh

        note = "" if mesh is not None else " (id changed; geometry unchanged)"
        self.report({"INFO"}, f"{len(targets)} object(s) now use '{model_id}'{note}")
        return {"FINISHED"}

    def _load_mesh(self, context, library, model_id):
        """Build the new model's mesh, or return None if unavailable."""
        from blender_io.mesh_bridge import build_model_mesh
        from core.coordinates import (
            DEFAULT_HEIGHT_SCALE,
            DEFAULT_XY_SCALE,
            CoordinateTransform,
        )
        from formats.exm.gam import read_model

        asset = library.get(model_id)
        if asset is None or asset.resolved_path is None:
            self.report(
                {"WARNING"},
                f"'{model_id}' has no installed .gam — id changed, geometry kept",
            )
            return None

        # The scale the map was imported with, so a replaced model
        # matches the objects around it.
        transform = CoordinateTransform(
            xy_scale=context.scene.get("exm_scene_xy_scale", DEFAULT_XY_SCALE),
            height_scale=context.scene.get("exm_scene_height_scale", DEFAULT_HEIGHT_SCALE),
        )
        try:
            model = read_model(asset.resolved_path)
        except EXMeditorError as exc:
            self.report({"WARNING"}, f"Could not read '{model_id}': {exc.message}")
            return None

        return build_model_mesh(model, f"ExM_Model_{model_id}", transform=transform)


class EXM_OT_validate_map(bpy.types.Operator):
    """Check the map for problems before exporting."""

    bl_idname = "exmachina.validate_map"
    bl_label = "Validate Map"
    bl_description = (
        "Check every object for missing models, bad scales and placements "
        "outside the playable area"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        source_dir = context.scene.get(SOURCE_DIR_PROP, "")
        if not source_dir:
            self.report({"ERROR"}, "Import a map first")
            return {"CANCELLED"}

        try:
            scene = self._collect(context, source_dir)
        except EXMeditorError as exc:
            self.report({"ERROR"}, f"Validation failed: {exc.message}")
            return {"CANCELLED"}

        library = get_library(context)
        report = validate_map(scene, library=library, bounds=self._bounds(scene))

        print("")
        print("=== Map Validation ===")
        if library is None:
            print("(no asset library — model ids were not checked)")
        for line in report.summary_lines():
            print(line)

        if report.errors:
            self.report(
                {"ERROR"},
                f"{len(report.errors)} error(s), {len(report.warnings)} warning(s) "
                "— details in the console",
            )
        elif report.warnings:
            self.report({"WARNING"}, f"{len(report.warnings)} warning(s) — see console")
        else:
            self.report({"INFO"}, "Map validates cleanly")
        return {"FINISHED"}

    @staticmethod
    def _collect(context, source_dir):
        """Rebuild the scene as it would be exported."""
        from addon.operators import EXM_OT_export_map
        from core.coordinates import (
            DEFAULT_HEIGHT_SCALE,
            DEFAULT_XY_SCALE,
            CoordinateTransform,
        )

        exporter = EXM_OT_export_map()
        exporter.xy_scale = context.scene.get("exm_scene_xy_scale", DEFAULT_XY_SCALE)
        exporter.height_scale = context.scene.get(
            "exm_scene_height_scale", DEFAULT_HEIGHT_SCALE,
        )
        exporter.ground_level_offset = 0.0
        transform = CoordinateTransform(
            xy_scale=exporter.xy_scale, height_scale=exporter.height_scale,
        )
        return exporter._collect_scene(context, transform, source_dir)

    @staticmethod
    def _bounds(scene):
        """Playable area from the manifest, if it declares one."""
        if scene.manifest is None:
            return None
        return scene.manifest.safe_bounds


_CLASSES = (EXM_OT_browse_assets, EXM_OT_replace_model, EXM_OT_validate_map)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    _LIBRARY_CACHE.clear()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
