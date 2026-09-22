# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for creating new map objects in Blender.

Until this existed the SDK could only export what it had imported:
anything modelled in Blender was skipped, because export had no way to
know its class or model. The tests below cover the two halves of
fixing that — marking an object as a node, and giving it a name the
map can actually use.

Naming is the subtle half. Nodes are referenced by name from other map
files and from scripts, so an imported node must keep the name it came
in with, while a new one must get an unused number from the map's own
sequence.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.world_bridge import (  # noqa: E402
    ASSET_ID_PROP,
    CLASS_PROP,
    NEW_OBJECT_PROP,
    ORIGINAL_NAME_PROP,
    ORG_REL_PROP,
    build_object_tree,
    extract_object_tree,
)
from core.coordinates import CoordinateTransform  # noqa: E402
from core.node_naming import NodeNameAllocator  # noqa: E402
from core.objects import ObjectInstance  # noqa: E402
from utils.math import Vector3  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402


def _node(name, asset_id="rock", node_class="SgAnimatedModelNode"):
    return ObjectInstance(
        name=name, node_class=node_class, asset_id=asset_id,
        org=Vector3(1, 2, 3), org_rel=True,
    )


def _blender_object(name, *, node_class="SgAnimatedModelNode", asset_id="house3"):
    mesh = fake_bpy.FakeMesh(name + "_mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = fake_bpy.FakeObject(name, mesh)
    obj[CLASS_PROP] = node_class
    obj[ASSET_ID_PROP] = asset_id
    obj[ORG_REL_PROP] = 1
    obj[NEW_OBJECT_PROP] = 1
    return obj


# --- name allocation ---


def test_allocates_names_above_the_recorded_last_id() -> None:
    allocator = NodeNameAllocator.from_scene([_node("Object5")], {"LastId": "100"})
    assert allocator.allocate() == "Object101"
    assert allocator.last_id == 101


def test_allocates_above_the_highest_name_in_use() -> None:
    """LastId and the actual highest number can disagree — a
    hand-edited file, or objects that were renumbered. Trusting either
    alone risks handing out a name already taken."""
    allocator = NodeNameAllocator.from_scene(
        [_node("Object900"), _node("Object5")], {"LastId": "100"},
    )
    assert allocator.allocate() == "Object901"


def test_skips_names_already_in_use() -> None:
    allocator = NodeNameAllocator.from_scene(
        [_node("Object101"), _node("Object102")], {"LastId": "100"},
    )
    assert allocator.allocate() == "Object103"


def test_handles_a_missing_or_broken_last_id() -> None:
    assert NodeNameAllocator.from_scene([_node("Object7")], {}).allocate() == "Object8"
    assert NodeNameAllocator.from_scene(
        [_node("Object7")], {"LastId": "not a number"},
    ).allocate() == "Object8"


def test_ignores_hand_typed_node_names() -> None:
    """Real maps contain labels like 'Vill_1760' alongside the numbered
    sequence. They are left alone rather than parsed as numbers."""
    allocator = NodeNameAllocator.from_scene(
        [_node("Vill_1760"), _node("noise"), _node("Object3")], {"LastId": "0"},
    )
    assert allocator.allocate() == "Object4"


def test_records_every_issued_name() -> None:
    allocator = NodeNameAllocator.from_scene([], {"LastId": "10"})
    allocator.allocate()
    allocator.allocate()
    assert allocator.issued == ["Object11", "Object12"]
    assert allocator.last_id == 12


def test_counts_nested_nodes_when_scanning() -> None:
    parent = _node("Object1", node_class="SgNode")
    parent.children.append(_node("Object500"))
    allocator = NodeNameAllocator.from_scene([parent], {"LastId": "5"})
    assert allocator.allocate() == "Object501"


# --- export of new objects ---


def test_a_newly_assigned_object_is_exported() -> None:
    """The gap this feature closes: objects created in Blender used to
    be skipped entirely."""
    collection = fake_bpy.FakeCollection("Objects")
    obj = _blender_object("MyBuilding.001")
    obj.location = (100.0, 200.0, 300.0)
    collection.objects.link(obj)

    allocator = NodeNameAllocator.from_scene([], {"LastId": "50"})
    exported = extract_object_tree(
        [obj], transform=CoordinateTransform(), allocator=allocator,
    )
    assert len(exported) == 1
    assert exported[0].node_class == "SgAnimatedModelNode"
    assert exported[0].asset_id == "house3"


def test_new_objects_get_a_map_name_not_a_blender_name() -> None:
    """'MyBuilding.001' is not a valid node name; the map uses its own
    numbered sequence."""
    obj = _blender_object("MyBuilding.001")
    allocator = NodeNameAllocator.from_scene([], {"LastId": "50"})
    exported = extract_object_tree(
        [obj], transform=CoordinateTransform(), allocator=allocator,
    )
    assert exported[0].name == "Object51"


def test_imported_objects_keep_their_original_name() -> None:
    """Other map files and scripts refer to nodes by name, so renaming
    one on export would break those references."""
    collection = fake_bpy.FakeCollection("Objects")
    built = build_object_tree(
        [_node("Object4762")], collection, transform=CoordinateTransform(),
    )
    # Blender may have renamed the object on link (duplicate names)
    built[0].name = "Object4762.001"

    allocator = NodeNameAllocator.from_scene([_node("Object4762")], {"LastId": "5000"})
    exported = extract_object_tree(
        built, transform=CoordinateTransform(), allocator=allocator,
    )
    assert exported[0].name == "Object4762"
    assert allocator.issued == [], "an imported node must not consume a new name"


def test_new_object_position_converts_to_game_space() -> None:
    obj = _blender_object("New")
    obj.location = (100.0, 200.0, 30.0)
    allocator = NodeNameAllocator.from_scene([], {"LastId": "0"})
    transform = CoordinateTransform(xy_scale=10.0, height_scale=10.0)

    exported = extract_object_tree([obj], transform=transform, allocator=allocator)
    org = exported[0].org
    assert abs(org.x - 10.0) < 1e-6    # Blender X / xy_scale
    assert abs(org.y - 3.0) < 1e-6     # Blender Z (height) / height_scale
    assert abs(org.z - 20.0) < 1e-6    # Blender Y / xy_scale


def test_several_new_objects_get_distinct_names() -> None:
    objects = [_blender_object(f"New{i}") for i in range(3)]
    allocator = NodeNameAllocator.from_scene([], {"LastId": "10"})
    exported = extract_object_tree(
        objects, transform=CoordinateTransform(), allocator=allocator,
    )
    names = [o.name for o in exported]
    assert names == ["Object11", "Object12", "Object13"]
    assert len(set(names)) == 3


def test_objects_without_a_class_are_still_skipped() -> None:
    """Unassigned Blender objects must not leak into the map — that is
    what the assign step is for."""
    plain = fake_bpy.FakeObject("JustACube", None)
    allocator = NodeNameAllocator.from_scene([], {"LastId": "0"})
    assert extract_object_tree(
        [plain], transform=CoordinateTransform(), allocator=allocator,
    ) == []


def test_export_without_an_allocator_still_works() -> None:
    """Callers that don't create objects shouldn't be forced to supply
    one."""
    collection = fake_bpy.FakeCollection("Objects")
    built = build_object_tree(
        [_node("Object1")], collection, transform=CoordinateTransform(),
    )
    exported = extract_object_tree(built, transform=CoordinateTransform())
    assert exported[0].name == "Object1"


def test_imported_objects_record_their_original_name() -> None:
    collection = fake_bpy.FakeCollection("Objects")
    built = build_object_tree(
        [_node("Object4762")], collection, transform=CoordinateTransform(),
    )
    assert built[0][ORIGINAL_NAME_PROP] == "Object4762"


# --- readable names ---


def test_object_names_carry_the_id_and_model() -> None:
    """Blender's own names say nothing about which node an object is,
    so working in the viewport meant cross-referencing the outliner
    against custom properties."""
    from core.naming import STYLE_ID_MODEL, object_name

    assert object_name(
        "Object4762", "SgAnimatedModelNode", "house1", STYLE_ID_MODEL,
    ) == "4762_house1"


def test_naming_styles() -> None:
    from core.naming import (
        STYLE_ID_CLASS_MODEL,
        STYLE_ID_ONLY,
        STYLE_ORIGINAL,
        object_name,
    )

    assert object_name(
        "Object4762", "SgAnimatedModelNode", "house1", STYLE_ID_ONLY,
    ) == "4762"
    assert object_name(
        "Object4762", "SgAnimatedModelNode", "house1", STYLE_ID_CLASS_MODEL,
    ) == "4762_Model_house1"
    assert object_name(
        "Object4762", "SgAnimatedModelNode", "house1", STYLE_ORIGINAL,
    ) == "Object4762"


def test_hand_typed_node_names_are_left_alone() -> None:
    """Real maps contain labels a level designer typed ('Vill_1760').
    Rewriting them as numbers would lose that meaning."""
    from core.naming import STYLE_ID_MODEL, object_name

    assert object_name("Vill_1760", "SgNode", None, STYLE_ID_MODEL) == "Vill_1760"


def test_renaming_in_blender_does_not_change_the_exported_node_name() -> None:
    """The viewport name is for the user; the node's real name comes
    from the property. Blender renames duplicates on its own, so the
    two must not be the same thing."""
    collection = fake_bpy.FakeCollection("Objects")
    built = build_object_tree(
        [_node("Object4762", asset_id="house1")],
        collection,
        transform=CoordinateTransform(),
    )
    assert built[0].name == "4762_house1"

    built[0].name = "whatever the user typed"
    exported = extract_object_tree(built, transform=CoordinateTransform())
    assert exported[0].name == "Object4762"


# --- export validation ---


def test_validation_accepts_the_duplicate_names_real_maps_contain() -> None:
    """Regression: an integrity check rejecting duplicate node names
    blocked export of an unmodified shipped map, which contains 84
    nodes called 'noise'. The game loads that map, so refusing to write
    it back was a worse failure than the one being guarded against."""
    from core.scene import MapScene
    from formats.exm.plugin import ExMachinaPlugin

    scene = MapScene(
        objects=[_node("noise", asset_id="rock"), _node("noise", asset_id="rock")],
        world_root_attributes={"LastId": "5"},
    )
    assert ExMachinaPlugin().validate(scene) == []


def test_validation_rejects_a_node_with_no_model() -> None:
    from core.scene import MapScene
    from formats.exm.plugin import ExMachinaPlugin

    scene = MapScene(
        objects=[_node("Object1", asset_id=None)],
        world_root_attributes={"LastId": "5"},
    )
    assert any("no model id" in p for p in ExMachinaPlugin().validate(scene))


def test_validation_rejects_an_unnamed_node() -> None:
    from core.scene import MapScene
    from formats.exm.plugin import ExMachinaPlugin

    scene = MapScene(
        objects=[_node("", asset_id="rock")], world_root_attributes={"LastId": "5"},
    )
    assert any("no name" in p for p in ExMachinaPlugin().validate(scene))


# --- the export scan (regression) ---


def test_a_new_object_outside_the_objects_collection_still_exports() -> None:
    """The reported bug, reproduced exactly.

    Assign ExMachina Node reported success, but the object never
    reached world.xml. Cause: the export scan walked only the "Objects"
    sub-collection created during import, while Blender links a newly
    created mesh into the ACTIVE collection. The object's properties
    were all correct; it was simply never visited.
    """
    from blender_io.scene_bridge import extract_objects_from_collection

    root = fake_bpy.FakeCollection("Map")
    imported_collection = fake_bpy.FakeCollection("Objects")
    root.children.link(imported_collection)
    build_object_tree(
        [_node("Object1", asset_id="rock")],
        imported_collection,
        transform=CoordinateTransform(),
    )

    # The user's object goes where Blender puts it: the root collection.
    new_object = _blender_object("MyNewObject")
    root.objects.link(new_object)

    allocator = NodeNameAllocator.from_scene([_node("Object1")], {"LastId": "100"})
    exported = extract_objects_from_collection(
        root, transform=CoordinateTransform(), allocator=allocator,
    )

    names = {o.name for o in exported}
    assert "Object101" in names, "the user-created object was lost by the scan"
    assert "Object1" in names, "the imported object stopped exporting"


def test_a_new_object_in_a_user_made_sub_collection_exports() -> None:
    """Users organise their work; a node three collections deep is
    still a node."""
    from blender_io.scene_bridge import extract_objects_from_collection

    root = fake_bpy.FakeCollection("Map")
    middle = fake_bpy.FakeCollection("MyBuildings")
    deep = fake_bpy.FakeCollection("Phase2")
    root.children.link(middle)
    middle.children.link(deep)
    deep.objects.link(_blender_object("Deep"))

    allocator = NodeNameAllocator.from_scene([], {"LastId": "10"})
    exported = extract_objects_from_collection(
        root, transform=CoordinateTransform(), allocator=allocator,
    )
    assert [o.name for o in exported] == ["Object11"]


def test_the_wider_scan_does_not_duplicate_nested_nodes() -> None:
    """Walking every collection risks collecting a child both on its own
    and through its parent. A child of an assigned node is exported via
    the parent only."""
    from blender_io.scene_bridge import extract_objects_from_collection

    parent = _node("Object1", asset_id=None, node_class="SgNode")
    child = ObjectInstance(
        name="Object2",
        node_class="SgAnimatedModelNode",
        asset_id="rock",
        org=Vector3(1, 0, 1),
        org_rel=False,
    )
    parent.children.append(child)

    root = fake_bpy.FakeCollection("Map")
    sub = fake_bpy.FakeCollection("Objects")
    root.children.link(sub)
    build_object_tree([parent], sub, transform=CoordinateTransform())

    exported = extract_objects_from_collection(root, transform=CoordinateTransform())

    def count(nodes):
        return sum(1 + count(n.children) for n in nodes)

    assert [o.name for o in exported] == ["Object1"]
    assert count(exported) == 2, "a nested node was exported twice"


def test_the_wider_scan_ignores_other_layers() -> None:
    """Roads, obstacles and dynamic objects live in sibling
    collections and export through their own paths."""
    from blender_io.scene_bridge import extract_objects_from_collection

    root = fake_bpy.FakeCollection("Map")
    roads = fake_bpy.FakeCollection("Roads")
    root.children.link(roads)
    curve = fake_bpy.FakeObject("SomeRoad", None)
    curve["exm_road_chain"] = 1
    roads.objects.link(curve)

    assert extract_objects_from_collection(root, transform=CoordinateTransform()) == []


def test_an_unmodified_map_exports_unchanged_after_the_scan_widened() -> None:
    """Backward compatibility: widening the scan must not alter what a
    map without new objects produces."""
    from blender_io.scene_bridge import extract_objects_from_collection

    root = fake_bpy.FakeCollection("Map")
    sub = fake_bpy.FakeCollection("Objects")
    root.children.link(sub)
    originals = [_node("Object1", asset_id="rock"), _node("Object2", asset_id="tree")]
    build_object_tree(originals, sub, transform=CoordinateTransform())

    allocator = NodeNameAllocator.from_scene(originals, {"LastId": "100"})
    exported = extract_objects_from_collection(
        root, transform=CoordinateTransform(), allocator=allocator,
    )

    assert [o.name for o in exported] == ["Object1", "Object2"]
    assert allocator.issued == [], "an unmodified map consumed a new id"
    assert allocator.last_id == 100, "LastId changed with nothing added"


# --- placement (regression) ---


def test_a_new_node_carries_ndm_action() -> None:
    """1561 of 1734 nodes on a real map carry ndmAction, and the game's
    own editor writes it on every model node it creates. A node without
    it is not obviously broken, but matching what the editor produces
    avoids finding out the hard way."""
    from blender_io.world_bridge import NDM_ACTION_PROP

    obj = _blender_object("New")
    obj[NDM_ACTION_PROP] = "0"
    exported = extract_object_tree(
        [obj],
        transform=CoordinateTransform(),
        allocator=NodeNameAllocator.from_scene([], {"LastId": "0"}),
    )
    assert exported[0].ndm_action == "0"


def test_terrain_sampling_is_interpolated_not_nearest_vertex() -> None:
    """Reported symptom: a new node exported with org.y = -137 and was
    invisible in the editor, because node heights are offsets ABOVE the
    ground and it was buried.

    Nearest-vertex snapping fixed the sign but left a few units of
    residual offset — the grid step is 10 Blender units, so a point
    between vertices sits that far from the nearest one. Real nodes sit
    at exactly 0.
    """
    from addon.assign_operator import _sample_terrain_height

    mesh = fake_bpy.FakeMesh("terrain")
    # A 2x2 patch: heights 0, 10, 20, 30 at the corners of a 10-unit cell.
    mesh.from_pydata(
        [(0, 0, 0), (10, 0, 10), (0, 10, 20), (10, 10, 30)], [], [],
    )
    terrain = fake_bpy.FakeObject("Terrain", mesh)

    # Exactly on a corner.
    assert abs(_sample_terrain_height(terrain, 0.0, 0.0) - 0.0) < 1e-6
    # Midway along the bottom edge: between 0 and 10.
    assert abs(_sample_terrain_height(terrain, 5.0, 0.0) - 5.0) < 1e-6
    # Centre of the cell: the average of all four.
    assert abs(_sample_terrain_height(terrain, 5.0, 5.0) - 15.0) < 1e-6


def test_terrain_sampling_returns_none_without_terrain() -> None:
    """Snapping is skipped rather than guessed when there is no terrain
    to snap to."""
    from addon.assign_operator import _sample_terrain_height

    empty = fake_bpy.FakeObject("NotTerrain", None)
    assert _sample_terrain_height(empty, 0.0, 0.0) is None


def test_terrain_is_found_anywhere_in_the_collection_tree() -> None:
    from addon.assign_operator import _find_terrain

    root = fake_bpy.FakeCollection("Map")
    sub = fake_bpy.FakeCollection("Terrain")
    root.children.link(sub)
    mesh = fake_bpy.FakeMesh("t")
    mesh.from_pydata([(0, 0, 0)], [], [])
    terrain = fake_bpy.FakeObject("ExM_Terrain", mesh)
    terrain["exm_cell_size"] = 8.0
    sub.objects.link(terrain)

    assert _find_terrain(root) is terrain
    assert _find_terrain(fake_bpy.FakeCollection("Empty")) is None


def test_a_mesh_cannot_be_assigned_as_a_group_node() -> None:
    """Reported: 36 meshes were assigned as SgNode, exported fine, and
    re-imported as plain axes. SgNode is a container that never draws —
    the objects took up ids and were invisible, which looks exactly
    like a broken export rather than the wrong class."""
    from addon import assign_operator

    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = fake_bpy.FakeObject("MyMesh", mesh)

    class Context:
        selected_objects = [obj]
        active_object = obj
        collection = fake_bpy.FakeCollection("Map")
        scene = fake_bpy.FakeObject("S", None)

    operator = assign_operator.EXM_OT_assign_node()
    operator.node_class = "SgNode"
    operator.model_id = ""
    operator.top_level = True

    assert operator.execute(Context()) == {"CANCELLED"}
    assert CLASS_PROP not in obj, "the object was assigned anyway"


def test_model_ids_are_derived_from_a_user_typed_name() -> None:
    """The design fix: asking for a model id was a contradiction for a
    mesh that has no id yet. The user names it; the id is derived."""
    from addon.create_model_operator import make_model_id

    # Spaces are kept: the game's own editor created a working model
    # with id="MSCV NOD", so replacing them was an invented restriction.
    assert make_model_id("MSCV NOD") == "MSCV NOD"
    assert make_model_id("My Watchtower") == "My Watchtower"
    assert make_model_id("tripo_part_0") == "tripo_part_0"
    assert make_model_id("  ") == "model"
    # Characters that would break XML or a path are still replaced.
    assert make_model_id("Дом №3").isascii()


def test_registering_a_model_makes_it_findable() -> None:
    """A .gam on disk is invisible: the game resolves models by id
    through the catalogue and never scans the disk. Converting a file
    and dropping it in place therefore appeared to do nothing."""
    import shutil

    from formats.exm.model_catalog import read_model_catalog, register_model

    source = corpus("animmodels.xml")
    if not os.path.isfile(source):
        return

    path = os.path.join(tempfile.mkdtemp(), "AnimModels.xml")
    shutil.copy2(source, path)
    before = len(read_model_catalog(path))

    register_model(path, "my_house", "data\\models\\custom\\my_house.gam")
    after = read_model_catalog(path)

    assert len(after) == before + 1
    assert after.get("my_house").file_path.endswith("my_house.gam")
    # every shipped entry survives
    assert after.get("big_crag_11") is not None


def test_registering_twice_replaces_rather_than_duplicates() -> None:
    from formats.exm.model_catalog import read_model_catalog, register_model

    path = os.path.join(tempfile.mkdtemp(), "AnimModels.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write('<?xml version="1.0"?>\n<AnimatedModels>\n</AnimatedModels>')

    register_model(path, "thing", "a.gam")
    register_model(path, "thing", "b.gam")
    catalog = read_model_catalog(path)
    assert len(catalog) == 1
    assert catalog.get("thing").file_path == "b.gam"


def test_a_mesh_without_a_material_is_refused_before_export() -> None:
    """HTAToolchain's exporter fails on this deep inside itself with an
    AttributeError on a None skin list, which says nothing about the
    cause. The requirement is real — the format stores a material index
    per mesh — so it is checked here where it can be explained."""
    from blender_io.gam_export import _check_exportable
    from utils.errors import EXMeditorError

    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.uv_layers.new(name="UVMap")   # so the material check is what fires
    mesh.materials = []
    obj = fake_bpy.FakeObject("MyMesh", mesh)

    try:
        _check_exportable(obj)
        raise AssertionError("expected a refusal")
    except EXMeditorError as exc:
        assert "material" in exc.message

    mesh.materials = [object()]
    _check_exportable(obj)   # now acceptable


def test_multi_part_models_are_allowed() -> None:
    """A .gam holds many meshes — a shipped vehicle cab has 28 — so
    refusing more than one object contradicted the format and forced
    users to destroy their part structure with Ctrl+J."""
    from blender_io.gam_export import _check_exportable

    for name in ("part_a", "part_b"):
        mesh = fake_bpy.FakeMesh(name)
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        mesh.uv_layers.new(name="UVMap")
        mesh.materials = [object()]
        _check_exportable(fake_bpy.FakeObject(name, mesh))


def test_an_empty_material_slot_is_refused_with_its_own_message() -> None:
    """Joining parts collects empty slots easily, and they reach the
    exporter as None — failing with the same unhelpful AttributeError
    as having no material at all."""
    from blender_io.gam_export import _check_exportable
    from utils.errors import EXMeditorError

    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.uv_layers.new(name="UVMap")
    mesh.materials = [object(), None]
    obj = fake_bpy.FakeObject("Joined", mesh)

    try:
        _check_exportable(obj)
        raise AssertionError("expected a refusal")
    except EXMeditorError as exc:
        assert "empty material slot" in exc.message


def test_background_export_is_used_when_the_file_holds_bare_meshes() -> None:
    """HTAToolchain iterates bpy.data.objects — every object in the
    BLEND FILE, not the scene — and needs each mesh to have a material.
    With a map open that means the terrain and thousands of objects, so
    a temporary scene changes nothing; an earlier attempt at exactly
    that is why the same error kept appearing."""
    import bpy

    from blender_io.gam_export import _foreign_meshes_without_materials

    mesh = fake_bpy.FakeMesh("terrain")
    mesh.from_pydata([(0, 0, 0)], [], [])
    mesh.materials = []
    terrain = fake_bpy.FakeObject("Terrain", mesh)

    original = bpy.data.objects
    try:
        bpy.data.objects = [terrain]
        assert _foreign_meshes_without_materials() is True

        # A material alone is not enough now: the gate also requires a
        # UV map, because calc_tangents() needs one on every mesh.
        mesh.materials = [object()]
        # Flagged for render: the gate now tests the same condition
        # calc_tangents() does, not merely that a layer exists.
        mesh.uv_layers.new(name="UVMap").active_render = True
        assert _foreign_meshes_without_materials() is False
    finally:
        bpy.data.objects = original


def test_a_written_model_is_verified_before_being_registered() -> None:
    """A .gam that exists but is empty registers and assigns exactly
    like a good one, so the first sign of trouble would be an invisible
    object in the game with nothing to explain it."""
    from blender_io.gam_export import verify_written_model
    from utils.errors import EXMeditorError

    real = corpus("big_crag_11.gam")
    if os.path.isfile(real):
        verify_written_model(real)   # a good file passes

    empty = os.path.join(tempfile.mkdtemp(), "empty.gam")
    with open(empty, "wb") as f:
        f.write(b"\x00" * 16)
    try:
        verify_written_model(empty)
        raise AssertionError("expected a refusal")
    except EXMeditorError as exc:
        assert "empty file" in exc.message


def test_the_size_threshold_comes_from_measured_data() -> None:
    """A default Blender cube is 2 units; a terrain cell is 8 and a
    shipped rock 30-60. Exported as-is such a mesh is a speck, which is
    the commonest reason a correctly created model 'does not appear'."""
    from blender_io.gam_export import MINIMUM_VISIBLE_EXTENT

    assert MINIMUM_VISIBLE_EXTENT > 2.0, "a default cube must be flagged"
    assert MINIMUM_VISIBLE_EXTENT < 8.0, "must not flag models around one cell"


def test_a_model_must_be_listed_in_servers_xml_too() -> None:
    """SUPERSEDED — kept because the writer still exists and works.

    The conclusion this once encoded was wrong. It reasoned that
    servers.xml registration is required because all 114 models a
    reference map places appear there; that was correlation, since
    those 114 ship with the map. Watching the game's own editor
    integrate a model settled it: the editor writes nothing to
    servers.xml and the model works. The SDK no longer touches it."""
    import shutil
    import xml.etree.ElementTree as ET

    from formats.exm.model_catalog import register_in_servers

    source = corpus("servers.xml")
    if not os.path.isfile(source):
        return

    path = os.path.join(tempfile.mkdtemp(), "servers.xml")
    shutil.copy2(source, path)
    before = len(list(ET.parse(path).getroot().iter("Item")))

    assert register_in_servers(path, "my_model", "data\\models\\AnimModels.xml")

    items = [i.get("id") for i in ET.parse(path).getroot().iter("Item")]
    assert len(items) == before + 1
    assert "my_model" in items
    assert "cargo" in items, "an existing entry was lost"

    # Adding it twice must not duplicate the entry.
    assert register_in_servers(path, "my_model", "x") is False


def test_geometry_is_exported_around_the_model_origin() -> None:
    """The defect that made created models invisible.

    A .gam stores geometry around its OWN origin; the map node supplies
    the world position. HTAToolchain bakes the object transform into
    the vertices, so an object sitting at (1400, 900, -1200) on the map
    exported with those coordinates inside the mesh. Measured on a real
    created file: vertices spanned x 1361..1559 where a shipped rock
    spans -10..19.

    The same root cause corrupted the bounding box, which the toolchain
    initialises to zero and only expands — harmless for geometry
    straddling the origin, meaningless for geometry 1400 units away.
    """
    from blender_io.gam_export import _move_to_origin, _restore_transforms

    first = fake_bpy.FakeObject("part_a", None)
    first.location = (1400.0, 900.0, -1200.0)
    second = fake_bpy.FakeObject("part_b", None)
    second.location = (1410.0, 900.0, -1200.0)

    saved = _move_to_origin([first, second])
    assert first.location == (0.0, 0.0, 0.0)
    # Parts keep their relative arrangement; centring each independently
    # would collapse a multi-part model onto one spot.
    assert second.location == (10.0, 0.0, 0.0)

    _restore_transforms(saved)
    assert first.location == (1400.0, 900.0, -1200.0)


def test_a_model_with_baked_world_position_is_rejected() -> None:
    """Verified against a real file produced before the fix."""
    from blender_io.gam_export import verify_written_model
    from utils.errors import EXMeditorError

    broken = corpus("cube12.gam")
    good = corpus("big_crag_11.gam")
    if not (os.path.isfile(broken) and os.path.isfile(good)):
        return

    verify_written_model(good)   # a shipped model passes

    try:
        verify_written_model(broken)
        raise AssertionError("a model with baked world position was accepted")
    except EXMeditorError:
        pass


def test_the_static_vertex_format_is_forced_on_export() -> None:
    """Identified by comparing a created model against a shipped one:
    the only difference in the mesh header was the vertex type — 15
    (a vehicle format with tangents but no vertex colour) against 9,
    which map decorations use. The wrong format rendered as a wireframe
    with no surface.

    Set here rather than through HTAToolchain's preference, because
    that preference is read when its add-on registers: changing it has
    no effect until Blender restarts, so a user who changes it and
    exports immediately still gets the old format."""
    import types

    from blender_io.gam_export import (
        STATIC_VERTEX_TYPE,
        _restore_vertex_types,
        _set_static_vertex_type,
    )

    obj = fake_bpy.FakeObject("cube", None)
    obj.htatools = types.SimpleNamespace(vertex_type="15")

    saved = _set_static_vertex_type([obj])
    assert obj.htatools.vertex_type == STATIC_VERTEX_TYPE

    _restore_vertex_types(saved)
    assert obj.htatools.vertex_type == "15", "the user's setting was not restored"


def test_scale_and_rotation_are_not_baked_twice() -> None:
    """The exporter bakes the object transform into the vertices while
    the map node carries it as well, so leaving it applied means it is
    applied twice — a cube scaled 26x arrived large enough to contain
    the whole scene."""
    from blender_io.gam_export import _move_to_origin, _restore_transforms

    obj = fake_bpy.FakeObject("c", None)
    obj.location = (1400.0, 900.0, -1200.0)
    obj.scale = (26.3, 26.3, 26.3)
    # Explicit: the reset now follows rotation_mode, because assigning
    # rotation_quaternion on an Euler object does nothing at all.
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = (0.7, 0.7, 0.0, 0.0)

    saved = _move_to_origin([obj])
    assert obj.location == (0.0, 0.0, 0.0)
    assert obj.scale == (1.0, 1.0, 1.0)
    assert obj.rotation_quaternion == (1.0, 0.0, 0.0, 0.0)

    _restore_transforms(saved)
    assert obj.location == (1400.0, 900.0, -1200.0)
    assert obj.scale == (26.3, 26.3, 26.3)
    assert obj.rotation_quaternion == (0.7, 0.7, 0.0, 0.0)


def test_a_mesh_without_a_uv_map_is_refused() -> None:
    """HTAToolchain calls calc_tangents(), which raises deep inside its
    own code with a message naming neither the object nor the remedy.
    The requirement is real — the format stores UVs per vertex and
    every shipped model has them — so it is checked where it can be
    explained."""
    from blender_io.gam_export import _check_exportable
    from utils.errors import EXMeditorError

    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.materials = [object()]
    obj = fake_bpy.FakeObject("Cube", mesh)

    try:
        _check_exportable(obj)
        raise AssertionError("expected a refusal")
    except EXMeditorError as exc:
        assert "UV map" in exc.message

    mesh.uv_layers.new(name="UVMap")
    _check_exportable(obj)   # now acceptable


def test_a_uv_map_is_flagged_for_render_before_export() -> None:
    """Reported after a correct unwrap: calc_tangents() with no argument
    uses the layer flagged active_render and reports a missing one as
    'UV Map "(null)" not found' — which reads as "there is no UV map"
    even when there is. Smart UV Project does not always set the flag,
    so the user saw the same error after doing exactly the right
    thing."""
    from blender_io.gam_export import _ensure_render_uv

    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    layer = mesh.uv_layers.new(name="UVMap")
    assert layer.active_render is False

    _ensure_render_uv([fake_bpy.FakeObject("Cube", mesh)])
    assert layer.active_render is True

    # An object with no mesh must not raise.
    _ensure_render_uv([fake_bpy.FakeObject("Empty", None)])


def test_the_render_uv_flag_is_read_from_the_layers_not_the_collection() -> None:
    """Regression: reported as `Tangent space computation needs a UV Map,
    "(null)" not found` on a mesh that HAD been unwrapped.

    Blender's uv_layers collection also exposes `active_render`, and it
    answers with a layer rather than with "something is flagged". A
    guard that asked the collection therefore saw a value on every mesh,
    concluded the flag was already set and assigned nothing — so the
    flag was never set and the export died on exactly the error the
    guard existed to prevent.
    """
    from blender_io.gam_export import _ensure_render_uv

    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    layer = mesh.uv_layers.new(name="UVMap")

    # The collection answers, but nothing is actually flagged.
    assert mesh.uv_layers.active_render is not None
    assert layer.active_render is False

    _ensure_render_uv([fake_bpy.FakeObject("Cube", mesh)])
    assert layer.active_render is True, "the collection was trusted over the layers"


def test_the_background_script_creates_the_addon_preferences_entry() -> None:
    """Regression: the background Blender never produced a file.

    HTAToolchain reads its own preferences at module level, and
    addon_utils.enable() imports the module before creating that entry.
    Under --factory-startup no add-on preferences exist at all, so the
    import died with KeyError and the export silently fell back
    in-process — where it failed on the terrain, sending the
    investigation after a UV problem that was never the cause.
    """
    from blender_io.gam_export import _BACKGROUND_SCRIPT

    script = _BACKGROUND_SCRIPT
    assert "addons.new()" in script, "the preferences entry is never created"
    # rindex: the explanation above the code mentions the call by name,
    # and it is the CALL that must come second.
    assert script.index("addons.new()") < script.rindex("addon_utils.enable("), (
        "the entry must exist BEFORE the module is imported"
    )


def test_the_background_failure_reason_survives_the_fallback() -> None:
    """The in-process error alone names the wrong problem.

    When isolation cannot start, the fallback runs against the whole
    open file and fails on a mesh the isolation existed to avoid. That
    error is true and misleading, so the original reason travels with
    it.
    """
    import inspect

    from blender_io import gam_export

    source = inspect.getsource(gam_export._export_in_isolation)
    assert "background_error = exc.message" in source
    assert "consequence" in source, "the fallback error must say which is which"


def test_a_file_written_this_run_is_not_reused_when_it_fails_verification() -> None:
    """Reported as a model the game could not load.

    HTAToolchain wrote a truncated .gam — 396 bytes of a declared 1234 —
    and verification caught it. The operator then saw a file at the
    target path, treated it as one from an earlier successful run, and
    registered it anyway. A file this run just wrote is not a fallback
    for this run failing.
    """
    import inspect

    from addon import create_model_operator

    source = inspect.getsource(create_model_operator.EXM_OT_create_model.execute)
    assert "pre_existing = os.path.isfile(target)" in source
    assert "if pre_existing and not self.overwrite:" in source, (
        "a file written by this run must not be reused as a fallback"
    )


def test_the_vertex_format_is_a_choice_not_a_constant() -> None:
    """Measured: MSCV NOD, which the game's editor renders, uses
    component count 15 and stride 48 on all 36 of its meshes. The
    forced value was 9, which has never been measured on a model
    confirmed to work. Both may be legitimate, so the format is
    selectable and the experiment can be run in one click.
    """
    from blender_io.gam_export import STATIC_VERTEX_TYPE, TANGENT_VERTEX_TYPE
    import inspect
    from blender_io import gam_export

    assert TANGENT_VERTEX_TYPE == "15"
    assert STATIC_VERTEX_TYPE == "9"

    signature = inspect.signature(gam_export.export_meshes_to_gam)
    assert "vertex_type" in signature.parameters


def test_creating_a_model_lists_it_in_servers_xml_too() -> None:
    """Reported: the .gam is written, the catalogue entry is correct,
    and the editor's model list still does not show it.

    register_in_servers() was written, tested and documented as
    necessary — every model the reference map places appears there —
    and then never called from anywhere. The catalogue says a model
    exists; servers.xml says a map loads it, and only the second puts
    it in front of the user.
    """
    import inspect

    from addon import create_model_operator

    source = inspect.getsource(create_model_operator.EXM_OT_create_model)
    assert "register_in_servers(" in source, (
        "the catalogue alone does not make a model appear"
    )
    assert "_servers_index" in source


def test_servers_xml_is_taken_from_the_map_folder() -> None:
    """Read off M3DEditor's own log, which names what it opens:

        Loading Servers: data\\maps\\r1m1\\servers.xml
        Loading Servers: data\\models\\commonservers.xml

    data\\models\\servers.xml is not among them. A registration audit
    had found a gap there — a real gap, in a file nothing reads — and
    writing to it changed nothing except adding entries the game never
    looks at.
    """
    import tempfile

    from addon.create_model_operator import EXM_OT_create_model

    root = tempfile.mkdtemp()
    models = os.path.join(root, "data", "models")
    map_dir = os.path.join(root, "data", "maps", "mymap")
    os.makedirs(models)
    os.makedirs(map_dir)

    catalogue = os.path.join(models, "animmodels.xml")
    open(catalogue, "w").write("<models/>")
    decoy = os.path.join(models, "servers.xml")
    open(decoy, "w").write("<s/>")
    wanted = os.path.join(map_dir, "servers.xml")
    open(wanted, "w").write("<s/>")

    class _Context:
        """Minimal stand-in: the method reads one scene property."""
        scene = {"exm_source_dir": map_dir}

    context = _Context()

    found = EXM_OT_create_model._servers_index(
        EXM_OT_create_model(), context, root, catalogue,
    )
    assert found is not None
    assert os.path.samefile(found, wanted), "the map's own servers.xml, not the catalogue's neighbour"


def _unused_test_servers_xml_is_found_beside_the_catalogue() -> None:
    """Measured by a registration audit: a model the editor offers and
    one it does not differed in exactly one file,
    ``data\\models\\servers.xml`` — which sits BESIDE the catalogue,
    not under a map. Resolving it through the map manifest found
    nothing and left the model catalogued but unlisted.
    """
    import tempfile

    from addon.create_model_operator import EXM_OT_create_model

    root = tempfile.mkdtemp()
    models = os.path.join(root, "data", "models")
    os.makedirs(models)
    catalogue = os.path.join(models, "animmodels.xml")
    open(catalogue, "w").write("<models/>")
    servers = os.path.join(models, "servers.xml")
    open(servers, "w").write("<s/>")

    found = EXM_OT_create_model._servers_index(
        EXM_OT_create_model(), None, root, catalogue,
    )
    assert found is not None and os.path.samefile(found, servers)


def test_the_catalogue_reference_is_copied_from_existing_entries() -> None:
    """The Game Folder may itself be data\\models, so a path relative
    to it would read "animmodels.xml" while every neighbouring entry
    reads the full game-relative path."""
    import tempfile

    from addon.create_model_operator import _catalogue_reference

    folder = tempfile.mkdtemp()
    servers = os.path.join(folder, "servers.xml")
    with open(servers, "w") as handle:
        handle.write('<s><Item id="MSCV NOD" file="data\\models\\AnimModels.xml" /></s>')

    reference = _catalogue_reference(servers, os.path.join(folder, "animmodels.xml"))
    assert reference == "data\\models\\AnimModels.xml"


def test_the_servers_index_is_resolved_separately_from_the_catalogue() -> None:
    """Two files, two jobs. Conflating them is how one came to be
    written to and the other forgotten."""
    from addon.create_model_operator import EXM_OT_create_model

    assert hasattr(EXM_OT_create_model, "_servers_index")
    assert hasattr(EXM_OT_create_model, "_catalogue_path")


def test_a_lone_image_node_is_renamed_so_its_texture_is_written() -> None:
    """Measured: a generated cube's skin chunk named a shader and no
    image file at all, while a working model's named two .dds files.
    HTAToolchain matches by NODE NAME, so Blender's default "Image
    Texture" exports nothing and the game binds something arbitrary.
    """
    from blender_io.gam_export import (
        _name_texture_nodes_for_export,
        _restore_texture_node_names,
    )

    material = fake_bpy.FakeMaterial("Material")
    node = material.node_tree.nodes.new("TEX_IMAGE")
    mesh = fake_bpy.FakeMesh("m")
    mesh.materials = [material]
    obj = fake_bpy.FakeObject("Cube", mesh)

    saved = _name_texture_nodes_for_export([obj])
    assert node.name == "Diffuse"

    _restore_texture_node_names(saved)
    assert node.name == "TEX_IMAGE", "the user's material must be put back"


def test_several_image_nodes_are_left_alone() -> None:
    """Which one is the diffuse map is the user's decision."""
    from blender_io.gam_export import _name_texture_nodes_for_export

    material = fake_bpy.FakeMaterial("Material")
    first = material.node_tree.nodes.new("TEX_IMAGE")
    first.name = "Base"
    second = material.node_tree.nodes.new("TEX_IMAGE")
    second.name = "Rough"
    mesh = fake_bpy.FakeMesh("m")
    mesh.materials = [material]
    obj = fake_bpy.FakeObject("Cube", mesh)

    assert _name_texture_nodes_for_export([obj]) == []
    assert first.name == "Base" and second.name == "Rough"


def test_an_already_named_node_is_not_touched() -> None:
    from blender_io.gam_export import _name_texture_nodes_for_export

    material = fake_bpy.FakeMaterial("Material")
    node = material.node_tree.nodes.new("TEX_IMAGE")
    node.name = "Diffuse"
    mesh = fake_bpy.FakeMesh("m")
    mesh.materials = [material]
    obj = fake_bpy.FakeObject("Cube", mesh)

    assert _name_texture_nodes_for_export([obj]) == []
    assert node.name == "Diffuse"


def test_an_euler_object_really_has_its_rotation_reset() -> None:
    """Regression: rotation_quaternion was assigned on objects whose
    rotation_mode is XYZ Euler — Blender's default — where it changes
    nothing. The rotation stayed applied, was baked into the vertices
    AND applied again by the map node.
    """
    from blender_io.gam_export import _move_to_origin, _restore_transforms

    obj = fake_bpy.FakeObject("Cube", fake_bpy.FakeMesh("m"))
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = (0.5, 0.25, 0.125)

    saved = _move_to_origin([obj])
    assert tuple(obj.rotation_euler) == (0.0, 0.0, 0.0)

    _restore_transforms(saved)
    assert tuple(obj.rotation_euler) == (0.5, 0.25, 0.125)


def test_imported_uv_layers_are_flagged_for_render() -> None:
    """The root cause, fixed where the layer is created.

    An imported model whose UV map is not flagged for render cannot be
    exported, and a map full of them cannot be exported at all. A mesh
    with exactly one UV map has no other sensible render layer, so the
    flag is set as the layer is written.
    """
    from blender_io.mesh_bridge import _apply_uv_layer

    mesh = fake_bpy.FakeMesh("imported")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])

    _apply_uv_layer(mesh, "UVMap", [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)])

    assert len(mesh.uv_layers) == 1
    assert mesh.uv_layers[0].active_render is True


