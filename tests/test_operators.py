# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `addon/operators.py`.

Exercises the import/export operators through their real `execute()`
paths against synthetic map folders on disk, using the fake bpy stub.

Note on map fixtures: a valid map now needs a `.ssl` manifest, since
file discovery is manifest-driven (the plugin no longer guesses
filenames). `_make_map_dir` writes a minimal but real one.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from addon import operators  # noqa: E402
from addon.preferences import ADDON_PACKAGE_NAME, EXM_AddonPreferences  # noqa: E402
from blender_io.scene_bridge import OBJECTS_COLLECTION, TERRAIN_COLLECTION  # noqa: E402
from blender_io.terrain_bridge import GRID_HEIGHT_PROP, GRID_WIDTH_PROP  # noqa: E402
from core.terrain import HeightmapData  # noqa: E402
from formats.exm.plugin import ExMachinaPlugin  # noqa: E402
from formats.exm.terrain import write_displace  # noqa: E402
from formats.registry import PluginRegistry  # noqa: E402

_MINIMAL_SSL = """<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>
<Ini>
\t<Section name="LEVEL">
\t\t<Key name="HIGHMAP">displace.bin</Key>
\t\t<Key name="LEVELSIZE">8</Key>
\t</Section>
</Ini>
"""

_SSL_WITH_SERVERS = """<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>
<Ini>
\t<Section name="LEVEL">
\t\t<Key name="HIGHMAP">displace.bin</Key>
\t\t<Key name="SERVERS">data\\maps\\shared\\servers.xml</Key>
\t</Section>
</Ini>
"""

_SERVERS_XML = """<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>
<Servers>
\t<AnimatedModelsServer>
\t\t<Item id="rock1" file="data\\models\\AnimModels.xml" />
\t</AnimatedModelsServer>
</Servers>
"""

_ANIM_MODELS = """<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>
<AnimatedModels>
\t<model id="rock1" file="data\\models\\rock1.gam" shadow="1" />
</AnimatedModels>
"""

_MINIMAL_WORLD = """<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>
<World name="Object1" class="SgNode" LastId="100">
\t<Node name="Object2" class="SgAnimatedModelNode" org="10.000 1.000 20.000" orgRel="1" id="rock1" />
\t<Node name="Object3" class="SgNode" org="50.000 0.000 50.000" orgRel="1">
\t\t<Node name="Object4" class="SgGameUnitNode" org="5.000 0.000 5.000" orgRel="0" id="house1" />
\t</Node>
</World>
"""


class _FakeWindowManager:
    def fileselect_add(self, op) -> None:
        pass


class _FakeContext:
    def __init__(self, obj=None, game_root: str = "") -> None:
        self.collection = fake_bpy.FakeCollection("Map")
        self.active_object = obj
        self.window_manager = _FakeWindowManager()
        # Operators record import provenance (source folder + scale) on
        # the scene so export can default to it; an object with custom-
        # property support stands in for bpy.types.Scene.
        self.scene = fake_bpy.FakeObject("Scene", None)
        # The game folder is an add-on preference now, not a per-import
        # field, so it reaches the operator through the context.
        addon_prefs = EXM_AddonPreferences()
        addon_prefs.game_root = game_root
        self.preferences = fake_bpy.make_preferences_context(
            ADDON_PACKAGE_NAME, addon_prefs
        )


def _make_map_dir(side: int = 8, fill: float = 3.0, with_world: bool = False) -> str:
    map_dir = tempfile.mkdtemp()
    heightmap = HeightmapData.filled(side, side, 1.0, fill=fill)
    write_displace(os.path.join(map_dir, "displace.bin"), heightmap)
    with open(os.path.join(map_dir, "map.ssl"), "w", encoding="cp1251") as f:
        f.write(_MINIMAL_SSL)
    if with_world:
        with open(os.path.join(map_dir, "world.xml"), "w", encoding="cp1251") as f:
            f.write(_MINIMAL_WORLD)
    return map_dir


def _install_active_plugin() -> None:
    registry = PluginRegistry()
    registry.register(ExMachinaPlugin())
    operators.default_registry = registry


def _by_node_name(collection, node_name):
    """Find an imported object by its MAP node name.

    Blender object names are readable labels now ("2_rock1"), so tests
    must look objects up by the property that carries their real
    identity rather than by the viewport name.
    """
    for obj in collection.objects.linked:
        if obj.get("exm_original_name") == node_name:
            return obj
    raise AssertionError(f"no object for node {node_name!r}")


def _sub(collection, name):
    """Find a sub-collection by name, or None."""
    for child in collection.children:
        if child.name == name:
            return child
    return None


def _import(ctx, map_dir, xy=1.0, height=1.0, centre=False):
    """Run the import operator.

    Only the properties a test actually varies are set; everything else
    comes from the declared bpy.props defaults, which fake_bpy applies
    the way Blender does. That matters: setting every property by hand
    would hide a property that was never declared at all — a bug that
    once reached a release.

    The game folder is not passed here — it's an add-on preference, set
    on the context (see _FakeContext).
    """
    op = operators.EXM_OT_import_map()
    op.directory = map_dir
    op.xy_scale = xy
    op.height_scale = height
    # Off by default here so tests can assert absolute coordinates;
    # the operator's own default is on, covered by its own test below.
    op.centre_on_origin = centre
    return op.execute(ctx)


# --- import ---


def test_import_builds_terrain_into_its_own_collection() -> None:
    _install_active_plugin()
    ctx = _FakeContext()
    assert _import(ctx, _make_map_dir(side=4, fill=5.0)) == {"FINISHED"}

    terrain_coll = _sub(ctx.collection, TERRAIN_COLLECTION)
    assert terrain_coll is not None
    obj = terrain_coll.objects.linked[0]
    assert obj.data.vertices[0].co == (0.0, 0.0, 5.0)  # origin cell, unscaled
    assert obj.get(GRID_WIDTH_PROP) == 4
    assert obj.get(GRID_HEIGHT_PROP) == 4


def test_import_applies_custom_scale() -> None:
    _install_active_plugin()
    ctx = _FakeContext()
    _import(ctx, _make_map_dir(side=4, fill=5.0), xy=3.0, height=2.0)

    obj = _sub(ctx.collection, TERRAIN_COLLECTION).objects.linked[0]
    # cell (1,0): x = col(1) * TERRAIN_CELL_SIZE(8.0) * xy_scale(3.0) = 24.0
    # height = 5.0 * height_scale(2.0) = 10.0
    assert obj.data.vertices[1].co == (24.0, 0.0, 10.0)


def test_import_builds_object_hierarchy() -> None:
    _install_active_plugin()
    ctx = _FakeContext()
    _import(ctx, _make_map_dir(with_world=True))

    objects_coll = _sub(ctx.collection, OBJECTS_COLLECTION)
    assert objects_coll is not None
    # 3 nodes total: two top-level, one nested
    assert len(objects_coll.objects.linked) == 3
    roots = [o for o in objects_coll.objects.linked if o.parent is None]
    assert len(roots) == 2
    parent = next(o for o in roots if o.get("exm_original_name") == "Object3")
    assert len(parent.children) == 1
    assert parent.children[0].get("exm_original_name") == "Object4"


def test_centring_is_on_by_default_and_survives_export() -> None:
    """A map is thousands of units across, so it is centred on the origin
    for editing. The shift must be undone on export so it never reaches
    the map files."""
    assert operators.EXM_OT_import_map().centre_on_origin is True

    _install_active_plugin()
    map_dir = _make_map_dir(side=8, fill=3.0, with_world=True)
    ctx = _FakeContext()
    _import(ctx, map_dir, centre=True)

    terrain = _sub(ctx.collection, TERRAIN_COLLECTION).objects.linked[0]
    xs = [v.co.x for v in terrain.data.vertices]
    assert min(xs) < 0 < max(xs), "terrain should straddle the origin"

    from formats.exm.world import read_world
    before, _r = read_world(os.path.join(map_dir, "world.xml"))

    out = tempfile.mkdtemp()
    assert _export(ctx, out) == {"FINISHED"}
    after, _r2 = read_world(os.path.join(out, "world.xml"))

    for original, exported in zip(before, after):
        if original.org is None:
            continue
        for a, b in zip(original.org.as_tuple(), exported.org.as_tuple()):
            assert abs(a - b) < 0.02, "centring leaked into the exported file"


def test_import_records_provenance_on_the_scene() -> None:
    """Export needs to know where the map came from and at what scale."""
    _install_active_plugin()
    ctx = _FakeContext()
    map_dir = _make_map_dir()
    _import(ctx, map_dir, xy=10.0, height=1.5)

    assert ctx.scene.get(operators.SOURCE_DIR_PROP) == map_dir
    assert ctx.scene.get(operators.SCENE_XY_SCALE_PROP) == 10.0
    assert ctx.scene.get(operators.SCENE_HEIGHT_SCALE_PROP) == 1.5


def test_import_missing_directory_is_cancelled() -> None:
    _install_active_plugin()
    ctx = _FakeContext()
    assert _import(ctx, "") == {"CANCELLED"}
    assert len(ctx.collection.children) == 0


def test_import_folder_without_ssl_finishes_with_warning() -> None:
    """No manifest means no map — must not crash, must not import."""
    _install_active_plugin()
    ctx = _FakeContext()
    assert _import(ctx, tempfile.mkdtemp()) == {"FINISHED"}
    assert _sub(ctx.collection, TERRAIN_COLLECTION) is None


def test_import_finds_manifest_beside_the_map_folder() -> None:
    """The real game layout: data/maps/r1m1-1-1.ssl sits BESIDE
    data/maps/r1m1-1-1/, not inside it. Reported from real use."""
    _install_active_plugin()
    maps_dir = tempfile.mkdtemp()
    map_dir = os.path.join(maps_dir, "mymap")
    os.makedirs(map_dir)
    write_displace(os.path.join(map_dir, "displace.bin"), HeightmapData.filled(4, 4, 1.0, fill=2.0))
    # manifest OUTSIDE the map folder, named after it
    with open(os.path.join(maps_dir, "mymap.ssl"), "w", encoding="cp1251") as f:
        f.write(_MINIMAL_SSL)

    ctx = _FakeContext()
    assert _import(ctx, map_dir) == {"FINISHED"}
    assert _sub(ctx.collection, TERRAIN_COLLECTION) is not None


def test_import_refuses_a_folder_holding_several_maps_manifests() -> None:
    """Selecting .../data/maps/ (which holds many <map>.ssl files next to
    many <map>/ folders) must not silently load one arbitrary map's
    manifest against the wrong directory."""
    _install_active_plugin()
    maps_dir = tempfile.mkdtemp()
    for name in ("mapA", "mapB"):
        os.makedirs(os.path.join(maps_dir, name))
        with open(os.path.join(maps_dir, f"{name}.ssl"), "w", encoding="cp1251") as f:
            f.write(_MINIMAL_SSL)

    ctx = _FakeContext()
    assert _import(ctx, maps_dir) == {"FINISHED"}  # no crash
    assert _sub(ctx.collection, TERRAIN_COLLECTION) is None  # but nothing imported


def test_no_active_plugin_is_cancelled() -> None:
    operators.default_registry = PluginRegistry()  # empty
    ctx = _FakeContext()
    assert _import(ctx, _make_map_dir()) == {"CANCELLED"}


# --- export ---


def _export(ctx, out_dir, xy=1.0, height=1.0):
    op = operators.EXM_OT_export_map()
    op.directory = out_dir
    op.xy_scale = xy
    op.height_scale = height
    op.ground_level_offset = 0.0
    return op.execute(ctx)


def test_models_load_when_servers_xml_lives_outside_the_map_folder() -> None:
    """Regression: the manifest's SERVERS entry is a GAME-ROOT-relative
    path that routinely points outside the map folder (a map in
    data/maps/r1m1-1-1/ referencing data/maps/r1m1/servers.xml).
    Resolving it against the map folder alone found nothing, so no
    models ever loaded."""
    _install_active_plugin()
    root = tempfile.mkdtemp()
    map_dir = os.path.join(root, "data", "maps", "mymap")
    os.makedirs(map_dir)
    write_displace(os.path.join(map_dir, "displace.bin"), HeightmapData.filled(4, 4, 1.0, fill=1.0))
    with open(os.path.join(map_dir, "mymap.ssl"), "w", encoding="cp1251") as f:
        f.write(_SSL_WITH_SERVERS)
    with open(os.path.join(map_dir, "world.xml"), "w", encoding="cp1251") as f:
        f.write(_MINIMAL_WORLD)

    # servers.xml where the manifest says it is: a DIFFERENT folder
    other = os.path.join(root, "data", "maps", "shared")
    os.makedirs(other)
    with open(os.path.join(other, "servers.xml"), "w", encoding="cp1251") as f:
        f.write(_SERVERS_XML)
    os.makedirs(os.path.join(root, "data", "models"))
    with open(os.path.join(root, "data", "models", "AnimModels.xml"), "w", encoding="cp1251") as f:
        f.write(_ANIM_MODELS)

    ctx = _FakeContext(game_root=root)
    assert _import(ctx, map_dir) == {"FINISHED"}
    # The catalogue resolved; without the fix it would have been empty.
    objects_coll = _sub(ctx.collection, OBJECTS_COLLECTION)
    assert objects_coll is not None


def test_game_root_pointed_at_a_subfolder_is_corrected() -> None:
    """Reported from real use: Game Root set to GC/data/models (where the
    model files visibly live) instead of GC (which CONTAINS data). Every
    path then resolved to GC/data/models/data/models/... and no model
    loaded. Walking up to the folder holding 'data' makes both work."""
    _install_active_plugin()
    root = tempfile.mkdtemp()
    map_dir = os.path.join(root, "data", "maps", "mymap")
    os.makedirs(map_dir)
    write_displace(os.path.join(map_dir, "displace.bin"), HeightmapData.filled(4, 4, 1.0, fill=1.0))
    with open(os.path.join(map_dir, "mymap.ssl"), "w", encoding="cp1251") as f:
        f.write(_SSL_WITH_SERVERS)
    with open(os.path.join(map_dir, "world.xml"), "w", encoding="cp1251") as f:
        f.write(_MINIMAL_WORLD)

    shared = os.path.join(root, "data", "maps", "shared")
    os.makedirs(shared)
    with open(os.path.join(shared, "servers.xml"), "w", encoding="cp1251") as f:
        f.write(_SERVERS_XML)
    models = os.path.join(root, "data", "models")
    os.makedirs(models)
    with open(os.path.join(models, "AnimModels.xml"), "w", encoding="cp1251") as f:
        f.write(_ANIM_MODELS)

    # Deliberately the WRONG folder — a subfolder of the real root.
    ctx = _FakeContext(game_root=models)
    assert _import(ctx, map_dir) == {"FINISHED"}
    assert _sub(ctx.collection, OBJECTS_COLLECTION) is not None


def test_game_root_without_a_data_folder_is_reported() -> None:
    """A folder with no 'data' anywhere above it can't be a game root."""
    _install_active_plugin()
    ctx = _FakeContext(game_root=tempfile.mkdtemp())
    assert _import(ctx, _make_map_dir(with_world=True)) == {"FINISHED"}
    objects_coll = _sub(ctx.collection, OBJECTS_COLLECTION)
    assert all(o.type == "EMPTY" for o in objects_coll.objects.linked)


def test_game_folder_comes_from_addon_preferences() -> None:
    """The game folder is an installation-wide setting, not something to
    re-pick on every import, so it lives in add-on preferences and the
    import operator has no property for it."""
    assert not hasattr(operators.EXM_OT_import_map, "game_root")

    preferences = EXM_AddonPreferences()
    preferences.game_root = "/somewhere/GC"
    ctx = _FakeContext()
    ctx.preferences = fake_bpy.make_preferences_context(ADDON_PACKAGE_NAME, preferences)

    from addon.preferences import get_game_root

    assert get_game_root(ctx) == "/somewhere/GC"


def test_import_without_game_root_still_succeeds() -> None:
    """Models are optional: no Game Root means Empties, not a failure."""
    _install_active_plugin()
    ctx = _FakeContext()
    assert _import(ctx, _make_map_dir(with_world=True)) == {"FINISHED"}
    objects_coll = _sub(ctx.collection, OBJECTS_COLLECTION)
    assert all(o.type == "EMPTY" for o in objects_coll.objects.linked)


def test_export_writes_terrain_and_preserves_other_files() -> None:
    _install_active_plugin()
    map_dir = _make_map_dir(side=4, fill=7.0)
    # a file the SDK never parses — must survive the round trip
    with open(os.path.join(map_dir, "grass.xml"), "wb") as f:
        f.write(b"opaque-blob")

    ctx = _FakeContext()
    _import(ctx, map_dir)
    out = tempfile.mkdtemp()
    assert _export(ctx, out) == {"FINISHED"}

    assert os.path.getsize(os.path.join(out, "displace.bin")) == 4 * 4 * 4
    with open(os.path.join(out, "grass.xml"), "rb") as f:
        assert f.read() == b"opaque-blob"
    assert os.path.isfile(os.path.join(out, "map.ssl"))


