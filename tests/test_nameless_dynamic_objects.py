# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Dynamic records with no Name must survive an export.

MEASURED on r1m1 (2026-09-19): sixteen placed records carry no Name
attribute — eleven ``LightObject2``, four ``CameraPoint`` under
TheTown's nameless ``EntryPath``/``ExitPath``, and one ``Item``
(``BugForSale``, Pos="0.000 369.722 0.000") with five nameless
children of its own (Parts, Repository, BASKET, CABIN, CHASSIS). The
export matched Blender objects back to records by name, found no
Blender object called "" and took each of the sixteen for a deletion,
their subtrees with them: 21 records. The exported dynamicscene.xml
was the r1m1_22 copy minus exactly those; the editor died loading the
map right after ``DynamicScene.cpp[1470] Scene loading begin``.
Whether the missing records are what killed it is a hypothesis; that
they were dropped is not. The fixed bridge round-trips r1m1_22 with
nothing deleted: 5732 records in, 5732 out.

Each test here failed on the pre-fix bridge.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from core.coordinates import CoordinateTransform  # noqa: E402
from blender_io.dynamic_bridge import (  # noqa: E402
    DYNAMIC_KEY_PROP,
    DYNAMIC_PROP,
    build_dynamic_scene,
    dynamic_key,
    extract_dynamic_scene,
    remove_deleted_objects,
)
from formats.exm.dynamic_scene import (  # noqa: E402
    read_dynamic_scene,
    write_dynamic_scene,
)

# The shape of the r1m1 records, reduced: named scenery around nameless
# lights and one nameless item, all placed.
_SCENE = """<?xml version="1.0" encoding="windows-1251"?>
<Scene>
\t<Object Name="fence1" Belong="-1" Prototype="Breakable_WoodFence1" Pos="10.000 20.000 30.000" />
\t<LightObject2 Prototype="pointLight" Pos="100.000 5.000 100.000" Color="1.0 0.9 0.8" />
\t<LightObject2 Prototype="pointLight" Pos="200.000 5.000 100.000" Color="1.0 0.9 0.8" />
\t<Object Name="TheTown_Workshop" Prototype="workshop" Pos="300.000 0.000 300.000">
\t\t<Vehicles>
\t\t\t<Item Prototype="BugForSale" Pos="0.000 369.722 0.000">
\t\t\t\t<Parts>
\t\t\t\t\t<BASKET Prototype="bugCargo01" />
\t\t\t\t\t<CABIN Prototype="bugCab01" />
\t\t\t\t</Parts>
\t\t\t\t<Repository />
\t\t\t</Item>
\t\t</Vehicles>
\t\t<EntryPath>
\t\t\t<CameraPoint Pos="310.000 0.000 300.000" />
\t\t\t<CameraPoint Pos="320.000 0.000 300.000" />
\t\t</EntryPath>
\t</Object>
\t<Object Name="fence2" Belong="-1" Prototype="Breakable_WoodFence1" Pos="40.000 20.000 50.000" />
\t<Object Name="team1" Prototype="settlementTeam" />
</Scene>
"""


