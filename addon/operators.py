# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Add-on operators.

Thin glue only: pick a folder, delegate to the active plugin, hand the
result to ``blender_io``, report what happened. Parsing, validation
and unit conversion all live in the layers below.

Export uses the snapshot strategy (``core.snapshot``): the source map
folder is copied wholesale, then only the files this SDK regenerates
are overwritten. Every map file the SDK doesn't parse — grass, tiles,
lightmaps, scripts — survives byte-identically because it's copied
rather than rebuilt.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty

from blender_io.scene_bridge import (
    build_scene,
    confirm_dynamic_written,
    extract_dynamic_from_collection,
    extract_objects_from_collection,
    extract_obstacles_from_collection,
    extract_roads_from_collection,
    stamped_node_names,
)
from blender_io.terrain_bridge import (
    CELL_SIZE_PROP,
    HEIGHT_SCALE_PROP,
    XY_SCALE_PROP,
    extract_heightmap,
    log_terrain_summary,
)
from core.scene import MapScene
from utils.math import Vector3
from core.node_naming import NodeNameAllocator
from core.prototypes import read_prototype_catalog
from formats.exm.dynamic_scene import (
    model_for_prototype,
    parts_for_prototype,
    resolve_model_id,
    resolve_parts,
    sub_objects_for_prototype,
)
from core.snapshot import (
    backup_files,
    contains_folder,
    copy_map_folder,
    is_same_folder,
    verify_preserved,
)
from core.terrain_transform import offset_height
from blender_io.mesh_provider import MeshProvider
from core.coordinates import (
    DEFAULT_HEIGHT_SCALE,
    DEFAULT_XY_SCALE,
    CoordinateTransform,
)
from formats.exm.model_catalog import (
    ModelCatalog,
    normalise_game_root,
    read_catalogs_from_servers,
)
from formats.exm.plugin import find_map_folder
from formats.exm.ssl import resolve_in_folder
from formats.registry import default_registry
from utils.errors import EXMeditorError
from addon.preferences import (
    blend_tiles,
    get_game_root,
    get_naming_style,
    import_water,
    lightmap_time,
    import_grass,
    import_lighting,
    road_models,
    standard_view_transform,
    terrain_detail,
    textured_roads,
)
from utils.logging import OperatorReportHandler, get_logger

logger = get_logger("addon.operators")


