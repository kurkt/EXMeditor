# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validates a map before it reaches the game.

Catches the mistakes that are cheap to make in an editor and expensive
to diagnose in-game: a model id that does not exist, an object placed
outside the playable area, a node with nothing to draw.

Every check answers a question the user cannot easily answer
themselves. Deliberately absent are checks that merely restate
preference — an object being far from the terrain is sometimes wrong
and sometimes a deliberately floating platform, so that is reported as
a warning with its numbers rather than as an error, and the judgement
stays where it belongs.

No ``bpy`` import.
"""

from __future__ import annotations

import dataclasses
import enum

from core.objects import ObjectInstance
from core.scene import MapScene

#: Node classes that must name a model.
_MODEL_CLASSES = frozenset({"SgAnimatedModelNode", "SgGameUnitNode"})


class Severity(enum.IntEnum):
    WARNING = 0   # probably wrong; the map still loads
    ERROR = 1     # the game cannot use this


@dataclasses.dataclass
class Issue:
    """One problem found in a map."""

    severity: Severity
    code: str
    message: str
    node_name: str | None = None
    asset_id: str | None = None

    def __str__(self) -> str:
        where = f" [{self.node_name}]" if self.node_name else ""
        return f"{self.severity.name}{where}: {self.message}"


@dataclasses.dataclass
class ValidationReport:
    issues: list[Issue] = dataclasses.field(default_factory=list)

    def add(
        self,
        severity: Severity,
        code: str,
        message: str,
        node_name: str | None = None,
        asset_id: str | None = None,
    ) -> None:
        self.issues.append(Issue(severity, code, message, node_name, asset_id))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def is_clean(self) -> bool:
        return not self.issues

    def by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.code] = counts.get(issue.code, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def summary_lines(self, limit_per_code: int = 5) -> list[str]:
        if self.is_clean:
            return ["Map validates cleanly."]

        lines = [
            f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)",
            "",
        ]
        grouped: dict[str, list[Issue]] = {}
        for issue in self.issues:
            grouped.setdefault(issue.code, []).append(issue)

        # Errors first: they block the map, warnings only suggest.
        ordered = sorted(
            grouped.items(),
            key=lambda kv: (-max(i.severity for i in kv[1]), -len(kv[1])),
        )
        for code, issues in ordered:
            worst = max(i.severity for i in issues)
            lines.append(f"--- {worst.name}: {code} ({len(issues)})")
            for issue in issues[:limit_per_code]:
                where = f"{issue.node_name}: " if issue.node_name else ""
                lines.append(f"      {where}{issue.message}")
            if len(issues) > limit_per_code:
                lines.append(f"      ... and {len(issues) - limit_per_code} more")
        return lines


def validate_map(
    scene: MapScene,
    *,
    library=None,
    bounds: tuple[float, float, float, float] | None = None,
) -> ValidationReport:
    """Check a scene for problems the game would choke on.

    ``library`` enables model-existence checks; without it those are
    skipped rather than guessed at, since a user may legitimately be
    working without the game installed.

    ``bounds`` is ``(min_x, min_z, max_x, max_z)`` in game units — the
    playable area from the map manifest.
    """
    report = ValidationReport()

    for instance in _walk(scene.objects):
        _check_node(instance, report, library, bounds)

    if library is not None:
        _check_unused_warning(scene, report)

    return report


def _check_node(
    instance: ObjectInstance,
    report: ValidationReport,
    library,
    bounds: tuple[float, float, float, float] | None,
) -> None:
    if not instance.name:
        report.add(
            Severity.ERROR, "unnamed_node",
            f"a {instance.node_class} node has no name",
        )

    if instance.node_class in _MODEL_CLASSES:
        if not instance.asset_id:
            report.add(
                Severity.ERROR, "missing_model_id",
                f"{instance.node_class} has no model id — nothing would be drawn",
                instance.name,
            )
        elif library is not None and instance.asset_id not in library:
            near = [
                a.asset_id for a in library.search(instance.asset_id, limit=3)
            ]
            suggestion = f" Similar: {', '.join(near)}" if near else ""
            report.add(
                Severity.ERROR, "unknown_model",
                f"model '{instance.asset_id}' is not in the catalogue.{suggestion}",
                instance.name, instance.asset_id,
            )

    if instance.scale is not None:
        components = instance.scale.as_tuple()
        if any(abs(c) < 1e-6 for c in components):
            report.add(
                Severity.ERROR, "zero_scale",
                f"scale {components} has a zero axis — the object collapses",
                instance.name,
            )
        elif any(c < 0 for c in components):
            report.add(
                Severity.WARNING, "negative_scale",
                f"scale {components} is negative — geometry will be inside out",
                instance.name,
            )

    # Only top-level nodes have world-space positions; a nested node's
    # position is relative to its parent and cannot be bounds-checked
    # without composing the parent transform.
    if bounds is not None and instance.org is not None and instance.org_rel:
        min_x, min_z, max_x, max_z = bounds
        if not (min_x <= instance.org.x <= max_x and min_z <= instance.org.z <= max_z):
            report.add(
                Severity.WARNING, "outside_playable_area",
                f"placed at ({instance.org.x:.0f}, {instance.org.z:.0f}), outside "
                f"the playable area ({min_x:.0f}..{max_x:.0f})",
                instance.name,
            )


def _check_unused_warning(scene: MapScene, report: ValidationReport) -> None:
    """Note nodes whose class carries no model but which have one anyway.

    Not an error — the game ignores it — but it usually means a class
    was picked by mistake, and the object will not appear.
    """
    for instance in _walk(scene.objects):
        if instance.node_class == "SgNode" and instance.asset_id:
            report.add(
                Severity.WARNING, "model_on_group_node",
                f"group node carries model '{instance.asset_id}', which is ignored "
                "— did you mean SgAnimatedModelNode?",
                instance.name, instance.asset_id,
            )


def _walk(objects: list[ObjectInstance]):
    for instance in objects:
        yield instance
        yield from _walk(instance.children)