def _write(content: str) -> str:
    path = os.path.join(tempfile.mkdtemp(), "dynamicscene.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write(content)
    return path


def _built():
    scene = read_dynamic_scene(_write(_SCENE))
    collection = fake_bpy.FakeCollection("DynamicObjects")
    created = build_dynamic_scene(scene, collection, transform=CoordinateTransform())
    return scene, collection, created


def test_nameless_records_are_read_as_nameless() -> None:
    """The premise: the reader gives them "" — not None, not a
    generated name — so nothing downstream can mistake one for
    another."""
    scene = read_dynamic_scene(_write(_SCENE))
    placed_nameless = [o.tag for o in scene.walk() if not o.name and o.position is not None]
    assert placed_nameless == ["LightObject2", "LightObject2", "Item", "CameraPoint", "CameraPoint"]
    assert sum(1 for o in scene.walk() if not o.name) == 5 + 6, "plus the nameless containers"


def test_every_placed_record_gets_a_distinct_key() -> None:
    scene, _collection, created = _built()
    keys = [o[DYNAMIC_KEY_PROP] for o in created]
    assert len(keys) == len(set(keys)) == 8, keys
    # Named records keep their name as the key; nameless ones get their
    # walk index, so two identical lights stay two.
    assert {"fence1", "fence2", "TheTown_Workshop"} <= set(keys)
    assert len([k for k in keys if k.startswith("#")]) == 5


def test_an_export_with_nothing_deleted_keeps_the_nameless_records() -> None:
    scene, collection, _created = _built()
    before = len(list(scene.walk()))

    assert remove_deleted_objects(scene, collection) == 0
    updated = extract_dynamic_scene(scene, collection, transform=CoordinateTransform())

    assert len(list(updated.walk())) == before
    kept = [o.tag for o in updated.walk() if not o.name and o.position is not None]
    assert kept == ["LightObject2", "LightObject2", "Item", "CameraPoint", "CameraPoint"]
    item = next(o for o in updated.walk() if o.tag == "Item")
    assert [c.tag for c in item.children] == ["Parts", "Repository"], "the Item's subtree"
    assert [c.tag for c in item.children[0].children] == ["BASKET", "CABIN"]


def test_the_nameless_records_reach_the_file_unchanged() -> None:
    scene, collection, _created = _built()
    updated = extract_dynamic_scene(scene, collection, transform=CoordinateTransform())
    out = os.path.join(tempfile.mkdtemp(), "dynamicscene.xml")
    write_dynamic_scene(out, updated)

    with open(out, encoding="cp1251") as f:
        text = f.read()
    assert text.count("<LightObject2 ") == 2
    assert text.count("<CameraPoint ") == 2
    assert 'Prototype="BugForSale"' in text
    assert 'Prototype="bugCab01"' in text
    assert 'Pos="0.000 369.722 0.000"' in text
    assert 'Name=""' not in text, "a nameless record must not grow an empty Name"


def test_a_moved_nameless_record_is_written_at_its_new_position() -> None:
    """Matching by walk index is not only about survival: an edit to a
    nameless light has to land on that light and no other."""
    scene, collection, created = _built()
    second_light = next(o for o in created if o[DYNAMIC_KEY_PROP] == "#2")
    x, y, z = second_light.location
    second_light.location = (x + 50.0, y, z)

    updated = extract_dynamic_scene(scene, collection, transform=CoordinateTransform())
    lights = [o for o in updated.walk() if o.tag == "LightObject2"]
    assert abs(lights[0].position.x - 100.0) < 0.01, "the first light must not move"
    assert abs(lights[1].position.x - 250.0) < 0.01


def test_deleting_a_named_neighbour_does_not_take_a_nameless_record_with_it() -> None:
    scene, collection, created = _built()
    fence = next(o for o in created if o.name == "fence1")
    collection.objects.unlink(fence)

    removed = remove_deleted_objects(scene, collection)

    assert removed == 1
    assert [o.name for o in scene.walk() if o.name] == ["TheTown_Workshop", "fence2", "team1"]
    assert sum(1 for o in scene.walk() if not o.name and o.position is not None) == 5


def test_a_nameless_record_is_never_counted_as_deleted() -> None:
    """The wholesale-loss guard sees only the named records, because
    only those could have been deleted by anyone."""
    scene = read_dynamic_scene(_write(_SCENE))
    only_a_fence = fake_bpy.FakeCollection("DynamicObjects")
    fence = fake_bpy.FakeObject("fence1", None)
    fence[DYNAMIC_PROP] = 1
    only_a_fence.objects.link(fence)

    # Three named placed records, one present. Two of three is over the
    # wholesale-loss threshold, so this is refused — and the refusal
    # must count named records only: 2 of 3, not 7 of 8.
    from utils.errors import EXMeditorError
    try:
        remove_deleted_objects(scene, only_a_fence)
    except EXMeditorError as exc:
        assert "2 of 3" in exc.message, exc.message
    else:
        raise AssertionError("losing two of three named records must be refused")
    assert sum(1 for o in scene.walk() if not o.name and o.position is not None) == 5


def test_objects_built_before_the_key_existed_still_match_by_name() -> None:
    """A .blend saved by an earlier release has no exm_dynamic_key on
    its objects; the name is the key then, as it always was."""
    scene = read_dynamic_scene(_write(_SCENE))
    collection = fake_bpy.FakeCollection("DynamicObjects")
    for name in ("fence1", "TheTown_Workshop", "fence2"):
        obj = fake_bpy.FakeObject(name, None)
        obj[DYNAMIC_PROP] = 1
        collection.objects.link(obj)

    assert remove_deleted_objects(scene, collection) == 0
    assert dynamic_key(next(o for o in scene.walk() if o.name == "fence1"), 0) == "fence1"
