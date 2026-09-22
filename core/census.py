# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""A census of ``world.xml``: everything in the file, not everything parsed.

Every other report in this SDK counts what it managed to read, which
makes it structurally incapable of showing what it missed. If a node
class is skipped during parsing, it never reaches the object model, so
no count derived from that model can mention it. The blind spot is
invisible by construction.

This module works from the XML directly and counts everything: known
classes and unknown ones, modelled attributes and ignored ones. It is
deliberately independent of ``formats/exm/world.py`` — sharing the
parser would reintroduce the same blindness, since the parser's
decisions are exactly what needs auditing.

The question it answers: *has 100% of this file been accounted for, and
if not, what exactly was left out?*

No ``bpy`` import.
"""

from __future__ import annotations

import dataclasses
import re
import xml.etree.ElementTree as ET

from core.objects import KNOWN_NODE_CLASSES
from utils.errors import ErrorContext, ParsingError

_ENCODING = "cp1251"

#: Attributes ``formats/exm/world.py`` turns into fields on
#: ``ObjectInstance``. Anything else is preserved verbatim in
#: ``raw_attrs`` — carried through an export correctly, but never
#: interpreted, which is a different thing and worth reporting.
MODELLED_ATTRIBUTES = frozenset({
    "name", "class", "org", "orgRel", "rot", "scale",
    "id", "skin", "CastShadow", "ndmAction",
})


#: Node names the editor issues, ``ObjectN``.
_OBJECT_NAME = re.compile(r"^Object(\d+)$")


@dataclasses.dataclass
class ClassCensus:
    """Everything seen for one node class."""

    class_name: str
    count: int = 0
    known: bool = False
    attribute_counts: dict[str, int] = dataclasses.field(default_factory=dict)
    attribute_samples: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    #: First node of this class encountered, for pointing at a real
    #: example rather than describing one.
    first_name: str | None = None
    depths: set[int] = dataclasses.field(default_factory=set)

    def observe(self, element: ET.Element, depth: int, max_samples: int = 5) -> None:
        self.count += 1
        self.depths.add(depth)
        if self.first_name is None:
            self.first_name = element.get("name")
        for key, value in element.attrib.items():
            self.attribute_counts[key] = self.attribute_counts.get(key, 0) + 1
            samples = self.attribute_samples.setdefault(key, [])
            if value not in samples and len(samples) < max_samples:
                samples.append(value)

    @property
    def modelled_attributes(self) -> set[str]:
        return {a for a in self.attribute_counts if a in MODELLED_ATTRIBUTES}

    @property
    def ignored_attributes(self) -> set[str]:
        """Attributes read and preserved, but never interpreted.

        These round-trip correctly through an export — nothing is lost
        — but the SDK assigns them no meaning, so a difference between
        the game and Blender could easily be hiding in one of them.
        """
        return {a for a in self.attribute_counts if a not in MODELLED_ATTRIBUTES}


@dataclasses.dataclass
class WorldCensus:
    """The whole file, accounted for."""

    classes: dict[str, ClassCensus] = dataclasses.field(default_factory=dict)
    root_attributes: dict[str, str] = dataclasses.field(default_factory=dict)
    #: Highest N seen in an ``ObjectN`` node name, for comparing against
    #: the ``LastId`` counter new nodes are allocated above.
    highest_object_number: int = 0
    total_attribute_instances: int = 0

    # --- totals ---

    @property
    def total_nodes(self) -> int:
        return sum(c.count for c in self.classes.values())

    @property
    def known_nodes(self) -> int:
        return sum(c.count for c in self.classes.values() if c.known)

    @property
    def unknown_nodes(self) -> int:
        return sum(c.count for c in self.classes.values() if not c.known)

    def known_classes(self) -> list[ClassCensus]:
        return sorted(
            (c for c in self.classes.values() if c.known), key=lambda c: -c.count,
        )

    def unknown_classes(self) -> list[ClassCensus]:
        return sorted(
            (c for c in self.classes.values() if not c.known), key=lambda c: -c.count,
        )

    @property
    def modelled_attribute_instances(self) -> int:
        return sum(
            count
            for census in self.classes.values()
            for name, count in census.attribute_counts.items()
            if name in MODELLED_ATTRIBUTES
        )

    @property
    def ignored_attribute_instances(self) -> int:
        return self.total_attribute_instances - self.modelled_attribute_instances

    def ignored_attribute_summary(self) -> dict[str, int]:
        """Every attribute the SDK doesn't interpret, with its frequency."""
        totals: dict[str, int] = {}
        for census in self.classes.values():
            for name, count in census.attribute_counts.items():
                if name not in MODELLED_ATTRIBUTES:
                    totals[name] = totals.get(name, 0) + count
        return dict(sorted(totals.items(), key=lambda kv: -kv[1]))

    # --- rendering ---

    def summary_lines(self) -> list[str]:
        lines = ["=== world.xml Census ==="]
        lines.append(f"Total <Node> elements : {self.total_nodes}")
        lines.append(f"  handled by the SDK  : {self.known_nodes}")
        lines.append(f"  skipped (unknown)   : {self.unknown_nodes}")
        lines.append("")

        lines.append("Known classes:")
        for census in self.known_classes():
            lines.append(f"  {census.class_name:<28} {census.count:6d}")
        if not self.known_classes():
            lines.append("  (none)")

        lines.append("")
        unknown = self.unknown_classes()
        if unknown:
            lines.append("Unknown classes (skipped, nothing imported from them):")
            for census in unknown:
                example = f"  e.g. name={census.first_name}" if census.first_name else ""
                lines.append(f"  {census.class_name:<28} {census.count:6d}{example}")
        else:
            lines.append("Unknown classes: none — every node class is handled.")

        lines.append("")
        lines.append("Attribute coverage:")
        lines.append(f"  attribute instances read      : {self.total_attribute_instances}")
        lines.append(f"  interpreted by the SDK        : {self.modelled_attribute_instances}")
        lines.append(f"  preserved but not interpreted : {self.ignored_attribute_instances}")
        lines += self.id_counter_lines()
        return lines

    def id_counter_lines(self) -> list[str]:
        """``LastId`` beside the node numbers actually in the file.

        The SDK allocates new node names above both, which is right in
        principle and only as sound as the larger of the two. A map was
        seen issuing ``Object76476712`` where every node the editor had
        ever written was numbered in the thousands — and the editor
        dropped that node on the next save. Whether the number caused
        it or not, a counter ten thousand times past everything in use
        is worth seeing rather than inferring from a node name.
        """
        recorded = None
        raw = self.root_attributes.get("LastId")
        if raw is not None:
            try:
                recorded = int(raw)
            except ValueError:
                return ["", f"LastId is not a number: {raw!r}"]

        highest = self.highest_object_number

        if recorded is None and not highest:
            return []

        lines = ["", "Node id counter:"]
        lines.append(f"  LastId recorded in <World>    : {recorded}")
        lines.append(f"  highest ObjectN actually used : {highest}")

        if recorded is not None and highest:
            gap = recorded - highest
            if gap > 1_000_000:
                lines += [
                    f"  ! LastId runs {gap} past the highest node in the file.",
                    "    New nodes are allocated above it, so they will carry",
                    "    numbers unlike anything the editor has ever written.",
                ]
            elif recorded < highest:
                lines.append(
                    "  ! LastId is BEHIND the file — nodes exist above the "
                    "counter"
                )
        return lines

    def attribute_lines(self, limit: int = 30) -> list[str]:
        ignored = self.ignored_attribute_summary()
        if not ignored:
            return ["Every attribute in the file is interpreted."]

        lines = [
            "--- Attributes preserved but not interpreted ---",
            "(these round-trip through an export unchanged, but the SDK",
            " assigns them no meaning — a game/Blender difference could",
            " be hiding in one of them)",
        ]
        for name, count in list(ignored.items())[:limit]:
            classes = sorted(
                census.class_name for census in self.classes.values()
                if name in census.attribute_counts
            )
            samples: list[str] = []
            for census in self.classes.values():
                samples.extend(census.attribute_samples.get(name, []))
            shown = ", ".join(dict.fromkeys(samples))[:70]
            lines.append(f"  {name:<20} {count:6d}  on {', '.join(classes)}")
            if shown:
                lines.append(f"       values: {shown}")
        if len(ignored) > limit:
            lines.append(f"  ... and {len(ignored) - limit} more")
        return lines

    def class_detail_lines(self, class_name: str) -> list[str]:
        census = self.classes.get(class_name)
        if census is None:
            return [f"'{class_name}' does not appear in this file."]

        lines = [
            f"=== {census.class_name} ===",
            f"count        : {census.count}",
            f"handled      : {'yes' if census.known else 'no — nodes are skipped'}",
            f"depths       : {', '.join(str(d) for d in sorted(census.depths))}",
            f"first example: {census.first_name}",
            "",
            "Attributes:",
        ]
        for name in sorted(census.attribute_counts):
            mark = "interpreted" if name in MODELLED_ATTRIBUTES else "IGNORED    "
            samples = ", ".join(census.attribute_samples.get(name, []))[:60]
            lines.append(
                f"  [{mark}] {name:<16} {census.attribute_counts[name]:6d}  {samples}"
            )
        return lines


