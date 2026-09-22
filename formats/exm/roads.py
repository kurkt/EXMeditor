# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""``LevelRoads.xml`` codec.

Reading turns a flat, link-referenced node list into ordered chains;
writing flattens chains back into a node list and regenerates the
``FwdZLink``/``BackZLink`` attributes from the chain order.

Regenerating the links on write (rather than preserving whatever the
source said) is deliberate: chain order is the editable thing, so
after a user reorders, splits or extends a road in Blender, the stored
links would be stale. Derived-from-order is the only representation
that can't disagree with itself.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from core.roads import RoadChain, RoadNetwork, RoadNode
from utils.errors import ErrorContext, ParsingError
from utils.math import Vector3

_ENCODING = "cp1251"

_MODELED_ATTRS = frozenset({
    "class", "name", "org", "roadset", "skinNumber", "ModelNum",
    "AsCliff", "FwdZLink", "BackZLink",
})


def _parse_org(raw: str, *, node_name: str, source_file: str) -> Vector3:
    parts = raw.split()
    if len(parts) != 3:
        raise ParsingError(
            f"road node 'org' expects 3 numbers, got {len(parts)}",
            context=ErrorContext(source_file=source_file, field="org", xml_node=node_name),
        )
    try:
        x, y, z = (float(p) for p in parts)
    except ValueError as exc:
        raise ParsingError(
            "road node 'org' contains a non-numeric value",
            context=ErrorContext(source_file=source_file, field="org", xml_node=node_name),
        ) from exc
    return Vector3(x, y, z)


def _parse_optional_int(element: ET.Element, key: str, *, node_name: str, source_file: str) -> int | None:
    raw = element.get(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ParsingError(
            f"road node attribute {key!r} is not an integer",
            context=ErrorContext(source_file=source_file, field=key, xml_node=node_name, extra={"value": raw}),
        ) from exc


def read_roads(path: str) -> RoadNetwork:
    """Read ``LevelRoads.xml`` into a ``RoadNetwork`` of ordered chains.

    Chains are reconstructed by starting from every node with no
    ``BackZLink`` and following ``FwdZLink``. Any node not reached that
    way (which would mean a closed loop — not observed on real data,
    but structurally possible) is still emitted as its own chain, so no
    node is ever silently dropped.

    Raises
    ------
    ParsingError
        On unreadable/malformed XML, a missing required attribute, a
        malformed ``org``, or a link naming a node that doesn't exist.
    """
    try:
        with open(path, encoding=_ENCODING) as f:
            text = f.read()
    except OSError as exc:
        raise ParsingError(
            "could not read LevelRoads.xml",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ParsingError(
            "LevelRoads.xml is not well-formed XML",
            context=ErrorContext(source_file=path, extra={"parse_error": str(exc)}),
        ) from exc

    nodes: dict[str, RoadNode] = {}
    forward: dict[str, str] = {}
    has_back: set[str] = set()
    order: list[str] = []

    for element in root.findall("RoadNode"):
        name = element.get("name")
        if name is None:
            raise ParsingError(
                "<RoadNode> is missing the required 'name' attribute",
                context=ErrorContext(source_file=path, xml_node="RoadNode"),
            )
        raw_org = element.get("org")
        if raw_org is None:
            raise ParsingError(
                "<RoadNode> is missing the required 'org' attribute",
                context=ErrorContext(source_file=path, xml_node=name),
            )

        nodes[name] = RoadNode(
            name=name,
            org=_parse_org(raw_org, node_name=name, source_file=path),
            roadset=element.get("roadset"),
            skin_number=_parse_optional_int(element, "skinNumber", node_name=name, source_file=path),
            model_num=_parse_optional_int(element, "ModelNum", node_name=name, source_file=path),
            as_cliff=element.get("AsCliff"),
            node_class=element.get("class", "RoadNode"),
            raw_attrs={k: v for k, v in element.attrib.items() if k not in _MODELED_ATTRS},
        )
        order.append(name)

        fwd = element.get("FwdZLink")
        if fwd is not None:
            forward[name] = fwd
        if element.get("BackZLink") is not None:
            has_back.add(name)

    # Validate links before walking, so a bad reference is reported as
    # such rather than surfacing later as a mysteriously short chain.
    for source_name, target in forward.items():
        if target not in nodes:
            raise ParsingError(
                f"road node {source_name!r} links forward to unknown node {target!r}",
                context=ErrorContext(source_file=path, xml_node=source_name, field="FwdZLink"),
            )

    network = RoadNetwork()
    visited: set[str] = set()

    for name in order:
        if name in has_back or name in visited:
            continue
        chain = RoadChain()
        current: str | None = name
        while current is not None and current not in visited:
            visited.add(current)
            chain.nodes.append(nodes[current])
            current = forward.get(current)
        network.chains.append(chain)

    # Anything left is part of a cycle (every node in it has a
    # BackZLink, so no start was found). Emit each as its own chain
    # rather than dropping it.
    for name in order:
        if name in visited:
            continue
        chain = RoadChain()
        current = name
        while current is not None and current not in visited:
            visited.add(current)
            chain.nodes.append(nodes[current])
            current = forward.get(current)
        network.chains.append(chain)

    return network


def _node_to_element(
    node: RoadNode,
    previous: RoadNode | None,
    following: RoadNode | None,
) -> ET.Element:
    element = ET.Element("RoadNode")
    element.set("class", node.node_class)
    element.set("name", node.name)
    if node.roadset is not None:
        element.set("roadset", node.roadset)
    if node.skin_number is not None:
        element.set("skinNumber", str(node.skin_number))
    if node.as_cliff is not None:
        element.set("AsCliff", node.as_cliff)
    # Links are regenerated from chain order — see the module docstring.
    if following is not None:
        element.set("FwdZLink", following.name)
    if previous is not None:
        element.set("BackZLink", previous.name)
    element.set("org", " ".join(f"{v:.3f}" for v in node.org.as_tuple()))
    if node.model_num is not None:
        element.set("ModelNum", str(node.model_num))
    for key, value in node.raw_attrs.items():
        element.set(key, value)
    return element


def write_roads(path: str, network: RoadNetwork) -> None:
    """Write a ``RoadNetwork`` back out as ``LevelRoads.xml``.

    ``FwdZLink``/``BackZLink`` are derived from each chain's node
    order, so edits to that order are reflected correctly.
    """
    root = ET.Element("Roads")
    for chain in network.chains:
        for index, node in enumerate(chain.nodes):
            previous = chain.nodes[index - 1] if index > 0 else None
            following = chain.nodes[index + 1] if index + 1 < len(chain.nodes) else None
            root.append(_node_to_element(node, previous, following))

    ET.indent(root, space="\t")
    try:
        ET.ElementTree(root).write(path, encoding=_ENCODING, xml_declaration=True)
    except OSError as exc:
        raise ParsingError(
            "could not write LevelRoads.xml",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc
