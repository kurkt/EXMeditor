# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Analyze Scene operator.

Runs the geometry audit over an imported map and produces three things:

* a console report, ranked by suspicion;
* a ``Diagnostics`` collection of coloured markers sitting on the
  suspicious objects, so they can be found in the viewport;
* a CSV file, so the ranking can be sorted and kept.

The point is triage. 1579 models cannot be inspected by hand; twenty
can. Nothing here proves a model is wrong — every check is a heuristic,
and the report says why each object is listed so the judgement stays
with the user.
"""

from __future__ import annotations

import csv
import os

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from blender_io.scene_audit import SceneObjectAudit, audit_collection
from core.geometry_audit import Severity
from utils.logging import get_logger

logger = get_logger("addon.audit")

DIAGNOSTICS_COLLECTION = "Diagnostics"

#: Marker colours by verdict. Blender shows an object's colour in the
#: viewport when shading is set to Object colour, and in the outliner
#: regardless, so a red marker is findable either way.
_MARKER_COLOURS = {
    "SUSPECT": (0.9, 0.1, 0.1, 1.0),
    "WARNING": (0.9, 0.7, 0.1, 1.0),
    "OK": (0.1, 0.8, 0.2, 1.0),
}


class EXM_OT_analyze_scene(bpy.types.Operator):
    """Audit imported map geometry and rank objects by how odd they look."""

    bl_idname = "exmachina.analyze_scene"
    bl_label = "Analyze Scene"
    bl_description = (
        "Measure every imported object's geometry, flag anomalies, and rank "
        "them by how likely they are to be wrong"
    )
    bl_options = {"REGISTER", "UNDO"}

    top_count: IntProperty(
        name="Show Top",
        description="How many of the most suspicious objects to list in the console",
        default=20,
        min=1,
        max=500,
    )

    create_markers: BoolProperty(
        name="Create Markers",
        description=(
            "Add a coloured Empty at each flagged object, in a 'Diagnostics' "
            "collection, so they can be found in the viewport"
        ),
        default=True,
    )

    include_ok: BoolProperty(
        name="Mark Clean Objects Too",
        description="Also place green markers on objects with no findings",
        default=False,
    )

    csv_path: StringProperty(
        name="CSV Report",
        description="Optional: write the full ranking to this file",
        subtype="FILE_PATH",
    )

    def execute(self, context):
        audits = audit_collection(context.collection)
        # Empties are the expected result for models that could not be
        # loaded; the resolution diagnostics explains those. Auditing
        # them here would fill the ranking with objects whose problem is
        # already known and reported elsewhere.
        geometry = [a for a in audits if a.has_mesh_data]

        if not geometry:
            self.report(
                {"WARNING"},
                "No mesh objects found — import a map with models first "
                "(objects imported as Empties have nothing to audit)",
            )
            return {"CANCELLED"}

        # Sort by worst severity first, then score, then name — so a
        # definitively broken object always outranks a merely unusual
        # one even if the unusual one accumulated more points.
        severity_rank = {"SUSPECT": 0, "WARNING": 1, "OK": 2}
        ranked = sorted(
            geometry, key=lambda a: (severity_rank[a.verdict], -a.score, a.name),
        )
        self._print_report(ranked, len(audits))

        if self.create_markers:
            created = self._create_markers(context, ranked)
            self.report({"INFO"}, f"{created} diagnostic marker(s) created")

        if self.csv_path:
            try:
                self._write_csv(ranked)
            except OSError as exc:
                self.report({"WARNING"}, f"Could not write CSV: {exc}")
            else:
                self.report({"INFO"}, f"Report written to {self.csv_path}")

        flagged = sum(1 for a in ranked if a.score > 0)
        self.report(
            {"INFO"},
            f"Audited {len(ranked)} mesh object(s); {flagged} flagged — "
            "details in the console (Window > Toggle System Console)",
        )
        return {"FINISHED"}

    # --- reporting ---

    def _print_report(self, ranked: list[SceneObjectAudit], total: int) -> None:
        suspect = [a for a in ranked if a.verdict == "SUSPECT"]
        warning = [a for a in ranked if a.verdict == "WARNING"]
        clean = [a for a in ranked if a.verdict == "OK"]

        print("")
        print("=== Scene Geometry Audit ===")
        print(f"Objects in collection : {total}")
        print(f"  with mesh geometry  : {len(ranked)}")
        print(f"  suspect             : {len(suspect)}")
        print(f"  warning             : {len(warning)}")
        print(f"  clean               : {len(clean)}")
        print("")

        if not suspect and not warning:
            print("Nothing flagged. Every mesh object measured as expected.")
            return

        print(f"--- Top {min(self.top_count, len(ranked))} by suspicion ---")
        for index, audit in enumerate(ranked[: self.top_count], start=1):
            if audit.score == 0:
                break
            print(f"{index:3d}. {audit.name}  score {audit.score}  [{audit.verdict}]")
            print(audit.describe())
            print("")

    def _write_csv(self, ranked: list[SceneObjectAudit]) -> None:
        # bpy.path.abspath resolves Blender's '//' relative-to-blend
        # prefix; guarded because it isn't present outside Blender.
        path = self.csv_path
        blender_path = getattr(bpy, "path", None)
        if blender_path is not None and hasattr(blender_path, "abspath"):
            path = blender_path.abspath(self.csv_path)
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            raise OSError(f"directory does not exist: {directory}")

        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "score", "verdict", "object", "model_id", "vertices", "triangles",
                "dim_x", "dim_y", "dim_z", "world_x", "world_y", "world_z",
                "visible", "collection", "findings",
            ])
            for audit in ranked:
                dimensions = audit.dimensions or None
                position = audit.world_position or None
                writer.writerow([
                    audit.score,
                    audit.verdict,
                    audit.name,
                    audit.asset_id or "",
                    audit.vertex_count,
                    audit.polygon_count,
                    f"{dimensions.x:.4f}" if dimensions else "",
                    f"{dimensions.y:.4f}" if dimensions else "",
                    f"{dimensions.z:.4f}" if dimensions else "",
                    f"{position.x:.2f}" if position else "",
                    f"{position.y:.2f}" if position else "",
                    f"{position.z:.2f}" if position else "",
                    "yes" if audit.visible else "no",
                    audit.collection or "",
                    "; ".join(f.message for f in audit.findings),
                ])

    # --- markers ---

    def _create_markers(self, context, ranked: list[SceneObjectAudit]) -> int:
        collection = self._diagnostics_collection(context)
        self._clear(collection)

        created = 0
        for audit in ranked:
            verdict = audit.verdict
            if verdict == "OK" and not self.include_ok:
                continue
            if audit.world_position is None:
                continue

            marker = bpy.data.objects.new(f"DIAG_{verdict}_{audit.name}", None)
            marker.empty_display_type = "SPHERE"
            # Sized relative to the object so a marker on a large
            # building is still visible without swamping a small prop.
            largest = 1.0
            if audit.dimensions is not None:
                largest = max(
                    audit.dimensions.x, audit.dimensions.y, audit.dimensions.z, 1.0,
                )
            marker.empty_display_size = max(largest * 0.6, 2.0)
            marker.location = audit.world_position.as_tuple()
            marker.color = _MARKER_COLOURS[verdict]
            marker["exm_diag_score"] = audit.score
            marker["exm_diag_object"] = audit.name
            marker["exm_diag_reasons"] = "; ".join(f.message for f in audit.findings)

            collection.objects.link(marker)
            created += 1

        return created

    @staticmethod
    def _diagnostics_collection(context) -> bpy.types.Collection:
        for child in context.collection.children:
            if child.name.startswith(DIAGNOSTICS_COLLECTION):
                return child
        created = bpy.data.collections.new(DIAGNOSTICS_COLLECTION)
        context.collection.children.link(created)
        return created

    @staticmethod
    def _clear(collection: bpy.types.Collection) -> None:
        """Remove markers from a previous run.

        Without this a second audit doubles the markers, and stale ones
        would point at problems that may already be fixed — worse than
        no markers at all.
        """
        for obj in list(collection.objects):
            unlink = getattr(collection.objects, "unlink", None)
            if unlink is not None:
                unlink(obj)
            remove = getattr(bpy.data.objects, "remove", None)
            if remove is not None:
                try:
                    remove(obj)
                except (RuntimeError, TypeError, ReferenceError):
                    pass


_CLASSES = (EXM_OT_analyze_scene,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