def test_an_unflagged_uv_map_forces_the_background_export() -> None:
    """Reported twice: the same calc_tangents error after the render-UV
    fix, because the fix only touched the SELECTED objects.

    HTAToolchain iterates every mesh in the file. Imported models all
    carry a UV map, so a presence check passed and the in-process path
    was chosen — then the export died on the first imported model whose
    UV map was not flagged for render. The gate has to test the same
    condition calc_tangents does.
    """
    import bpy
    from blender_io.gam_export import _foreign_meshes_the_exporter_rejects

    mesh = fake_bpy.FakeMesh("imported")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.materials = [object()]
    layer = mesh.uv_layers.new(name="UVMap")   # present, but not flagged
    imported = fake_bpy.FakeObject("Imported", mesh)

    original = bpy.data.objects
    try:
        bpy.data.objects = [imported]
        assert _foreign_meshes_the_exporter_rejects() is True

        layer.active_render = True
        assert _foreign_meshes_the_exporter_rejects() is False
    finally:
        bpy.data.objects = original


def test_an_already_flagged_uv_layer_is_left_alone() -> None:
    """A mesh whose second UV map is the render one must keep it."""
    from blender_io.gam_export import _ensure_render_uv

    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    first = mesh.uv_layers.new(name="UVMap")
    second = mesh.uv_layers.new(name="UVMap2")
    second.active_render = True

    _ensure_render_uv([fake_bpy.FakeObject("Cube", mesh)])
    assert second.active_render is True
    assert first.active_render is False, "the render layer was overwritten"


