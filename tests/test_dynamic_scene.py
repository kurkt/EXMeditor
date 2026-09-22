# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the second placement layer and its prototype catalogue.

This layer was invisible to the SDK for a long time: every report said
"100% imported" and was telling the truth about ``world.xml`` while
4449 objects sat unread in another file, referenced by a name that
resolved nowhere in the map's own data.

The chain under test::

    dynamicscene.xml  Prototype="Breakable_WoodFence1"
        -> breakableobjects.xml  ModelFile="wood_fence1"
        -> AnimModels.xml        file="...wood_fence1_test.gam"
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.dynamic_bridge import (  # noqa: E402
    DYNAMIC_PROP,
    PROTOTYPE_PROP,
    build_dynamic_scene,
    extract_dynamic_scene,
)
from core.coordinates import CoordinateTransform  # noqa: E402
from core.prototypes import (  # noqa: E402
    PrototypeCatalog,
    Prototype,
    read_prototype_catalog,
    read_prototype_file,
)
from formats.exm.dynamic_scene import (  # noqa: E402
    read_dynamic_scene,
    resolve_model_id,
    write_dynamic_scene,
)
from formats.exm.model_catalog import ModelCatalog, ModelEntry  # noqa: E402
from utils.errors import ParsingError  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402

_SCENE = """<?xml version="1.0" encoding="windows-1251"?>
<DynamicScene>
\t<Object Name="fence1" Belong="-1" Prototype="Breakable_WoodFence1" Pos="10.000 20.000 30.000" Rot="0.000 0.500 0.000 0.866" />
\t<Object Name="fence2" Belong="-1" Prototype="Breakable_WoodFence1" Pos="40.000 20.000 50.000" />
\t<Object Name="lamp1" Prototype="lamppost2" Pos="1.000 2.000 3.000" />
\t<Object Name="zone1" Prototype="genericLocation" Pos="5.000 6.000 7.000" Radius="20.000" />
\t<Object Name="team1" Prototype="settlementTeam" />
\t<Object Name="npc1" Prototype="NPC" ModelName="r1_man" Pos="8.000 9.000 10.000" />
\t<Object Name="withPoints" Prototype="Something" Pos="0.000 0.000 0.000">
\t\t<Point Pos="1220.000 2970.000" />
\t</Object>
</DynamicScene>
"""

_PROTOTYPES = """<?xml version="1.0" encoding="windows-1251"?>
<Prototypes>
\t<Prototype Class="BreakableObject" Name="Breakable_WoodFence1" ModelFile="wood_fence1"
\t\tBrokenModel="wood_fence1_broken" Mass="100" />
\t<Prototype Class="BreakableObject" Name="Breakable_Dub3" ModelFile="dub3" />
\t<Prototype Class="Team" Name="settlementTeam" />
</Prototypes>
"""


def _write(content: str, name: str = "dynamicscene.xml") -> str:
    path = os.path.join(tempfile.mkdtemp(), name)
    with open(path, "w", encoding="cp1251") as f:
        f.write(content)
    return path


def _model_catalog() -> ModelCatalog:
    catalog = ModelCatalog()
    for model_id in ("wood_fence1", "dub3", "lamppost2", "r1_man"):
        catalog.add(ModelEntry(model_id=model_id, file_path=f"data\\models\\{model_id}.gam"))
    return catalog


def _prototype_catalog() -> PrototypeCatalog:
    catalog = PrototypeCatalog()
    for prototype in read_prototype_file(_write(_PROTOTYPES, "breakableobjects.xml")):
        catalog.add(prototype)
    return catalog


# --- prototype catalogue ---


def test_reads_prototype_definitions() -> None:
    prototypes = read_prototype_file(_write(_PROTOTYPES, "breakableobjects.xml"))
    by_name = {p.name: p for p in prototypes}
    assert by_name["Breakable_WoodFence1"].model_id == "wood_fence1"
    assert by_name["Breakable_WoodFence1"].prototype_class == "BreakableObject"
    assert by_name["Breakable_WoodFence1"].broken_model == "wood_fence1_broken"


def test_prototypes_without_a_model_are_kept() -> None:
    """Gameplay-only prototypes (teams, zones) legitimately have no
    model; dropping them would make a missing definition look the same
    as a definition that simply isn't visual."""
    catalog = _prototype_catalog()
    team = catalog.get("settlementTeam")
    assert team is not None
    assert not team.has_model


def test_unmodelled_prototype_attributes_are_preserved() -> None:
    catalog = _prototype_catalog()
    assert catalog.get("Breakable_WoodFence1").raw_attrs["Mass"] == "100"


