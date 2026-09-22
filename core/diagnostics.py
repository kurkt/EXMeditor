# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Model resolution diagnostics.

Answers "why is this specific model missing?" instead of leaving the
question to inference. Every object referenced by ``world.xml`` gets a
record of how far it travelled along the chain::

    world.xml -> object id -> catalogue -> .gam path
              -> file found -> parsed -> Blender object -> linked -> visible

and, when it stopped early, exactly where and why.

The stages are deliberately ordered and explicit: the value of this
tool is that a break at "catalogue entry missing" and a break at "file
not on disk" look nothing alike and need different fixes, whereas both
previously showed up as "the model isn't there".

No ``bpy`` import — the Blender-side stages are filled in by the
caller, so the whole chain can also be produced from a CLI or a test
without launching Blender.
"""

from __future__ import annotations

import dataclasses
import enum


class Stage(enum.IntEnum):
    """Steps in resolving an object to a visible Blender model.

    Ordered: a record's ``reached`` value is the last stage completed,
    so comparing against these constants tells you what happened.
    """

    REFERENCED = 0        # named by a world.xml node
    HAS_ID = 1            # the node actually carries an id
    CATALOGUE_ENTRY = 2   # the id was found in a model catalogue
    FILE_PATH = 3         # the entry gave a .gam path
    FILE_FOUND = 4        # that path resolved to a file on disk
    PARSED = 5            # the .gam was read successfully
    HAS_GEOMETRY = 6      # it contained at least one mesh with triangles
    OBJECT_CREATED = 7    # a Blender object was created for the node
    LINKED = 8            # that object was linked into a collection
    VISIBLE = 9           # and it is actually visible in the viewport


#: Human-readable names, used in reports.
STAGE_NAMES = {
    Stage.REFERENCED: "referenced by world.xml",
    Stage.HAS_ID: "node has a model id",
    Stage.CATALOGUE_ENTRY: "id found in catalogue",
    Stage.FILE_PATH: "catalogue gives a .gam path",
    Stage.FILE_FOUND: "file found on disk",
    Stage.PARSED: "file parsed",
    Stage.HAS_GEOMETRY: "contains geometry",
    Stage.OBJECT_CREATED: "Blender object created",
    Stage.LINKED: "linked into a collection",
    Stage.VISIBLE: "visible",
}


@dataclasses.dataclass
class ResolutionRecord:
    """How far one object got, and why it stopped.

    One record per ``world.xml`` node, not per model id — the same
    model can succeed for one node and fail for another (e.g. it was
    created but not linked), and collapsing them would hide that.
    """

    node_name: str
    asset_id: str | None = None
    node_class: str | None = None

    reached: Stage = Stage.REFERENCED
    failure_reason: str | None = None
    #: True for nodes that are not supposed to have a model at all —
    #: sound emitters and plain grouping nodes. Counting these as
    #: failures buries the real ones under expected noise, so they are
    #: reported separately.
    expected_no_model: bool = False

    # Filled in as the chain progresses; each is None until its stage
    # is reached, so a report can show exactly what was known when
    # things stopped.
    catalogue_source: str | None = None   # which catalogue file supplied the entry
    game_path: str | None = None          # the path as written in the catalogue
    resolved_path: str | None = None      # where it was actually found on disk
    mesh_count: int | None = None
    vertex_count: int | None = None
    triangle_count: int | None = None
    blender_object: str | None = None
    collection: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.reached >= Stage.VISIBLE

    @property
    def failed_at(self) -> Stage | None:
        """The stage that was NOT reached, or ``None`` if fully resolved."""
        if self.succeeded:
            return None
        return Stage(self.reached + 1)

    def advance(self, stage: Stage) -> None:
        """Record that ``stage`` completed."""
        if stage > self.reached:
            self.reached = stage

    def not_applicable(self, reason: str) -> None:
        """Record that this node legitimately has no model."""
        self.expected_no_model = True
        self.failure_reason = reason

    def fail(self, stage: Stage, reason: str) -> None:
        """Record that the chain stopped before ``stage``.

        ``reached`` is left at the previous stage, so ``failed_at``
        reports ``stage`` itself as the missing step.
        """
        self.reached = Stage(max(int(stage) - 1, 0))
        self.failure_reason = reason

    def describe_chain(self) -> str:
        """Multi-line rendering of the whole chain for one object."""
        lines = [f"{self.node_name}" + (f'  (id "{self.asset_id}")' if self.asset_id else "")]
        for stage in Stage:
            if stage == Stage.REFERENCED:
                continue
            if self.reached >= stage:
                mark, note = "OK  ", self._detail_for(stage)
            elif self.failed_at == stage:
                mark, note = "STOP", self.failure_reason or ""
            else:
                mark, note = "  - ", ""
            suffix = f"  {note}" if note else ""
            lines.append(f"    [{mark}] {STAGE_NAMES[stage]}{suffix}")
        return "\n".join(lines)

    def _detail_for(self, stage: Stage) -> str:
        if stage == Stage.CATALOGUE_ENTRY and self.catalogue_source:
            return f"from {self.catalogue_source}"
        if stage == Stage.FILE_PATH and self.game_path:
            return self.game_path
        if stage == Stage.FILE_FOUND and self.resolved_path:
            return self.resolved_path
        if stage == Stage.HAS_GEOMETRY and self.mesh_count is not None:
            return (
                f"{self.mesh_count} mesh(es), {self.vertex_count} verts, "
                f"{self.triangle_count} tris"
            )
        if stage == Stage.OBJECT_CREATED and self.blender_object:
            return self.blender_object
        if stage == Stage.LINKED and self.collection:
            return self.collection
        return ""


@dataclasses.dataclass
class DiagnosticsReport:
    """Every object's record, plus the comparison counts."""

    records: list[ResolutionRecord] = dataclasses.field(default_factory=list)

    # --- comparison view ---

    def referenced(self) -> int:
        return len(self.records)

    def expecting_a_model(self) -> list[ResolutionRecord]:
        """Only the nodes that should produce geometry."""
        return [r for r in self.records if not r.expected_no_model]

    def not_applicable(self) -> int:
        """Sound emitters and grouping nodes — no model expected."""
        return sum(1 for r in self.records if r.expected_no_model)

    def resolved(self) -> int:
        return sum(1 for r in self.records if r.succeeded)

    def failed_at_stage(self, stage: Stage) -> list[ResolutionRecord]:
        return [
            r for r in self.records
            if not r.expected_no_model and r.failed_at == stage
        ]

    def counts_by_failure(self) -> dict[Stage, int]:
        """How many objects stopped at each stage, in stage order."""
        counts: dict[Stage, int] = {}
        for record in self.expecting_a_model():
            stage = record.failed_at
            if stage is not None:
                counts[stage] = counts.get(stage, 0) + 1
        return dict(sorted(counts.items()))

    def affected_ids(self, stage: Stage) -> dict[str, int]:
        """Which model ids stopped at ``stage``, and how many times.

        Grouping by id is what turns "412 objects missing" into "3
        models missing, used 412 times" — a very different problem.
        """
        ids: dict[str, int] = {}
        for record in self.failed_at_stage(stage):
            key = record.asset_id or "(no id)"
            ids[key] = ids.get(key, 0) + 1
        return dict(sorted(ids.items(), key=lambda kv: -kv[1]))

    # --- rendering ---

    def summary_lines(self) -> list[str]:
        """The comparison table, one line per row."""
        expecting = len(self.expecting_a_model())
        lines = [
            f"Objects referenced by world.xml   : {self.referenced()}",
            f"  of which expect a model         : {expecting}",
            f"  sound/group nodes (no model)    : {self.not_applicable()}",
            f"Objects successfully resolved     : {self.resolved()}",
        ]
        counts = self.counts_by_failure()
        for stage, count in counts.items():
            lines.append(f"  stopped at {STAGE_NAMES[stage]:<28}: {count}")
        if not counts:
            lines.append("  no failures")
        return lines

    def failure_detail_lines(self, max_ids_per_stage: int = 12) -> list[str]:
        """Per-stage breakdown naming the model ids responsible."""
        lines: list[str] = []
        for stage, count in self.counts_by_failure().items():
            ids = self.affected_ids(stage)
            lines.append(f"--- {STAGE_NAMES[stage]}: {count} object(s), {len(ids)} distinct id(s)")
            for model_id, uses in list(ids.items())[:max_ids_per_stage]:
                example = next(
                    r for r in self.failed_at_stage(stage)
                    if (r.asset_id or "(no id)") == model_id
                )
                reason = f" — {example.failure_reason}" if example.failure_reason else ""
                lines.append(f"      {model_id}  (used {uses}x){reason}")
            if len(ids) > max_ids_per_stage:
                lines.append(f"      ... and {len(ids) - max_ids_per_stage} more")
        return lines

    def find(self, name_or_id: str) -> list[ResolutionRecord]:
        """Records matching a node name or a model id, for drilling in."""
        needle = name_or_id.lower()
        return [
            r for r in self.records
            if r.node_name.lower() == needle or (r.asset_id or "").lower() == needle
        ]
