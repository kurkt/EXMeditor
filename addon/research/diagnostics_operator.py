# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Model Resolution Diagnostics operator.

Explains why a specific model is missing, instead of leaving it to be
guessed at. Runs the same resolution chain the importer runs, but
records every step and reports where it stopped.

Deliberately read-only: it changes nothing in the scene and fixes
nothing. A tool that quietly compensated for problems could not
describe them.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, StringProperty

from addon.preferences import get_game_root
from blender_io.diagnostics_bridge import annotate_with_scene
from core.diagnostics import STAGE_NAMES, Stage
from formats.exm.model_catalog import normalise_game_root
from formats.exm.model_trace import build_catalogue_with_sources, trace_objects
from formats.exm.plugin import find_manifest
from formats.exm.ssl import read_manifest
from core.unknown_nodes import UnknownNodeReport
from formats.exm.world import read_world
from formats.exm.ssl import resolve_in_folder
from utils.errors import EXMeditorError
from utils.logging import get_logger

logger = get_logger("addon.diagnostics")  # reserved for errors; report output goes to print()

#: Written by the import operator; the folder the current scene came from.
SOURCE_DIR_PROP = "exm_source_dir"


class EXM_OT_diagnose_models(bpy.types.Operator):
    """Report why map models are missing, stage by stage."""

    bl_idname = "exmachina.diagnose_models"
    bl_label = "Diagnose Model Resolution"
    bl_description = (
        "Trace every map object from world.xml through the catalogues to a "
        "loaded model, and report exactly where the chain breaks"
    )
    # REGISTER *and* UNDO. Blender only offers the "Adjust Last
    # Operation" panel for an operator that pushed an undo step, so
    # with REGISTER alone the properties below are unreachable: the
    # operator runs on its defaults and the fields it tells the user to
    # fill in never appear anywhere. The step is harmless here — this
    # operator reads files and changes nothing.
    bl_options = {"REGISTER", "UNDO"}

    map_folder: StringProperty(
        name="Map Folder",
        description=(
            "Map to diagnose. Leave empty to use the map imported into this "
            "scene"
        ),
        subtype="DIR_PATH",
    )

    focus_id: StringProperty(
        name="Focus On",
        description=(
            "Optional: a model id or object name (e.g. 'petrolstation'). "
            "Prints its full chain instead of the summary"
        ),
    )

    parse_files: BoolProperty(
        name="Parse Model Files",
        description=(
            "Actually read each .gam file. Turn off for a fast check that "
            "only verifies catalogue entries and file paths"
        ),
        default=True,
    )

    unknown_nodes_json: StringProperty(
        name="Unknown Nodes Report",
        description=(
            "Optional: write every unrecognised node class, with its "
            "attributes and examples, to this JSON file for analysis"
        ),
        subtype="FILE_PATH",
    )

    check_scene: BoolProperty(
        name="Check Blender Scene",
        description=(
            "Also check whether each object was created, linked and is "
            "visible. Requires the map to be imported into this scene"
        ),
        default=True,
    )

    def execute(self, context):
        folder = self.map_folder or context.scene.get(SOURCE_DIR_PROP, "")
        if not folder or not os.path.isdir(folder):
            self.report(
                {"ERROR"},
                "No map to diagnose — import a map first, or set Map Folder",
            )
            return {"CANCELLED"}

        configured = get_game_root(context)
        if not configured or not os.path.isdir(configured):
            self.report(
                {"ERROR"},
                "Set the Game Folder in Preferences > Add-ons > EXMeditor "
                "first — model resolution cannot be traced without it",
            )
            return {"CANCELLED"}

        # The same normalisation the importer applies, from the same
        # shared function — a report that looked in a different folder
        # from the import would contradict the thing it is explaining.
        game_root = normalise_game_root(configured)
        if game_root is None:
            self.report(
                {"ERROR"},
                f"No 'data' folder found at or above {configured} — the Game "
                "Folder should be the game's install folder",
            )
            return {"CANCELLED"}
        self._game_root = game_root

        try:
            report = self._build_report(folder, game_root, context)
        except EXMeditorError as exc:
            self.report({"ERROR"}, f"Diagnostics failed: {exc.message}")
            return {"CANCELLED"}

        if self.focus_id:
            self._print_focus(report)
        else:
            self._print_summary(report)

        return {"FINISHED"}

    def _build_report(self, folder: str, game_root: str, context):
        manifest_path = find_manifest(folder)
        if manifest_path is None:
            raise EXMeditorError("no .ssl manifest found for this map")
        manifest = read_manifest(manifest_path)

        world_path = resolve_in_folder(folder, "world.xml")
        if world_path is None:
            raise EXMeditorError("world.xml not found in the map folder")
        self._unknown = UnknownNodeReport()
        objects, _root = read_world(world_path, unknown=self._unknown)

        catalogue, sources, problems = build_catalogue_with_sources(
            manifest, folder, game_root,
        )
        self._catalogue_problems = problems
        self._catalogue_size = len(catalogue)
        self._catalogue_files = sorted(set(sources.values()))

        report = trace_objects(
            objects, catalogue, sources, game_root, parse_files=self.parse_files,
        )

        if self.check_scene:
            annotate_with_scene(report, context.collection)

        return report

    def _emit(self, line: str) -> None:
        """Print one line of the report to the system console.

        Only ``print`` — the logger writes to the same console in
        Blender, so emitting to both would duplicate the whole report.
        Headline numbers go to the info log separately via
        ``self.report``, which is what shows up in the status bar.
        """
        print(line)

    def _print_unknown_nodes(self) -> None:
        """Report node classes the SDK skipped.

        Printed before the resolution summary because a skipped class
        means those objects never entered the chain at all — their
        absence from the counts below would otherwise be unexplained.
        """
        report = getattr(self, "_unknown", None)
        if not report:
            return

        self._emit("")
        for line in report.summary_lines():
            self._emit(line)
        self._emit("")
        self._emit(report.describe())

        if self.unknown_nodes_json:
            path = self.unknown_nodes_json
            blender_path = getattr(bpy, "path", None)
            if blender_path is not None and hasattr(blender_path, "abspath"):
                path = blender_path.abspath(path)
            try:
                report.write_json(path)
            except OSError as exc:
                self.report({"WARNING"}, f"Could not write unknown-node report: {exc}")
            else:
                self._emit(f"Unknown-node report written to {path}")

        self.report(
            {"WARNING"},
            f"{report.total_skipped} object(s) skipped: "
            + ", ".join(sorted(report.classes)),
        )

    def _print_summary(self, report) -> None:
        self._print_unknown_nodes()
        self._emit("")
        self._emit("=== Model Resolution Diagnostics ===")
        self._emit(f"Game folder: {self._game_root}")
        self._emit(
            f"Catalogue: {self._catalogue_size} entries from "
            f"{', '.join(self._catalogue_files) or 'no files'}"
        )
        # Capped: a servers index can point at dozens of auxiliary
        # catalogues (decals, lights, projectors) that have nothing to
        # do with map objects, and listing them all would bury the one
        # line that matters.
        shown = self._catalogue_problems[:6]
        for problem in shown:
            self._emit(f"  ! {problem}")
        remaining = len(self._catalogue_problems) - len(shown)
        if remaining > 0:
            self._emit(f"  ! ... and {remaining} more catalogue issue(s)")
        self._emit("")
        for line in report.summary_lines():
            self._emit(line)
        self._emit("")
        for line in report.failure_detail_lines():
            self._emit(line)
        self._emit("")
        self._emit(
            "Run again with 'Focus On' set to a model id for its full chain."
        )

        resolved = report.resolved()
        expecting = len(report.expecting_a_model())
        self.report(
            {"INFO"},
            f"{resolved}/{expecting} models resolved — full report in the console "
            "(Window > Toggle System Console)",
        )
        for stage, count in report.counts_by_failure().items():
            self.report({"WARNING"}, f"{count} stopped at: {STAGE_NAMES[stage]}")

    def _print_focus(self, report) -> None:
        matches = report.find(self.focus_id)
        if not matches:
            self.report({"WARNING"}, f"No object or id matching '{self.focus_id}'")
            return

        self._emit("")
        self._emit(f"=== Chain for '{self.focus_id}' ({len(matches)} object(s)) ===")
        # Distinct outcomes only: 400 identical chains help nobody, but
        # one id succeeding for some objects and failing for others is
        # exactly what needs to be seen.
        seen: set[tuple] = set()
        for record in matches:
            key = (record.reached, record.failure_reason)
            if key in seen:
                continue
            seen.add(key)
            self._emit(record.describe_chain())
            self._emit("")

        if len(matches) > len(seen):
            self._emit(f"({len(matches)} objects, {len(seen)} distinct outcome(s))")

        first = matches[0]
        if first.succeeded:
            self.report({"INFO"}, f"'{self.focus_id}' resolves fully")
        else:
            self.report(
                {"WARNING"},
                f"'{self.focus_id}' stops at: {STAGE_NAMES[first.failed_at]}"
                + (f" — {first.failure_reason}" if first.failure_reason else ""),
            )


_CLASSES = (EXM_OT_diagnose_models,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
