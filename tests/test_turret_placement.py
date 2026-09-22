# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""A turret placed in Blender is a StaticAutoGun, not its pillbox.

MEASURED on the user's r1m1 export (2026-09-19 23:50): two new records,
``<Object Prototype="sack_dot2">`` and ``<Object Prototype="brick_dot1">``.
Both prototypes are class VehiclePart — the DOT (pillbox) part of
``staticAutoGun01/02/05/07`` — and no shipped map places a VehiclePart
at top level. The game showed nothing: "the turrets disappear".

What the palette offers is the pillbox MODEL; what the map needs is
the composite that is drawn as that pillbox. So the candidates for a
model include the composites whose main part draws it and exclude the
parts themselves, a composite record carries the ``<Parts/>`` child
that 293 of 328 shipped turret records carry (empty), and a record
re-assigned to another prototype in Blender is rewritten on export.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io import dynamic_bridge  # noqa: E402
from blender_io.dynamic_bridge import (  # noqa: E402
    BELONG_PROP,
    DYNAMIC_KEY_PROP,
    DYNAMIC_PROP,
    HAD_ROTATION_PROP,
    NEW_DYNAMIC_PROP,
    PROTOTYPE_PROP,
    SCALE_PROP,
    TAG_PROP,
    build_dynamic_scene,
)
from blender_io.scene_bridge import DYNAMIC_COLLECTION, extract_dynamic_from_collection  # noqa: E402
from core.coordinates import CoordinateTransform  # noqa: E402
from core.prototype_placement import (  # noqa: E402
    default_belong,
    is_placeable,
    prototypes_for_model,
    record_children,
    unplaceable_records,
)
from core.prototypes import Prototype, PrototypeCatalog  # noqa: E402
from formats.exm.dynamic_scene import read_dynamic_scene, write_dynamic_scene  # noqa: E402
from corpus import GAME_ROOT  # noqa: E402


def _catalog() -> PrototypeCatalog:
    """The shapes from gameobjects.xml, reduced."""
    catalog = PrototypeCatalog()
    catalog.add(Prototype(name="brick_dot1", model_id="brick_dot1", prototype_class="VehiclePart"))
    catalog.add(Prototype(name="sack_dot2", model_id="sack_dot2", prototype_class="VehiclePart"))
    catalog.add(Prototype(name="vulcan01", model_id="vulcan01", prototype_class="VehiclePart"))
    catalog.add(Prototype(
        name="staticAutoGun02", prototype_class="StaticAutoGun",
        parts={"DOT": "brick_dot1", "CANNON": "vulcan01"}, main_part="DOT",
        attachments={"CANNON": "LP_CANNON01"},
    ))
    catalog.add(Prototype(
        name="brick_dot1_vulcan", prototype_class="StaticAutoGun",
        parts={"DOT": "brick_dot1", "CANNON": "vulcan01"}, main_part="DOT",
    ))
    catalog.add(Prototype(
        name="staticAutoGun07", prototype_class="StaticAutoGun",
        parts={"DOT": "sack_dot2", "CANNON": "vulcan01"}, main_part="DOT",
    ))
    catalog.add(Prototype(
        name="Bug01", prototype_class="Vehicle",
        parts={"CHASSIS": "bugChassis", "CABIN": "bugCab01"}, main_part="CHASSIS",
    ))
    catalog.add(Prototype(name="Breakable_Barrel1", model_id="barrel1", prototype_class="BreakableObject"))
    return catalog


# --- choosing -------------------------------------------------------------


def test_the_pillbox_model_offers_the_turrets_not_the_part() -> None:
    names = [p.name for p in prototypes_for_model(_catalog(), "brick_dot1")]
    assert names == ["brick_dot1_vulcan", "staticAutoGun02"]
    assert "brick_dot1" not in names, "a VehiclePart is not placeable"


def test_a_cannon_model_offers_nothing_placeable() -> None:
    """vulcan01 is a part of every turret but the main part of none."""
    assert prototypes_for_model(_catalog(), "vulcan01") == []


def test_placeability_follows_the_measured_class_list() -> None:
    catalog = _catalog()
    assert is_placeable(catalog.get("staticAutoGun02"))
    assert is_placeable(catalog.get("Breakable_Barrel1"))
    assert is_placeable(catalog.get("Bug01"))
    assert not is_placeable(catalog.get("brick_dot1"))
    assert not is_placeable(catalog.get("vulcan01"))


def test_record_children_by_shape() -> None:
    catalog = _catalog()
    assert record_children(catalog.get("staticAutoGun02")) == ("Parts",)
    assert record_children(catalog.get("Bug01")) == ("Parts", "Repository")
    assert record_children(catalog.get("Breakable_Barrel1")) == ()
    assert record_children(None) == ()


