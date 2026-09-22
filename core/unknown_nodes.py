# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Records node classes the SDK doesn't model, instead of guessing.

When ``world.xml`` contains a class this SDK has never seen, there are
two bad options and one good one. Guessing its meaning risks importing
something wrong; aborting the file discards every object in it,
including the thousands that parse perfectly. Both were tried: the
abort behaviour turned one unrecognised class into "0 models loaded"
on an otherwise valid map.

The third option, implemented here: skip the node, keep everything
else, and record enough about what was skipped to decide what it
actually is — attribute names, value samples, where it sits in the
hierarchy, and which models it references. That turns a new node class
from a failure into a dataset.

No ``bpy`` import.
"""

from __future__ import annotations

import dataclasses
import json


@dataclasses.dataclass
class UnknownNodeSample:
    """One skipped node, kept verbatim for inspection."""

    name: str
    attributes: dict[str, str]
    parent_class: str | None = None
    child_classes: list[str] = dataclasses.field(default_factory=list)
    depth: int = 0


@dataclasses.dataclass
class UnknownClassInfo:
    """Everything observed about one unrecognised node class."""

    class_name: str
    count: int = 0
    attribute_names: set[str] = dataclasses.field(default_factory=set)
    #: A few example values per attribute — enough to tell a model
    #: reference from a texture name without dumping the whole map.
    attribute_values: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    parent_classes: set[str] = dataclasses.field(default_factory=set)
    child_classes: set[str] = dataclasses.field(default_factory=set)
    samples: list[UnknownNodeSample] = dataclasses.field(default_factory=list)

    #: Attributes whose values look like references to something else.
    #: Which one carries the model id is the key question for a new
    #: class, and it is not always called "id".
    REFERENCE_LIKE = ("id", "model", "sprite", "texture", "material", "file", "name")

    def observe(self, sample: UnknownNodeSample, max_samples: int = 5, max_values: int = 8) -> None:
        self.count += 1
        self.attribute_names.update(sample.attributes)
        if sample.parent_class:
            self.parent_classes.add(sample.parent_class)
        self.child_classes.update(sample.child_classes)

        for key, value in sample.attributes.items():
            values = self.attribute_values.setdefault(key, [])
            if value not in values and len(values) < max_values:
                values.append(value)

        if len(self.samples) < max_samples:
            self.samples.append(sample)

    def referenced_values(self) -> dict[str, list[str]]:
        """Values from attributes that plausibly name another asset."""
        return {
            key: values
            for key, values in self.attribute_values.items()
            if key.lower() in self.REFERENCE_LIKE and values
        }

    def describe(self) -> str:
        lines = [
            f"=== Unknown node class: {self.class_name} ===",
            f"Occurrences : {self.count}",
            f"Attributes  : {', '.join(sorted(self.attribute_names)) or 'none'}",
        ]
        if self.parent_classes:
            lines.append(f"Parent(s)   : {', '.join(sorted(self.parent_classes))}")
        if self.child_classes:
            lines.append(f"Children    : {', '.join(sorted(self.child_classes))}")
        else:
            lines.append("Children    : none (leaf node)")

        references = self.referenced_values()
        if references:
            lines.append("Reference-like attributes:")
            for key, values in sorted(references.items()):
                shown = ", ".join(values[:6])
                more = "" if len(values) <= 6 else f" (+{len(values) - 6} more)"
                lines.append(f"    {key} = {shown}{more}")

        lines.append("Sample values:")
        for key in sorted(self.attribute_names):
            values = self.attribute_values.get(key, [])
            shown = ", ".join(values[:4])
            lines.append(f"    {key:<14} {shown}")

        lines.append("Examples:")
        for sample in self.samples:
            attributes = " ".join(f'{k}="{v}"' for k, v in sample.attributes.items())
            lines.append(f"    <Node {attributes} />")
        return "\n".join(lines)


@dataclasses.dataclass
class UnknownNodeReport:
    """Every unrecognised class found while reading a map."""

    classes: dict[str, UnknownClassInfo] = dataclasses.field(default_factory=dict)

    def record(
        self,
        class_name: str,
        name: str,
        attributes: dict[str, str],
        *,
        parent_class: str | None = None,
        child_classes: list[str] | None = None,
        depth: int = 0,
    ) -> None:
        info = self.classes.setdefault(class_name, UnknownClassInfo(class_name=class_name))
        info.observe(UnknownNodeSample(
            name=name,
            attributes=dict(attributes),
            parent_class=parent_class,
            child_classes=list(child_classes or []),
            depth=depth,
        ))

    def __bool__(self) -> bool:
        return bool(self.classes)

    @property
    def total_skipped(self) -> int:
        return sum(info.count for info in self.classes.values())

    def summary_lines(self) -> list[str]:
        if not self.classes:
            return ["No unknown node classes."]
        lines = [
            f"{len(self.classes)} unknown node class(es), "
            f"{self.total_skipped} node(s) skipped:",
        ]
        for name, info in sorted(self.classes.items(), key=lambda kv: -kv[1].count):
            lines.append(f"    {name}: {info.count}")
        return lines

    def describe(self) -> str:
        blocks = [info.describe() for _, info in sorted(
            self.classes.items(), key=lambda kv: -kv[1].count,
        )]
        return "\n\n".join(blocks)

    def to_dict(self) -> dict:
        """Serialisable form, for writing a report file."""
        return {
            "unknown_classes": [
                {
                    "class": info.class_name,
                    "count": info.count,
                    "attributes": sorted(info.attribute_names),
                    "attribute_values": {
                        k: v for k, v in sorted(info.attribute_values.items())
                    },
                    "reference_like": info.referenced_values(),
                    "parent_classes": sorted(info.parent_classes),
                    "child_classes": sorted(info.child_classes),
                    "samples": [
                        {
                            "name": s.name,
                            "depth": s.depth,
                            "parent_class": s.parent_class,
                            "child_classes": s.child_classes,
                            "attributes": s.attributes,
                        }
                        for s in info.samples
                    ],
                }
                for _, info in sorted(self.classes.items(), key=lambda kv: -kv[1].count)
            ],
        }

    def write_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, ensure_ascii=False)
