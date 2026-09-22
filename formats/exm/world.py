# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""``world.xml`` codec — object placement scene graph.

Implements ``WorldXML_Format_Spec.md``. Key points that shape this
module:

- The file is a **tree**, not a flat list: nesting expresses
  parent-child, and there is no ``parent=`` attribute (spec §5).
- ``orgRel`` distinguishes world-space (top-level) from parent-local
  (nested) positions — it is read and preserved, never recomputed
  from the tree shape.
- Attribute *absence* is meaningful: a writer must not emit an
  attribute that wasn't in the source, or the round trip isn't
  lossless (spec §11). Hence the ``None``-means-absent convention on
  ``ObjectInstance``.
- ``id`` is round-tripped as an opaque string. Resolving it to an
  actual model is the model catalogue's job (``model_catalog.py``,
  ``model_trace.py``), not this reader's.

No ``bpy`` import.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from core.objects import KNOWN_NODE_CLASSES, ObjectInstance
from core.unknown_nodes import UnknownNodeReport
from utils.errors import ErrorContext, ParsingError
from utils.math import Vector3

_ENCODING = "cp1251"

# Attributes this module models explicitly. Anything else found on a
# node is preserved verbatim in ObjectInstance.raw_attrs so it can be
# written back unchanged — the format spec's attribute list is
# exhaustive for the map examined, but a different map could carry
# something unseen, and dropping it silently would be a data-loss bug
# rather than a graceful degradation.
_MODELED_ATTRS = frozenset({
    "name", "class", "org", "orgRel", "rot", "scale",
    "id", "skin", "CastShadow", "ndmAction",
})


def _parse_floats(raw: str, count: int, *, attr: str, node_name: str, source_file: str) -> tuple[float, ...]:
    parts = raw.split()
    if len(parts) != count:
        raise ParsingError(
            f"attribute {attr!r} expects {count} numbers, got {len(parts)}",
            context=ErrorContext(source_file=source_file, field=attr, xml_node=node_name, extra={"value": raw}),
        )
    try:
        return tuple(float(p) for p in parts)
    except ValueError as exc:
        raise ParsingError(
            f"attribute {attr!r} contains a non-numeric value",
            context=ErrorContext(source_file=source_file, field=attr, xml_node=node_name, extra={"value": raw}),
        ) from exc


def _parse_node(
    element: ET.Element,
    source_file: str,
    unknown: "UnknownNodeReport | None" = None,
    parent_class: str | None = None,
    depth: int = 0,
) -> ObjectInstance | None:
    """Convert one ``<Node>`` element (and its subtree) to an ObjectInstance."""
    name = element.get("name")
    if name is None:
        raise ParsingError(
            "<Node> is missing the required 'name' attribute",
            context=ErrorContext(source_file=source_file, xml_node="Node"),
        )

    node_class = element.get("class")
    if node_class is None:
        raise ParsingError(
            "<Node> is missing the required 'class' attribute",
            context=ErrorContext(source_file=source_file, xml_node=name),
        )
    if node_class not in KNOWN_NODE_CLASSES:
        # Skipped, not fatal. Guessing an unknown class's semantics
        # risks importing something wrong, but aborting the file
        # discards every object in it — one unrecognised class turned a
        # valid map into "0 models loaded". Skipping keeps the rest and
        # records what was skipped (see core/unknown_nodes.py), which
        # is what makes adding real support possible later.
        if unknown is not None:
            unknown.record(
                node_class,
                name,
                dict(element.attrib),
                parent_class=parent_class,
                child_classes=[
                    child.get("class", "?") for child in element.findall("Node")
                ],
                depth=depth,
            )
        return None

    org = None
    raw_org = element.get("org")
    if raw_org is not None:
        x, y, z = _parse_floats(raw_org, 3, attr="org", node_name=name, source_file=source_file)
        org = Vector3(x, y, z)

    org_rel = None
    raw_org_rel = element.get("orgRel")
    if raw_org_rel is not None:
        org_rel = raw_org_rel.strip() == "1"

    raw_rotation = None
    raw_rot = element.get("rot")
    if raw_rot is not None:
        raw_rotation = _parse_floats(raw_rot, 4, attr="rot", node_name=name, source_file=source_file)

    scale = None
    raw_scale = element.get("scale")
    if raw_scale is not None:
        sx, sy, sz = _parse_floats(raw_scale, 3, attr="scale", node_name=name, source_file=source_file)
        scale = Vector3(sx, sy, sz)

    skin = None
    raw_skin = element.get("skin")
    if raw_skin is not None:
        try:
            skin = int(raw_skin)
        except ValueError as exc:
            raise ParsingError(
                "attribute 'skin' is not an integer",
                context=ErrorContext(source_file=source_file, field="skin", xml_node=name, extra={"value": raw_skin}),
            ) from exc

    cast_shadow = None
    raw_cast = element.get("CastShadow")
    if raw_cast is not None:
        cast_shadow = raw_cast.strip().lower() not in ("no", "0", "false")

    instance = ObjectInstance(
        name=name,
        node_class=node_class,
        org=org,
        org_rel=org_rel,
        raw_rotation=raw_rotation,
        scale=scale,
        asset_id=element.get("id"),
        skin=skin,
        cast_shadow=cast_shadow,
        ndm_action=element.get("ndmAction"),
        raw_attrs={k: v for k, v in element.attrib.items() if k not in _MODELED_ATTRS},
    )

    for child in element.findall("Node"):
        parsed = _parse_node(child, source_file, unknown, node_class, depth + 1)
        if parsed is not None:
            instance.children.append(parsed)

    return instance


