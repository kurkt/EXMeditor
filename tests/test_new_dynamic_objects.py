# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""A barrel placed in Blender becomes a Breakable_Barrel1 record.

The report: "the barrel lost its properties — a physical object that
reacts to damage (explodes)". MEASURED on r1m1: every barrel is a
``<Object Prototype="Breakable_Barrel1" …>`` in dynamicscene.xml; the
prototype (breakableobjects.xml) carries the mass, the broken and
destroyed models, the blast wave and the effect. The SDK's Assign made
the barrel a world.xml SgAnimatedModelNode — a model at a position,
which is all that file can express. Nothing was wrong with the export;
the object went into the wrong file.

Covered here: writing a NEW record of the dynamic layer, its name,
rotation, NodeScale and Belong; NodeScale on import and export for
existing records; the real rotation of Euler-mode objects; and the
operator's two layers.
"""

from __future__ import annotations

import math
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
    extract_dynamic_scene,
)
from blender_io.object_rotation import euler_to_quaternion, object_rotation  # noqa: E402
from blender_io.scene_bridge import DYNAMIC_COLLECTION, extract_dynamic_from_collection  # noqa: E402
from core.coordinates import CoordinateTransform  # noqa: E402
from formats.exm.dynamic_scene import read_dynamic_scene, write_dynamic_scene  # noqa: E402
from corpus import GAME_ROOT  # noqa: E402

_SCENE = """<?xml version="1.0" encoding="windows-1251"?>
<DynamicScene LastId="4375">
\t<Object Name="barrel12782" Belong="-1" Prototype="Breakable_Barrel1" Pos="3383.955 371.381 3332.991" Rot="0.000 -0.507 0.000 0.862" />
\t<Object Name="Object4375" Belong="-1" Prototype="Breakable_WoodFence1" Pos="40.000 20.000 50.000" />
\t<Object Name="Object9000" Belong="-1" Prototype="Breakable_WoodFence1" Pos="41.000 20.000 50.000" />
\t<Object Name="poddon10" Belong="-1" Prototype="poddon" Pos="629.426 228.481 2008.827" Rot="0.000 0.952 0.000 -0.305" NodeScale="1.190" />
\t<Object Name="team1" Prototype="settlementTeam" />
</DynamicScene>
"""


def _write(content: str) -> str:
    path = os.path.join(tempfile.mkdtemp(), "dynamicscene.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write(content)
    return path


def _map():
    """A built map: root collection with the Dynamic sub-collection."""
    scene = read_dynamic_scene(_write(_SCENE))
    root = fake_bpy.FakeCollection("Map")
    dynamic = fake_bpy.FakeCollection(DYNAMIC_COLLECTION)
    root.children.link(dynamic)
    build_dynamic_scene(scene, dynamic, transform=CoordinateTransform())
    return scene, root, dynamic


def _new_barrel(name="barrel1.001", prototype="Breakable_Barrel1", belong="-1"):
    obj = fake_bpy.FakeObject(name, None)
    obj[DYNAMIC_PROP] = 1
    obj[TAG_PROP] = "Object"
    obj[PROTOTYPE_PROP] = prototype
    obj[BELONG_PROP] = belong
    obj[NEW_DYNAMIC_PROP] = 1
    obj[HAD_ROTATION_PROP] = 0
    obj[SCALE_PROP] = 1.0
    obj.location = (100.0, 200.0, 30.0)
    return obj


# --- writing a new record ---------------------------------------------


def test_a_new_object_becomes_a_record_of_its_prototype() -> None:
    scene, root, _dynamic = _map()
    barrel = _new_barrel()
    root.objects.link(barrel)              # the ACTIVE collection, not Dynamic

    before = scene.object_count()
    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())

    assert updated.object_count() == before + 1
    record = updated.objects[-1]
    assert record.tag == "Object"
    assert record.prototype == "Breakable_Barrel1"
    assert record.belong == "-1"
    # Blender (100, 200, 30) -> game (x, y=height, z): (100, 30, 200)
    assert record.position.as_tuple() == (100.0, 30.0, 200.0)
    assert record.raw_rotation is None, "an unturned object writes no Rot"
    assert "NodeScale" not in record.raw_attrs, "an unscaled object writes no NodeScale"


def test_the_name_is_above_last_id_and_every_object_number_in_use() -> None:
    """LastId says 4375 and a hand-typed Object9000 exists: the new
    record must clear both, and LastId follows."""
    scene, root, _dynamic = _map()
    root.objects.link(_new_barrel())

    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())

    assert updated.objects[-1].name == "Object9001"
    assert updated.root_attributes["LastId"] == "9001"
    names = [o.name for o in updated.walk() if o.name]
    assert len(names) == len(set(names)), "names must stay unique"


def test_the_record_identity_is_stamped_so_a_second_export_updates_it() -> None:
    scene, root, _dynamic = _map()
    barrel = _new_barrel()
    root.objects.link(barrel)

    extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    assert barrel[DYNAMIC_KEY_PROP] == "Object9001"
    assert barrel[NEW_DYNAMIC_PROP] == 1, "still new until the file is written"
    # The export operator confirms once dynamicscene.xml is on disk.
    assert dynamic_bridge.confirm_written(root) == 1
    assert NEW_DYNAMIC_PROP not in barrel
    count = scene.object_count()

    barrel.location = (110.0, 200.0, 30.0)
    extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    assert scene.object_count() == count, "the second export must not add a record"
    record = next(o for o in scene.walk() if o.name == "Object9001")
    assert record.position.x == 110.0


def test_an_export_that_failed_after_stamping_does_not_hijack_another_record() -> None:
    """Export 1 stamped Object9001 and then failed before writing; the
    file was reloaded and meanwhile holds an Object9001 of its own.
    The still-new object must get a different name, not that record."""
    scene, root, _dynamic = _map()
    barrel = _new_barrel()
    barrel[DYNAMIC_KEY_PROP] = "Object9000"       # the fence's name, in the file
    root.objects.link(barrel)

    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    fence = next(o for o in updated.walk() if o.name == "Object9000")
    assert fence.position.x == 41.0, "the fence kept its own position"
    assert updated.objects[-1].name == "Object9001"
    assert barrel[DYNAMIC_KEY_PROP] == "Object9001"


def test_a_created_object_is_written_again_when_the_source_lacks_it() -> None:
    """Export to another folder, twice. The baseline is always the
    SOURCE map, which never gains the record; the created object must
    still be written on the second pass, under the same name."""
    scene, root, _dynamic = _map()
    barrel = _new_barrel()
    root.objects.link(barrel)
    extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    dynamic_bridge.confirm_written(root)
    assert barrel[DYNAMIC_KEY_PROP] == "Object9001"

    # Second export: the baseline is re-read from the untouched source.
    fresh = read_dynamic_scene(_write(_SCENE))
    updated = extract_dynamic_from_collection(root, fresh, transform=CoordinateTransform())
    assert [o.name for o in updated.objects[-1:]] == ["Object9001"]
    assert updated.object_count() == 5 + 1


def test_an_imported_object_whose_record_vanished_is_not_resurrected() -> None:
    """The other side of the same rule: a record removed from the file
    behind Blender's back stays removed — only CREATED objects come
    back."""
    scene, root, dynamic = _map()
    fence = next(o for o in dynamic.objects if o.name == "Object9000")
    line = next(l for l in _SCENE.splitlines(keepends=True) if 'Name="Object9000"' in l)
    without = read_dynamic_scene(_write(_SCENE.replace(line, "")))
    assert without.object_count() == 4
    updated = extract_dynamic_from_collection(root, without, transform=CoordinateTransform())
    assert updated.object_count() == 4
    assert fence[DYNAMIC_KEY_PROP] == "Object9000"


def test_a_turned_new_object_writes_its_rotation() -> None:
    """The object is in XYZ Euler mode, as every dropped asset is; the
    rotation must come from rotation_euler, not rotation_quaternion."""
    scene, root, _dynamic = _map()
    barrel = _new_barrel()
    barrel.rotation_mode = "XYZ"
    barrel.rotation_euler = (0.0, 0.0, math.radians(90))
    root.objects.link(barrel)

    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    record = updated.objects[-1]
    assert record.raw_rotation is not None
    # Blender (w, x, y, z) = (0.7071, 0, 0, 0.7071) -> game (-x, -z, -y, w)
    assert [round(v, 3) for v in record.raw_rotation] == [0.0, -0.707, 0.0, 0.707]
    assert barrel[HAD_ROTATION_PROP] == 1


def test_a_scaled_new_object_writes_node_scale() -> None:
    scene, root, _dynamic = _map()
    barrel = _new_barrel()
    barrel.scale = (1.5, 1.5, 1.5)
    root.objects.link(barrel)

    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    assert updated.objects[-1].raw_attrs["NodeScale"] == "1.500"
    assert abs(barrel[SCALE_PROP] - 1.5) < 1e-6


def test_the_new_record_reaches_the_file_in_the_shipped_shape() -> None:
    scene, root, _dynamic = _map()
    barrel = _new_barrel()
    barrel.rotation_mode = "XYZ"
    barrel.rotation_euler = (0.0, 0.0, math.radians(45))
    barrel.scale = (0.8, 0.8, 0.8)
    root.objects.link(barrel)
    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())

    out = os.path.join(tempfile.mkdtemp(), "dynamicscene.xml")
    write_dynamic_scene(out, updated)
    back = read_dynamic_scene(out)
    record = next(o for o in back.walk() if o.name == "Object9001")
    assert record.prototype == "Breakable_Barrel1"
    assert record.belong == "-1"
    assert record.raw_rotation is not None
    assert record.raw_attrs["NodeScale"] == "0.800"
    assert back.root_attributes["LastId"] == "9001"


def test_an_object_without_a_prototype_is_not_written() -> None:
    scene, root, _dynamic = _map()
    nameless = _new_barrel()
    del nameless[PROTOTYPE_PROP]
    root.objects.link(nameless)
    before = scene.object_count()
    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    assert updated.object_count() == before


def test_a_stamp_the_file_never_got_is_reused_and_a_taken_one_is_not() -> None:
    scene, root, _dynamic = _map()
    orphan = _new_barrel("a")
    orphan[DYNAMIC_KEY_PROP] = "Object7777"      # export failed before writing
    clash = _new_barrel("b")
    clash[DYNAMIC_KEY_PROP] = "Object9000"        # somebody else's record
    root.objects.link(orphan)
    root.objects.link(clash)

    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    new_names = [o.name for o in updated.objects[-2:]]
    assert "Object7777" in new_names, "a free stamp is kept"
    assert clash[DYNAMIC_KEY_PROP] == "Object9001", "a taken stamp is replaced"
    names = [o.name for o in updated.walk() if o.name]
    assert len(names) == len(set(names))


def test_new_objects_do_not_disturb_the_deletion_guard() -> None:
    """Adding records must not count as losing any."""
    scene, root, _dynamic = _map()
    for i in range(3):
        root.objects.link(_new_barrel(f"b{i}"))
    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    assert updated.object_count() == 5 + 3


# --- NodeScale on existing records -------------------------------------


def test_node_scale_is_applied_on_import() -> None:
    scene, _root, dynamic = _map()
    pallet = next(o for o in dynamic.objects if o.name == "poddon10")
    assert tuple(round(v, 3) for v in pallet.scale) == (1.19, 1.19, 1.19)
    assert abs(pallet[SCALE_PROP] - 1.19) < 1e-6
    fence = next(o for o in dynamic.objects if o.name == "Object4375")
    assert tuple(fence.scale) == (1.0, 1.0, 1.0)
    assert fence[SCALE_PROP] == 1.0


def test_an_untouched_node_scale_keeps_its_own_text() -> None:
    """"1.190" must come out as "1.190", not as a re-formatted float."""
    scene, root, _dynamic = _map()
    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    pallet = next(o for o in updated.walk() if o.name == "poddon10")
    assert pallet.raw_attrs["NodeScale"] == "1.190"


def test_a_rescaled_record_writes_the_new_node_scale() -> None:
    scene, root, dynamic = _map()
    pallet = next(o for o in dynamic.objects if o.name == "poddon10")
    pallet.scale = (2.0, 2.0, 2.0)
    fence = next(o for o in dynamic.objects if o.name == "Object4375")
    fence.scale = (0.5, 0.5, 0.5)

    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    assert next(o for o in updated.walk() if o.name == "poddon10").raw_attrs["NodeScale"] == "2.000"
    assert next(o for o in updated.walk() if o.name == "Object4375").raw_attrs["NodeScale"] == "0.500"


# --- rotation of existing records ---------------------------------------


def test_a_record_without_rot_turned_in_euler_mode_gets_one() -> None:
    scene, root, dynamic = _map()
    fence = next(o for o in dynamic.objects if o.name == "Object4375")
    assert fence.rotation_mode == "XYZ", "a record with no Rot stays in Blender's default mode"
    fence.rotation_euler = (0.0, 0.0, math.radians(90))

    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    record = next(o for o in updated.walk() if o.name == "Object4375")
    assert record.raw_rotation is not None
    assert [round(v, 3) for v in record.raw_rotation] == [0.0, -0.707, 0.0, 0.707]


def test_an_untouched_record_without_rot_still_has_none() -> None:
    scene, root, _dynamic = _map()
    updated = extract_dynamic_from_collection(root, scene, transform=CoordinateTransform())
    assert next(o for o in updated.walk() if o.name == "Object4375").raw_rotation is None


def test_euler_to_quaternion_matches_mathutils() -> None:
    """Reference from mathutils.Euler((30°, -45°, 120°), 'XYZ')."""
    q = euler_to_quaternion((math.radians(30), math.radians(-45), math.radians(120)))
    assert [round(v, 6) for v in q] == [0.360423, 0.43968, 0.02226, 0.822363]

    obj = fake_bpy.FakeObject("x", None)
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = (0.5, 0.5, 0.5, 0.5)
    assert object_rotation(obj) == (0.5, 0.5, 0.5, 0.5)


# --- the operator --------------------------------------------------------


def _context(objects, root=None):
    class Scene:
        collection = root

    class Context:
        selected_objects = list(objects)
        active_object = objects[0] if objects else None
        collection = root if root is not None else fake_bpy.FakeCollection("Map")
        scene = Scene()

    return Context()


def test_assigning_to_the_dynamic_layer_marks_a_new_record() -> None:
    from addon import assign_operator
    from blender_io.world_bridge import CLASS_PROP, NEW_OBJECT_PROP

    obj = fake_bpy.FakeObject("barrel1.001", None)
    obj["exm_asset_id"] = "barrel1"          # as dropped from the palette
    obj[CLASS_PROP] = "SgAnimatedModelNode"  # previously assigned as a node
    obj[NEW_OBJECT_PROP] = 1
    _scene, root, dynamic = _map()

    op = assign_operator.EXM_OT_assign_node()
    op.layer = "DYNAMIC"
    op.model_id = "barrel1"
    op.prototype = "Breakable_Barrel1"
    op.belong = "-1"
    op.snap_to_ground = False

    assert op.execute(_context([obj], root)) == {"FINISHED"}
    assert obj[DYNAMIC_PROP] == 1
    assert obj[PROTOTYPE_PROP] == "Breakable_Barrel1"
    assert obj[BELONG_PROP] == "-1"
    assert obj[NEW_DYNAMIC_PROP] == 1
    assert CLASS_PROP not in obj, "one file per object: it left the world layer"
    assert NEW_OBJECT_PROP not in obj
    assert any(o is obj for o in dynamic.objects), "moved into the Dynamic collection"


def test_reassigning_an_imported_record_keeps_its_identity() -> None:
    from addon import assign_operator

    _scene, root, dynamic = _map()
    barrel = next(o for o in dynamic.objects if o.name == "barrel12782")

    op = assign_operator.EXM_OT_assign_node()
    op.layer = "DYNAMIC"
    op.model_id = "barrel1"
    op.prototype = "Breakable_Barrel_Explosive2"
    op.belong = "-1"
    op.snap_to_ground = False

    assert op.execute(_context([barrel], root)) == {"FINISHED"}
    assert barrel[PROTOTYPE_PROP] == "Breakable_Barrel_Explosive2"
    assert barrel[DYNAMIC_KEY_PROP] == "barrel12782"
    assert NEW_DYNAMIC_PROP not in barrel


def test_a_dynamic_assignment_needs_a_prototype() -> None:
    from addon import assign_operator

    obj = fake_bpy.FakeObject("x", None)
    op = assign_operator.EXM_OT_assign_node()
    op.layer = "DYNAMIC"
    op.prototype = "   "
    assert op.execute(_context([obj])) == {"CANCELLED"}
    assert DYNAMIC_PROP not in obj


def test_assigning_to_the_world_layer_leaves_the_dynamic_one() -> None:
    from addon import assign_operator
    from blender_io.world_bridge import CLASS_PROP

    obj = _new_barrel()
    op = assign_operator.EXM_OT_assign_node()
    op.layer = "WORLD"
    op.node_class = "SgAnimatedModelNode"
    op.model_id = "barrel1"
    op.top_level = True
    op.snap_to_ground = False
    op.validate_model = False

    assert op.execute(_context([obj])) == {"FINISHED"}
    assert obj[CLASS_PROP] == "SgAnimatedModelNode"
    assert DYNAMIC_PROP not in obj and PROTOTYPE_PROP not in obj


def test_clear_strips_the_dynamic_layer_too() -> None:
    from addon import assign_operator

    obj = _new_barrel()
    op = assign_operator.EXM_OT_clear_node()
    assert op.execute(_context([obj])) == {"FINISHED"}
    assert DYNAMIC_PROP not in obj and PROTOTYPE_PROP not in obj


# --- choosing the prototype -----------------------------------------------


def test_prototypes_for_a_model_come_breakable_first() -> None:
    from core.prototype_placement import default_belong, prototypes_for_model
    from core.prototypes import Prototype, PrototypeCatalog

    catalog = PrototypeCatalog()
    catalog.add(Prototype(name="CrazyBase", model_id="barrel1", prototype_class="Town"))
    catalog.add(Prototype(name="Breakable_Barrel_Explosive2", model_id="barrel1", prototype_class="BreakableObject"))
    catalog.add(Prototype(name="Breakable_Barrel1", model_id="barrel1", prototype_class="BreakableObject"))
    catalog.add(Prototype(name="house3", model_id="house3", prototype_class="Town"))

    names = [p.name for p in prototypes_for_model(catalog, "Barrel1")]
    assert names == ["Breakable_Barrel1", "Breakable_Barrel_Explosive2", "CrazyBase"]
    assert prototypes_for_model(catalog, "nothing") == []
    assert prototypes_for_model(catalog, "") == []

    assert default_belong({"Breakable_Barrel1": ["-1", "-1", "1001"]}, "Breakable_Barrel1") == "-1"
    assert default_belong({"Breakable_WoodFence1": ["1001", "1001"]}, "Breakable_Barrel1") == "1001"
    assert default_belong({}, "Breakable_Barrel1") == "-1"


def test_the_real_barrel_prototype_is_found_when_the_game_is_there() -> None:
    from core.prototype_placement import prototypes_for_model
    from core.prototypes import read_prototype_catalog

    root = GAME_ROOT
    if not root or not os.path.isdir(os.path.join(root, "data", "gamedata", "gameobjects")):
        return
    names = [p.name for p in prototypes_for_model(read_prototype_catalog(root), "barrel1")]
    assert names[0] == "Breakable_Barrel1", names
    assert "CrazyBase" in names and names.index("CrazyBase") > names.index("Breakable_Barrel_Explosive3")
