# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Game object prototypes — the missing link for the second scene layer.

``dynamicscene.xml`` places thousands of objects by ``Prototype`` name
rather than by model id, and that name resolves nowhere in the map's
own files. The definitions live in the game's global data
(``data/gamedata/gameobjects/*.xml``), which the map manifest never
references — which is why the chain looked broken from inside a map.

The complete chain, confirmed against real data::

    dynamicscene.xml   Prototype="Breakable_WoodFence1"
          |
    breakableobjects.xml  <Prototype Name="Breakable_WoodFence1"
                                     ModelFile="wood_fence1" />
          |
    AnimModels.xml        <model id="wood_fence1"
                                 file="...\\wood_fence1_test.gam" />
          |
    wood_fence1_test.gam

The attribute carrying the model is ``ModelFile``, not ``id`` or
``Name`` — and its value is a model id, not a path, so it still has to
go through the model catalogue afterwards.

No ``bpy`` import.
"""

from __future__ import annotations

import dataclasses
import os
import xml.etree.ElementTree as ET

from utils.errors import ErrorContext, ParsingError
from utils.logging import get_logger

logger = get_logger("core.prototypes")

_ENCODINGS = ("cp1251", "utf-8", "latin-1")

#: Where the prototype files live, relative to the game root. Not
#: referenced by any map manifest, so the location has to be known
#: rather than discovered.
PROTOTYPE_DIRECTORY = os.path.join("data", "gamedata", "gameobjects")

#: Attributes that may name the model, in order of preference.
#: ``ModelFile`` is the one used by every prototype examined; the
#: others are accepted because the same catalogue defines many object
#: classes and a different one may use a different spelling.
MODEL_ATTRIBUTES = ("ModelFile", "ModelName", "Model")


@dataclasses.dataclass(frozen=True)
class SubObject:
    """One entry of a prefab's ``<ObjInfos>``: a prototype placed at an
    offset from the prefab's origin, turned about the up axis."""

    prototype: str
    rel_pos: tuple[float, float, float]
    rel_angle: float


@dataclasses.dataclass
class Prototype:
    """One ``<Prototype>`` definition."""

    name: str
    model_id: str | None = None
    prototype_class: str | None = None
    #: Alternate models for damaged/destroyed states. Not used for
    #: import — a map shows objects intact — but kept so the data is
    #: not silently discarded.
    broken_model: str | None = None
    destroyed_model: str | None = None
    source_file: str | None = None
    raw_attrs: dict[str, str] = dataclasses.field(default_factory=dict)
    #: A composite prototype — a StaticAutoGun, a vehicle — has no model
    #: of its own. It is assembled from PARTS, each a prototype in its
    #: own right::
    #:
    #:     <Prototype Class="StaticAutoGun" Name="staticAutoGun04">
    #:         <MainPartDescription id="DOT" ...>
    #:             <PartDescription id="CANNON" lpName="LP_CANNON01" />
    #:         </MainPartDescription>
    #:         <Parts>
    #:             <Part id="DOT"    Prototype="heavy_dot4" />
    #:             <Part id="CANNON" Prototype="vulcan01" />
    #:         </Parts>
    #:     </Prototype>
    #:
    #: MEASURED on r1m1: its 14 turrets are dynamic objects with
    #: prototypes staticAutoGun02/04/07/08, all of this shape, and none
    #: carries ModelFile — so they imported as Empties.
    parts: dict[str, str] = dataclasses.field(default_factory=dict)
    #: Which part is the body: the ``MainPartDescription`` id.
    main_part: str | None = None
    #: part id -> the locator node on the MAIN part it mounts on
    #: (``lpName``). The locator's position is in the body's ``.gam``.
    attachments: dict[str, str] = dataclasses.field(default_factory=dict)
    #: A PREFAB — a Barricade — is a third shape again: no model, no
    #: parts, but a list of whole prototypes placed around its origin::
    #:
    #:     <Prototype Class="Barricade" Name="barricade4_wGw">
    #:         <ObjInfos>
    #:             <ObjInfo Prototype="Breakable_SackWall1" RelPos="-6 0 0" RelAngle="0" />
    #:             <ObjInfo Prototype="Breakable_SackWall1" RelPos="6 0 0"  RelAngle="0" />
    #:             <ObjInfo Prototype="staticAutoGun08"     RelPos="0 0 0"  RelAngle="0" />
    #:         </ObjInfos>
    #:     </Prototype>
    #:
    #: 142 such entries across the game's prototype files; 33 of the
    #: RelAngle values are 0 and 91 are absent, so the sign convention
    #: of the few non-zero ones is UNVERIFIED (ГИПОТЕЗА: degrees about
    #: the up axis, applied as-is).
    sub_objects: list = dataclasses.field(default_factory=list)

    @property
    def has_model(self) -> bool:
        return bool(self.model_id)

    @property
    def is_prefab(self) -> bool:
        return bool(self.sub_objects)

    @property
    def is_composite(self) -> bool:
        return bool(self.parts)

    @property
    def main_part_prototype(self) -> str | None:
        """The prototype name of the body, or None."""
        if not self.parts:
            return None
        if self.main_part and self.main_part in self.parts:
            return self.parts[self.main_part]
        # No MainPartDescription: the first part listed is the body.
        return next(iter(self.parts.values()))


class PrototypeCatalog:
    """Every prototype the game defines, by name."""

    def __init__(self) -> None:
        self._entries: dict[str, Prototype] = {}

    def add(self, prototype: Prototype) -> None:
        self._entries[prototype.name] = prototype

    def get(self, name: str) -> Prototype | None:
        return self._entries.get(name)

    def entries(self) -> list[Prototype]:
        return list(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: str) -> bool:
        return name in self._entries


def _read_text(path: str) -> str:
    with open(path, "rb") as handle:
        raw = handle.read()
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1251", errors="replace")


def read_prototype_file(path: str) -> list[Prototype]:
    """Read one ``gameobjects`` XML file.

    Returns an empty list rather than raising when the file isn't
    well-formed: the directory holds a couple of dozen files covering
    unrelated categories, and losing all of them because one is
    malformed would be a poor trade.
    """
    try:
        text = _read_text(path)
    except OSError as exc:
        raise ParsingError(
            "could not read prototype file",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        logger.warning("%s is not well-formed XML: %s", os.path.basename(path), exc)
        return []

    source = os.path.basename(path)
    prototypes: list[Prototype] = []
    for element in root.iter("Prototype"):
        name = element.get("Name")
        if not name:
            continue
        model_id = next(
            (element.get(attr) for attr in MODEL_ATTRIBUTES if element.get(attr)), None,
        )
        parts, main_part, attachments = _read_parts(element)
        sub_objects = _read_sub_objects(element)
        prototypes.append(Prototype(
            name=name,
            model_id=model_id,
            prototype_class=element.get("Class"),
            broken_model=element.get("BrokenModel"),
            destroyed_model=element.get("DestroyedModel"),
            source_file=source,
            parts=parts,
            main_part=main_part,
            attachments=attachments,
            sub_objects=sub_objects,
            raw_attrs={
                k: v for k, v in element.attrib.items()
                if k not in ("Name", "Class", "BrokenModel", "DestroyedModel")
                and k not in MODEL_ATTRIBUTES
            },
        ))
    return prototypes


def _read_parts(element):
    """The parts of a composite prototype, and where each one mounts.

    Returns ``(parts, main_part, attachments)``. Empty for the ordinary
    case of a prototype that is one model.
    """
    parts: dict[str, str] = {}
    for part in element.iter("Part"):
        part_id = part.get("id")
        proto = part.get("Prototype")
        if part_id and proto:
            parts[part_id] = proto

    main_part = None
    attachments: dict[str, str] = {}
    main = element.find("MainPartDescription")
    if main is not None:
        main_part = main.get("id")
        for desc in main.iter("PartDescription"):
            part_id = desc.get("id")
            locator = desc.get("lpName")
            if part_id and locator:
                attachments[part_id] = locator
    return parts, main_part, attachments


def _read_sub_objects(element) -> list:
    """The ``<ObjInfo>`` entries of a prefab. Empty for anything else."""
    found = []
    for info in element.iter("ObjInfo"):
        proto = info.get("Prototype")
        if not proto:
            continue
        pos = (0.0, 0.0, 0.0)
        raw = (info.get("RelPos") or "").split()
        if len(raw) == 3:
            try:
                pos = tuple(float(v) for v in raw)
            except ValueError:
                pass
        try:
            angle = float(info.get("RelAngle") or 0.0)
        except ValueError:
            angle = 0.0
        found.append(SubObject(prototype=proto, rel_pos=pos, rel_angle=angle))
    return found


def read_prototype_catalog(game_root: str) -> PrototypeCatalog:
    """Read every prototype definition under the game folder.

    Scans the whole ``gameobjects`` directory rather than a fixed list
    of filenames: the categories are split across ~22 files whose names
    describe content (vehicles, towns, bosses, wares), and a map may
    reference any of them.
    """
    catalog = PrototypeCatalog()
    directory = _resolve_directory(game_root)
    if directory is None:
        logger.warning("prototype directory not found under %s", game_root)
        return catalog

    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(".xml"):
            continue
        for prototype in read_prototype_file(os.path.join(directory, name)):
            catalog.add(prototype)

    with_models = sum(1 for p in catalog.entries() if p.has_model)
    composite = sum(1 for p in catalog.entries() if p.is_composite)
    prefabs = sum(1 for p in catalog.entries() if p.is_prefab)
    logger.info(
        "Prototype catalogue: %d definition(s), %d with a model, %d assembled "
        "from parts, %d prefabs", len(catalog), with_models, composite, prefabs,
    )
    return catalog


def _resolve_directory(game_root: str) -> str | None:
    """Locate the gameobjects directory, case-insensitively."""
    current = os.path.abspath(game_root)
    for part in PROTOTYPE_DIRECTORY.split(os.sep):
        try:
            entries = {name.lower(): name for name in os.listdir(current)}
        except OSError:
            return None
        match = entries.get(part.lower())
        if match is None:
            return None
        current = os.path.join(current, match)
    return current if os.path.isdir(current) else None
