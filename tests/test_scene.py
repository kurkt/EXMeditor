# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `core/scene.py`'s tree-aware `MapScene`.

`world.xml` nests nodes, so `MapScene.objects` holds only top-level
nodes and descendants hang off `ObjectInstance.children`. These tests
pin that interface down before roads/paths/collision modules start
consuming it — per the v0.2 plan's "stabilize this before more code
depends on it" item.
"""

from __future__ import annotations

import array
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.objects import ObjectInstance  # noqa: E402
from core.scene import MapScene  # noqa: E402
from core.terrain import HeightmapData  # noqa: E402
from utils.math import Vector3  # noqa: E402


def _node(name: str, x: float = 0.0, y: float = 0.0, z: float = 0.0, cls: str = "SgNode") -> ObjectInstance:
    return ObjectInstance(name=name, node_class=cls, org=Vector3(x, y, z), org_rel=True)


def _nested_scene() -> MapScene:
    """A 3-level tree: root -> child -> grandchild, plus a second root."""
    grandchild = _node("Grandchild", 1, 1, 1)
    child = _node("Child", 2, 2, 2)
    child.children.append(grandchild)
    root = _node("Root", 3, 3, 3)
    root.children.append(child)
    other = _node("OtherRoot", 4, 4, 4)

    scene = MapScene()
    scene.objects.extend([root, other])
    return scene


def test_objects_holds_only_top_level_nodes() -> None:
    scene = _nested_scene()
    assert len(scene.objects) == 2
    assert [o.name for o in scene.objects] == ["Root", "OtherRoot"]


def test_walk_objects_yields_every_depth() -> None:
    scene = _nested_scene()
    names = [o.name for o in scene.walk_objects()]
    assert sorted(names) == sorted(["Root", "Child", "Grandchild", "OtherRoot"])


def test_object_count_counts_all_depths() -> None:
    assert _nested_scene().object_count() == 4


def test_find_object_searches_at_any_depth() -> None:
    scene = _nested_scene()
    assert scene.find_object("Root") is not None
    assert scene.find_object("Grandchild") is not None
    assert scene.find_object("Grandchild").org == Vector3(1, 1, 1)
    assert scene.find_object("NoSuchNode") is None


def test_add_object_top_level() -> None:
    scene = MapScene()
    scene.add_object(_node("A"))
    assert len(scene.objects) == 1
    assert scene.object_count() == 1


def test_add_object_as_child() -> None:
    scene = MapScene()
    parent = _node("Parent")
    scene.add_object(parent)
    scene.add_object(_node("Kid"), parent=parent)

    assert len(scene.objects) == 1  # still one TOP-LEVEL object
    assert scene.object_count() == 2  # but two objects overall
    assert scene.find_object("Kid") is not None


def test_remove_top_level_object() -> None:
    scene = _nested_scene()
    assert scene.remove_object("OtherRoot") is True
    assert scene.find_object("OtherRoot") is None
    assert scene.object_count() == 3


def test_remove_nested_object() -> None:
    scene = _nested_scene()
    assert scene.remove_object("Child") is True
    assert scene.find_object("Child") is None
    # removing a node removes its whole subtree
    assert scene.find_object("Grandchild") is None
    assert scene.object_count() == 2


def test_remove_missing_object_returns_false() -> None:
    scene = _nested_scene()
    assert scene.remove_object("NoSuchNode") is False
    assert scene.object_count() == 4


def test_bounding_box_empty_scene_is_none() -> None:
    assert MapScene().bounding_box() is None


def test_bounding_box_terrain_only() -> None:
    heightmap = HeightmapData(width=3, height=3, cell_size=2.0, values=array.array("f", range(9)))
    scene = MapScene(terrain=heightmap)
    box = scene.bounding_box()
    assert box is not None
    assert box.max_corner.x == 4.0  # (3-1) * cell_size 2.0


def test_bounding_box_objects_only_uses_top_level() -> None:
    """Nested children's `org` is parent-relative, so including it in a
    world-space AABB without composing the parent transform would be
    wrong — the box covers top-level objects only, by design."""
    scene = MapScene()
    root = _node("Root", 10, 0, 10)
    # a child at a small parent-RELATIVE offset; if it were wrongly
    # treated as world-space it would drag the box toward the origin
    root.children.append(_node("Child", 0, 0, 0))
    scene.add_object(root)
    scene.add_object(_node("Far", 100, 0, 100))

    box = scene.bounding_box()
    assert box is not None
    assert box.min_corner.x == 10  # NOT 0 — the child did not contribute
    assert box.max_corner.x == 100


def test_bounding_box_combines_terrain_and_objects() -> None:
    heightmap = HeightmapData(width=2, height=2, cell_size=1.0, values=array.array("f", [0, 0, 0, 0]))
    scene = MapScene(terrain=heightmap)
    scene.add_object(_node("Far", 50, 0, 50))
    box = scene.bounding_box()
    assert box is not None
    assert box.min_corner.x == 0.0
    assert box.max_corner.x == 50


def test_objects_without_org_are_skipped_in_bounding_box() -> None:
    """A plain SgNode container may have no `org` at all — it must not
    crash the AABB computation."""
    scene = MapScene()
    scene.objects.append(ObjectInstance(name="NoOrg", node_class="SgNode"))
    scene.add_object(_node("HasOrg", 5, 0, 5))
    box = scene.bounding_box()
    assert box is not None
    assert box.min_corner.x == 5


_ALL_TESTS = (
    test_objects_holds_only_top_level_nodes,
    test_walk_objects_yields_every_depth,
    test_object_count_counts_all_depths,
    test_find_object_searches_at_any_depth,
    test_add_object_top_level,
    test_add_object_as_child,
    test_remove_top_level_object,
    test_remove_nested_object,
    test_remove_missing_object_returns_false,
    test_bounding_box_empty_scene_is_none,
    test_bounding_box_terrain_only,
    test_bounding_box_objects_only_uses_top_level,
    test_bounding_box_combines_terrain_and_objects,
    test_objects_without_org_are_skipped_in_bounding_box,
)


if __name__ == "__main__":
    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            test_fn()
        except Exception as exc:  # noqa: BLE001 - test runner, want to catch everything
            failures += 1
            print(f"FAIL: {test_fn.__name__}: {exc}")
        else:
            print(f"PASS: {test_fn.__name__}")
    print(f"\n{len(_ALL_TESTS) - failures}/{len(_ALL_TESTS)} passed")
    sys.exit(1 if failures else 0)


def test_export_refuses_a_world_xml_that_changed_since_import() -> None:
    """write_world reproduces the SCENE, not the file, so a node the
    scene does not hold is a node the written file will not hold.

    Seen in practice: the SDK wrote a node, the game's editor saved and
    dropped it, an export from a scene imported before that save then
    dropped the editor's node too. Neither tool merges, so whoever
    writes last wins — and every measurement in between was taken on a
    file that had moved underneath.
    """
    import os
    import tempfile
    import time

    from formats.exm.plugin import _refuse_stale_overwrite, world_fingerprint
    from utils.errors import ValidationError

    path = os.path.join(tempfile.mkdtemp(), "world.xml")
    with open(path, "w") as handle:
        handle.write("<World/>")
    loaded = world_fingerprint(path)

    _refuse_stale_overwrite(path, loaded)  # unchanged: allowed

    time.sleep(1.1)
    with open(path, "w") as handle:
        handle.write("<World><Node/></World>")

    try:
        _refuse_stale_overwrite(path, loaded)
    except ValidationError as exc:
        assert "changed on disk" in exc.message
        assert "Re-import the map" in exc.message
    else:
        raise AssertionError("a stale export must be refused, not written")


def test_a_scene_never_loaded_from_disk_is_not_blocked() -> None:
    from formats.exm.plugin import _refuse_stale_overwrite

    _refuse_stale_overwrite("/nonexistent/world.xml", "")