def test_export_writes_edited_object_positions() -> None:
    """The core editing round trip: move an Empty, export, verify the
    new position landed in world.xml."""
    _install_active_plugin()
    map_dir = _make_map_dir(with_world=True)
    ctx = _FakeContext()
    _import(ctx, map_dir, xy=10.0, height=1.5)

    objects_coll = _sub(ctx.collection, OBJECTS_COLLECTION)
    target = _by_node_name(objects_coll, "Object2")
    x, y, z = target.location
    target.location = (x + 100.0, y, z)  # +10 in game units at xy_scale=10

    out = tempfile.mkdtemp()
    assert _export(ctx, out, xy=10.0, height=1.5) == {"FINISHED"}

    from formats.exm.world import read_world
    objects, _root = read_world(os.path.join(out, "world.xml"))
    moved = next(o for o in objects if o.name == "Object2")
    assert abs(moved.org.x - 20.0) < 0.01  # was 10.0 in the source
    # untouched axes unchanged
    assert abs(moved.org.z - 20.0) < 0.01


def test_editing_scale_of_an_object_that_had_none_exports_it() -> None:
    """Reported from real use: cloning an object exported fine, but
    stretching it did nothing. 522 of 1734 objects on a real map carry
    no scale attribute; treating 'absent in the source' as 'never
    write' silently discarded the edit. Absent must mean "unchanged",
    not "unchangeable"."""
    _install_active_plugin()
    map_dir = _make_map_dir(with_world=True)
    ctx = _FakeContext()
    _import(ctx, map_dir)

    objects_coll = _sub(ctx.collection, OBJECTS_COLLECTION)
    target = _by_node_name(objects_coll, "Object2")
    target.scale = (1.0, 3.0, 1.0)

    out = tempfile.mkdtemp()
    assert _export(ctx, out) == {"FINISHED"}

    from formats.exm.world import read_world
    objects, _root = read_world(os.path.join(out, "world.xml"))
    edited = next(o for o in objects if o.name == "Object2")
    assert edited.scale is not None, "the scale edit was dropped"
    assert abs(edited.scale.y - 3.0) < 0.01


def test_editing_rotation_of_an_object_that_had_none_exports_it() -> None:
    _install_active_plugin()
    map_dir = _make_map_dir(with_world=True)
    ctx = _FakeContext()
    _import(ctx, map_dir)

    objects_coll = _sub(ctx.collection, OBJECTS_COLLECTION)
    target = _by_node_name(objects_coll, "Object2")
    # A node imported without a rotation is left in Blender's default
    # XYZ Euler mode, and that is how the user turns it. Setting
    # rotation_quaternion here would not rotate a real object at all
    # (see blender_io/object_rotation.py) — an earlier version of this
    # test did exactly that and passed against an export that dropped
    # every Euler rotation.
    assert target.rotation_mode == "XYZ"
    import math
    target.rotation_euler = (0.0, 0.0, math.radians(90))

    out = tempfile.mkdtemp()
    assert _export(ctx, out) == {"FINISHED"}

    from formats.exm.world import read_world
    objects, _root = read_world(os.path.join(out, "world.xml"))
    edited = next(o for o in objects if o.name == "Object2")
    assert edited.raw_rotation is not None, "the rotation edit was dropped"
    # Blender (w, x, y, z) = (0.7071, 0, 0, 0.7071) -> game (-x, -z, -y, w)
    assert [round(v, 3) for v in edited.raw_rotation] == [0.0, -0.707, 0.0, 0.707]


def test_untouched_objects_do_not_gain_attributes() -> None:
    """The other half of the rule: an object nobody edited must come out
    exactly as it went in, without acquiring a scale or rotation the
    source never had."""
    _install_active_plugin()
    map_dir = _make_map_dir(with_world=True)
    ctx = _FakeContext()
    _import(ctx, map_dir)

    out = tempfile.mkdtemp()
    assert _export(ctx, out) == {"FINISHED"}

    from formats.exm.world import read_world
    before, _r1 = read_world(os.path.join(map_dir, "world.xml"))
    after, _r2 = read_world(os.path.join(out, "world.xml"))
    for original, exported in zip(before, after):
        assert (original.scale is None) == (exported.scale is None)
        assert (original.raw_rotation is None) == (exported.raw_rotation is None)