def census_world(path: str) -> WorldCensus:
    """Count every node and attribute in a ``world.xml``.

    Reads the XML itself rather than going through the object model,
    so nodes the parser skips are still counted — which is the entire
    point.
    """
    try:
        with open(path, encoding=_ENCODING) as handle:
            text = handle.read()
    except OSError as exc:
        raise ParsingError(
            "could not read world.xml",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ParsingError(
            "world.xml is not well-formed XML",
            context=ErrorContext(source_file=path, extra={"parse_error": str(exc)}),
        ) from exc

    census = WorldCensus(root_attributes=dict(root.attrib))

    def visit(element: ET.Element, depth: int) -> None:
        class_name = element.get("class") or "(no class attribute)"
        entry = census.classes.setdefault(
            class_name,
            ClassCensus(class_name=class_name, known=class_name in KNOWN_NODE_CLASSES),
        )
        entry.observe(element, depth)
        match = _OBJECT_NAME.match(element.get("name") or "")
        if match:
            census.highest_object_number = max(
                census.highest_object_number, int(match.group(1))
            )
        census.total_attribute_instances += len(element.attrib)
        for child in element.findall("Node"):
            visit(child, depth + 1)

    for child in root.findall("Node"):
        visit(child, 1)

    return census


@dataclasses.dataclass
class DynamicSceneCensus:
    """A census of ``dynamicscene.xml`` — the second placement layer.

    Objects live in two files, and only one of them was ever imported.
    ``world.xml`` holds the static scene graph; ``dynamicscene.xml``
    holds thousands more placed objects (breakable fences and trees,
    cables, poles, named locations, NPCs) that never appear in it.

    They are referenced differently: by ``Prototype`` name rather than
    by a model id, and that name does not resolve through
    ``AnimModels.xml`` or ``servers.xml``. Until the catalogue that
    defines prototypes is known, this counts what is there rather than
    guessing how to load it.
    """

    total_objects: int = 0
    placed_objects: int = 0        # have a Pos attribute
    prototypes: dict[str, int] = dataclasses.field(default_factory=dict)
    model_names: dict[str, int] = dataclasses.field(default_factory=dict)
    attribute_counts: dict[str, int] = dataclasses.field(default_factory=dict)

    def split_by_resolvability(self, catalog) -> tuple[list, list]:
        """Split prototypes into those that are model ids and those that
        are not.

        Not all prototypes are alike: some name a model directly and
        would load with no new machinery, while the rest are
        game-object prototypes that need a definition file this SDK
        has never seen. Reporting them as one number hides that the
        problem has two halves of very different difficulty.
        """
        direct, needs_catalogue = [], []
        for name, count in sorted(self.prototypes.items(), key=lambda kv: -kv[1]):
            (direct if catalog.get(name) else needs_catalogue).append((name, count))
        return direct, needs_catalogue

    def resolvability_lines(self, catalog, limit: int = 15) -> list[str]:
        direct, needs_catalogue = self.split_by_resolvability(catalog)
        direct_objects = sum(c for _, c in direct)
        other_objects = sum(c for _, c in needs_catalogue)

        lines = [
            "--- Prototype resolvability ---",
            f"  name a known model      : {len(direct):3d} prototype(s), "
            f"{direct_objects} object(s)",
            f"  need a prototype file   : {len(needs_catalogue):3d} prototype(s), "
            f"{other_objects} object(s)",
        ]
        if direct:
            lines.append("")
            lines.append("  Resolvable today:")
            for name, count in direct[:limit]:
                entry = catalog.get(name)
                lines.append(f"    {name:<26} {count:5d}  {entry.file_path}")
        if needs_catalogue:
            lines.append("")
            lines.append("  Unresolved (no catalogue entry):")
            for name, count in needs_catalogue[:limit]:
                lines.append(f"    {name:<26} {count:5d}")
            if len(needs_catalogue) > limit:
                lines.append(f"    ... and {len(needs_catalogue) - limit} more")
        return lines

    def summary_lines(self, limit: int = 20) -> list[str]:
        lines = [
            "=== dynamicscene.xml Census ===",
            f"Objects                     : {self.total_objects}",
            f"  with a world position     : {self.placed_objects}",
            f"  distinct prototypes       : {len(self.prototypes)}",
            f"  with an explicit model    : {len(self.model_names)}",
            "",
            "NOTE: none of these are imported. They are placed by",
            "Prototype name, which does not resolve through the model",
            "catalogues this SDK reads.",
            "",
            "Most used prototypes:",
        ]
        for name, count in sorted(self.prototypes.items(), key=lambda kv: -kv[1])[:limit]:
            lines.append(f"  {name:<28} {count:6d}")
        if len(self.prototypes) > limit:
            lines.append(f"  ... and {len(self.prototypes) - limit} more")

        if self.model_names:
            lines.append("")
            lines.append("Direct model references (these DO resolve):")
            for name, count in sorted(self.model_names.items(), key=lambda kv: -kv[1]):
                lines.append(f"  {name:<28} {count:6d}")
        return lines


def census_dynamic_scene(path: str) -> DynamicSceneCensus:
    """Count the objects in ``dynamicscene.xml``.

    Counts placement, not semantics: the point is to make the size of
    the unimported layer visible, since a report saying "every model
    imported" is misleading while thousands of objects sit in a file
    nobody reads.
    """
    try:
        with open(path, encoding=_ENCODING) as handle:
            text = handle.read()
    except OSError as exc:
        raise ParsingError(
            "could not read dynamicscene.xml",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ParsingError(
            "dynamicscene.xml is not well-formed XML",
            context=ErrorContext(source_file=path, extra={"parse_error": str(exc)}),
        ) from exc

    census = DynamicSceneCensus()

    def visit(element: ET.Element) -> None:
        if element.tag == "Object":
            census.total_objects += 1
            if element.get("Pos"):
                census.placed_objects += 1
            prototype = element.get("Prototype")
            if prototype:
                census.prototypes[prototype] = census.prototypes.get(prototype, 0) + 1
            model = element.get("ModelName")
            if model:
                census.model_names[model] = census.model_names.get(model, 0) + 1
            for key in element.attrib:
                census.attribute_counts[key] = census.attribute_counts.get(key, 0) + 1
        for child in element:
            visit(child)

    for child in root:
        visit(child)

    return census
