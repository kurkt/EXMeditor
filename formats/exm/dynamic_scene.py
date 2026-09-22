# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""``dynamicscene.xml`` codec.

Reads the second placement layer. Structure confirmed against real
data: a flat-ish tree of ``<Object>`` elements with ``Prototype``,
``Pos`` and ``Rot``, interspersed with other element tags (``Post``,
``Point``, ``Parts``, ``Polygon``) that belong to specific object
kinds.

Those other tags are preserved as-is rather than modelled: they are
gameplay structures — patrol posts, area polygons, vehicle part
lists — with no visual representation, and inventing a model for them
would be guessing. Keeping them intact is what lets an edited file be
written back without losing the parts this SDK doesn't understand.
"""

from __future__ import annotations

import dataclasses
import xml.etree.ElementTree as ET

from core.dynamic_objects import DynamicObject, DynamicScene
from utils.errors import ErrorContext, ParsingError
from utils.math import Vector3

_ENCODING = "cp1251"

#: Attributes turned into fields. Everything else is kept in
#: ``raw_attrs`` and written back untouched.
_MODELLED_ATTRS = frozenset({"Name", "Prototype", "Pos", "Rot", "Belong", "ModelName"})


def _parse_floats(raw: str, count: int, *, attr: str, name: str, source_file: str):
    parts = raw.split()
    if len(parts) != count:
        raise ParsingError(
            f"attribute {attr!r} expects {count} numbers, got {len(parts)}",
            context=ErrorContext(source_file=source_file, field=attr, xml_node=name),
        )
    try:
        return tuple(float(p) for p in parts)
    except ValueError as exc:
        raise ParsingError(
            f"attribute {attr!r} contains a non-numeric value",
            context=ErrorContext(source_file=source_file, field=attr, xml_node=name),
        ) from exc


def _parse_object(element: ET.Element, source_file: str) -> DynamicObject:
    name = element.get("Name") or ""

    position = None
    raw_pos = element.get("Pos")
    if raw_pos is not None:
        # Not every Pos is a 3D point: <Point> elements use two
        # components (a horizontal coordinate pair). Only a 3-component
        # value is a placement; anything else is kept verbatim in
        # raw_attrs so it round-trips without being misread as a
        # position that happens to be missing its height.
        parts = raw_pos.split()
        if len(parts) == 3:
            x, y, z = _parse_floats(
                raw_pos, 3, attr="Pos", name=name, source_file=source_file,
            )
            position = Vector3(x, y, z)

    raw_rotation = None
    raw_rot = element.get("Rot")
    if raw_rot is not None:
        raw_rotation = _parse_floats(raw_rot, 4, attr="Rot", name=name, source_file=source_file)

    extra_attrs = {k: v for k, v in element.attrib.items() if k not in _MODELLED_ATTRS}
    if raw_pos is not None and position is None:
        extra_attrs["Pos"] = raw_pos

    obj = DynamicObject(
        name=name,
        prototype=element.get("Prototype"),
        position=position,
        raw_rotation=raw_rotation,
        belong=element.get("Belong"),
        model_name=element.get("ModelName"),
        tag=element.tag,
        raw_attrs=extra_attrs,
    )

    for child in element:
        obj.children.append(_parse_object(child, source_file))

    return obj


def read_dynamic_scene(path: str) -> DynamicScene:
    """Read ``dynamicscene.xml``.

    Raises
    ------
    ParsingError
        On unreadable/malformed XML or a malformed numeric attribute.
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

    scene = DynamicScene(root_tag=root.tag, root_attributes=dict(root.attrib))
    for child in root:
        scene.objects.append(_parse_object(child, path))
    return scene


def _to_element(obj: DynamicObject) -> ET.Element:
    element = ET.Element(obj.tag)
    if obj.name:
        element.set("Name", obj.name)
    if obj.belong is not None:
        element.set("Belong", obj.belong)
    if obj.prototype is not None:
        element.set("Prototype", obj.prototype)
    if obj.model_name is not None:
        element.set("ModelName", obj.model_name)
    if obj.position is not None:
        element.set("Pos", " ".join(f"{v:.3f}" for v in obj.position.as_tuple()))
    if obj.raw_rotation is not None:
        element.set("Rot", " ".join(f"{v:.3f}" for v in obj.raw_rotation))
    for key, value in obj.raw_attrs.items():
        element.set(key, value)
    for child in obj.children:
        element.append(_to_element(child))
    return element


def write_dynamic_scene(path: str, scene: DynamicScene) -> None:
    """Write a ``DynamicScene`` back out."""
    root = ET.Element(scene.root_tag)
    for key, value in scene.root_attributes.items():
        root.set(key, value)
    for obj in scene.objects:
        root.append(_to_element(obj))

    ET.indent(root, space="\t")
    try:
        ET.ElementTree(root).write(path, encoding=_ENCODING, xml_declaration=True)
    except OSError as exc:
        raise ParsingError(
            "could not write dynamicscene.xml",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc


def resolve_model_id(
    obj: DynamicObject, prototypes, model_catalog,
) -> str | None:
    """Work out which model an object should draw, or ``None``.

    Three sources, in order of specificity:

    1. an explicit ``ModelName`` on the object itself (NPCs use this);
    2. the prototype's ``ModelFile``;
    3. the prototype name itself, for the handful that are already
       model ids (lamp posts, road signs).

    Returns a model id for the model catalogue, not a path — the
    prototype's ``ModelFile`` is itself an id and still has to be
    looked up.
    """
    if obj.model_name and model_catalog.get(obj.model_name):
        return obj.model_name

    if obj.prototype:
        return model_for_prototype(obj.prototype, prototypes, model_catalog)

    return None


def model_for_prototype(name: str, prototypes, model_catalog) -> str | None:
    """The model a PROTOTYPE draws, or None.

    Its ``ModelFile``; the name itself when that is already a model id;
    or, for a composite prototype, the BODY part's model. A StaticAutoGun
    has no ModelFile — it is a pillbox part plus a gun part, and the
    pillbox is what stands on the map. MEASURED on r1m1: all 14 turrets
    are staticAutoGun02/04/07/08 and imported as Empties for want of
    this step. A prefab (Barricade) has no model of its own at all: its
    contents come from :func:`resolve_sub_objects`.
    """
    prototype = prototypes.get(name) if prototypes else None
    if prototype and prototype.model_id and model_catalog.get(prototype.model_id):
        return prototype.model_id
    if model_catalog.get(name):
        return name
    if prototype and prototype.is_composite:
        return _part_model(prototype.main_part_prototype, prototypes, model_catalog)
    return None


def _part_model(part_prototype: str | None, prototypes, model_catalog) -> str | None:
    """The model id a PART prototype resolves to, or None."""
    if not part_prototype or not prototypes:
        return None
    part = prototypes.get(part_prototype)
    if part and part.model_id and model_catalog.get(part.model_id):
        return part.model_id
    if model_catalog.get(part_prototype):
        return part_prototype
    return None


@dataclasses.dataclass(frozen=True)
class PartPlacement:
    """A part to mount on the body: which model, on which locator."""

    part_id: str
    model_id: str
    locator: str


def resolve_parts(obj: DynamicObject, prototypes, model_catalog) -> list[PartPlacement]:
    """The parts of a composite prototype OTHER than its body."""
    return parts_for_prototype(obj.prototype, prototypes, model_catalog)


def parts_for_prototype(name: str | None, prototypes, model_catalog) -> list[PartPlacement]:
    """The parts of a composite prototype OTHER than its body.

    Each names a model and the locator on the body it mounts on::

        <MainPartDescription id="DOT">
            <PartDescription id="CANNON" lpName="LP_CANNON01" />
        </MainPartDescription>
        <Parts>
            <Part id="DOT"    Prototype="heavy_dot4" />
            <Part id="CANNON" Prototype="vulcan01" />
        </Parts>

    gives one placement: ``vulcan01`` on ``LP_CANNON01``. A part with no
    locator is left out — there is nowhere to put it.
    """
    if not name or not prototypes:
        return []
    prototype = prototypes.get(name)
    if not prototype or not prototype.is_composite:
        return []
    body_part = prototype.main_part or next(iter(prototype.parts))
    placements = []
    for part_id, part_prototype in prototype.parts.items():
        if part_id == body_part:
            continue
        locator = prototype.attachments.get(part_id)
        if not locator:
            continue
        model_id = _part_model(part_prototype, prototypes, model_catalog)
        if model_id:
            placements.append(PartPlacement(part_id, model_id, locator))
    return placements


def sub_objects_for_prototype(name: str | None, prototypes) -> list:
    """The contents of a prefab prototype (its ``ObjInfos``), or []."""
    if not name or not prototypes:
        return []
    prototype = prototypes.get(name)
    if not prototype or not prototype.is_prefab:
        return []
    return list(prototype.sub_objects)
