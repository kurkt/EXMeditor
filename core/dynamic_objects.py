# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``dynamicscene.xml`` placement layer.

A map places objects in two files, not one. ``world.xml`` holds the
static scene graph; this holds thousands more — breakable fences and
trees, cables, lamp posts, named locations, NPCs — with their own
attribute set and their own way of naming what to draw.

Kept deliberately separate from ``core/objects.py``. The two layers
share nothing but the idea of a position: this one identifies objects
by ``Prototype``, carries gameplay fields (``Belong``, ``Flags``,
``Radius``) that the scene graph has no notion of, and uses different
attribute spellings for the same concepts (``Pos``/``Rot`` rather than
``org``/``rot``). Merging them into one type would mean a model where
half the fields are meaningless for any given object, and an export
that has to guess which file each object came from.
"""

from __future__ import annotations

import dataclasses

from utils.math import Vector3


@dataclasses.dataclass
class DynamicObject:
    """One ``<Object>`` from ``dynamicscene.xml``.

    Fields are ``None`` when the source attribute was absent, matching
    the convention used for ``world.xml`` — it lets a writer reproduce
    the original exactly instead of emitting defaults that were never
    there.
    """

    name: str
    prototype: str | None = None
    position: Vector3 | None = None
    raw_rotation: tuple[float, ...] | None = None
    belong: str | None = None
    #: Some objects name a model directly instead of relying on the
    #: prototype — NPCs do this. Checked before the prototype, since an
    #: explicit value is more specific than an inherited one.
    model_name: str | None = None
    children: list["DynamicObject"] = dataclasses.field(default_factory=list)
    raw_attrs: dict[str, str] = dataclasses.field(default_factory=dict)
    #: Element tag, since this file mixes Object with Post, Point,
    #: Parts and others that must round-trip unchanged.
    tag: str = "Object"

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


@dataclasses.dataclass
class DynamicScene:
    """Everything in one ``dynamicscene.xml``."""

    objects: list[DynamicObject] = dataclasses.field(default_factory=list)
    root_tag: str = "DynamicScene"
    root_attributes: dict[str, str] = dataclasses.field(default_factory=dict)

    def walk(self):
        for obj in self.objects:
            yield from obj.walk()

    def placed(self) -> list[DynamicObject]:
        """Objects with a world position — the ones that can be drawn."""
        return [obj for obj in self.walk() if obj.position is not None]

    def object_count(self) -> int:
        return sum(1 for _ in self.walk())