def _addon_version() -> str:
    """The running add-on's version, from the package's bl_info.

    Diagnostics only. Reported on every import so that a Blender
    session still holding a cached copy of an older build is visible
    in the log — otherwise a stale install looks identical to a bug in
    the current one.
    """
    import sys

    # Normal case: the add-on is loaded as a package, so its top-level
    # module is in sys.modules with bl_info attached.
    if __package__:
        package = sys.modules.get(__package__.split(".")[0])
        version = getattr(package, "bl_info", {}).get("version") if package else None
        if version:
            return ".".join(str(part) for part in version)

    # Fallback: read the version straight out of the package's
    # __init__.py, which works even when modules were imported flatly
    # (as they are in the test harness).
    try:
        import ast
        import os as _os

        init_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "__init__.py")
        with open(init_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(getattr(t, "id", None) == "bl_info" for t in node.targets):
                continue
            info = ast.literal_eval(node.value)
            version = info.get("version")
            if version:
                return ".".join(str(part) for part in version)
    except Exception:  # noqa: BLE001 - diagnostics must never break import
        pass

    return "unknown"

# Scene-level custom properties recording where a map came from and how
# it was scaled, so export can default to the same values without the
# user re-entering them.
SOURCE_DIR_PROP = "exm_source_dir"
SCENE_XY_SCALE_PROP = "exm_scene_xy_scale"
SCENE_HEIGHT_SCALE_PROP = "exm_scene_height_scale"
ORIGIN_OFFSET_PROP = "exm_origin_offset"

# Calibrated scale defaults live in core.coordinates, next to the cell
# size they must stay consistent with (see that module for why).


class EXM_OT_import_map(bpy.types.Operator):
    """Import an Ex Machina map folder: terrain and placed objects."""

    bl_idname = "exmachina.import_map"
    bl_label = "Import Map"
    bl_description = "Import an Ex Machina map (select the map folder)"
    bl_options = {"REGISTER", "UNDO"}

    # File-select properties. Blender fills `filepath` with the picked
    # file and `directory` with the folder shown; accepting both means
    # the operator works whether the user selects the .ssl or just
    # navigates into the map folder.
    filepath: StringProperty(
        name="Map File",
        description="The map's .ssl file — its data folder is found automatically",
        subtype="FILE_PATH",
    )

    directory: StringProperty(
        name="Map Folder",
        description="Folder containing the map's .ssl manifest and data files",
        subtype="DIR_PATH",
    )

    filter_glob: StringProperty(default="*.ssl", options={"HIDDEN"})

    xy_scale: FloatProperty(
        name="XY Scale",
        description=(
            "Blender world units per game XY unit. 1.25 reproduces the "
            "proportions verified in-game (one terrain cell = 10 units)."
        ),
        default=DEFAULT_XY_SCALE,
        soft_min=0.0001,
        soft_max=1000.0,
    )
    height_scale: FloatProperty(
        name="Height Scale",
        description="Blender world units per game height unit (1.5 verified in-game)",
        default=DEFAULT_HEIGHT_SCALE,
        soft_min=0.0001,
        soft_max=1000.0,
    )

    centre_on_origin: BoolProperty(
        name="Centre on Origin",
        description=(
            "Place the map's centre at Blender's origin instead of its "
            "corner. A map is thousands of units across, so leaving it in "
            "the +X/+Y quadrant puts everything far from the origin. The "
            "shift is undone on export and never reaches the map files"
        ),
        default=True,
    )

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context) -> None:
        layout = self.layout
        box = layout.box()
        box.label(text="World Scale", icon="INFO")
        box.prop(self, "xy_scale")
        box.prop(self, "height_scale")
    def execute(self, context):
        folder = self._resolve_map_folder()
        if folder is None:
            self.report(
                {"ERROR"},
                "Select a map's .ssl file (or the folder containing one)",
            )
            return {"CANCELLED"}

        handler = OperatorReportHandler(self.report)
        logger.addHandler(handler)
        try:
            # Logged every run: if Blender is still holding a cached
            # copy of an older build, the version here won't match the
            # one that was installed, which is otherwise very hard to
            # spot from the outside.
            logger.info("EXMeditor %s", _addon_version())

            try:
                plugin = default_registry.get_active()
            except EXMeditorError as exc:
                self.report({"ERROR"}, f"No game plugin available: {exc.message}")
                return {"CANCELLED"}

            try:
                scene = plugin.load(folder)
            except EXMeditorError as exc:
                self.report({"ERROR"}, f"Failed to load map: {exc.message}")
                return {"CANCELLED"}

            origin_offset = Vector3.zero()
            if self.centre_on_origin and scene.terrain is not None:
                origin_offset = CoordinateTransform.centre_offset_for_grid(
                    scene.terrain.width, scene.terrain.cell_size,
                )
            # Recorded on the scene so export can undo exactly the same
            # shift, whatever the user picks next time.
            context.scene[ORIGIN_OFFSET_PROP] = list(origin_offset.as_tuple())

            transform = CoordinateTransform(
                xy_scale=self.xy_scale,
                height_scale=self.height_scale,
                origin_offset=origin_offset,
            )
            if not transform.is_uniform:
                # Only matters once real geometry is on screen: a
                # non-uniform scale shears anything rotated, and object
                # rotations can no longer be represented exactly.
                self.report(
                    {"WARNING"},
                    f"XY Scale ({self.xy_scale}) and Height Scale ({self.height_scale}) "
                    "differ, which stretches rotated models. Set them equal for "
                    "geometrically faithful objects.",
                )
            from blender_io.texture_bridge import reset_missing_textures

            reset_missing_textures()
            mesh_provider = self._build_mesh_provider(context, folder, scene, transform)
            resolve_dynamic = self._build_dynamic_resolver(context, mesh_provider)
            result = build_scene(
                scene,
                context.collection,
                transform=transform,
                mesh_provider=mesh_provider,
                resolve_dynamic_model=resolve_dynamic,
                resolve_dynamic_parts=getattr(resolve_dynamic, "parts", None),
                dynamic_assembler=getattr(resolve_dynamic, "assembler", None),
                naming_style=get_naming_style(context),
                game_root=normalise_game_root(get_game_root(context)),
                build_water_surface=import_water(context),
                lightmap_time=lightmap_time(context),
                terrain_detail=terrain_detail(context),
                blend_tiles=blend_tiles(context),
                textured_roads=textured_roads(context),
                road_models=road_models(context),
                import_lighting=import_lighting(context),
                import_grass=import_grass(context),
            )

            if standard_view_transform(context):
                from blender_io.scene_bridge import use_display_referred_colours

                if use_display_referred_colours(context.scene):
                    self.report(
                        {"INFO"},
                        "View transform set to Standard — Filmic washes out "
                        "textures authored for display",
                    )

            # Remember provenance on the Blender scene so export can
            # snapshot the right source folder and invert the same
            # scale without asking again.
            context.scene[SOURCE_DIR_PROP] = folder
            context.scene[SCENE_XY_SCALE_PROP] = self.xy_scale
            context.scene[SCENE_HEIGHT_SCALE_PROP] = self.height_scale

            for warning in scene.warnings:
                self.report({"WARNING"}, warning)

            summary = []
            if result.terrain_object is not None:
                summary.append(f"terrain {scene.terrain.width}x{scene.terrain.height}")
            if result.object_roots:
                summary.append(f"{scene.object_count()} objects")
            if result.road_curves:
                summary.append(f"{len(result.road_curves)} roads")
            if result.obstacle_boxes:
                summary.append(f"{len(result.obstacle_boxes)} collision boxes")
            if result.dynamic_objects:
                summary.append(f"{len(result.dynamic_objects)} dynamic objects")
            if mesh_provider is not None:
                self.report({"INFO"}, mesh_provider.summary())
                self._report_textures()
            if summary:
                self.report({"INFO"}, "Imported " + ", ".join(summary))
            else:
                self.report({"WARNING"}, "Nothing imported — see warnings above")
        finally:
            logger.removeHandler(handler)

        return {"FINISHED"}


    def _report_textures(self) -> None:
        """Say how many textures could not be found.

        Reported as a count rather than silence: a model whose texture
        is missing still imports, so the only sign would be a grey
        object among textured ones — easy to mistake for a modelling
        problem rather than a missing file.
        """
        from blender_io.texture_bridge import missing_textures, unreadable_textures

        def _sample(names) -> str:
            shown = ", ".join(sorted(names)[:4])
            return shown + (f" and {len(names) - 4} more" if len(names) > 4 else "")

        missing = missing_textures()
        if missing:
            self.report(
                {"WARNING"},
                f"{len(missing)} texture(s) not found under the Game Folder: "
                f"{_sample(missing)}",
            )

        # A separate line on purpose. "Not under the game folder" is a
        # path or an install; "there and unreadable" is the file, and
        # the two need different things done about them.
        unreadable = unreadable_textures()
        if unreadable:
            self.report(
                {"WARNING"},
                f"{len(unreadable)} texture(s) found and unreadable by both "
                f"Blender and this add-on: {_sample(unreadable)}",
            )

    def _build_mesh_provider(self, context, map_folder, scene, transform):
        """Set up model loading, or return None to fall back to Empties.

        Every failure path reports why, because "no models appeared" is
        otherwise indistinguishable from "models are off", and the user
        has no way to tell which.
        """
        configured = get_game_root(context)
        if not configured:
            # Not an error — models are simply off. But say so, or "no
            # models appeared" looks like a failure with no explanation.
            self.report(
                {"INFO"},
                "Objects imported as Empties. To load 3D models, set the Game "
                "Folder in Preferences > Add-ons > EXMeditor.",
            )
            return None
        if not os.path.isdir(configured):
            self.report({"WARNING"}, f"Configured Game Folder is not a folder: {configured}")
            return None

        game_root = normalise_game_root(configured)
        if game_root is None:
            self.report(
                {"WARNING"},
                f"No 'data' folder found at or above {configured} — the Game "
                "Folder should be the game's install folder (the one containing 'data').",
            )
            return None
        if os.path.normcase(game_root) != os.path.normcase(os.path.abspath(configured)):
            self.report({"INFO"}, f"Using Game Root: {game_root}")

        if scene.manifest is None:
            self.report({"WARNING"}, "No map manifest — objects stay as Empties")
            return None

        # Catalogue references are game-root-relative paths with Windows
        # separators, and they routinely point OUTSIDE the map folder
        # (e.g. SERVERS = data\maps\r1m1\servers.xml for a map living in
        # data\maps\r1m1-1-1\, and STATICSERVERS = data\models\...).
        # Resolving them against the map folder alone finds nothing.
        catalog_sources = []
        for key in ("SERVERS", "STATICSERVERS"):
            reference = scene.manifest.file_ref(key)
            if not reference:
                continue
            resolved = self._resolve_game_path(reference, map_folder, game_root)
            if resolved is None:
                self.report({"WARNING"}, f"{key} file not found: {reference}")
                continue
            catalog_sources.append(resolved)

        if not catalog_sources:
            self.report(
                {"WARNING"},
                "No model catalogue found under the Game Folder — objects stay as "
                "Empties. Check that it is the folder containing 'data'.",
            )
            return None

        catalog = ModelCatalog()
        for source in catalog_sources:
            try:
                found = read_catalogs_from_servers(source, game_root)
            except EXMeditorError as exc:
                self.report({"WARNING"}, f"Could not read {os.path.basename(source)}: {exc.message}")
                continue
            for entry in found.entries():
                catalog.add(entry)

        if len(catalog) == 0:
            self.report(
                {"WARNING"},
                "Model catalogue is empty — the catalogue files it points to "
                "were not found under the Game Folder.",
            )
            return None

        return MeshProvider(catalog, game_root, transform=transform)

    def _build_dynamic_resolver(self, context, mesh_provider):
        """Return a callable mapping a dynamic object to a model id.

        The second placement layer names objects by Prototype, which
        resolves through the game's global prototype definitions —
        files no map manifest references, so they have to be found
        under the game folder directly.
        """
        if mesh_provider is None:
            return None
        configured = get_game_root(context)
        game_root = normalise_game_root(configured) if configured else None
        if game_root is None:
            return None

        prototypes = read_prototype_catalog(game_root)
        if len(prototypes) == 0:
            self.report(
                {"WARNING"},
                "No prototype definitions found under the Game Folder — "
                "dynamic objects will import without models",
            )
            return None

        catalog = mesh_provider.catalog

        def resolve(obj):
            return resolve_model_id(obj, prototypes, catalog)

        # The parts of a composite prototype ride along as an attribute:
        # one resolver object, two questions.
        resolve.parts = lambda obj: resolve_parts(obj, prototypes, catalog)

        class _Assembler:
            """Answers by prototype NAME, for prefab contents."""

            @staticmethod
            def model_of(name):
                return model_for_prototype(name, prototypes, catalog)

            @staticmethod
            def parts_of(name):
                return parts_for_prototype(name, prototypes, catalog)

            @staticmethod
            def sub_objects_of(name):
                return sub_objects_for_prototype(name, prototypes)

        resolve.assembler = _Assembler()
        return resolve

    def _resolve_game_path(self, reference: str, map_folder: str, game_root: str) -> str | None:
        """Locate a manifest file reference on disk.

        Tries, in order: the path relative to Game Root (the usual case
        for catalogue references), then the bare filename inside the map
        folder (the usual case for per-map files). Both lookups are
        case-insensitive, since shipped map data is inconsistently cased
        and only Windows forgives that.
        """
        from formats.exm.model_catalog import resolve_game_relative_path

        resolved = resolve_game_relative_path(reference, game_root)
        if resolved is not None:
            return resolved
        return resolve_in_folder(map_folder, os.path.basename(reference.replace("\\", "/")))


    def _resolve_map_folder(self) -> str | None:
        """Work out which folder holds the map data.

        Accepts either a picked ``.ssl`` file — the normal case, since
        that is what a user thinks of as "the map" — or a folder, which
        is what earlier versions took and what the file browser gives
        when nothing is selected.
        """
        picked = (self.filepath or "").strip()
        if picked and os.path.isfile(picked):
            resolved = find_map_folder(picked)
            if resolved is not None:
                return resolved

        folder = (self.directory or "").strip()
        if folder and os.path.isdir(folder):
            return folder

        # A path that is a directory can arrive in either property
        # depending on how the browser was used.
        if picked and os.path.isdir(picked):
            return picked
        return None


