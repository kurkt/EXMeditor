# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Map Coverage Report operator.

Answers "does the scene account for the whole map?" — and when it
doesn't, which model id is short and by how many.

This is the report that separates two situations a user cannot tell
apart: a model that was never imported, and a model that was imported
but can't be seen. If coverage says ``fuelstation: expected 6,
imported 6``, the import pipeline is exonerated and the search moves
elsewhere — which is as useful as finding a fault.

Also compares two maps, on the reasoning that a model failing on one
map and used only by that map narrows the search from a
thousand-entry catalogue to a handful of ids.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, StringProperty

from blender_io.coverage_bridge import annotate_with_scene
from core.coverage import compare_maps, count_expectations
from core.unknown_nodes import UnknownNodeReport
from formats.exm.plugin import find_manifest
from formats.exm.ssl import resolve_in_folder
from formats.exm.world import read_world
from utils.errors import EXMeditorError
from utils.logging import get_logger

logger = get_logger("addon.coverage")

SOURCE_DIR_PROP = "exm_source_dir"


class EXM_OT_coverage_report(bpy.types.Operator):
    """Compare what the map expects against what the scene contains."""

    bl_idname = "exmachina.coverage_report"
    bl_label = "Map Coverage Report"
    bl_description = (
        "Count every model the map expects and every one the scene contains, "
        "per model id, and report any discrepancy"
    )
    bl_options = {"REGISTER"}

    map_folder: StringProperty(
        name="Map Folder",
        description="Map to report on. Leave empty to use the map imported into this scene",
        subtype="DIR_PATH",
    )

    compare_with: StringProperty(
        name="Compare With",
        description=(
            "Optional: another map folder. Reports which model ids each map "
            "uses that the other does not"
        ),
        subtype="DIR_PATH",
    )

    focus_id: StringProperty(
        name="Focus On",
        description="Optional: a model id to report on in detail (e.g. 'fuelstation')",
    )

    list_usage: BoolProperty(
        name="List Models by Usage",
        description="Print every model id with its expected and imported counts",
        default=True,
    )

    def execute(self, context):
        folder = self.map_folder or context.scene.get(SOURCE_DIR_PROP, "")
        if not folder or not os.path.isdir(folder):
            self.report(
                {"ERROR"},
                "No map to report on — import a map first, or set Map Folder",
            )
            return {"CANCELLED"}

        try:
            report = self._build(folder, context)
        except EXMeditorError as exc:
            self.report({"ERROR"}, f"Coverage report failed: {exc.message}")
            return {"CANCELLED"}

        if self.focus_id:
            self._print_focus(report)
        else:
            self._print_report(report, folder)

        if self.compare_with:
            self._print_comparison(folder, report)

        return {"FINISHED"}

    # --- building ---

    def _read_map(self, folder: str):
        manifest_path = find_manifest(folder)
        if manifest_path is None:
            raise EXMeditorError(f"no .ssl manifest found in {folder}")
        world_path = resolve_in_folder(folder, "world.xml")
        if world_path is None:
            raise EXMeditorError(f"world.xml not found in {folder}")

        unknown = UnknownNodeReport()
        objects, _root = read_world(world_path, unknown=unknown)
        unknown_counts = {name: info.count for name, info in unknown.classes.items()}
        return count_expectations(objects, unknown_counts)

    def _build(self, folder: str, context):
        report = self._read_map(folder)
        annotate_with_scene(report, context.collection)
        return report

    # --- output ---

    def _print_report(self, report, folder: str) -> None:
        print("")
        print("=== Map Coverage Report ===")
        print(f"Map: {folder}")
        print("")
        for line in report.summary_lines():
            print(line)
        print("")
        for line in report.class_visualisation_lines():
            print(line)
        print("")
        for line in report.mismatch_lines():
            print(line)

        if self.list_usage:
            print("")
            for line in report.usage_lines():
                print(line)

        incomplete = report.incomplete()
        if incomplete:
            self.report(
                {"WARNING"},
                f"{len(incomplete)} model id(s) do not match — "
                f"{report.expected_models - report.imported_models} instance(s) short",
            )
        else:
            self.report(
                {"INFO"},
                f"All {report.expected_models} expected model instance(s) present "
                f"across {len(report.models)} id(s)",
            )

    def _print_focus(self, report) -> None:
        coverage = report.models.get(self.focus_id)
        if coverage is None:
            print("")
            print(f"'{self.focus_id}' is not referenced by this map at all.")
            near = [
                model_id for model_id in report.models
                if self.focus_id.lower() in model_id.lower()
            ]
            if near:
                print(f"Similar ids present: {', '.join(sorted(near)[:10])}")
            self.report({"WARNING"}, f"'{self.focus_id}' is not used by this map")
            return

        print("")
        print(f"=== Coverage for '{coverage.model_id}' ===")
        print(f"  expected instances : {coverage.expected}")
        print(f"  imported instances : {coverage.imported}")
        if coverage.complete:
            print("  status             : complete")
            print("")
            print("  Every instance the map asks for exists in the scene, so")
            print("  anything still not visible is a display problem, not an")
            print("  import one — check the Scene Geometry Audit next.")
        elif coverage.missing:
            print(f"  status             : MISSING {coverage.missing}")
        else:
            print(f"  status             : {coverage.unexpected} MORE than expected")

        print("  map nodes expecting it:")
        for node in coverage.expected_nodes:
            print(f"      {node}")
        if coverage.expected > len(coverage.expected_nodes):
            print(f"      ... and {coverage.expected - len(coverage.expected_nodes)} more")

        level = {"INFO"} if coverage.complete else {"WARNING"}
        self.report(
            level,
            f"'{coverage.model_id}': expected {coverage.expected}, "
            f"imported {coverage.imported}",
        )

    def _print_comparison(self, folder: str, report) -> None:
        other = self.compare_with
        if not os.path.isdir(other):
            self.report({"WARNING"}, f"Compare With is not a folder: {other}")
            return
        try:
            other_report = self._read_map(other)
        except EXMeditorError as exc:
            self.report({"WARNING"}, f"Could not read the other map: {exc.message}")
            return

        comparison = compare_maps(
            report, os.path.basename(os.path.normpath(folder)) or "this map",
            other_report, os.path.basename(os.path.normpath(other)) or "other map",
        )
        print("")
        for line in comparison.lines():
            print(line)

        self.report(
            {"INFO"},
            f"{len(comparison.only_right)} model id(s) used only by the other map",
        )


_CLASSES = (EXM_OT_coverage_report,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
