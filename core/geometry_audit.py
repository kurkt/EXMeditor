# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Geometry auditing: statistics, anomalies and a suspicion score.

Answers a different question from ``core/diagnostics.py``. That module
asks "did the model load?"; this one asks "does what loaded make
sense?" — a model can resolve perfectly and still be a thousand times
too small, lying on its side, hovering above the ground, or made of
degenerate triangles.

The output is deliberately a *ranked list of suspicions*, not a
pass/fail. None of these checks can prove a model is wrong: a genuinely
flat object legitimately looks like a fallen-over one, and a small prop
legitimately has a small bounding box. What the score does is turn
"inspect 1579 models by hand" into "inspect the 20 that look odd", and
say why each one is on the list so the judgement stays with the user.

No ``bpy`` import — every check here runs on parsed model data, so the
whole audit works from a CLI or a test.
"""

from __future__ import annotations

import dataclasses
import enum
import math

from core.mesh import Model
from utils.math import AABB, Vector3


class Severity(enum.IntEnum):
    """How strongly a finding suggests something is wrong."""

    INFO = 0     # worth knowing, not suspicious on its own
    WARNING = 1  # unusual; plausible but worth a look
    SUSPECT = 2  # very likely a real problem


#: Points added to the suspicion score, by severity. Deliberately
#: coarse: the score exists to order a list, and finer weights would
#: imply a precision these heuristics don't have.
_SEVERITY_POINTS = {
    Severity.INFO: 0,
    Severity.WARNING: 15,
    Severity.SUSPECT: 35,
}

# --- thresholds, all in game units ---

#: Below this in every axis, a model is almost certainly mis-scaled —
#: for reference, a truck cab measures ~5 units and a large rock ~57.
TINY_EXTENT = 0.05
#: A map is ~4096 units across, so a single model near this size is
#: either a whole town or a scale error.
HUGE_EXTENT = 900.0
#: A building lying on its side: much wider than tall.
FLAT_ASPECT_RATIO = 20.0
#: How far the pivot may sit outside the geometry, relative to the
#: model's own size, before it looks misplaced.
PIVOT_OFFSET_RATIO = 3.0
#: Fraction of vertices that may share a position before the mesh looks
#: degenerate rather than merely welded.
DUPLICATE_VERTEX_RATIO = 0.95
#: Zero-area triangles a model may carry before it looks defective
#: rather than merely imperfect. Real exported assets routinely have a
#: handful.
DEGENERATE_TOLERANCE = 10
#: Vertical gap between a model's base and the ground, in game units,
#: beyond which it reads as floating or sunk.
GROUND_GAP = 5.0


@dataclasses.dataclass
class Finding:
    """One reason a model looks suspicious."""

    severity: Severity
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.name}] {self.message}"


@dataclasses.dataclass
class GeometryStats:
    """Level 1: plain measurements, no judgement."""

    mesh_count: int = 0
    vertex_count: int = 0
    triangle_count: int = 0
    bounds: AABB | None = None
    has_normals: bool = False
    has_uvs: bool = False
    has_colors: bool = False
    empty_meshes: int = 0
    degenerate_triangles: int = 0
    unique_positions: int = 0

    @property
    def dimensions(self) -> Vector3:
        if self.bounds is None:
            return Vector3(0.0, 0.0, 0.0)
        return Vector3(
            self.bounds.max_corner.x - self.bounds.min_corner.x,
            self.bounds.max_corner.y - self.bounds.min_corner.y,
            self.bounds.max_corner.z - self.bounds.min_corner.z,
        )

    @property
    def centre(self) -> Vector3:
        if self.bounds is None:
            return Vector3(0.0, 0.0, 0.0)
        return Vector3(
            (self.bounds.min_corner.x + self.bounds.max_corner.x) * 0.5,
            (self.bounds.min_corner.y + self.bounds.max_corner.y) * 0.5,
            (self.bounds.min_corner.z + self.bounds.max_corner.z) * 0.5,
        )

    @property
    def max_extent(self) -> float:
        d = self.dimensions
        return max(d.x, d.y, d.z)


def verdict_for(findings: list[Finding]) -> str:
    """Overall verdict for a set of findings.

    Driven by the WORST finding, not by the point total: one
    definitive problem ("vertices but no faces — nothing will be
    drawn") means the object is broken regardless of how few other
    issues it has. Scoring by points alone labelled exactly that case
    as a mere warning.

    The score still exists, but only to order the list.
    """
    if any(f.severity == Severity.SUSPECT for f in findings):
        return "SUSPECT"
    if any(f.severity == Severity.WARNING for f in findings):
        return "WARNING"
    return "OK"


@dataclasses.dataclass
class ModelAudit:
    """Everything known about one model's geometry, plus its findings."""

    model_id: str
    stats: GeometryStats = dataclasses.field(default_factory=GeometryStats)
    findings: list[Finding] = dataclasses.field(default_factory=list)

    @property
    def score(self) -> int:
        """Suspicion score, 0 (clean) to 100 (very likely wrong)."""
        total = sum(_SEVERITY_POINTS[f.severity] for f in self.findings)
        return min(total, 100)

    @property
    def verdict(self) -> str:
        return verdict_for(self.findings)

    def add(self, severity: Severity, code: str, message: str) -> None:
        self.findings.append(Finding(severity, code, message))


def measure(model: Model) -> GeometryStats:
    """Level 1: collect the statistics, without judging them."""
    stats = GeometryStats()
    all_positions: list[Vector3] = []
    seen: set[tuple[float, float, float]] = set()

    for mesh in model.meshes:
        stats.mesh_count += 1
        stats.vertex_count += mesh.vertex_count
        stats.triangle_count += mesh.triangle_count
        stats.has_normals = stats.has_normals or bool(mesh.normals)
        stats.has_uvs = stats.has_uvs or bool(mesh.uvs)
        stats.has_colors = stats.has_colors or bool(mesh.colors)

        if mesh.vertex_count == 0 or mesh.triangle_count == 0:
            stats.empty_meshes += 1

        for position in mesh.positions:
            all_positions.append(position)
            seen.add((position.x, position.y, position.z))

        for a, b, c in mesh.triangles:
            if len({a, b, c}) < 3:
                stats.degenerate_triangles += 1
                continue
            if _triangle_area(mesh.positions, a, b, c) <= 1e-9:
                stats.degenerate_triangles += 1

    stats.unique_positions = len(seen)
    stats.bounds = AABB.from_points(all_positions)
    return stats


def _triangle_area(positions: list[Vector3], a: int, b: int, c: int) -> float:
    if max(a, b, c) >= len(positions):
        return 0.0
    p, q, r = positions[a], positions[b], positions[c]
    ux, uy, uz = q.x - p.x, q.y - p.y, q.z - p.z
    vx, vy, vz = r.x - p.x, r.y - p.y, r.z - p.z
    cx = uy * vz - uz * vy
    cy = uz * vx - ux * vz
    cz = ux * vy - uy * vx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def audit_model(model_id: str, model: Model) -> ModelAudit:
    """Levels 1, 2, 4 and 6: measure, then look for anomalies.

    Terrain-relative checks (level 5) need placement data and live in
    :func:`audit_placement`, so a model can be audited on its own.
    """
    audit = ModelAudit(model_id=model_id, stats=measure(model))
    stats = audit.stats

    # --- level 4: degenerate geometry ---
    if stats.vertex_count == 0:
        audit.add(Severity.SUSPECT, "no_vertices", "model has no vertices at all")
        return audit  # nothing else is meaningful

    if stats.triangle_count == 0:
        audit.add(
            Severity.SUSPECT, "no_triangles",
            f"{stats.vertex_count} vertices but no triangles — nothing will be drawn",
        )

    if stats.empty_meshes:
        severity = Severity.SUSPECT if stats.empty_meshes == stats.mesh_count else Severity.WARNING
        audit.add(
            severity, "empty_meshes",
            f"{stats.empty_meshes} of {stats.mesh_count} mesh(es) contain no geometry",
        )

    if stats.degenerate_triangles:
        share = stats.degenerate_triangles / max(stats.triangle_count, 1)
        # Shipped assets routinely carry a few zero-area triangles;
        # flagging those put every large model on the list and drowned
        # the real problems. Judged by absolute count AND proportion:
        # a share alone misjudges small models (one bad triangle in
        # twelve is 8%, which is not a defect), and a count alone
        # misjudges large ones.
        if share > 0.5:
            severity = Severity.SUSPECT
        elif stats.degenerate_triangles <= DEGENERATE_TOLERANCE and share < 0.10:
            severity = Severity.INFO
        elif share >= 0.01:
            severity = Severity.WARNING
        else:
            severity = Severity.INFO
        audit.add(
            severity, "degenerate_triangles",
            f"{stats.degenerate_triangles} zero-area or repeated-index triangle(s)"
            f" ({share:.1%})",
        )

    if stats.vertex_count > 3:
        duplicate_share = 1.0 - stats.unique_positions / stats.vertex_count
        if stats.unique_positions == 1:
            audit.add(
                Severity.SUSPECT, "collapsed",
                "every vertex is at the same position — the model is collapsed to a point",
            )
        elif duplicate_share >= DUPLICATE_VERTEX_RATIO:
            audit.add(
                Severity.SUSPECT, "duplicate_vertices",
                f"{duplicate_share:.0%} of vertices share a position",
            )

    # --- level 2: size anomalies ---
    dimensions = stats.dimensions
    extent = stats.max_extent

    if extent <= TINY_EXTENT:
        audit.add(
            Severity.SUSPECT, "tiny",
            f"largest dimension is {extent:.4f} units — almost certainly a scale error",
        )
    elif extent >= HUGE_EXTENT:
        audit.add(
            Severity.WARNING, "huge",
            f"largest dimension is {extent:.0f} units, a sizeable fraction of the whole map",
        )

    # --- level 6: orientation ---
    footprint = max(dimensions.x, dimensions.z)
    height = dimensions.y
    if height > 1e-6 and footprint / height >= FLAT_ASPECT_RATIO:
        audit.add(
            Severity.WARNING, "flat",
            f"{footprint:.1f} units wide but only {height:.1f} tall — "
            "may be lying on its side",
        )

    # --- level 3 (model half): pivot placement ---
    centre = stats.centre
    offset = math.sqrt(centre.x ** 2 + centre.y ** 2 + centre.z ** 2)
    if extent > 1e-6 and offset / extent >= PIVOT_OFFSET_RATIO:
        audit.add(
            Severity.WARNING, "pivot_offset",
            f"geometry sits {offset:.1f} units from its own origin "
            f"({offset / extent:.1f}x its size) — pivot may be misplaced",
        )

    # --- missing attributes ---
    if not stats.has_normals:
        audit.add(Severity.WARNING, "no_normals", "no normals — shading will be flat")
    if not stats.has_uvs:
        audit.add(Severity.INFO, "no_uvs", "no UV coordinates — cannot be textured")

    return audit


def audit_placement(
    audit: ModelAudit,
    world_position: Vector3,
    ground_height: float | None,
) -> None:
    """Level 5: check the placed model against the terrain surface.

    ``world_position`` and ``ground_height`` are both in game units and
    in the game's own axis convention (Y is height). Appends findings
    to ``audit`` rather than returning, since placement is one more
    source of suspicion for the same model.

    Only meaningful for objects that should sit on the ground; a lamp
    on a pole or a bird legitimately floats, which is why this is a
    warning and never a hard failure.
    """
    if ground_height is None or audit.stats.bounds is None:
        return

    base = world_position.y + audit.stats.bounds.min_corner.y
    gap = base - ground_height

    if gap > GROUND_GAP:
        audit.add(
            Severity.WARNING, "floating",
            f"base sits {gap:.1f} units above the terrain ({base:.1f} vs {ground_height:.1f})",
        )
    elif gap < -GROUND_GAP:
        audit.add(
            Severity.WARNING, "sunken",
            f"base sits {-gap:.1f} units below the terrain ({base:.1f} vs {ground_height:.1f})",
        )


def rank(audits: list[ModelAudit]) -> list[ModelAudit]:
    """Most suspicious first; ties broken by name for stable output."""
    return sorted(audits, key=lambda a: (-a.score, a.model_id))