def read_world(
    path: str, *, unknown: "UnknownNodeReport | None" = None,
) -> tuple[list[ObjectInstance], dict[str, str]]:
    """Read ``world.xml`` into a list of top-level objects.

    Returns
    -------
    ``(top_level_objects, root_attributes)`` — the root ``<World>``
    element's own attributes (``name``, ``class``, ``LastId``) are
    returned alongside the tree so a writer can reproduce them exactly.
    ``LastId`` in particular is the editor's object-ID counter and must
    survive a round trip untouched unless new objects are added (spec
    §11).

    ``unknown``, when given, collects nodes whose ``class`` this SDK
    does not model. Those nodes are skipped rather than aborting the
    read — see ``core/unknown_nodes.py`` for why.

    Raises
    ------
    ParsingError
        On unreadable/malformed XML, a missing required attribute, or a
        malformed numeric attribute. An unrecognised node class is NOT
        an error.
    """
    try:
        with open(path, encoding=_ENCODING) as f:
            text = f.read()
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

    objects = []
    for child in root.findall("Node"):
        parsed = _parse_node(child, path, unknown, root.get("class"), 1)
        if parsed is not None:
            objects.append(parsed)
    return objects, dict(root.attrib)


def _format_float(value: float) -> str:
    """Format a float the way the source file does: 3 decimal places.

    Matches the observed formatting of ``org``/``scale`` values in real
    files (``"3107.170 0.078 406.802"``). Rotation is formatted
    separately with 4 decimals, matching its own observed style.
    """
    return f"{value:.3f}"


def _node_to_element(instance: ObjectInstance) -> ET.Element:
    element = ET.Element("Node")
    # Attribute order follows the source files' own convention so a
    # diff against the original stays readable.
    element.set("name", instance.name)
    element.set("class", instance.node_class)

    if instance.org is not None:
        element.set("org", " ".join(_format_float(v) for v in instance.org.as_tuple()))
    if instance.org_rel is not None:
        element.set("orgRel", "1" if instance.org_rel else "0")
    if instance.raw_rotation is not None:
        element.set("rot", " ".join(f"{v:.4f}" for v in instance.raw_rotation))
    if instance.scale is not None:
        element.set("scale", " ".join(_format_float(v) for v in instance.scale.as_tuple()))
    if instance.asset_id is not None:
        element.set("id", instance.asset_id)
    if instance.skin is not None:
        element.set("skin", str(instance.skin))
    if instance.cast_shadow is not None:
        element.set("CastShadow", "yes" if instance.cast_shadow else "no")
    if instance.ndm_action is not None:
        element.set("ndmAction", instance.ndm_action)

    for key, value in instance.raw_attrs.items():
        element.set(key, value)

    for child in instance.children:
        element.append(_node_to_element(child))

    return element


def write_world(path: str, objects: list[ObjectInstance], root_attributes: dict[str, str]) -> None:
    """Write a node tree back out as ``world.xml``.

    ``root_attributes`` should be what ``read_world`` returned, so the
    ``<World>`` element (including ``LastId``) round-trips unchanged.

    Note on fidelity: this reproduces the *semantic* content exactly —
    every node, attribute, value and nesting relationship — but not
    necessarily the original file byte-for-byte, since the source's
    exact whitespace/attribute-ordering conventions aren't fully
    characterized. Whether byte-identical output is even the right
    goal is still open (see spec §12: the editor may itself reformat
    on save), which is why this doesn't attempt to guess at matching
    the original's exact layout.
    """
    root = ET.Element("World")
    for key, value in root_attributes.items():
        root.set(key, value)
    for obj in objects:
        root.append(_node_to_element(obj))

    ET.indent(root, space="\t")
    tree = ET.ElementTree(root)
    try:
        tree.write(path, encoding=_ENCODING, xml_declaration=True)
    except OSError as exc:
        raise ParsingError(
            "could not write world.xml",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc
