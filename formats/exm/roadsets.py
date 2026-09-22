# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""``roads.xml`` — which models a road is built from.

A road in this game is not a generated ribbon. ``levelroads.xml`` gives
a chain of nodes, each naming a ``roadset`` and a ``ModelNum``, and
``roads.xml`` maps those to ``.gam`` models::

    <Set name="road_country" scale="1.000 1.000 1.000"
         WheelTrace="data\\fx\\protectors\\protector_ground.tga">
        <Item model="data\\models\\roads\\road_country\\road_country.gam"/>
        <Item model="...\\road_country_end.gam" type="1"/>
        <Item model="...\\cross_small.gam"      type="2"/>
        <Item model="...\\small_end.gam"        type="3"/>
    </Set>

The engine lays those pieces along the chain. ``type`` is the shape:
the untyped item is the straight run, and the sample data comments
1, 2 and 3 as the T junction, the X junction and the end cap.

Why this matters for the import
-------------------------------

Without it a road can only be guessed at: a strip of some width with
some texture. With it the width comes from the model's own bounding
box and the surface from its own skin chunk, so the road in Blender is
the size and the material the game draws.

``road.fx`` also settles how it is drawn — "simple diffuse shader for
roads (alpha-blended)", with a lightmap sampled at a scale of its own.
Alpha blending is why a road in the editor melts into the ground
instead of sitting on it as a slab.
"""

from __future__ import annotations

import dataclasses
import os
import re

from utils.logging import get_logger

logger = get_logger("formats.exm.roadsets")

#: ``type`` values in ``roads.xml``, from the comments beside them in
#: the shipped file: ``type="1"`` T перекрестoк, ``type="2"``
#: X перекрестoк, ``type="3"`` конец. An untyped item is the straight
#: run.
STRAIGHT = 0
T_JUNCTION = 1
X_JUNCTION = 2
END_CAP = 3

#: Node degree -> the piece that node is drawn with. MEASURED on
#: ``r1m2/levelroads.xml``, 3985 nodes::
#:
#:     degree 1   371   only one link          -> end cap
#:     degree 2  3599   FwdZLink + BackZLink   -> straight
#:     degree 3     9   + one pair of X links  -> T junction
#:     degree 4     6   all four links         -> X junction
#:
#: 371 + 3599 + 9 + 6 = 3985, so every node is accounted for and the
#: degrees stop at four — which is what a road graph of straights,
#: tees and crossings has to look like.
#:
#: The degree is the count of link attributes a node carries:
#: ``FwdZLink``, ``BackZLink``, ``FwdXLink``, ``BackXLink``.
#:
#: **``ModelNum`` is not this.** It reads 0 on all 3985 nodes of that
#: map, so a reader that trusts it draws every junction and every dead
#: end as a straight piece — which is what this SDK has been doing.
PIECE_FOR_DEGREE = {
    1: END_CAP,
    2: STRAIGHT,
    3: T_JUNCTION,
    4: X_JUNCTION,
}

#: The link attributes whose presence gives a node its degree.
LINK_ATTRIBUTES = ("FwdZLink", "BackZLink", "FwdXLink", "BackXLink")


def node_degree(attributes) -> int:
    """How many roads meet at a node, from the links it names."""
    return sum(1 for key in LINK_ATTRIBUTES if (attributes.get(key) or "").strip())


def piece_for_degree(degree: int) -> int:
    """Which ``roads.xml`` piece type a node of this degree is drawn with.

    A degree the measurement never saw falls back to the straight run
    rather than to nothing: an unfamiliar node still deserves a road.
    """
    return PIECE_FOR_DEGREE.get(degree, STRAIGHT)

_SET_PATTERN = re.compile(
    r'<Set\s+name="([^"]+)"(.*?)</Set>', re.S | re.I
)
_ITEM_PATTERN = re.compile(
    r'<Item\s+model="([^"]+)"(?:[^>]*?type="(\d+)")?', re.I
)
_SCALE_PATTERN = re.compile(r'scale="([^"]+)"', re.I)


@dataclasses.dataclass
class RoadSet:
    """One named road, and the pieces it is built from."""

    name: str
    #: Straight-run models, in file order. More than one means the node
    #: picks between them by ``skinNumber``.
    straights: list = dataclasses.field(default_factory=list)
    #: ``type`` -> game-relative model path, for the junctions and caps.
    models: dict = dataclasses.field(default_factory=dict)
    scale: tuple = (1.0, 1.0, 1.0)

    def straight(self, skin: int = 0) -> str:
        """The model for a plain run, for a given ``skinNumber``.

        An untyped ``<Item>`` is a straight-run variant, and a set may
        list several: ``rock_cliff`` names ``rock_clif_1`` through
        ``rock_clif_4``, and the sample map's ``skinNumber`` runs 0..3.
        Keeping only the last one — which is what storing them in a
        dict by type did — collapses four different pieces of cliff
        into one.
        """
        if not self.straights:
            return ""
        return self.straights[skin % len(self.straights)]

    def piece(self, model_num: int, skin: int = 0) -> str:
        """The model for one node, by piece type and ``skinNumber``.

        ``model_num`` is a piece type — :data:`STRAIGHT`,
        :data:`T_JUNCTION`, :data:`X_JUNCTION`, :data:`END_CAP` — and
        NOT a node's ``ModelNum`` attribute, whatever the name suggests:
        that attribute is 0 on every node measured. Callers get the
        type from :func:`piece_for_degree`.
        """
        if model_num == STRAIGHT:
            return self.straight(skin)
        return self.models.get(model_num, "")

    def piece_for_node(self, attributes, skin: int = 0) -> str:
        """The model for a node, from the links it carries."""
        return self.piece(piece_for_degree(node_degree(attributes)), skin)


def read_road_sets(path: str) -> dict:
    """Read ``roads.xml`` into ``name -> RoadSet``.

    Parsed with expressions rather than an XML reader for the same
    reason the rest of this project does: the shipped files carry
    comments in Windows-1251 mid-attribute and are not always
    well-formed, and losing every road because one of them is malformed
    would be a poor trade.
    """
    try:
        with open(path, "rb") as handle:
            text = handle.read().decode("cp1251", errors="replace")
    except OSError as exc:
        logger.warning("could not read %s: %s", path, exc)
        return {}

    sets: dict = {}
    for name, body in _SET_PATTERN.findall(text):
        road = RoadSet(name=name)

        scale_match = _SCALE_PATTERN.search(body)
        if scale_match:
            parts = scale_match.group(1).split()
            if len(parts) == 3:
                try:
                    road.scale = tuple(float(p) for p in parts)
                except ValueError:
                    pass

        for model, type_text in _ITEM_PATTERN.findall(body):
            if type_text:
                road.models[int(type_text)] = model
            else:
                road.straights.append(model)

        if road.straights or road.models:
            sets[name] = road

    logger.info("%s: %s road set(s)", os.path.basename(path), len(sets))
    return sets


def find_road_sets(game_root: str) -> dict:
    """Locate and read ``roads.xml`` under the game folder."""
    if not game_root:
        return {}
    # ``data/Roads.xml``, verbatim from the editor's own string
    # constant: "Can't read roadset file: data/Roads.xml". The map
    # folder holds a copy on some installs, but this is the one the
    # engine reads and the one that carries every set.
    for candidate in (
        os.path.join(game_root, "data", "Roads.xml"),
        os.path.join(game_root, "data", "roads.xml"),
        os.path.join(game_root, "data", "maps", "roads.xml"),
    ):
        if os.path.isfile(candidate):
            return read_road_sets(candidate)

    for folder, _dirs, files in os.walk(os.path.join(game_root, "data")):
        for name in files:
            if name.lower() == "roads.xml":
                return read_road_sets(os.path.join(folder, name))
    logger.info("no roads.xml under %s", game_root)
    return {}
