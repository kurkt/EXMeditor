# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Road domain model (``LevelRoads.xml``).

Confirmed structure, from real map data: a flat list of ``RoadNode``
elements linked into chains by ``FwdZLink``/``BackZLink`` name
references. On the reference map, 1236 nodes form 52 chains covering
every node — no closed loops, no dangling links.

A chain is what a user thinks of as "a road", so that's the unit this
module models: ``RoadChain`` holds its nodes in traversal order, and
becomes one editable Blender curve.

Per-node attributes (``roadset``, ``skinNumber``, ``ModelNum``,
``AsCliff``) are kept per node rather than promoted to the chain, even
though they're usually uniform along one: ``skinNumber`` was observed
varying *within* a single chain on real data, so promoting it would
silently lose information on export.
"""

from __future__ import annotations

import dataclasses

from utils.math import Vector3


@dataclasses.dataclass
class RoadNode:
    """One ``<RoadNode>`` element.

    ``name`` is the identifier other nodes link to; it must survive a
    round trip unchanged or the chain structure breaks.

    Fields are ``None`` when the source attribute was absent, so a
    writer can reproduce the original exactly rather than emitting
    attributes the source never had.
    """

    name: str
    org: Vector3
    roadset: str | None = None
    skin_number: int | None = None
    model_num: int | None = None
    #: Present only on cliff-type nodes (19 of 1236 on the reference
    #: map, all in the ``r2_cliff`` roadset). Kept verbatim.
    as_cliff: str | None = None
    node_class: str = "RoadNode"
    raw_attrs: dict[str, str] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class RoadChain:
    """A connected run of road nodes, in traversal order.

    Built by following ``FwdZLink`` from a node that has no
    ``BackZLink``. One chain becomes one Blender curve.
    """

    nodes: list[RoadNode] = dataclasses.field(default_factory=list)

    @property
    def roadset(self) -> str | None:
        """The roadset of the first node, for naming/display.

        Only a label — the authoritative per-node value is on each
        ``RoadNode``, since a chain is not guaranteed to be uniform.
        """
        return self.nodes[0].roadset if self.nodes else None

    def __len__(self) -> int:
        return len(self.nodes)


@dataclasses.dataclass
class RoadNetwork:
    """Every road on a map."""

    chains: list[RoadChain] = dataclasses.field(default_factory=list)

    def node_count(self) -> int:
        return sum(len(chain) for chain in self.chains)

    def all_nodes(self):
        """Yield every node across every chain, in chain order."""
        for chain in self.chains:
            yield from chain.nodes


#: A quarter turn about the length axis, applied to a road piece before
#: it is arrayed along its chain.
#:
#: OBSERVED, in the viewport, not derived: an imported road came in
#: standing on its edge, and turning the surface object 90 degrees about
#: its own Y — the piece's length axis — laid it perfectly flat. Either
#: sign worked, on roads running in any direction.
#:
#: What that rules out was measured first. The piece mesh is built
#: correctly (X 19.77 wide, Y 14.45 long, Z 0.19 high), the chain curve
#: is 3D with twist Z_UP, the surface object carries no rotation of its
#: own, and the modifiers are ARRAY then CURVE on POS_Y. None of those
#: is the cause. What is left is Blender's own convention for which way
#: a Curve modifier lays a cross-section down on ``POS_Y``, and this is
#: the quarter turn that answers it.
#:
#: It belongs to the PIECE, not to the object. The modifier maps the
#: mesh's own length axis onto the curve tangent, so a roll about that
#: axis in the mesh's frame is a roll about the tangent everywhere
#: along the curve — right on a road that bends, which turning the
#: object is not.
#:
#: If a road ever comes in showing its underside, this is the sign to
#: flip and nothing else.
def roll_about_length(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Turn a point a quarter turn about the piece's length.

    In MODEL space — which is where road pieces are rolled, before the
    coordinate transform — the axes are X across, **Y up** and **Z
    along**. So the length is Z and this leaves Z alone, swapping the
    width and the height.

    Getting that wrong is easy and quiet: rotating about model Y
    exchanges the width with the LENGTH, which leaves the piece just as
    flat as it was and merely 19.77 long instead of 14.45. It looks
    like nothing happened.
    """
    return (y, -x, z)