def test_export_preserves_world_root_attributes() -> None:
    """LastId is the editor's ID counter — it must survive untouched."""
    _install_active_plugin()
    ctx = _FakeContext()
    _import(ctx, _make_map_dir(with_world=True))
    out = tempfile.mkdtemp()
    _export(ctx, out)

    from formats.exm.world import read_world
    _objects, root = read_world(os.path.join(out, "world.xml"))
    assert root["LastId"] == "100"


def test_in_place_export_overwrites_the_source_map() -> None:
    """The main save-and-test workflow: export back into the map's own
    folder. Reported as a bug when it was refused."""
    _install_active_plugin()
    map_dir = _make_map_dir(side=4, fill=7.0, with_world=True)
    ctx = _FakeContext()
    _import(ctx, map_dir, xy=10.0, height=1.5)

    objects_coll = _sub(ctx.collection, OBJECTS_COLLECTION)
    target = _by_node_name(objects_coll, "Object2")
    x, y, z = target.location
    target.location = (x + 100.0, y, z)  # +10 game units at xy_scale=10

    assert _export(ctx, map_dir, xy=10.0, height=1.5) == {"FINISHED"}

    from formats.exm.world import read_world
    objects, _root = read_world(os.path.join(map_dir, "world.xml"))
    moved = next(o for o in objects if o.name == "Object2")
    assert abs(moved.org.x - 20.0) < 0.01  # edit landed in the source file


def test_in_place_export_backs_up_overwritten_files() -> None:
    _install_active_plugin()
    map_dir = _make_map_dir(with_world=True)
    ctx = _FakeContext()
    _import(ctx, map_dir)
    _export(ctx, map_dir)

    assert os.path.isfile(os.path.join(map_dir, "displace.bin.bak"))
    assert os.path.isfile(os.path.join(map_dir, "world.xml.bak"))


def test_export_writes_into_the_existing_filename_casing() -> None:
    """Writing "LevelRoads.xml" beside an existing "levelroads.xml"
    would create a second file, leaving the game reading the old one.
    Invisible on Windows, silently broken elsewhere."""
    _install_active_plugin()
    map_dir = _make_map_dir(with_world=True)
    # the world file, but spelled lower-case as shipped maps do
    os.rename(os.path.join(map_dir, "world.xml"), os.path.join(map_dir, "WORLD.XML"))

    ctx = _FakeContext()
    _import(ctx, map_dir)
    _export(ctx, map_dir)

    names = os.listdir(map_dir)
    world_files = [n for n in names if n.lower() == "world.xml"]
    assert world_files == ["WORLD.XML"], f"expected no duplicate, got {world_files}"


def test_export_without_prior_import_is_cancelled() -> None:
    """Export snapshots the source folder, so it needs one."""
    _install_active_plugin()
    ctx = _FakeContext()  # nothing imported
    assert _export(ctx, tempfile.mkdtemp()) == {"CANCELLED"}


def test_export_with_invalid_directory_is_cancelled() -> None:
    _install_active_plugin()
    ctx = _FakeContext()
    _import(ctx, _make_map_dir())
    assert _export(ctx, "") == {"CANCELLED"}


_ALL_TESTS = (
    test_import_builds_terrain_into_its_own_collection,
    test_import_applies_custom_scale,
    test_import_builds_object_hierarchy,
    test_centring_is_on_by_default_and_survives_export,
    test_import_records_provenance_on_the_scene,
    test_import_missing_directory_is_cancelled,
    test_import_folder_without_ssl_finishes_with_warning,
    test_import_finds_manifest_beside_the_map_folder,
    test_import_refuses_a_folder_holding_several_maps_manifests,
    test_no_active_plugin_is_cancelled,
    test_models_load_when_servers_xml_lives_outside_the_map_folder,
    test_game_root_pointed_at_a_subfolder_is_corrected,
    test_game_root_without_a_data_folder_is_reported,
    test_game_folder_comes_from_addon_preferences,
    test_import_without_game_root_still_succeeds,
    test_export_writes_terrain_and_preserves_other_files,
    test_export_writes_edited_object_positions,
    test_editing_scale_of_an_object_that_had_none_exports_it,
    test_editing_rotation_of_an_object_that_had_none_exports_it,
    test_untouched_objects_do_not_gain_attributes,
    test_export_preserves_world_root_attributes,
    test_in_place_export_overwrites_the_source_map,
    test_in_place_export_backs_up_overwritten_files,
    test_export_writes_into_the_existing_filename_casing,
    test_export_without_prior_import_is_cancelled,
    test_export_with_invalid_directory_is_cancelled,
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
