# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Which prototype a placed model should become.

A model from the palette is only geometry. What makes a barrel a
barrel — mass, the damage model, the explosion — is the PROTOTYPE that
names that model, and a prototype is placed through
``dynamicscene.xml``, not ``world.xml``::

    <Prototype Class="BreakableObject" Name="Breakable_Barrel1"
               ModelFile="barrel1" BrokenModel="barrel_1_brocken"
               DestroyedModel="barrel_1_destroy" Mass="20.5"
               BlastWave="smallBlastWave" BreakEffect="ET_PS_VEH_EXP1_SMALL_11" …/>

    <Object Name="barrel12782" Belong="-1" Prototype="Breakable_Barrel1"
            Pos="3383.955 371.381 3332.991" Rot="0.000 -0.507 0.000 0.862" />

MEASURED: 379 distinct models are named by 547 prototypes; 295 by
exactly one, 84 by several (``barrel1``: Breakable_Barrel1,
Breakable_Barrel_Explosive2/3 and the Town ``CrazyBase``). So a model
maps to a *list* of candidates, ranked by how often each class is
placed as a top-level dynamic object on the shipped maps.

No ``bpy`` import.
"""

from __future__ import annotations

import collections

from core.prototypes import Prototype, PrototypeCatalog

#: Prototype classes seen as top-level placed ``<Object>`` records on
#: the eleven shipped maps, most frequent first. MEASURED:
#: BreakableObject 35 262, Location 658, PhysicUnit 234,
#: ParticleSplinter 135, Barricade 133, Town 87, Chest 76, LightObj 39,
#: ObjPrefab 37, StaticAutoGun 37, then single bosses. Location is
#: pushed down: it is a gameplay zone, not a thing with a model.
PLACEABLE_CLASS_ORDER = (
    "BreakableObject",
    "PhysicUnit",
    "Chest",
    "ParticleSplinter",
    "Town",
    "StaticAutoGun",
    "Barricade",
    "ObjPrefab",
    "LightObj",
    "Location",
)

#: Classes a top-level ``<Object>`` may instantiate — every class seen
#: placed on the shipped maps (the ten above plus vehicles and bosses:
#: Vehicle 44, Boss04Station 3, Boss03 2, Boss02 1, Boss04 1). What is
#: NOT here matters more: VehiclePart (a pillbox, a cannon, a cabin),
#: Wheel, Gadget, BulletLauncher… are parts of something, and a record
#: naming one shows nothing. MEASURED: the user's turrets went out as
#: ``Prototype="sack_dot2"`` / ``"brick_dot1"`` — VehiclePart, the DOT
#: of staticAutoGun0X — and vanished.
PLACEABLE_CLASSES = frozenset(PLACEABLE_CLASS_ORDER) | frozenset((
    "Vehicle", "Boss02", "Boss03", "Boss04", "Boss04Station",
))

#: Child elements a new record of a composite carries. MEASURED over
#: 328 shipped StaticAutoGun records: every one has a ``<Parts>``
#: child, 293 of them empty (the game fills the parts from the
#: prototype); 41 of 44 placed vehicles have ``<Parts/>`` and
#: ``<Repository/>``.
_CHILDREN_BY_CLASS = {
    "Vehicle": ("Parts", "Repository"),
}

#: What ``Belong`` a new record gets when the map gives no lead.
#: MEASURED: every breakable on r1m1..r4m2 carries -1 except r0m0's
#: 2148 (1001); the level designer's own maps mix the two.
DEFAULT_BELONG = "-1"


def prototypes_for_model(catalog: PrototypeCatalog, model_id: str) -> list[Prototype]:
    """Every PLACEABLE prototype that shows ``model_id``, best first.

    Two ways a prototype shows a model: it names it (``ModelFile``), or
    it is a composite whose main part names it — a ``staticAutoGun02``
    is drawn as its ``brick_dot1`` pillbox. The pillbox's own prototype
    is a VehiclePart and is left out: placing it puts nothing on the
    map.
    """
    wanted = (model_id or "").strip().lower()
    if not wanted:
        return []
    direct = [
        prototype for prototype in catalog.entries()
        if prototype.model_id and prototype.model_id.lower() == wanted
    ]
    drawn_by = {p.name for p in direct}
    found = [p for p in direct if is_placeable(p)]
    for prototype in catalog.entries():
        if not prototype.is_composite or not is_placeable(prototype):
            continue
        body = prototype.main_part_prototype
        if body in drawn_by:
            found.append(prototype)
    return sorted(found, key=_rank)


def is_placeable(prototype: Prototype) -> bool:
    """Can a top-level record instantiate this prototype?"""
    return (prototype.prototype_class or "") in PLACEABLE_CLASSES


def record_children(prototype: Prototype | None) -> tuple[str, ...]:
    """Child element tags a new record of ``prototype`` carries."""
    if prototype is None:
        return ()
    by_class = _CHILDREN_BY_CLASS.get(prototype.prototype_class or "")
    if by_class is not None:
        return by_class
    if prototype.is_composite:
        return ("Parts",)
    return ()


def _rank(prototype: Prototype) -> tuple[int, str]:
    cls = prototype.prototype_class or ""
    try:
        order = PLACEABLE_CLASS_ORDER.index(cls)
    except ValueError:
        order = len(PLACEABLE_CLASS_ORDER)
    return (order, prototype.name.lower())


def unplaceable_records(scene, catalog: PrototypeCatalog) -> list[tuple[str, str, str]]:
    """Top-level ``<Object>`` records naming a prototype no record can
    instantiate: ``(name, prototype, class)`` each. MEASURED on the
    user's r1m1: ``Object4378`` sack_dot2 and ``Object4379`` brick_dot1,
    both VehiclePart — invisible in the game."""
    found = []
    for obj in scene.objects:
        if obj.tag != "Object" or not obj.prototype or obj.position is None:
            continue
        prototype = catalog.get(obj.prototype)
        if prototype is None or is_placeable(prototype):
            continue
        found.append((obj.name, obj.prototype, prototype.prototype_class or "?"))
    return found


def default_belong(
    belongs_by_prototype: dict[str, list[str]],
    prototype: str,
    siblings: tuple[str, ...] | list[str] = (),
) -> str:
    """The ``Belong`` most of this map's records of ``prototype`` carry;
    failing that, of its ``siblings`` (other prototypes of the same
    class — a turret's faction is what the other turrets have, not
    what the barrels have); failing that, the most common on the map;
    failing that, -1.

    ``belongs_by_prototype`` maps a prototype name to the Belong values
    of the records already placed, as read from the imported scene.
    MEASURED: r1m1's turrets carry 1008 and 1002, its barrels -1.
    """
    own = belongs_by_prototype.get(prototype) or []
    if own:
        return collections.Counter(own).most_common(1)[0][0]
    kin = [v for name in siblings for v in belongs_by_prototype.get(name, [])]
    if kin:
        return collections.Counter(kin).most_common(1)[0][0]
    everything = [value for values in belongs_by_prototype.values() for value in values]
    if everything:
        return collections.Counter(everything).most_common(1)[0][0]
    return DEFAULT_BELONG
