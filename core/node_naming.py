# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Allocates ``world.xml`` node names for objects created in Blender.

The format names every node ``ObjectN`` and keeps the highest number
issued in the ``<World LastId="...">`` attribute. A new object needs a
name from that sequence, and ``LastId`` needs updating to match —
otherwise the editor would hand out a number already in use and two
nodes would share a name, which the format resolves by name.

Deliberately conservative: allocation starts above BOTH the recorded
``LastId`` and the highest number actually in use. Those can disagree
(a hand-edited file, or a map whose objects were renumbered), and
trusting either one alone risks a collision that is invisible until
something in the game references the wrong object.

No ``bpy`` import.
"""

from __future__ import annotations

import re

from core.objects import ObjectInstance

#: The naming pattern the editor uses. Nodes with other names exist
#: (hand-typed labels like "Vill_1760"), and they are left alone —
#: only the numbered sequence is allocated from.
_NAME_PATTERN = re.compile(r"^Object(\d+)$")


class NodeNameAllocator:
    """Issues unused ``ObjectN`` names and tracks the new ``LastId``."""

    def __init__(self, last_id: int = 0, used_names: set[str] | None = None) -> None:
        self._next = last_id
        self._used = set(used_names or ())
        self._issued: list[str] = []
        self._claimed: set[str] = set()

    @classmethod
    def from_scene(
        cls,
        objects: list[ObjectInstance],
        root_attributes: dict[str, str] | None,
        reserved: set[str] | None = None,
    ) -> "NodeNameAllocator":
        """Build an allocator from a loaded map.

        Reads ``LastId`` if present, then raises the starting point to
        clear the highest number actually in use — see the module note
        on why both are consulted.

        ``reserved`` are names the caller already holds outside the
        file: the stamps on Blender objects from an export that
        allocated a name and then failed before writing. They count
        as used, so a later export cannot hand the same number to a
        different object.
        """
        used: set[str] = set()
        highest = 0
        for name in [i.name for i in _walk(objects)] + sorted(reserved or ()):
            used.add(name)
            match = _NAME_PATTERN.match(name)
            if match:
                highest = max(highest, int(match.group(1)))

        recorded = 0
        if root_attributes:
            try:
                recorded = int(root_attributes.get("LastId", "0"))
            except ValueError:
                recorded = 0

        return cls(last_id=max(recorded, highest), used_names=used)

    def allocate(self) -> str:
        """Return a name not used by any node in the map."""
        while True:
            self._next += 1
            candidate = f"Object{self._next}"
            if candidate not in self._used:
                self._used.add(candidate)
                self._issued.append(candidate)
                return candidate

    def claim(self, name: str) -> bool:
        """Record that a node in THIS export carries ``name``.

        False when another node in the same export already claimed it.
        That happens when a Blender object is duplicated (Shift+D
        copies the custom properties, stamp included): two nodes with
        one name would be resolved by name — to one of them — by the
        game, so the second must be given a name of its own.
        """
        if name in self._claimed:
            return False
        self._claimed.add(name)
        return True

    @property
    def last_id(self) -> int:
        """The value ``<World LastId>`` should carry after allocation."""
        return self._next

    @property
    def issued(self) -> list[str]:
        return list(self._issued)


def _walk(objects: list[ObjectInstance]):
    for instance in objects:
        yield instance
        yield from _walk(instance.children)