def test_a_mesh_without_uvs_also_forces_the_background_export() -> None:
    """HTAToolchain calls calc_tangents() on every mesh in the file, so
    the terrain — which has no UV map and never will — aborts an
    in-process export.

    An earlier version of this gate checked only materials. Once the
    terrain had one, the in-process path was chosen again and the
    export died on the terrain's missing UVs instead, reporting a UV
    error for a cube the user had unwrapped correctly.
    """
    import bpy

    from blender_io.gam_export import _foreign_meshes_the_exporter_rejects

    def make(name, *, material=True, uv=True):
        mesh = fake_bpy.FakeMesh(name)
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        mesh.materials = [object()] if material else []
        if uv:
            # Flagged: a usable mesh has a render layer, and that is
            # what the gate tests.
            mesh.uv_layers.new(name="UVMap").active_render = True
        return fake_bpy.FakeObject(name, mesh)

    original = bpy.data.objects
    try:
        bpy.data.objects = [make("cube")]
        assert _foreign_meshes_the_exporter_rejects() is False

        bpy.data.objects = [make("cube"), make("terrain", uv=False)]
        assert _foreign_meshes_the_exporter_rejects() is True

        bpy.data.objects = [make("cube"), make("terrain", material=False)]
        assert _foreign_meshes_the_exporter_rejects() is True
    finally:
        bpy.data.objects = original