class EXM_OT_export_map(bpy.types.Operator):
    """Export the edited map back to a map folder.

    Writes terrain and objects; every other file in the source map
    folder is copied through untouched.
    """

    bl_idname = "exmachina.export_map"
    bl_label = "Export Map"
    bl_description = "Export the edited map (terrain + objects) to a folder"
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(
        name="Output Folder",
        description=(
            "Folder to write the map into. The original map folder is "
            "copied here first, then the edited files are written over it."
        ),
        subtype="DIR_PATH",
    )

    xy_scale: FloatProperty(
        name="XY Scale",
        description="Must match the XY Scale used on import to invert correctly",
        default=DEFAULT_XY_SCALE,
        soft_min=0.0001,
        soft_max=1000.0,
    )
    height_scale: FloatProperty(
        name="Height Scale",
        description="Must match the Height Scale used on import to invert correctly",
        default=DEFAULT_HEIGHT_SCALE,
        soft_min=0.0001,
        soft_max=1000.0,
    )
    ground_level_offset: FloatProperty(
        name="Ground Level Offset",
        description=(
            "Subtracted from every height sample (game units) before writing. "
            "Not part of the file format — 0 unless you have a specific reason."
        ),
        default=0.0,
    )

    def invoke(self, context, event):
        # Default to the scale the map was imported with.
        self.xy_scale = context.scene.get(SCENE_XY_SCALE_PROP, DEFAULT_XY_SCALE)
        self.height_scale = context.scene.get(SCENE_HEIGHT_SCALE_PROP, DEFAULT_HEIGHT_SCALE)

        # And to the map's own folder, so accepting straight away saves
        # in place. Without this the browser opens wherever it was last
        # used — and its Accept takes the folder currently SHOWN, which
        # is the folder of maps, one level above. Exporting there wrote
        # a copy of the map next to the other maps and left the map
        # itself untouched: "the changes were not saved".
        source_dir = context.scene.get(SOURCE_DIR_PROP)
        if source_dir and os.path.isdir(source_dir):
            self.directory = os.path.join(source_dir, "")

        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context) -> None:
        layout = self.layout
        layout.prop(self, "directory")
        box = layout.box()
        box.label(text="World Scale (must match import)", icon="INFO")
        box.prop(self, "xy_scale")
        box.prop(self, "height_scale")
        box.prop(self, "ground_level_offset")

    def execute(self, context):
        folder = self.directory
        if not folder or not os.path.isdir(folder):
            self.report({"ERROR"}, "Please select a valid output folder")
            return {"CANCELLED"}

        source_dir = context.scene.get(SOURCE_DIR_PROP)
        if not source_dir or not os.path.isdir(source_dir):
            self.report(
                {"ERROR"},
                "No imported map found in this scene — import a map before exporting",
            )
            return {"CANCELLED"}

        if contains_folder(folder, source_dir):
            self.report(
                {"ERROR"},
                f"{folder} is the folder that CONTAINS {os.path.basename(source_dir)}, "
                "not a map folder. Exporting there copies the map next to the "
                "other maps and writes the edits into files the game never "
                "reads — the map itself stays as it was. Go into "
                f"{os.path.basename(source_dir)} and accept from inside it, or "
                "type a new map folder name",
            )
            return {"CANCELLED"}

        handler = OperatorReportHandler(self.report)
        logger.addHandler(handler)
        try:
            try:
                plugin = default_registry.get_active()
            except EXMeditorError as exc:
                self.report({"ERROR"}, f"No game plugin available: {exc.message}")
                return {"CANCELLED"}

            stored_offset = context.scene.get(ORIGIN_OFFSET_PROP)
            origin_offset = (
                Vector3(*stored_offset) if stored_offset else Vector3.zero()
            )
            transform = CoordinateTransform(
                xy_scale=self.xy_scale,
                height_scale=self.height_scale,
                origin_offset=origin_offset,
            )

            # 1. Rebuild a game-space MapScene from what's in Blender.
            try:
                scene = self._collect_scene(context, transform, source_dir)
            except EXMeditorError as exc:
                self.report({"ERROR"}, f"Cannot read the edited map: {exc.message}")
                return {"CANCELLED"}

            problems = plugin.validate(scene)
            if problems:
                for problem in problems:
                    self.report({"ERROR"}, problem)
                return {"CANCELLED"}

            # 2. Snapshot: copy every original file, so anything this
            # SDK doesn't write survives untouched. Exporting back into
            # the map's own folder (the usual "save and test in-game"
            # workflow) needs no copy — the files are already there —
            # but the ones about to be overwritten get backed up first.
            in_place = is_same_folder(source_dir, folder)
            try:
                if in_place:
                    backup_files(folder, plugin.saved_filenames(scene, folder))
                else:
                    copy_map_folder(source_dir, folder, overwrite=True)
            except EXMeditorError as exc:
                self.report({"ERROR"}, f"Could not prepare output folder: {exc.message}")
                return {"CANCELLED"}

            # 3. Overwrite only what the SDK regenerates.
            try:
                plugin.save(scene, folder)
            except (EXMeditorError, NotImplementedError) as exc:
                self.report({"ERROR"}, f"Export failed: {exc}")
                return {"CANCELLED"}

            # The new dynamic records are in the file now: their
            # objects stop being new, and the next export updates them.
            confirm_dynamic_written(context.collection)

            # 4. Confirm nothing unexpected changed.
            unexpected = verify_preserved(source_dir, folder, plugin.saved_filenames(scene, folder))
            for problem in unexpected:
                self.report({"WARNING"}, f"Unexpected change: {problem}")

            written = sorted(plugin.saved_filenames(scene, folder))
            where = "in place" if in_place else f"to {folder}"
            self.report({"INFO"}, f"Exported {where} (wrote: {', '.join(written)})")
        finally:
            logger.removeHandler(handler)

        return {"FINISHED"}

    def _collect_scene(self, context, transform: CoordinateTransform, source_dir: str) -> MapScene:
        """Rebuild a game-space MapScene from the Blender scene.

        Reuses the plugin's own loader for the parts that aren't edited
        in Blender (manifest, root attributes) rather than reconstructing
        them, then overwrites terrain and objects with what's actually
        in the viewport.
        """
        plugin = default_registry.get_active()
        scene = plugin.load(source_dir)  # baseline: manifest + <World> attributes
        # Kept before clearing: name allocation must see every node the
        # map already contains, or a new object could be handed a name
        # that is already taken.
        baseline_objects = list(scene.objects)
        scene.terrain = None
        scene.objects = []
        scene.roads = None
        scene.obstacles = None

        collection = context.collection

        terrain_obj = self._find_terrain_object(collection)
        if terrain_obj is not None:
            game_heightmap = extract_heightmap(
                terrain_obj,
                cell_size=terrain_obj.get(CELL_SIZE_PROP),
                transform=transform,
            )
            log_terrain_summary(
                game_heightmap, xy_scale=self.xy_scale, height_scale=self.height_scale,
            )
            if self.ground_level_offset != 0.0:
                game_heightmap = offset_height(game_heightmap, -self.ground_level_offset)
            scene.terrain = game_heightmap

        # Object heights are stored relative to the ground, so
        # extraction needs the same terrain the import resolved against.
        # Objects created in Blender need names from the map's own
        # LastId sequence; the allocator starts above both the recorded
        # LastId and the highest number actually in use.
        allocator = NodeNameAllocator.from_scene(
            baseline_objects, scene.world_root_attributes,
            reserved=stamped_node_names(collection),
        )
        scene.objects = extract_objects_from_collection(
            collection,
            transform=transform,
            terrain=scene.terrain,
            allocator=allocator,
        )
        if allocator.issued and scene.world_root_attributes is not None:
            scene.world_root_attributes["LastId"] = str(allocator.last_id)
            logger.info(
                "Allocated %d new node name(s): %s",
                len(allocator.issued), ", ".join(allocator.issued[:5]),
            )
        extracted_roads = extract_roads_from_collection(
            collection, transform=transform, terrain=scene.terrain,
        )
        if extracted_roads is not None:
            scene.roads = extracted_roads
        extracted_obstacles = extract_obstacles_from_collection(collection, transform=transform)
        if extracted_obstacles is not None:
            scene.obstacles = extracted_obstacles
        prototypes = self._prototype_catalog(context)
        scene.dynamic_scene = extract_dynamic_from_collection(
            collection, scene.dynamic_scene, transform=transform,
            children_for=self._record_children(prototypes),
        )
        self._warn_about_unplaceable(scene.dynamic_scene, prototypes)
        return scene

    @staticmethod
    def _prototype_catalog(context):
        """The game's prototype catalogue, or None without a game folder."""
        configured = get_game_root(context)
        game_root = normalise_game_root(configured) if configured else None
        if game_root is None:
            return None
        try:
            prototypes = read_prototype_catalog(game_root)
        except Exception as exc:  # noqa: BLE001 - the export must not die on the catalogue
            logger.warning("could not read the prototype catalogue: %s", exc)
            return None
        return prototypes if len(prototypes) else None

    @staticmethod
    def _record_children(prototypes):
        """What child elements a new dynamic record needs, by prototype
        — a turret's ``<Parts/>``. None without a catalogue."""
        from core.prototype_placement import record_children

        if prototypes is None:
            return None
        return lambda name: record_children(prototypes.get(name))

    def _warn_about_unplaceable(self, dynamic_scene, prototypes) -> None:
        """A record naming a part (a pillbox, a cannon) shows nothing in
        the game. It is written as it is — the file is the user's — and
        reported, with what to do."""
        from core.prototype_placement import unplaceable_records

        if dynamic_scene is None or prototypes is None:
            return
        for name, prototype, cls in unplaceable_records(dynamic_scene, prototypes):
            self.report(
                {"WARNING"},
                f"dynamicscene.xml: {name} is a {cls} ({prototype}) — a part, not a "
                "thing the game places; it will be invisible. Select it in Blender, "
                "run Assign Map Object and pick the turret it belongs to.",
            )

    @staticmethod
    def _find_terrain_object(collection) -> bpy.types.Object | None:
        """Locate the imported terrain mesh anywhere under ``collection``."""
        for obj in collection.objects:
            if obj.type == "MESH" and CELL_SIZE_PROP in obj:
                return obj
        for child in collection.children:
            found = EXM_OT_export_map._find_terrain_object(child)
            if found is not None:
                return found
        return None


_CLASSES = (EXM_OT_import_map, EXM_OT_export_map)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
