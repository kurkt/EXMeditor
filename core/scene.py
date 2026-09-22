# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The central scene aggregate.

``MapScene`` is what every importer produces and every exporter/bridge
consumes — see the architecture doc's "MapScene is the central object"
decision. It is an *active* object (methods, not just fields), so
query/mutation logic lives in one place instead of being
re-implemented in every operator.

Object storage is a TREE, not a flat list: ``world.xml`` nests nodes
(confirmed — ``SgNode`` containers hold children, and a child's ``org``
is parent-relative, see ``WorldXML_Format_Spec.md`` §5). ``objects``
holds only the top-level nodes; descendants hang off
``ObjectInstance.children``. Callers that want every node regardless
of depth use ``walk_objects()`` rather than iterating ``objects``
directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import dataclasses
from typing import Iterator

from core.manifest import LevelManifest
from core.collision import ObstacleSet
from core.dynamic_objects import DynamicScene
from core.objects import ObjectInstance
from core.roads import RoadNetwork
from core.raster import RasterLayer

if TYPE_CHECKING:  # pragma: no cover - import cycle: formats imports core
    from formats.exm.tilemap import TileMap
from core.terrain import HeightmapData
from core.unknown_nodes import UnknownNodeReport
from utils.math import AABB


@dataclasses.dataclass
class MapScene:
    """Everything read from (or about to be written to) one map."""

    source_dir: str | None = None
    manifest: LevelManifest | None = None
    terrain: HeightmapData | None = None
    #: ``colormap.raw`` — one packed colour per heightfield vertex, the
    #: same 512x512 grid as ``displace.bin``. Kept beside the terrain
    #: rather than folded into it: it is a separate file with its own
    #: manifest key, and a map can have one without the other.
    colormap: "RasterLayer | None" = None
    #: ``level.tile`` — the ground texture list and which tile covers
    #: each cell of the terrain.
    tilemap: "TileMap | None" = None
    #: ``WATERLEVEL`` from the map manifest — a terrain height, in the
    #: same units as ``displace.bin``. None when the map names no water.
    water_level: float | None = None
    #: ``WATERABSRED``/``GREEN``/``BLUE``, absorption coefficients.
    water_absorption: tuple[float, float, float] | None = None
    #: The map's watermap. ``None`` means the map has no water at all —
    #: not that its water is unknown.
    water_map: "RasterLayer | None" = None
    #: Where the map's sun is and what colour its light is.
    lighting: "Lighting | None" = None
    #: The map's grass, if it ships any.
    grass: "GrassField | None" = None
    #: Folder the map was read from. ``landscape.dds`` lives there and
    #: no manifest key points at it.
    map_folder: str = ""
    objects: list[ObjectInstance] = dataclasses.field(default_factory=list)
    #: Size and mtime of world.xml when it was read, so an export can
    #: tell whether something else has written the file since. Empty
    #: for a scene not loaded from disk.
    world_fingerprint: str = ""
    #: The ``<World>`` root element's own attributes from ``world.xml``
    #: (``name``, ``class``, ``LastId``). Kept verbatim so a write-back
    #: reproduces them exactly — ``LastId`` in particular is the
    #: editor's object-ID counter and must not be invented.
    world_root_attributes: dict[str, str] | None = None
    roads: RoadNetwork | None = None
    obstacles: ObstacleSet | None = None
    #: The second placement layer (dynamicscene.xml) — thousands of
    #: objects that world.xml does not contain.
    dynamic_scene: DynamicScene | None = None
    #: Node classes found in world.xml that this SDK does not model.
    #: They are skipped, not fatal — see core/unknown_nodes.py.
    unknown_nodes: UnknownNodeReport = dataclasses.field(default_factory=UnknownNodeReport)
    warnings: list[str] = dataclasses.field(default_factory=list)

    # --- object tree access ---

    def walk_objects(self) -> Iterator[ObjectInstance]:
        """Yield every object in the tree, depth-first, at any depth.

        Use this instead of iterating ``objects`` whenever the intent
        is "every object in the map" — ``objects`` alone is only the
        top-level nodes.
        """
        for root in self.objects:
            yield from root.walk()

    def add_object(self, instance: ObjectInstance, parent: ObjectInstance | None = None) -> None:
        """Add ``instance`` as a top-level object, or as a child of ``parent``."""
        if parent is None:
            self.objects.append(instance)
        else:
            parent.children.append(instance)

    def find_object(self, name: str) -> ObjectInstance | None:
        """Return the object whose ``name`` matches, searched at any depth.

        ``name`` is ``world.xml``'s per-node identifier (e.g.
        ``"Object4762"``), which is the only stable per-instance handle
        the format provides — see ``WorldXML_Format_Spec.md`` §4.
        Returns ``None`` if no node matches.
        """
        for obj in self.walk_objects():
            if obj.name == name:
                return obj
        return None

    def remove_object(self, name: str) -> bool:
        """Remove the named object (and its whole subtree) from the tree.

        Returns ``True`` if something was removed. Searches at any
        depth, since a node's position in the hierarchy isn't known to
        the caller in general.
        """
        for i, root in enumerate(self.objects):
            if root.name == name:
                del self.objects[i]
                return True
        for parent in self.walk_objects():
            for i, child in enumerate(parent.children):
                if child.name == name:
                    del parent.children[i]
                    return True
        return False

    def object_count(self) -> int:
        """Total number of objects at every depth."""
        return sum(1 for _ in self.walk_objects())

    # --- geometry ---

    def bounding_box(self) -> AABB | None:
        """Combined AABB over terrain extents + all object positions.

        Returns ``None`` for a completely empty scene rather than
        raising, so callers don't need a special case for a
        partially-loaded map.

        Only top-level objects contribute: their ``org`` is world-space
        (``orgRel=1``), whereas nested children's ``org`` is
        parent-relative (``orgRel=0``) and would be meaningless to
        union directly without composing the parent transform first —
        see ``WorldXML_Format_Spec.md`` §5. Composing full world
        transforms for nested nodes is deferred until something
        actually needs it; a top-level-only box is correct-as-far-as-
        it-goes rather than subtly wrong.
        """
        box: AABB | None = None
        if self.terrain is not None:
            box = self.terrain.local_bounds()

        positions = [obj.org for obj in self.objects if obj.org is not None]
        if positions:
            points_box = AABB.from_points(positions)
            box = points_box if box is None else box.union(points_box)
        return box