def test_malformed_prototype_file_does_not_lose_the_others() -> None:
    """The directory holds ~22 files covering unrelated categories.
    Losing all of them because one is malformed would be a poor
    trade."""
    folder = tempfile.mkdtemp()
    root = os.path.join(folder, "data", "gamedata", "gameobjects")
    os.makedirs(root)
    with open(os.path.join(root, "good.xml"), "w", encoding="cp1251") as f:
        f.write(_PROTOTYPES)
    with open(os.path.join(root, "broken.xml"), "w", encoding="cp1251") as f:
        f.write("<Prototypes><Prototype")

    catalog = read_prototype_catalog(folder)
    assert catalog.get("Breakable_WoodFence1") is not None


def test_missing_prototype_directory_is_not_fatal() -> None:
    assert len(read_prototype_catalog(tempfile.mkdtemp())) == 0


# --- dynamicscene codec ---


def test_reads_placed_objects() -> None:
    scene = read_dynamic_scene(_write(_SCENE))
    assert scene.object_count() == 8   # 7 objects + 1 nested Point
    placed = scene.placed()
    assert len(placed) == 6            # team1 has no Pos; Point's Pos is 2D


def test_two_component_pos_is_not_read_as_a_position() -> None:
    """<Point> uses a two-number Pos — a horizontal pair, not a
    placement. Reading it as a 3D point would misplace it; rejecting
    the file outright would lose the whole layer."""
    scene = read_dynamic_scene(_write(_SCENE))
    point = next(o for o in scene.walk() if o.tag == "Point")
    assert point.position is None
    assert point.raw_attrs["Pos"] == "1220.000 2970.000"


def test_gameplay_attributes_are_preserved() -> None:
    scene = read_dynamic_scene(_write(_SCENE))
    zone = next(o for o in scene.walk() if o.name == "zone1")
    assert zone.raw_attrs["Radius"] == "20.000"


def test_round_trip_preserves_everything() -> None:
    scene = read_dynamic_scene(_write(_SCENE))
    out = os.path.join(tempfile.mkdtemp(), "dynamicscene.xml")
    write_dynamic_scene(out, scene)
    reloaded = read_dynamic_scene(out)

    original = list(scene.walk())
    written = list(reloaded.walk())
    assert len(original) == len(written)
    for a, b in zip(original, written):
        assert a.name == b.name
        assert a.prototype == b.prototype
        assert a.tag == b.tag
        assert (a.position is None) == (b.position is None)
        if a.position is not None:
            for p, q in zip(a.position.as_tuple(), b.position.as_tuple()):
                assert abs(p - q) < 0.002


def test_rejects_malformed_xml() -> None:
    try:
        read_dynamic_scene(_write("<DynamicScene><Object"))
        raise AssertionError("expected ParsingError")
    except ParsingError:
        pass


# --- model resolution ---


def test_prototype_resolves_through_the_definition_file() -> None:
    """The chain the whole layer depends on."""
    scene = read_dynamic_scene(_write(_SCENE))
    fence = next(o for o in scene.walk() if o.name == "fence1")
    assert resolve_model_id(fence, _prototype_catalog(), _model_catalog()) == "wood_fence1"


def test_prototype_that_is_itself_a_model_id_resolves() -> None:
    """A handful name a model directly and need no definition file."""
    scene = read_dynamic_scene(_write(_SCENE))
    lamp = next(o for o in scene.walk() if o.name == "lamp1")
    assert resolve_model_id(lamp, _prototype_catalog(), _model_catalog()) == "lamppost2"


def test_explicit_model_name_wins_over_the_prototype() -> None:
    """NPCs name their model directly; an explicit value is more
    specific than an inherited one."""
    scene = read_dynamic_scene(_write(_SCENE))
    npc = next(o for o in scene.walk() if o.name == "npc1")
    assert resolve_model_id(npc, _prototype_catalog(), _model_catalog()) == "r1_man"


def test_gameplay_only_objects_resolve_to_nothing() -> None:
    scene = read_dynamic_scene(_write(_SCENE))
    zone = next(o for o in scene.walk() if o.name == "zone1")
    assert resolve_model_id(zone, _prototype_catalog(), _model_catalog()) is None


def test_resolution_works_without_a_prototype_catalogue() -> None:
    scene = read_dynamic_scene(_write(_SCENE))
    lamp = next(o for o in scene.walk() if o.name == "lamp1")
    assert resolve_model_id(lamp, None, _model_catalog()) == "lamppost2"


# --- Blender bridge ---