def test_the_vertex_format_gets_the_data_it_requires() -> None:
    """XYZNCT2 stores position, normal, COLOUR and TWO UV sets — the
    name spells it out. A mesh with one UV map and no colour layer made
    the exporter dereference None and fail with a TypeError naming
    neither the mesh nor the missing piece.

    Both additions are neutral: a second UV set is what a model without
    a lightmap carries anyway, and white vertex colour is the identity
    for the shading the engine multiplies it into."""
    from blender_io.gam_export import _ensure_vertex_format_inputs

    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.uv_layers.new(name="UVMap")
    obj = fake_bpy.FakeObject("Cube", mesh)

    assert len(mesh.uv_layers) == 1
    assert len(mesh.vertex_colors) == 0

    _ensure_vertex_format_inputs([obj])

    assert len(mesh.uv_layers) == 2
    assert len(mesh.vertex_colors) == 1

    # Running twice must not keep adding layers.
    _ensure_vertex_format_inputs([obj])
    assert len(mesh.uv_layers) == 2
    assert len(mesh.vertex_colors) == 1


def test_catalogue_entries_match_what_the_editor_writes() -> None:
    """Observed by watching the game's own editor integrate a model:

        shadow="1" windwavy="0" trans="0" composite="0"

    Shipped entries carry the first three; the editor adds composite.
    Writing only `shadow`, as an earlier version did, produced entries
    unlike anything either the game or its editor creates."""
    from formats.exm.model_catalog import register_model

    path = os.path.join(tempfile.mkdtemp(), "AnimModels.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write('<?xml version="1.0"?>\n<AnimatedModels>\n</AnimatedModels>')

    register_model(path, "thing", "K:\\somewhere\\thing.gam")
    with open(path, encoding="cp1251") as f:
        text = f.read()

    for attribute in ('shadow="1"', 'windwavy="0"', 'trans="0"', 'composite="0"'):
        assert attribute in text, f"{attribute} missing from the entry"


_ALL_TESTS = (
    test_allocates_names_above_the_recorded_last_id,
    test_allocates_above_the_highest_name_in_use,
    test_skips_names_already_in_use,
    test_handles_a_missing_or_broken_last_id,
    test_ignores_hand_typed_node_names,
    test_records_every_issued_name,
    test_counts_nested_nodes_when_scanning,
    test_a_newly_assigned_object_is_exported,
    test_new_objects_get_a_map_name_not_a_blender_name,
    test_imported_objects_keep_their_original_name,
    test_new_object_position_converts_to_game_space,
    test_several_new_objects_get_distinct_names,
    test_objects_without_a_class_are_still_skipped,
    test_export_without_an_allocator_still_works,
    test_imported_objects_record_their_original_name,
    test_object_names_carry_the_id_and_model,
    test_naming_styles,
    test_hand_typed_node_names_are_left_alone,
    test_renaming_in_blender_does_not_change_the_exported_node_name,
    test_validation_accepts_the_duplicate_names_real_maps_contain,
    test_validation_rejects_a_node_with_no_model,
    test_validation_rejects_an_unnamed_node,
    test_a_new_object_outside_the_objects_collection_still_exports,
    test_a_new_object_in_a_user_made_sub_collection_exports,
    test_the_wider_scan_does_not_duplicate_nested_nodes,
    test_the_wider_scan_ignores_other_layers,
    test_an_unmodified_map_exports_unchanged_after_the_scan_widened,
    test_a_mesh_cannot_be_assigned_as_a_group_node,
    test_a_mesh_without_a_material_is_refused_before_export,
    test_a_mesh_without_a_uv_map_is_refused,
    test_a_uv_map_is_flagged_for_render_before_export,
    test_the_vertex_format_gets_the_data_it_requires,
    test_multi_part_models_are_allowed,
    test_background_export_is_used_when_the_file_holds_bare_meshes,
    test_a_mesh_without_uvs_also_forces_the_background_export,
    test_a_written_model_is_verified_before_being_registered,
    test_geometry_is_exported_around_the_model_origin,
    test_the_static_vertex_format_is_forced_on_export,
    test_scale_and_rotation_are_not_baked_twice,
    test_an_euler_object_really_has_its_rotation_reset,
    test_creating_a_model_lists_it_in_servers_xml_too,
    test_servers_xml_is_taken_from_the_map_folder,
    test_the_catalogue_reference_is_copied_from_existing_entries,
    test_the_servers_index_is_resolved_separately_from_the_catalogue,
    test_a_lone_image_node_is_renamed_so_its_texture_is_written,
    test_several_image_nodes_are_left_alone,
    test_an_already_named_node_is_not_touched,
    test_a_model_with_baked_world_position_is_rejected,
    test_a_model_must_be_listed_in_servers_xml_too,
    test_an_empty_material_slot_is_refused_with_its_own_message,
    test_model_ids_are_derived_from_a_user_typed_name,
    test_registering_a_model_makes_it_findable,
    test_catalogue_entries_match_what_the_editor_writes,
    test_registering_twice_replaces_rather_than_duplicates,
    test_a_new_node_carries_ndm_action,
    test_terrain_sampling_is_interpolated_not_nearest_vertex,
    test_terrain_sampling_returns_none_without_terrain,
    test_terrain_is_found_anywhere_in_the_collection_tree,
)


if __name__ == "__main__":
    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            test_fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL: {test_fn.__name__}: {exc}")
        else:
            print(f"PASS: {test_fn.__name__}")
    print(f"\n{len(_ALL_TESTS) - failures}/{len(_ALL_TESTS)} passed")
    sys.exit(1 if failures else 0)


def test_registration_does_not_multiply_carriage_returns() -> None:
    """Measured on a real, damaged file: a shipped servers.xml reached
    13 carriage returns per line — 34070 CR against 2636 LF — because
    every registration added one more.

    open(path, "w") translates \\n to \\r\\n on Windows, and the text had
    been read from a CRLF file, so \\r\\n became \\r\\r\\n on each write.
    The game's editor stopped loading that catalogue and silently fell
    back to another map's, which made every model registered in it
    invisible.
    """
    import os
    import tempfile

    from formats.exm.model_catalog import register_in_servers

    path = os.path.join(tempfile.mkdtemp(), "servers.xml")
    original = (
        '<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>\r\n'
        "<Servers>\r\n"
        "\t<AnimatedModelsServer>\r\n"
        "\t</AnimatedModelsServer>\r\n"
        "</Servers>\r\n"
    )
    with open(path, "w", encoding="cp1251", newline="") as handle:
        handle.write(original)

    before = open(path, "rb").read()
    assert b"\r\r" not in before

    for index in range(5):
        register_in_servers(path, f"Model{index}", "data\\models\\AnimModels.xml")

    after = open(path, "rb").read()
    assert b"\r\r" not in after, "a carriage return was added on every write"
    assert after.count(b"\r") == after.count(b"\n"), "CRLF must stay one-to-one"

    text = after.decode("cp1251")
    for index in range(5):
        assert f'id="Model{index}"' in text, "the entries must still be written"


def test_a_mesh_beyond_the_16_bit_index_limit_is_refused() -> None:
    """The format stores triangle indices as 16-bit numbers, so a mesh
    simply cannot address more than 65535 vertices."""
    from blender_io.gam_export import MAX_VERTICES_PER_MESH, _check_exportable

    mesh = fake_bpy.FakeMesh("dense")
    mesh.vertices = [object()] * (MAX_VERTICES_PER_MESH + 1)
    mesh.polygons = []
    mesh.materials = [object()]
    obj = fake_bpy.FakeObject("Sphere", mesh)

    try:
        _check_exportable(obj)
    except Exception as exc:
        assert "16-bit" in str(exc) and "65535" in str(exc)
    else:
        raise AssertionError("a mesh the format cannot address must be refused")


def test_a_dense_mesh_is_warned_about_before_it_kills_the_editor() -> None:
    """Measured: the editor died with
    'd3d: [ AddIbPoolField ] : Asked size is too big!!!' immediately
    after loading a freshly exported model. Shipped models run to a few
    hundred triangles — civilhouse1 is 733 across three meshes.
    """
    import logging

    from blender_io.gam_export import RISKY_TRIANGLE_COUNT, _check_exportable

    class _Face:
        vertices = (0, 1, 2)

    mesh = fake_bpy.FakeMesh("dense")
    mesh.vertices = [object()] * 1000
    mesh.polygons = [_Face()] * (RISKY_TRIANGLE_COUNT + 1)
    mesh.materials = [object()]
    mesh.uv_layers.new(name="UVMap").active_render = True
    obj = fake_bpy.FakeObject("Sphere", mesh)

    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    log = logging.getLogger("exmeditor.blender_io.gam_export")
    handler = _Capture()
    log.addHandler(handler)
    try:
        _check_exportable(obj)
    finally:
        log.removeHandler(handler)

    assert any("AddIbPoolField" in m for m in records), (
        "the warning must name the failure the user will otherwise see"
    )


def test_an_ordinary_mesh_passes_the_density_check_silently() -> None:
    from blender_io.gam_export import _check_density

    class _Face:
        vertices = (0, 1, 2, 3)

    mesh = fake_bpy.FakeMesh("cube")
    mesh.vertices = [object()] * 8
    mesh.polygons = [_Face()] * 6
    _check_density(fake_bpy.FakeObject("Cube", mesh), mesh)
