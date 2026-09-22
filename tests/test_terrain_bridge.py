# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `blender_io/terrain_bridge.py` and `blender_io/scene_bridge.py`.

Requires the fake `bpy`/`bmesh` stub (`tests/fake_bpy.py`) since these
modules import `bpy` — installed once at import time below.

`build_mesh`/`extract_heightmap` are purely mechanical now (no scale
math of their own) — scale composition is tested against
`core.world_transform.WorldTransform` separately in
`test_world_transform.py`; this file verifies `build_mesh` faithfully
reflects whatever `HeightmapData` it's handed, with zero constants of
its own.
"""

from __future__ import annotations

import array
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.scene_bridge import build_scene  # noqa: E402
from blender_io.terrain_bridge import (  # noqa: E402
    CELL_SIZE_PROP,
    GRID_HEIGHT_PROP,
    GRID_WIDTH_PROP,
    build_mesh,
)
from core.scene import MapScene  # noqa: E402
from core.terrain import HeightmapData  # noqa: E402
from core.coordinates import CoordinateTransform  # noqa: E402


def _make_grid(cell_size: float = 1.0) -> HeightmapData:
    """3x3 grid, unique heights per cell."""
    return HeightmapData(
        width=3, height=3, cell_size=cell_size,
        values=array.array("f", [10, 20, 30, 40, 50, 60, 70, 80, 90]),
    )


def test_vertex_positions_come_directly_from_heightmap() -> None:
    """No constant of any kind lives in build_mesh — positions are
    exactly (col*cell_size, row*cell_size, sample)."""
    obj = build_mesh(_make_grid(cell_size=1.0), name="t_default")
    verts = obj.data.vertices
    assert verts[0].co == (0.0, 0.0, 10.0)
    assert verts[1].co == (1.0, 0.0, 20.0)
    assert verts[-1].co == (2.0, 2.0, 90.0)


def test_vertex_spacing_reflects_heightmap_cell_size() -> None:
    """A pre-scaled HeightmapData.cell_size (e.g. the output of
    WorldTransform.to_world()) is honored exactly — build_mesh does not
    reinterpret or reapply any scale."""
    obj = build_mesh(_make_grid(cell_size=2.5), name="t_cellsize")
    verts = obj.data.vertices
    assert verts[1].co == (2.5, 0.0, 20.0)  # x = col(1) * cell_size(2.5)
    assert verts[-1].co == (5.0, 5.0, 90.0)


def test_heights_are_never_scaled_by_build_mesh() -> None:
    """Feeding build_mesh a HeightmapData with large or small raw values
    must reproduce them verbatim — proves there's no hidden height
    multiplier left over anywhere in this function."""
    grid = HeightmapData(width=2, height=1, cell_size=1.0, values=array.array("f", [0.001, 99999.5]))
    obj = build_mesh(grid, name="t_heights")
    verts = obj.data.vertices
    # values.array('f', [0.001, ...]) already rounds 0.001 to its
    # nearest float32 representation before build_mesh ever sees it —
    # compare against that rounded value, not the float64 literal.
    assert abs(verts[0].co.z - grid.values[0]) < 1e-9
    assert verts[1].co.z == 99999.5


def test_face_count_and_vertex_count() -> None:
    obj = build_mesh(_make_grid(), name="t_counts")
    assert len(obj.data.vertices) == 9  # 3x3
    assert len(obj.data.polygons) == 4  # 2x2 quads


def test_custom_properties_reflect_the_heightmap_shape() -> None:
    """build_mesh only knows about the HeightmapData it's given — it has
    no knowledge of any WorldTransform, so it only sets shape-related
    custom properties, not XY_SCALE_PROP/HEIGHT_SCALE_PROP (those are
    the calling operator's responsibility — see addon/operators.py)."""
    grid = _make_grid(cell_size=1.5)
    obj = build_mesh(grid, name="t_props")
    assert obj.get(GRID_WIDTH_PROP) == 3
    assert obj.get(GRID_HEIGHT_PROP) == 3
    assert obj.get(CELL_SIZE_PROP) == 1.5


def test_build_scene_links_terrain_into_a_sub_collection() -> None:
    """Terrain goes into a named 'Terrain' sub-collection rather than
    directly into the target, so a map with thousands of objects stays
    navigable in the outliner."""
    scene = MapScene(terrain=_make_grid())
    collection = fake_bpy.FakeCollection("Map")
    result = build_scene(scene, collection)

    terrain_coll = next(c for c in collection.children if c.name == "Terrain")
    assert len(terrain_coll.objects.linked) == 1
    assert result.terrain_object is terrain_coll.objects.linked[0]


def test_build_scene_applies_the_shared_transform() -> None:
    """build_scene must route terrain through the same
    CoordinateTransform every other spatial format uses — proof: a
    non-1.0 xy_scale changes the built mesh's vertex spacing."""
    scene = MapScene(terrain=_make_grid(cell_size=1.0))
    collection = fake_bpy.FakeCollection()
    transform = CoordinateTransform(xy_scale=10.0, height_scale=1.0)
    result = build_scene(scene, collection, transform=transform)
    assert result.terrain_object.data.vertices[1].co.x == 10.0  # col(1) * cell_size(1.0) * xy_scale(10.0)


def test_build_scene_with_no_transform_is_a_no_op() -> None:
    scene = MapScene(terrain=_make_grid(cell_size=1.0))
    collection = fake_bpy.FakeCollection()
    result = build_scene(scene, collection)  # no world_transform given
    assert result.terrain_object.data.vertices[1].co.x == 1.0


def test_build_scene_with_no_terrain_links_nothing() -> None:
    scene = MapScene()  # empty
    collection = fake_bpy.FakeCollection()
    result = build_scene(scene, collection)
    assert len(collection.objects.linked) == 0
    assert result.terrain_object is None


_ALL_TESTS = (
    test_vertex_positions_come_directly_from_heightmap,
    test_vertex_spacing_reflects_heightmap_cell_size,
    test_heights_are_never_scaled_by_build_mesh,
    test_face_count_and_vertex_count,
    test_custom_properties_reflect_the_heightmap_shape,
    test_build_scene_links_terrain_into_a_sub_collection,
    test_build_scene_applies_the_shared_transform,
    test_build_scene_with_no_transform_is_a_no_op,
    test_build_scene_with_no_terrain_links_nothing,
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
