# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for road surfaces, and for what level.tile does not yet say."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

import bpy  # noqa: E402

from blender_io.roads_bridge import (  # noqa: E402
    ROAD_HALF_WIDTH,
    ROAD_MATERIAL,
    ROAD_SURFACE_PROP,
    build_road_surfaces,
    texture_road_chains,
)
from corpus import CORPUS, corpus  # noqa: E402



def test_a_road_chain_gets_a_width_and_a_surface() -> None:
    """A spline says where a road goes and nothing about how it looks."""
    curve = bpy.data.curves.new("chain", type="CURVE")
    obj = bpy.data.objects.new("road", curve)

    assert texture_road_chains([obj], None) == 1

    # A flat ribbon swept along the spline. ``extrude`` would push the
    # curve along its own Z and stand the road on edge, which is what
    # made them look like white noodles.
    assert curve.extrude == 0.0
    assert curve.bevel_depth == 0.0
    assert curve.bevel_object is not None
    assert curve.twist_mode == "Z_UP"
    assert [m.name for m in curve.materials] == [ROAD_MATERIAL]


def test_the_road_profile_is_flat_and_the_right_width() -> None:
    """A horizontal two-point line, so the sweep lies down."""
    import bpy as _bpy

    from blender_io.roads_bridge import ROAD_PROFILE

    curve = _bpy.data.curves.new("chain", type="CURVE")
    curve.splines.new("POLY").points.add(1)
    texture_road_chains([_bpy.data.objects.new("road", curve)], None)

    profile = _bpy.data.objects.get(ROAD_PROFILE)
    assert profile is not None

    points = [tuple(p.co)[:3] for p in profile.data.splines[0].points]
    assert points[0][0] == -ROAD_HALF_WIDTH
    assert points[1][0] == ROAD_HALF_WIDTH
    # Flat: no height difference between the two ends.
    assert points[0][2] == points[1][2] == 0.0


def test_a_road_keeps_its_points_and_its_properties() -> None:
    """The export writes the same chain back, so nothing may move."""
    curve = bpy.data.curves.new("chain", type="CURVE")
    spline = curve.splines.new("POLY")
    spline.points.add(2)
    before = [tuple(p.co) for p in spline.points]

    obj = bpy.data.objects.new("road", curve)
    obj["exm_road_chain"] = 1
    texture_road_chains([obj], None)

    assert [tuple(p.co) for p in curve.splines[0].points] == before
    assert obj["exm_road_chain"] == 1


def test_texturing_something_that_is_not_a_curve_is_skipped() -> None:
    mesh = bpy.data.meshes.new("not a road")
    obj = bpy.data.objects.new("not a road", mesh)
    assert texture_road_chains([obj], None) == 0


# --- what level.tile does not establish ---------------------------------


def test_the_tile_map_is_a_map_and_not_noise() -> None:
    """The property that separates the right reading from the wrong one.

    At 128 cells of four bytes the map agrees with its neighbours about
    twice as often as its own histogram would by chance — ground laid
    out in regions. At 256 cells of one byte the same bytes sit at
    chance, and that reading put near-random squares over the terrain
    for several versions.
    """
    from formats.exm.tilemap import TILE_GRID_SIDE, read_tilemap

    path = os.path.join(CORPUS, "level.tile")
    if not os.path.isfile(path):
        return

    tilemap = read_tilemap(path)
    side, indices = tilemap.side, tilemap.indices
    assert side == TILE_GRID_SIDE == 128
    assert len(indices) == side * side
    assert max(indices) < len(tilemap.tiles)

    counts = {value: indices.count(value) for value in set(indices)}
    baseline = sum((n / len(indices)) ** 2 for n in counts.values())

    horizontal = sum(
        1
        for y in range(side)
        for x in range(side - 1)
        if indices[y * side + x] == indices[y * side + x + 1]
    ) / (side * (side - 1))
    vertical = sum(
        1
        for y in range(side - 1)
        for x in range(side)
        if indices[y * side + x] == indices[(y + 1) * side + x]
    ) / ((side - 1) * side)

    assert horizontal > baseline * 1.8, (horizontal, baseline)
    assert vertical > baseline * 1.8, (vertical, baseline)


def test_only_the_first_byte_of_a_cell_carries_the_tile() -> None:
    """The other three are zero across all 16384 cells but one."""
    from formats.exm.gam import read_container
    from formats.exm.tilemap import TILE_CELL_SIZE, TILE_GRID_SIDE

    path = os.path.join(CORPUS, "level.tile")
    if not os.path.isfile(path):
        return

    block = max(
        (c.data for c in read_container(path)[1]), key=len
    )
    cells = block[-(TILE_GRID_SIDE * TILE_GRID_SIDE * TILE_CELL_SIZE) :]

    for offset in (1, 2, 3):
        nonzero = sum(
            1
            for i in range(TILE_GRID_SIDE * TILE_GRID_SIDE)
            if cells[i * TILE_CELL_SIZE + offset]
        )
        assert nonzero <= 1, (offset, nonzero)


def test_the_ground_detail_is_on() -> None:
    """The baked map alone is four units to a texel."""
    from addon.preferences import terrain_detail

    class _Context:
        preferences = None

    assert terrain_detail(_Context()) is True


def test_the_profile_is_linked_into_the_scene() -> None:
    """A bevel is evaluated through the dependency graph.

    An object belonging to no collection is not in that graph, so an
    unlinked profile sweeps nothing and the roads stay bare lines —
    which is exactly what they did.
    """
    from blender_io.roads_bridge import ROAD_PROFILE

    collection = bpy.data.collections.new("Roads")
    curve = bpy.data.curves.new("chain", type="CURVE")
    curve.splines.new("POLY").points.add(2)
    obj = bpy.data.objects.new("road", curve)

    texture_road_chains([obj], None, collection=collection)

    profile = bpy.data.objects.get(ROAD_PROFILE)
    assert profile is not None
    assert profile in list(collection.objects)
    assert profile.hide_viewport is True
    assert curve.bevel_object is profile


def test_a_three_dimensional_curve_is_told_to_fill() -> None:
    """A 3D curve fills nothing unless asked.

    The sweep then exists with no surface, which on screen is
    indistinguishable from a curve that was never widened at all.
    """
    collection = bpy.data.collections.new("Roads")
    curve = bpy.data.curves.new("chain", type="CURVE")
    curve.splines.new("POLY").points.add(3)
    obj = bpy.data.objects.new("road", curve)

    texture_road_chains([obj], None, collection=collection)

    assert curve.dimensions == "3D"
    assert curve.fill_mode == "FULL"
    assert curve.bevel_object is not None


# --- roads as geometry --------------------------------------------------


def _chain(points):
    curve = bpy.data.curves.new("chain", type="CURVE")
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, (x, y, z) in zip(spline.points, points):
        point.co = (x, y, z, 1.0)
    return bpy.data.objects.new("ExM_Road_0", curve)


def test_a_road_surface_is_real_geometry() -> None:
    """Built vertex by vertex rather than asked of a curve bevel.

    A bevel is a request that either works or silently does not — the
    profile has to be in the dependency graph and the curve has to be
    told to fill, and when either is off the road draws as a bare line
    with nothing to say why. Three rounds went to that.
    """
    collection = bpy.data.collections.new("Roads")
    obj = _chain([(0, 0, 0), (20, 0, 0), (40, 0, 0), (60, 0, 0)])

    assert build_road_surfaces([obj], None, collection=collection) == 1

    surface = next(
        o for o in collection.objects if o.name.endswith("_surface")
    )
    mesh = surface.data
    # Two vertices per cross-section and one quad between each pair.
    # There are cross-sections between the nodes as well as at them, so
    # the surface can follow ground the chain says nothing about.
    assert len(mesh.vertices) == (len(mesh.polygons) + 1) * 2
    assert len(mesh.polygons) >= 3
    assert surface[ROAD_SURFACE_PROP] == obj.name


def test_the_surface_is_the_width_it_claims() -> None:
    collection = bpy.data.collections.new("Roads")
    obj = _chain([(0, 0, 0), (20, 0, 0)])
    build_road_surfaces([obj], None, collection=collection)

    surface = next(o for o in collection.objects if o.name.endswith("_surface"))
    ys = [v.co[1] for v in surface.data.vertices]
    assert max(ys) - min(ys) == ROAD_HALF_WIDTH * 2


def test_the_surface_lies_flat_however_the_road_climbs() -> None:
    """The offset is taken in the horizontal plane, so it stays level."""
    collection = bpy.data.collections.new("Roads")
    obj = _chain([(0, 0, 0), (20, 0, 30), (40, 0, 60)])
    build_road_surfaces([obj], None, collection=collection)

    surface = next(o for o in collection.objects if o.name.endswith("_surface"))
    vertices = [tuple(v.co) for v in surface.data.vertices]
    # The two vertices of each cross-section share a height.
    for i in range(0, len(vertices), 2):
        assert vertices[i][2] == vertices[i + 1][2]


def test_one_full_texture_lies_between_each_pair_of_nodes() -> None:
    """How the road model is unwrapped: its own UVs run 0..1 across a
    single piece rather than repeating by distance.

    Tiling by length instead stretched or cut the surface at every
    node, however far apart the nodes happened to be.
    """
    collection = bpy.data.collections.new("Roads")
    obj = _chain([(0, 0, 0), (80, 0, 0), (140, 0, 0), (150, 0, 0)])
    build_road_surfaces([obj], None, collection=collection)

    surface = next(o for o in collection.objects if o.name.endswith("_surface"))
    layer = surface.data.uv_layers[0]

    vs = [layer.data[i].uv[1] for i in range(len(layer.data))]
    # Four nodes, so V runs 0, 1, 2, 3 — one repeat per stretch,
    # regardless of the 80, 60 and 10 unit gaps between them.
    assert (min(vs), max(vs)) == (0.0, 3.0)

    us = [layer.data[i].uv[0] for i in range(len(layer.data))]
    assert (min(us), max(us)) == (0.0, 1.0)


def test_the_chain_itself_is_left_alone() -> None:
    """The export reads the curve, so nothing here may touch it."""
    collection = bpy.data.collections.new("Roads")
    obj = _chain([(0, 0, 0), (20, 0, 0), (40, 0, 0)])
    before = [tuple(p.co) for p in obj.data.splines[0].points]

    build_road_surfaces([obj], None, collection=collection)

    assert [tuple(p.co) for p in obj.data.splines[0].points] == before


def test_a_chain_of_one_point_gets_no_surface() -> None:
    collection = bpy.data.collections.new("Roads")
    obj = _chain([(0, 0, 0)])
    assert build_road_surfaces([obj], None, collection=collection) == 0


def test_the_road_texture_runs_along_the_road() -> None:
    """Road textures are drawn with the road running up the image.

    Putting the length in U instead lays every lane marking sideways
    across the carriageway, which is what it did.
    """
    collection = bpy.data.collections.new("Roads")
    obj = _chain([(0, 0, 0), (80, 0, 0)])
    build_road_surfaces([obj], None, collection=collection)

    surface = next(o for o in collection.objects if o.name.endswith("_surface"))
    layer = surface.data.uv_layers[0]
    us = [layer.data[i].uv[0] for i in range(len(layer.data))]
    vs = [layer.data[i].uv[1] for i in range(len(layer.data))]

    # Across the road: exactly one repeat, edge to edge.
    assert (min(us), max(us)) == (0.0, 1.0)
    # Along it: one repeat per stretch between nodes.
    assert (min(vs), max(vs)) == (0.0, 1.0)


def test_a_road_follows_the_ground_between_its_nodes() -> None:
    """A chain gives heights only at its nodes.

    A single straight span between two of them cuts through crests and
    floats over dips, which is what road sections rising off the map
    are. The engine solves it by projection — ``roadProjector.fx`` puts
    the road on the ground rather than trusting its own height — and
    sampling every cross-section does the same.
    """
    from core.coordinates import CoordinateTransform
    from core.terrain import HeightmapData

    ridge = [
        300.0 + (40.0 if 28 < x < 36 else 0.0)
        for _y in range(64)
        for x in range(64)
    ]
    heightmap = HeightmapData(width=64, height=64, cell_size=8.0, values=ridge)
    transform = CoordinateTransform()
    low = transform.game_height_to_blender(300.0)

    collection = bpy.data.collections.new("Roads")
    curve = bpy.data.curves.new("chain", type="CURVE")
    spline = curve.splines.new("POLY")
    spline.points.add(1)
    spline.points[0].co = (100.0, 100.0, low, 1.0)
    spline.points[1].co = (400.0, 100.0, low, 1.0)
    obj = bpy.data.objects.new("ExM_Road_0", curve)

    build_road_surfaces(
        [obj], None, collection=collection, terrain=heightmap, transform=transform
    )
    surface = next(o for o in collection.objects if o.name.endswith("_surface"))

    heights = {round(v.co[2], 1) for v in surface.data.vertices}
    # Both nodes sit on low ground; the ridge between them is only
    # reached because the surface is sampled along its length.
    assert len(heights) > 1
    assert max(heights) > low + 20


def test_the_texture_still_runs_one_repeat_per_node_stretch() -> None:
    """Subdividing must not multiply the texture along the road."""
    collection = bpy.data.collections.new("Roads")
    obj = _chain([(0, 0, 0), (80, 0, 0), (160, 0, 0)])
    build_road_surfaces([obj], None, collection=collection)

    surface = next(o for o in collection.objects if o.name.endswith("_surface"))
    layer = surface.data.uv_layers[0]
    vs = [layer.data[i].uv[1] for i in range(len(layer.data))]
    assert (min(vs), max(vs)) == (0.0, 2.0)