def test_builds_only_placed_objects() -> None:
    scene = read_dynamic_scene(_write(_SCENE))
    collection = fake_bpy.FakeCollection("DynamicObjects")
    created = build_dynamic_scene(scene, collection, transform=CoordinateTransform())
    assert len(created) == 6   # team1 and the 2D Point are not placeable
    assert all(DYNAMIC_PROP in o for o in created)


def test_prototype_is_recorded_on_the_object() -> None:
    scene = read_dynamic_scene(_write(_SCENE))
    collection = fake_bpy.FakeCollection("DynamicObjects")
    created = build_dynamic_scene(scene, collection, transform=CoordinateTransform())
    fence = next(o for o in created if o.name == "fence1")
    assert fence[PROTOTYPE_PROP] == "Breakable_WoodFence1"


def test_positions_use_the_shared_transform() -> None:
    scene = read_dynamic_scene(_write(_SCENE))
    collection = fake_bpy.FakeCollection("DynamicObjects")
    transform = CoordinateTransform(xy_scale=2.0, height_scale=2.0)
    created = build_dynamic_scene(scene, collection, transform=transform)

    fence = next(o for o in created if o.name == "fence1")
    # source Pos is (10, 20, 30): game Z -> Blender Y, game Y -> Blender Z
    assert fence.location[0] == 20.0
    assert fence.location[1] == 60.0
    assert fence.location[2] == 40.0


def test_edits_round_trip_back_into_the_original_scene() -> None:
    """Positions edited in Blender must reach the file, while the
    gameplay structures Blender cannot represent stay untouched."""
    scene = read_dynamic_scene(_write(_SCENE))
    collection = fake_bpy.FakeCollection("DynamicObjects")
    transform = CoordinateTransform(xy_scale=2.0, height_scale=2.0)
    created = build_dynamic_scene(scene, collection, transform=transform)

    fence = next(o for o in created if o.name == "fence1")
    x, y, z = fence.location
    fence.location = (x + 20.0, y, z)   # +10 game units at scale 2

    updated = extract_dynamic_scene(scene, collection, transform=transform)
    edited = next(o for o in updated.walk() if o.name == "fence1")
    assert abs(edited.position.x - 20.0) < 0.01

    # the nested Point, which has no Blender representation, survived
    point = next(o for o in updated.walk() if o.tag == "Point")
    assert point.raw_attrs["Pos"] == "1220.000 2970.000"


def test_untouched_objects_keep_their_positions() -> None:
    scene = read_dynamic_scene(_write(_SCENE))
    collection = fake_bpy.FakeCollection("DynamicObjects")
    transform = CoordinateTransform()
    build_dynamic_scene(scene, collection, transform=transform)

    updated = extract_dynamic_scene(scene, collection, transform=transform)
    fence2 = next(o for o in updated.walk() if o.name == "fence2")
    assert abs(fence2.position.x - 40.0) < 0.01


# --- real data ---


def test_real_map_layer_resolves_most_objects() -> None:
    real = corpus("dynamicscene.xml")
    definitions = corpus("breakableobjects.xml")
    models = corpus("animmodels.xml")
    if not all(os.path.isfile(p) for p in (real, definitions, models)):
        return

    from formats.exm.model_catalog import read_model_catalog

    scene = read_dynamic_scene(real)
    catalog = PrototypeCatalog()
    for prototype in read_prototype_file(definitions):
        catalog.add(prototype)
    models_catalog = read_model_catalog(models)

    placed = scene.placed()
    resolved = sum(1 for o in placed if resolve_model_id(o, catalog, models_catalog))
    assert len(placed) > 4000
    assert resolved > 3000, f"only {resolved} of {len(placed)} resolved"


_ALL_TESTS = (
    test_reads_prototype_definitions,
    test_prototypes_without_a_model_are_kept,
    test_unmodelled_prototype_attributes_are_preserved,
    test_malformed_prototype_file_does_not_lose_the_others,
    test_missing_prototype_directory_is_not_fatal,
    test_reads_placed_objects,
    test_two_component_pos_is_not_read_as_a_position,
    test_gameplay_attributes_are_preserved,
    test_round_trip_preserves_everything,
    test_rejects_malformed_xml,
    test_prototype_resolves_through_the_definition_file,
    test_prototype_that_is_itself_a_model_id_resolves,
    test_explicit_model_name_wins_over_the_prototype,
    test_gameplay_only_objects_resolve_to_nothing,
    test_resolution_works_without_a_prototype_catalogue,
    test_builds_only_placed_objects,
    test_prototype_is_recorded_on_the_object,
    test_positions_use_the_shared_transform,
    test_edits_round_trip_back_into_the_original_scene,
    test_untouched_objects_keep_their_positions,
    test_real_map_layer_resolves_most_objects,
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
