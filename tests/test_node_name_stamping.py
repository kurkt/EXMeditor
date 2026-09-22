# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""A new node's allocated name sticks to its Blender object.

MEASURED on r1m1 (2026-09-19): a barrel placed from the asset palette
went out as ``Object76476763`` on one export and as ``Object76476764``
on the next, LastId climbing by one each time. The name was allocated
during extraction and never written back onto the object, so every
export saw an unnamed node and allocated afresh. Nothing broke, but a
map edited ten times carries a node renamed ten times, and anything
that referred to the node by name after the first export refers to
nothing.

Three consequences are covered here: the stamp itself, a duplicated
object (Shift+D copies the stamp) getting a name of its own, and a
stamp that never reached the file being reserved on the next export.
"""

from __future__ import annotations

import os
import sys

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
    extract_object_tree,
    stamped_node_names,
)
from core.coordinates import CoordinateTransform  # noqa: E402
from core.node_naming import NodeNameAllocator  # noqa: E402
from core.objects import ObjectInstance  # noqa: E402
from utils.math import Vector3  # noqa: E402


def _new_object(name):
    obj = fake_bpy.FakeObject(name, None)
    obj[CLASS_PROP] = "SgAnimatedModelNode"
    obj[ASSET_ID_PROP] = "barrel1"
    obj[ORG_REL_PROP] = 1
    obj[NEW_OBJECT_PROP] = 1
    return obj


def _export(objects, allocator):
    return extract_object_tree(objects, transform=CoordinateTransform(), allocator=allocator)


def test_the_allocated_name_is_stamped_onto_the_object() -> None:
    obj = _new_object("Barrel.001")
    exported = _export([obj], NodeNameAllocator.from_scene([], {"LastId": "76476762"}))

    assert exported[0].name == "Object76476763"
    assert obj[ORIGINAL_NAME_PROP] == "Object76476763"


def test_a_second_export_writes_the_same_node_not_a_new_one() -> None:
    """The r1m1 sequence: export, then export again with the file now
    holding the node. The second allocator starts above the first
    export's LastId and must issue nothing."""
    obj = _new_object("Barrel.001")
    first = _export([obj], NodeNameAllocator.from_scene([], {"LastId": "76476762"}))
    in_file = [ObjectInstance(name=first[0].name, node_class="SgAnimatedModelNode",
                              asset_id="barrel1", org=Vector3(0, 0, 0), org_rel=True)]

    second_allocator = NodeNameAllocator.from_scene(in_file, {"LastId": "76476763"})
    second = _export([obj], second_allocator)

    assert second[0].name == "Object76476763"
    assert second_allocator.issued == []
    assert second_allocator.last_id == 76476763, "LastId must not climb on a re-export"


def test_a_duplicated_object_gets_a_name_of_its_own() -> None:
    """Shift+D copies custom properties, stamp included. Two nodes with
    one name is a file the game resolves to one of them."""
    original = _new_object("Barrel.001")
    original[ORIGINAL_NAME_PROP] = "Object100"
    copy = _new_object("Barrel.002")
    copy[ORIGINAL_NAME_PROP] = "Object100"

    allocator = NodeNameAllocator.from_scene([], {"LastId": "100"})
    exported = _export([original, copy], allocator)

    names = [o.name for o in exported]
    assert names == ["Object100", "Object101"]
    assert copy[ORIGINAL_NAME_PROP] == "Object101", "the copy is re-stamped"
    assert original[ORIGINAL_NAME_PROP] == "Object100", "the original is untouched"


def test_a_duplicate_under_a_parent_is_caught_too() -> None:
    parent = _new_object("Group")
    parent[ORIGINAL_NAME_PROP] = "Object10"
    child = _new_object("Child")
    child[ORIGINAL_NAME_PROP] = "Object11"
    twin = _new_object("Child.001")
    twin[ORIGINAL_NAME_PROP] = "Object11"
    child.parent = parent
    twin.parent = parent

    exported = _export([parent], NodeNameAllocator.from_scene([], {"LastId": "11"}))
    assert [c.name for c in exported[0].children] == ["Object11", "Object12"]


def test_a_stamp_the_file_never_got_is_reserved_on_the_next_export() -> None:
    """An export that allocated and then failed leaves a stamped
    object and an unchanged file. The next export must not hand that
    number to a different new object."""
    stamped = _new_object("Barrel.001")
    _export([stamped], NodeNameAllocator.from_scene([], {"LastId": "50"}))
    assert stamped[ORIGINAL_NAME_PROP] == "Object51"
    newcomer = _new_object("Crate.001")

    collection = fake_bpy.FakeCollection("Objects")
    collection.objects.link(stamped)
    collection.objects.link(newcomer)
    reserved = stamped_node_names(collection)
    assert reserved == {"Object51"}

    # The file still says 50: without the reservation the newcomer
    # would also be Object51.
    allocator = NodeNameAllocator.from_scene([], {"LastId": "50"}, reserved=reserved)
    exported = _export([stamped, newcomer], allocator)
    assert [o.name for o in exported] == ["Object51", "Object52"]


def test_stamped_names_are_collected_from_nested_collections() -> None:
    outer = fake_bpy.FakeCollection("Map")
    inner = fake_bpy.FakeCollection("Objects")
    outer.children.link(inner)
    a = _new_object("A"); a[ORIGINAL_NAME_PROP] = "Object7"
    b = _new_object("B"); b[ORIGINAL_NAME_PROP] = "Vill_1760"
    c = _new_object("C")   # no stamp
    outer.objects.link(a)
    inner.objects.link(b)
    inner.objects.link(c)

    assert stamped_node_names(outer) == {"Object7", "Vill_1760"}


def test_reserved_names_also_raise_the_starting_point() -> None:
    """A reservation above LastId — the file was replaced by an older
    copy — must not be walked into."""
    allocator = NodeNameAllocator.from_scene([], {"LastId": "10"}, reserved={"Object500"})
    assert allocator.allocate() == "Object501"


def test_without_an_allocator_nothing_is_stamped() -> None:
    """The bare bridge (no map to allocate from) keeps its old
    behaviour: the Blender name is used and the object is left alone."""
    obj = _new_object("Barrel.001")
    exported = extract_object_tree([obj], transform=CoordinateTransform())
    assert exported[0].name == "Barrel.001"
    assert ORIGINAL_NAME_PROP not in obj


def test_the_export_operator_reserves_the_stamps() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "addon", "operators.py"), encoding="utf-8") as fh:
        source = fh.read()
    assert "reserved=stamped_node_names(collection)" in source
