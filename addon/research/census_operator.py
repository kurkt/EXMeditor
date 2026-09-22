# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The World Census operator.

Reports what is in ``world.xml``, as opposed to what the SDK managed to
read. Every other report counts parsed results and therefore cannot
mention what it skipped; this one reads the XML directly, so a node
class or attribute the SDK ignores still shows up.

The goal is a provable statement rather than an optimistic one: either
"100% of this file is accounted for", or a named list of everything
that isn't.
"""

from __future__ import annotations

import json
import os

import bpy
from bpy.props import BoolProperty, StringProperty

from core.census import MODELLED_ATTRIBUTES, census_dynamic_scene, census_world
from formats.exm.ssl import resolve_in_folder
from utils.errors import EXMeditorError
from utils.logging import get_logger

logger = get_logger("addon.census")

SOURCE_DIR_PROP = "exm_source_dir"


class EXM_OT_world_census(bpy.types.Operator):
    """Account for every node and attribute in world.xml."""

    bl_idname = "exmachina.world_census"
    bl_label = "World Census"
    bl_description = (
        "Count every node class and attribute in world.xml — including the "
        "ones this SDK does not handle — so nothing is silently skipped"
    )
    bl_options = {"REGISTER"}

    map_folder: StringProperty(
        name="Map Folder",
        description="Map to census. Leave empty to use the map imported into this scene",
        subtype="DIR_PATH",
    )

    focus_class: StringProperty(
        name="Focus On Class",
        description=(
            "Optional: a node class (e.g. 'SgSpriteNode'). Prints its "
            "attributes and sample values instead of the summary"
        ),
    )

    list_attributes: BoolProperty(
        name="List Uninterpreted Attributes",
        description="Show every attribute the SDK preserves but assigns no meaning",
        default=True,
    )

    json_path: StringProperty(
        name="JSON Report",
        description="Optional: write the full census to this file",
        subtype="FILE_PATH",
    )

    def execute(self, context):
        folder = self.map_folder or context.scene.get(SOURCE_DIR_PROP, "")
        if not folder or not os.path.isdir(folder):
            self.report(
                {"ERROR"},
                "No map to census — import a map first, or set Map Folder",
            )
            return {"CANCELLED"}

        world_path = resolve_in_folder(folder, "world.xml")
        if world_path is None:
            self.report({"ERROR"}, f"world.xml not found in {folder}")
            return {"CANCELLED"}

        try:
            census = census_world(world_path)
        except EXMeditorError as exc:
            self.report({"ERROR"}, f"Census failed: {exc.message}")
            return {"CANCELLED"}

        if self.focus_class:
            for line in census.class_detail_lines(self.focus_class):
                print(line)
            self.report({"INFO"}, f"Census for '{self.focus_class}' printed to the console")
            return {"FINISHED"}

        print("")
        for line in census.summary_lines():
            print(line)
        if self.list_attributes:
            print("")
            for line in census.attribute_lines():
                print(line)

        self._census_dynamic_scene(folder)

        if self.json_path:
            self._write_json(census)

        self._report_verdict(census)
        return {"FINISHED"}

    def _census_dynamic_scene(self, folder: str) -> None:
        """Report the second placement layer.

        world.xml is not the only file that places objects, and a
        report covering only it can say "everything imported" while
        thousands of objects sit unread in another file.
        """
        path = resolve_in_folder(folder, "dynamicscene.xml")
        if path is None:
            return
        try:
            census = census_dynamic_scene(path)
        except EXMeditorError as exc:
            self.report({"WARNING"}, f"Could not census dynamicscene.xml: {exc.message}")
            return

        if census.total_objects == 0:
            return

        print("")
        for line in census.summary_lines():
            print(line)

        # Split by whether a prototype names a model the SDK can already
        # find: those two groups need completely different work.
        catalog = self._load_catalogue(folder)
        if catalog is not None:
            print("")
            for line in census.resolvability_lines(catalog):
                print(line)
            direct, needs = census.split_by_resolvability(catalog)
            self.report(
                {"WARNING"},
                f"dynamicscene.xml: {sum(c for _, c in direct)} object(s) name a known "
                f"model, {sum(c for _, c in needs)} need a prototype definition file",
            )
        else:
            self.report(
                {"WARNING"},
                f"dynamicscene.xml places {census.placed_objects} further object(s) "
                f"across {len(census.prototypes)} prototype(s) — none are imported",
            )

    def _load_catalogue(self, folder: str):
        """The model catalogue, or None if the game folder isn't set."""
        from addon.preferences import get_game_root
        from formats.exm.model_catalog import normalise_game_root
        from formats.exm.model_trace import build_catalogue_with_sources
        from formats.exm.plugin import find_manifest
        from formats.exm.ssl import read_manifest

        configured = get_game_root(bpy.context)
        if not configured:
            return None
        game_root = normalise_game_root(configured)
        if game_root is None:
            return None
        manifest_path = find_manifest(folder)
        if manifest_path is None:
            return None
        try:
            manifest = read_manifest(manifest_path)
        except EXMeditorError:
            return None
        catalog, _sources, _problems = build_catalogue_with_sources(
            manifest, folder, game_root,
        )
        return catalog if len(catalog) else None

    def _write_json(self, census) -> None:
        path = self.json_path
        blender_path = getattr(bpy, "path", None)
        if blender_path is not None and hasattr(blender_path, "abspath"):
            path = blender_path.abspath(path)

        data = {
            "total_nodes": census.total_nodes,
            "known_nodes": census.known_nodes,
            "unknown_nodes": census.unknown_nodes,
            "attribute_instances": census.total_attribute_instances,
            "interpreted_attribute_instances": census.modelled_attribute_instances,
            "classes": [
                {
                    "class": entry.class_name,
                    "count": entry.count,
                    "handled": entry.known,
                    "depths": sorted(entry.depths),
                    "first_example": entry.first_name,
                    "attributes": {
                        name: {
                            "count": count,
                            "interpreted": name in MODELLED_ATTRIBUTES,
                            "samples": entry.attribute_samples.get(name, []),
                        }
                        for name, count in sorted(entry.attribute_counts.items())
                    },
                }
                for entry in sorted(census.classes.values(), key=lambda c: -c.count)
            ],
        }
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
        except OSError as exc:
            self.report({"WARNING"}, f"Could not write census JSON: {exc}")
        else:
            print(f"\nCensus written to {path}")

    def _report_verdict(self, census) -> None:
        """State the result as a claim that can be checked, not a feeling."""
        if census.unknown_nodes == 0 and census.ignored_attribute_instances == 0:
            self.report(
                {"INFO"},
                f"100% accounted for: all {census.total_nodes} nodes and "
                f"{census.total_attribute_instances} attributes are handled",
            )
            return

        if census.unknown_nodes:
            names = ", ".join(c.class_name for c in census.unknown_classes())
            self.report(
                {"WARNING"},
                f"{census.unknown_nodes} node(s) skipped — unhandled class(es): {names}",
            )
        if census.ignored_attribute_instances:
            distinct = len(census.ignored_attribute_summary())
            self.report(
                {"WARNING"},
                f"{census.ignored_attribute_instances} attribute value(s) across "
                f"{distinct} attribute name(s) are preserved but not interpreted",
            )


_CLASSES = (EXM_OT_world_census,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
