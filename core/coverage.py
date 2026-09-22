# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Map coverage: what the map asks for versus what the scene contains.

``core/diagnostics.py`` answers "did this object resolve?" one object
at a time. This module answers the question that follows: does the
whole scene account for the whole map, and if not, exactly which model
is short and by how many.

The distinction matters because "I can't see the fuel station" and
"the fuel station wasn't imported" look identical to a user and need
opposite investigations. A coverage report that says
``fuelstation: expected 6, imported 6`` moves the search out of the
import pipeline entirely — which is worth as much as finding a bug.

Also breaks down what the mesh objects in a scene actually are. A scene
with 1579 expected models and 1580 mesh objects is fine (the extra is
terrain), but nobody should have to work that out by subtraction.

No ``bpy`` import.
"""

from __future__ import annotations

import dataclasses

from core.objects import ObjectInstance

#: Node classes that should produce a visible model. Everything else
#: (sound emitters, plain containers) is counted but never expected to
#: appear as geometry.
GEOMETRY_CLASSES = frozenset({"SgAnimatedModelNode", "SgGameUnitNode"})


@dataclasses.dataclass
class ModelCoverage:
    """Expected versus imported instances of one model id."""

    model_id: str
    expected: int = 0
    imported: int = 0
    #: Node names that expect this model, so a shortfall can be traced
    #: back to specific map objects rather than just a number.
    expected_nodes: list[str] = dataclasses.field(default_factory=list)

    @property
    def missing(self) -> int:
        return max(self.expected - self.imported, 0)

    @property
    def unexpected(self) -> int:
        """Imported more than the map asks for — usually a duplicate import."""
        return max(self.imported - self.expected, 0)

    @property
    def complete(self) -> bool:
        return self.expected == self.imported


@dataclasses.dataclass
class SceneComposition:
    """What the mesh objects in a scene actually are.

    Stated explicitly because the raw count invites a wrong reading:
    terrain and diagnostic markers are mesh objects too, so "1580
    meshes for 1579 models" is correct and looks like an off-by-one.
    """

    terrain: int = 0
    imported_models: int = 0
    diagnostic_markers: int = 0
    other: int = 0

    @property
    def total(self) -> int:
        return self.terrain + self.imported_models + self.diagnostic_markers + self.other

    def lines(self) -> list[str]:
        return [
            f"Terrain mesh          : {self.terrain}",
            f"Imported models       : {self.imported_models}",
            f"Diagnostic markers    : {self.diagnostic_markers}",
            f"Other meshes          : {self.other}",
            f"Total mesh objects    : {self.total}",
        ]


@dataclasses.dataclass
class ClassVisualisation:
    """Per node class: how many expected a model and how many got one.

    Exists because "the city doesn't appear" is a claim about a class
    of object, and a total that mixes classes cannot confirm or refute
    it. Splitting the count by class turns a hypothesis into a
    measurement.
    """

    class_name: str
    total: int = 0
    expecting_model: int = 0
    visualised: int = 0

    @property
    def without_visual(self) -> int:
        return max(self.expecting_model - self.visualised, 0)


@dataclasses.dataclass
class CoverageReport:
    """Everything the map expects, and what the scene delivered."""

    class_counts: dict[str, int] = dataclasses.field(default_factory=dict)
    models: dict[str, ModelCoverage] = dataclasses.field(default_factory=dict)
    composition: SceneComposition = dataclasses.field(default_factory=SceneComposition)
    unknown_classes: dict[str, int] = dataclasses.field(default_factory=dict)
    by_class: dict[str, ClassVisualisation] = dataclasses.field(default_factory=dict)

    # --- totals ---

    @property
    def total_nodes(self) -> int:
        return sum(self.class_counts.values())

    @property
    def expected_models(self) -> int:
        return sum(m.expected for m in self.models.values())

    @property
    def imported_models(self) -> int:
        return sum(m.imported for m in self.models.values())

    def incomplete(self) -> list[ModelCoverage]:
        """Model ids where the counts disagree, worst shortfall first."""
        return sorted(
            (m for m in self.models.values() if not m.complete),
            key=lambda m: (-m.missing, -m.unexpected, m.model_id),
        )

    def by_usage(self) -> list[ModelCoverage]:
        """Most-used models first — the shape of the map at a glance."""
        return sorted(self.models.values(), key=lambda m: (-m.expected, m.model_id))

    # --- rendering ---

    def class_visualisation_lines(self) -> list[str]:
        """Which node classes actually produced visible geometry.

        The breakdown that answers "do game units get models?" without
        having to reason from totals.
        """
        if not self.by_class:
            return []
        lines = ["--- Visual representation by node class ---"]
        for entry in sorted(self.by_class.values(), key=lambda c: -c.total):
            if entry.expecting_model == 0:
                lines.append(
                    f"  {entry.class_name:<24} {entry.total:5d} node(s), no model expected"
                )
                continue
            state = "all visualised" if entry.without_visual == 0 else (
                f"{entry.without_visual} WITHOUT a visual"
            )
            lines.append(
                f"  {entry.class_name:<24} {entry.total:5d} node(s), "
                f"{entry.expecting_model} expect a model, "
                f"{entry.visualised} visualised — {state}"
            )
        return lines

    def summary_lines(self) -> list[str]:
        lines = [f"world.xml nodes                : {self.total_nodes}"]
        for class_name, count in sorted(self.class_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {class_name:<28} : {count}")

        if self.unknown_classes:
            lines.append("")
            lines.append("Unknown node classes (skipped):")
            for class_name, count in sorted(
                self.unknown_classes.items(), key=lambda kv: -kv[1]
            ):
                lines.append(f"  {class_name:<28} : {count}")

        lines.append("")
        lines.append("Models")
        lines.append(f"  distinct ids                 : {len(self.models)}")
        lines.append(f"  expected instances           : {self.expected_models}")
        lines.append(f"  imported instances           : {self.imported_models}")

        shortfall = self.expected_models - self.imported_models
        if shortfall > 0:
            lines.append(f"  MISSING                      : {shortfall}")
        elif shortfall < 0:
            lines.append(f"  UNEXPECTED EXTRA             : {-shortfall}")
        else:
            lines.append("  every expected instance is present")

        lines.append("")
        lines.append("Scene composition")
        lines.extend(f"  {line}" for line in self.composition.lines())
        return lines

    def mismatch_lines(self, limit: int = 40) -> list[str]:
        incomplete = self.incomplete()
        if not incomplete:
            return ["No per-model discrepancies."]

        lines = [f"--- {len(incomplete)} model id(s) with a mismatch ---"]
        for coverage in incomplete[:limit]:
            state = (
                f"missing {coverage.missing}" if coverage.missing
                else f"{coverage.unexpected} more than expected"
            )
            lines.append(
                f"  {coverage.model_id:<28} expected {coverage.expected:4d}   "
                f"imported {coverage.imported:4d}   ({state})"
            )
            for node in coverage.expected_nodes[:3]:
                lines.append(f"       e.g. node {node}")
        if len(incomplete) > limit:
            lines.append(f"  ... and {len(incomplete) - limit} more")
        return lines

    def usage_lines(self, limit: int = 30) -> list[str]:
        lines = ["--- Models by usage ---"]
        for coverage in self.by_usage()[:limit]:
            mark = " " if coverage.complete else "!"
            lines.append(
                f" {mark} {coverage.model_id:<28} expected {coverage.expected:4d}   "
                f"imported {coverage.imported:4d}"
            )
        if len(self.models) > limit:
            lines.append(f"   ... and {len(self.models) - limit} more model id(s)")
        return lines


def count_expectations(
    objects: list[ObjectInstance],
    unknown_classes: dict[str, int] | None = None,
) -> CoverageReport:
    """Count what a parsed ``world.xml`` asks for.

    Walks the whole tree, so nested objects are included — they are as
    real as top-level ones and were being left out of manual counts.
    """
    report = CoverageReport()
    if unknown_classes:
        report.unknown_classes = dict(unknown_classes)

    for instance in _walk(objects):
        report.class_counts[instance.node_class] = (
            report.class_counts.get(instance.node_class, 0) + 1
        )
        entry = report.by_class.setdefault(
            instance.node_class, ClassVisualisation(class_name=instance.node_class),
        )
        entry.total += 1

        if instance.node_class not in GEOMETRY_CLASSES or not instance.asset_id:
            continue
        entry.expecting_model += 1
        coverage = report.models.setdefault(
            instance.asset_id, ModelCoverage(model_id=instance.asset_id),
        )
        coverage.expected += 1
        if len(coverage.expected_nodes) < 10:
            coverage.expected_nodes.append(instance.name)

    return report


def _walk(objects: list[ObjectInstance]):
    for instance in objects:
        yield instance
        yield from _walk(instance.children)


@dataclasses.dataclass
class MapComparison:
    """Which model ids two maps share, and which are unique to each."""

    left_name: str
    right_name: str
    shared: dict[str, tuple[int, int]] = dataclasses.field(default_factory=dict)
    only_left: dict[str, int] = dataclasses.field(default_factory=dict)
    only_right: dict[str, int] = dataclasses.field(default_factory=dict)

    def lines(self, limit: int = 40) -> list[str]:
        lines = [
            f"=== {self.left_name} vs {self.right_name} ===",
            f"shared model ids      : {len(self.shared)}",
            f"only in {self.left_name:<12} : {len(self.only_left)}",
            f"only in {self.right_name:<12} : {len(self.only_right)}",
        ]
        for label, group in (
            (f"Only in {self.left_name}", self.only_left),
            (f"Only in {self.right_name}", self.only_right),
        ):
            if not group:
                continue
            lines.append("")
            lines.append(f"--- {label} ({len(group)}) ---")
            for model_id, count in sorted(group.items(), key=lambda kv: -kv[1])[:limit]:
                lines.append(f"  {model_id:<28} x{count}")
            if len(group) > limit:
                lines.append(f"  ... and {len(group) - limit} more")
        return lines


def compare_maps(
    left: CoverageReport, left_name: str,
    right: CoverageReport, right_name: str,
) -> MapComparison:
    """Compare the model ids two maps use.

    The point is narrowing: if a model fails to appear on one map and
    that map is the only one using it, the search space collapses from
    the whole catalogue to a handful of ids.
    """
    comparison = MapComparison(left_name=left_name, right_name=right_name)
    left_ids = {k: v.expected for k, v in left.models.items()}
    right_ids = {k: v.expected for k, v in right.models.items()}

    for model_id, count in left_ids.items():
        if model_id in right_ids:
            comparison.shared[model_id] = (count, right_ids[model_id])
        else:
            comparison.only_left[model_id] = count
    for model_id, count in right_ids.items():
        if model_id not in left_ids:
            comparison.only_right[model_id] = count

    return comparison
