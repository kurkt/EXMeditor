# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Inspects what actually ended up in the Blender scene.

The chain here is deliberately about the scene, not the files::

    object -> mesh datablock -> vertices -> triangles -> bounding box
           -> local origin -> world position -> dimensions -> visible

An object can exist, carry a mesh datablock, and still show nothing:
the datablock can hold vertices but no faces, dimensions can be zero,
the object can be hidden, or scaled to nothing. Those cases are
indistinguishable in the viewport and need different fixes, so each is
reported separately.

Complements ``core/geometry_audit.py``, which judges the *source*
geometry. This module judges the *result* — the two can disagree, and
when they do, that disagreement is itself the finding: a model with
624 triangles in the file and 0 in the scene means the import lost
them.
"""

from __future__ import annotations

import dataclasses

import bpy

from core.geometry_audit import Finding, Severity, verdict_for
from utils.math import Vector3


@dataclasses.dataclass
class SceneObjectAudit:
    """What one Blender object actually contains."""

    name: str
    asset_id: str | None = None

    exists: bool = False
    object_type: str | None = None
    has_mesh_data: bool = False
    vertex_count: int = 0
    polygon_count: int = 0

    local_origin: Vector3 | None = None      # object origin in Blender space
    world_position: Vector3 | None = None    # same thing after parenting
    dimensions: Vector3 | None = None        # bounding box size in Blender units
    scale: Vector3 | None = None

    visible: bool = False
    hidden_reason: str | None = None
    collection: str | None = None

    findings: list[Finding] = dataclasses.field(default_factory=list)

    @property
    def score(self) -> int:
        from core.geometry_audit import _SEVERITY_POINTS

        return min(sum(_SEVERITY_POINTS[f.severity] for f in self.findings), 100)

    @property
    def verdict(self) -> str:
        return verdict_for(self.findings)

    def add(self, severity: Severity, code: str, message: str) -> None:
        self.findings.append(Finding(severity, code, message))

    def describe(self) -> str:
        """The per-object report block."""
        lines = [f"{self.name}" + (f'  (id "{self.asset_id}")' if self.asset_id else "")]
        if not self.exists:
            lines.append("    Object:        MISSING — no such object in the scene")
            return "\n".join(lines)

        lines.append(f"    Object:        OK ({self.object_type})")
        lines.append(
            "    Mesh:          "
            + ("OK" if self.has_mesh_data else "none — this is an Empty, not geometry")
        )
        lines.append(f"    Vertices:      {self.vertex_count}")
        lines.append(f"    Triangles:     {self.polygon_count}")
        if self.dimensions is not None:
            lines.append(
                "    Dimensions:    "
                f"{self.dimensions.x:.3f} x {self.dimensions.y:.3f} x {self.dimensions.z:.3f}"
            )
        if self.world_position is not None:
            lines.append(
                "    World pos:     "
                f"{self.world_position.x:.1f}, {self.world_position.y:.1f}, "
                f"{self.world_position.z:.1f}"
            )
        if self.scale is not None:
            lines.append(
                f"    Scale:         {self.scale.x:.3f}, {self.scale.y:.3f}, {self.scale.z:.3f}"
            )
        lines.append(f"    Collection:    {self.collection or 'none'}")
        lines.append(
            "    Visible:       "
            + ("yes" if self.visible else f"no — {self.hidden_reason}")
        )
        for finding in self.findings:
            lines.append(f"    ! {finding}")
        return "\n".join(lines)


def audit_scene_object(obj: bpy.types.Object, collection_name: str | None = None) -> SceneObjectAudit:
    """Inspect one Blender object end to end."""
    audit = SceneObjectAudit(name=obj.name, exists=True)
    audit.object_type = getattr(obj, "type", None)
    audit.collection = collection_name
    audit.asset_id = obj.get("exm_id")

    location = tuple(obj.location)
    audit.local_origin = Vector3(*location)
    audit.world_position = _world_position(obj)
    audit.scale = Vector3(*tuple(obj.scale))

    data = getattr(obj, "data", None)
    if data is not None and hasattr(data, "vertices"):
        audit.has_mesh_data = True
        audit.vertex_count = len(data.vertices)
        audit.polygon_count = len(getattr(data, "polygons", []))

    dimensions = getattr(obj, "dimensions", None)
    if dimensions is not None:
        audit.dimensions = Vector3(*tuple(dimensions))
    elif audit.has_mesh_data:
        audit.dimensions = _dimensions_from_mesh(data, audit.scale)

    audit.visible, audit.hidden_reason = _visibility(obj)

    _judge(audit)
    return audit


def _world_position(obj: bpy.types.Object) -> Vector3:
    """Object position after parent transforms.

    Uses ``matrix_world`` when available (real Blender); falls back to
    walking parents, which is what the test stub provides.
    """
    matrix = getattr(obj, "matrix_world", None)
    if matrix is not None:
        try:
            translation = matrix.translation
            return Vector3(translation[0], translation[1], translation[2])
        except (AttributeError, TypeError, IndexError):
            pass

    x, y, z = tuple(obj.location)
    parent = getattr(obj, "parent", None)
    while parent is not None:
        px, py, pz = tuple(parent.location)
        x, y, z = x + px, y + py, z + pz
        parent = getattr(parent, "parent", None)
    return Vector3(x, y, z)


def _dimensions_from_mesh(data, scale: Vector3) -> Vector3:
    """Bounding-box size of a mesh datablock, scaled like the object."""
    vertices = list(data.vertices)
    if not vertices:
        return Vector3(0.0, 0.0, 0.0)
    xs = [v.co[0] for v in vertices]
    ys = [v.co[1] for v in vertices]
    zs = [v.co[2] for v in vertices]
    return Vector3(
        (max(xs) - min(xs)) * scale.x,
        (max(ys) - min(ys)) * scale.y,
        (max(zs) - min(zs)) * scale.z,
    )


def _visibility(obj: bpy.types.Object) -> tuple[bool, str | None]:
    if getattr(obj, "hide_viewport", False):
        return False, "hidden in the viewport (monitor icon)"
    hide_get = getattr(obj, "hide_get", None)
    if callable(hide_get):
        try:
            if hide_get():
                return False, "hidden in the outliner (eye icon)"
        except (RuntimeError, TypeError):
            pass  # not in a view layer; the collection check covers it
    return True, None


def _judge(audit: SceneObjectAudit) -> None:
    """Turn the measurements into findings.

    Ordered so the most decisive problem is stated first — a mesh with
    no faces explains everything downstream, and reporting "dimensions
    are zero" alongside it would just be the same fact twice.
    """
    if not audit.has_mesh_data:
        # An Empty is expected when the model could not be loaded; the
        # resolution diagnostics explains why. Not a geometry fault.
        return

    if audit.vertex_count == 0:
        audit.add(
            Severity.SUSPECT, "no_vertices_in_scene",
            "mesh datablock exists but has no vertices",
        )
        return

    if audit.polygon_count == 0:
        audit.add(
            Severity.SUSPECT, "no_faces_in_scene",
            f"{audit.vertex_count} vertices but no faces — nothing will be drawn",
        )

    if audit.dimensions is not None:
        largest = max(audit.dimensions.x, audit.dimensions.y, audit.dimensions.z)
        if largest <= 1e-6:
            audit.add(
                Severity.SUSPECT, "zero_dimensions",
                "object has zero size in the scene",
            )

    if audit.scale is not None:
        if any(component < 0 for component in audit.scale.as_tuple()):
            audit.add(
                Severity.WARNING, "negative_scale",
                f"negative scale {audit.scale.as_tuple()} — geometry will be inside out",
            )
        if any(abs(component) <= 1e-6 for component in audit.scale.as_tuple()):
            audit.add(
                Severity.SUSPECT, "collapsed_scale",
                f"a scale axis is zero {audit.scale.as_tuple()} — the object is flattened away",
            )

    if not audit.visible:
        audit.add(Severity.WARNING, "hidden", audit.hidden_reason or "hidden")

    if audit.collection is None:
        audit.add(
            Severity.SUSPECT, "unlinked",
            "object is not in any collection — it will never be drawn",
        )


def audit_collection(collection: bpy.types.Collection) -> list[SceneObjectAudit]:
    """Audit every object under ``collection``, including sub-collections."""
    results: list[SceneObjectAudit] = []

    def visit(current: bpy.types.Collection) -> None:
        for obj in current.objects:
            results.append(audit_scene_object(obj, current.name))
        for child in current.children:
            visit(child)

    visit(collection)
    return results