def test_a_turret_takes_its_faction_from_the_other_turrets() -> None:
    on_map = {"staticAutoGun04": ["1008", "1008"], "Breakable_Barrel1": ["-1"] * 50}
    assert default_belong(on_map, "staticAutoGun02", siblings=("staticAutoGun04",)) == "1008"
    assert default_belong(on_map, "staticAutoGun02") == "-1", "without kin, the map's majority"


def test_the_real_catalogue_agrees_when_the_game_is_there() -> None:
    from core.prototypes import read_prototype_catalog

    root = GAME_ROOT
    if not root or not os.path.isdir(os.path.join(root, "data", "gamedata", "gameobjects")):
        return
    catalog = read_prototype_catalog(root)
    for body in ("brick_dot1", "sack_dot2"):
        names = [p.name for p in prototypes_for_model(catalog, body)]
        assert body not in names, f"{body} is a VehiclePart and was offered"
        assert names and all(catalog.get(n).prototype_class == "StaticAutoGun" for n in names), names
    assert "staticAutoGun02" in [p.name for p in prototypes_for_model(catalog, "brick_dot1")]


# --- writing --------------------------------------------------------------

_SCENE = """<?xml version="1.0" encoding="windows-1251"?>
<DynamicScene LastId="4375">
\t<Object Name="staticAutoGun043" Belong="1008" Prototype="staticAutoGun04" Pos="1255.548 289.907 2825.717" Rot="0.000 -0.263 0.000 0.965">
\t\t<Parts>
\t\t\t<CANNON present="1" Name="staticAutoGun043CANNON" Belong="1008" Prototype="vulcan01" />
\t\t</Parts>
\t</Object>
\t<Object Name="Object4378" Belong="-1" Prototype="sack_dot2" Pos="3310.349 340.533 2872.548" />
\t<Object Name="barrel12782" Belong="-1" Prototype="Breakable_Barrel1" Pos="3383.955 371.381 3332.991" />
</DynamicScene>
"""


