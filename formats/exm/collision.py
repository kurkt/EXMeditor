# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""``static_obstacles.xml`` codec."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from core.collision import ObstacleBox, ObstacleSet
from utils.errors import ErrorContext, ParsingError
from utils.math import Vector3

_ENCODING = "cp1251"
_MODELED_ATTRS = frozenset({"min", "max", "origin", "rotation"})


def _parse_floats(raw: str, count: int, *, attr: str, index: int, source_file: str) -> tuple[float, ...]:
    parts = raw.split()
    if len(parts) != count:
        raise ParsingError(
            f"obstacle attribute {attr!r} expects {count} numbers, got {len(parts)}",
            context=ErrorContext(source_file=source_file, field=attr, extra={"box_index": index}),
        )
    try:
        return tuple(float(p) for p in parts)
    except ValueError as exc:
        raise ParsingError(
            f"obstacle attribute {attr!r} contains a non-numeric value",
            context=ErrorContext(source_file=source_file, field=attr, extra={"box_index": index}),
        ) from exc


def _require(element: ET.Element, attr: str, *, index: int, source_file: str) -> str:
    value = element.get(attr)
    if value is None:
        raise ParsingError(
            f"<Box> is missing the required {attr!r} attribute",
            context=ErrorContext(source_file=source_file, field=attr, extra={"box_index": index}),
        )
    return value


def read_obstacles(path: str) -> ObstacleSet:
    """Read ``static_obstacles.xml`` into an ``ObstacleSet``.

    Raises
    ------
    ParsingError
        On unreadable/malformed XML, a missing required attribute, or a
        malformed numeric attribute.
    """
    try:
        with open(path, encoding=_ENCODING) as f:
            text = f.read()
    except OSError as exc:
        raise ParsingError(
            "could not read static_obstacles.xml",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ParsingError(
            "static_obstacles.xml is not well-formed XML",
            context=ErrorContext(source_file=path, extra={"parse_error": str(exc)}),
        ) from exc

    obstacles = ObstacleSet()
    for index, element in enumerate(root.findall("Box")):
        min_x, min_y, min_z = _parse_floats(
            _require(element, "min", index=index, source_file=path),
            3, attr="min", index=index, source_file=path,
        )
        max_x, max_y, max_z = _parse_floats(
            _require(element, "max", index=index, source_file=path),
            3, attr="max", index=index, source_file=path,
        )
        org_x, org_y, org_z = _parse_floats(
            _require(element, "origin", index=index, source_file=path),
            3, attr="origin", index=index, source_file=path,
        )
        rotation = _parse_floats(
            _require(element, "rotation", index=index, source_file=path),
            4, attr="rotation", index=index, source_file=path,
        )

        obstacles.boxes.append(ObstacleBox(
            min_corner=Vector3(min_x, min_y, min_z),
            max_corner=Vector3(max_x, max_y, max_z),
            origin=Vector3(org_x, org_y, org_z),
            raw_rotation=rotation,
            raw_attrs={k: v for k, v in element.attrib.items() if k not in _MODELED_ATTRS},
        ))

    return obstacles


def write_obstacles(path: str, obstacles: ObstacleSet) -> None:
    """Write an ``ObstacleSet`` back out as ``static_obstacles.xml``.

    Number formatting matches the source files: 3 decimals for
    positions and extents, 4 for quaternion components.
    """
    root = ET.Element("Boxes")
    for box in obstacles.boxes:
        element = ET.SubElement(root, "Box")
        element.set("min", " ".join(f"{v:.3f}" for v in box.min_corner.as_tuple()))
        element.set("max", " ".join(f"{v:.3f}" for v in box.max_corner.as_tuple()))
        element.set("origin", " ".join(f"{v:.3f}" for v in box.origin.as_tuple()))
        element.set("rotation", " ".join(f"{v:.4f}" for v in box.raw_rotation))
        for key, value in box.raw_attrs.items():
            element.set(key, value)

    ET.indent(root, space="\t")
    try:
        ET.ElementTree(root).write(path, encoding=_ENCODING, xml_declaration=True)
    except OSError as exc:
        raise ParsingError(
            "could not write static_obstacles.xml",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc
