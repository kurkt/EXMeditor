# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the dynamic layer's Blender bridge.

Focused on deletion. This layer holds most of a map's breakable
scenery — 4459 placed objects on the reference map against 1562 in
world.xml — and until now nothing the user removed in Blender was ever
removed from the file.

The safety rules matter as much as the feature: this layer also nests
gameplay records with no viewport presence at all, and an export that
silently dropped them would be unrecoverable.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

def _scene_with(names_and_positions, children=None):
    """A DynamicScene whose objects carry the given names/positions."""
    from core.dynamic_objects import DynamicObject, DynamicScene
    from utils.math import Vector3

    objects = []
    for name, placed in names_and_positions:
        objects.append(
            DynamicObject(
                name=name,
                position=Vector3(1.0, 2.0, 3.0) if placed else None,
                children=list(children or []) if name == "parent" else [],
            )
        )
    return DynamicScene(objects=objects)


def _collection_with(names):
    from blender_io.dynamic_bridge import DYNAMIC_PROP

    collection = fake_bpy.FakeCollection("Dynamic")
    for name in names:
        obj = fake_bpy.FakeObject(name, None)
        obj[DYNAMIC_PROP] = 1
        collection.objects.link(obj)
    return collection


def test_an_object_deleted_in_blender_is_removed_from_the_file() -> None:
    from blender_io.dynamic_bridge import remove_deleted_objects

    scene = _scene_with([("fence1", True), ("fence2", True), ("fence3", True)])
    removed = remove_deleted_objects(scene, _collection_with(["fence1", "fence3"]))

    assert removed == 1
    assert [o.name for o in scene.objects] == ["fence1", "fence3"]


def test_an_object_that_never_had_a_viewport_presence_is_left_alone() -> None:
    """Objects with no position are gameplay records — teams, quest
    state — that build_dynamic_scene skips. Their absence from the
    scene means nothing."""
    from blender_io.dynamic_bridge import remove_deleted_objects

    scene = _scene_with([("team_red", False), ("fence1", True)])
    removed = remove_deleted_objects(scene, _collection_with(["fence1"]))

    assert removed == 0
    assert [o.name for o in scene.objects] == ["team_red", "fence1"]


def test_a_deleted_parent_whose_children_survive_is_kept() -> None:
    """This layer nests records under placed objects; removing the
    parent would take them with it."""
    from blender_io.dynamic_bridge import remove_deleted_objects
    from core.dynamic_objects import DynamicObject
    from utils.math import Vector3

    child = DynamicObject(name="turret", position=Vector3(1.0, 2.0, 3.0))
    scene = _scene_with([("parent", True)], children=[child])

    removed = remove_deleted_objects(scene, _collection_with(["turret"]))

    assert removed == 0
    assert [o.name for o in scene.objects] == ["parent"]


def test_a_deleted_parent_with_a_deleted_subtree_goes_entirely() -> None:
    from blender_io.dynamic_bridge import remove_deleted_objects
    from core.dynamic_objects import DynamicObject
    from utils.math import Vector3

    child = DynamicObject(name="turret", position=Vector3(1.0, 2.0, 3.0))
    # Enough survivors that the wholesale-loss guard stays quiet: this
    # test is about a subtree, not about scale.
    scene = _scene_with(
        [("parent", True)] + [(f"fence{i}", True) for i in range(8)],
        children=[child],
    )

    removed = remove_deleted_objects(
        scene, _collection_with([f"fence{i}" for i in range(8)])
    )

    assert removed == 2, "the parent and its only child"
    assert "parent" not in [o.name for o in scene.objects]


def test_losing_most_of_the_layer_is_refused_rather_than_obeyed() -> None:
    """A scene imported from a different map would otherwise delete
    thousands of objects in one export."""
    from blender_io.dynamic_bridge import remove_deleted_objects
    from utils.errors import EXMeditorError

    scene = _scene_with([(f"fence{i}", True) for i in range(10)])

    try:
        remove_deleted_objects(scene, _collection_with(["fence0"]))
    except EXMeditorError as exc:
        assert "9 of 10" in exc.message
        assert "re-import" in exc.message.lower()
    else:
        raise AssertionError("wholesale loss must be refused")

    assert len(scene.objects) == 10, "nothing may be removed when refusing"


def test_deleting_nothing_changes_nothing() -> None:
    from blender_io.dynamic_bridge import remove_deleted_objects

    scene = _scene_with([("fence1", True), ("fence2", True)])
    assert remove_deleted_objects(scene, _collection_with(["fence1", "fence2"])) == 0
    assert len(scene.objects) == 2