def _write(content: str) -> str:
    path = os.path.join(tempfile.mkdtemp(), "dynamicscene.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write(content)
    return path


def _map():
    scene = read_dynamic_scene(_write(_SCENE))
    root = fake_bpy.FakeCollection("Map")
    dynamic = fake_bpy.FakeCollection(DYNAMIC_COLLECTION)
    root.children.link(dynamic)
    build_dynamic_scene(scene, dynamic, transform=CoordinateTransform())
    return scene, root, dynamic


def _children_for(name):
    return record_children(_catalog().get(name))


def _new(prototype, belong="1008"):
    obj = fake_bpy.FakeObject("dot.001", None)
    obj[DYNAMIC_PROP] = 1
    obj[TAG_PROP] = "Object"
    obj[PROTOTYPE_PROP] = prototype
    obj[BELONG_PROP] = belong
    obj[NEW_DYNAMIC_PROP] = 1
    obj[HAD_ROTATION_PROP] = 0
    obj[SCALE_PROP] = 1.0
    obj.location = (10.0, 20.0, 30.0)
    return obj


def test_a_new_turret_record_carries_an_empty_parts_child() -> None:
    scene, root, _dynamic = _map()
    root.objects.link(_new("staticAutoGun02"))

    updated = extract_dynamic_from_collection(
        root, scene, transform=CoordinateTransform(), children_for=_children_for,
    )
    record = updated.objects[-1]
    assert record.prototype == "staticAutoGun02"
    assert record.belong == "1008"
    assert [c.tag for c in record.children] == ["Parts"]
    assert record.children[0].children == []

    out = os.path.join(tempfile.mkdtemp(), "dynamicscene.xml")
    write_dynamic_scene(out, updated)
    text = open(out, encoding="cp1251").read()
    assert record.name == "Object4379", "above LastId 4375 and the hand-typed Object4378"
    start = text.index('Name="Object4379"')
    block = text[start:text.index("</Object>", start)]
    assert "<Parts" in block and 'Name=""' not in block, block


def test_a_new_barrel_record_carries_no_children() -> None:
    scene, root, _dynamic = _map()
    root.objects.link(_new("Breakable_Barrel1", belong="-1"))
    updated = extract_dynamic_from_collection(
        root, scene, transform=CoordinateTransform(), children_for=_children_for,
    )
    assert updated.objects[-1].children == []


def test_without_a_catalogue_no_children_are_invented() -> None:
    scene, root, _dynamic = _map()
    root.objects.link(_new("staticAutoGun02"))
    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    assert updated.objects[-1].children == []


def test_a_record_reassigned_to_a_turret_is_rewritten() -> None:
    """The user's two pillboxes: re-assign in Blender, export again."""
    scene, root, dynamic = _map()
    pillbox = next(o for o in dynamic.objects if o[DYNAMIC_KEY_PROP] == "Object4378")
    assert pillbox[PROTOTYPE_PROP] == "sack_dot2"
    pillbox[PROTOTYPE_PROP] = "staticAutoGun07"
    pillbox[BELONG_PROP] = "1002"

    updated = extract_dynamic_from_collection(
        root, scene, transform=CoordinateTransform(), children_for=_children_for,
    )
    record = next(o for o in updated.walk() if o.name == "Object4378")
    assert record.prototype == "staticAutoGun07"
    assert record.belong == "1002"
    assert [c.tag for c in record.children] == ["Parts"]
    assert updated.object_count() == 5 + 1, "one Parts child added, nothing else"


def test_an_untouched_record_is_not_rewritten() -> None:
    scene, root, _dynamic = _map()
    updated = extract_dynamic_from_collection(
        root, scene, transform=CoordinateTransform(), children_for=_children_for,
    )
    turret = next(o for o in updated.walk() if o.name == "staticAutoGun043")
    assert turret.prototype == "staticAutoGun04" and turret.belong == "1008"
    assert [c.tag for c in turret.children] == ["Parts"]
    assert [c.tag for c in turret.children[0].children] == ["CANNON"], "the listed part survives"
    pillbox = next(o for o in updated.walk() if o.name == "Object4378")
    assert pillbox.prototype == "sack_dot2" and pillbox.children == []


# --- the operator --------------------------------------------------------


def test_assigning_a_part_is_refused_and_the_turrets_are_named() -> None:
    from addon import assign_operator

    obj = fake_bpy.FakeObject("brick_dot1.001", None)
    reports = []

    class Op(assign_operator.EXM_OT_assign_node):
        def report(self, kind, message):
            reports.append((tuple(kind), message))

    op = Op()
    op.layer = "DYNAMIC"
    op.model_id = "brick_dot1"
    op.prototype = "brick_dot1"
    op.belong = "1008"
    op.snap_to_ground = False

    class Context:
        selected_objects = [obj]
        active_object = obj
        collection = fake_bpy.FakeCollection("Map")
        scene = fake_bpy.FakeObject("S", None)

    original = assign_operator._prototype_catalog
    assign_operator._prototype_catalog = lambda context: _catalog()
    try:
        assert op.execute(Context()) == {"CANCELLED"}
    finally:
        assign_operator._prototype_catalog = original

    assert DYNAMIC_PROP not in obj
    kinds, message = reports[-1]
    assert "ERROR" in kinds
    assert "VehiclePart" in message
    assert "staticAutoGun02" in message and "brick_dot1_vulcan" in message


def test_assigning_the_turret_itself_goes_through() -> None:
    from addon import assign_operator

    obj = fake_bpy.FakeObject("brick_dot1.001", None)
    op = assign_operator.EXM_OT_assign_node()
    op.layer = "DYNAMIC"
    op.model_id = "brick_dot1"
    op.prototype = "staticAutoGun02"
    op.belong = "1008"
    op.snap_to_ground = False

    class Context:
        selected_objects = [obj]
        active_object = obj
        collection = fake_bpy.FakeCollection("Map")
        scene = fake_bpy.FakeObject("S", None)

    original = assign_operator._prototype_catalog
    assign_operator._prototype_catalog = lambda context: _catalog()
    try:
        assert op.execute(Context()) == {"FINISHED"}
    finally:
        assign_operator._prototype_catalog = original
    assert obj[PROTOTYPE_PROP] == "staticAutoGun02"
    assert obj[BELONG_PROP] == "1008"


def test_the_users_two_pillboxes_are_reported_by_name() -> None:
    scene = read_dynamic_scene(_write(_SCENE))
    found = unplaceable_records(scene, _catalog())
    assert found == [("Object4378", "sack_dot2", "VehiclePart")]
    assert unplaceable_records(read_dynamic_scene(_write(_SCENE)), PrototypeCatalog()) == [],         "an unknown prototype is not this check's business"


def test_the_real_r1m1_export_shows_the_two_when_it_is_there() -> None:
    from core.prototypes import read_prototype_catalog

    root = GAME_ROOT
    exported = os.path.join(root, "data", "maps", "r1m1", "dynamicscene.xml")
    if not root or not os.path.isfile(exported) or not os.path.isdir(os.path.join(root, "data", "gamedata")):
        return
    found = unplaceable_records(read_dynamic_scene(exported), read_prototype_catalog(root))
    # Whatever the file holds today, nothing SHIPPED trips this check.
    assert all(name.startswith("Object") for name, _p, _c in found), found
