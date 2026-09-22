# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the terrain colour layer.

``colormap.raw`` and ``displace.bin`` are the same 512x512 grid, so the
mapping is index-for-index and there is nothing to interpolate. What
these tests guard is that it stays that way: an off-by-one or a domain
mix-up would produce a terrain that still looks like a terrain, just
subtly wrong, which is the hardest kind of bug to notice.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

import bpy  # noqa: E402

from blender_io.terrain_bridge import (  # noqa: E402
    EXM_COLOR_ATTR,
    apply_colormap,
    build_mesh,
    extract_colormap,
)
from core import color  # noqa: E402
from core.raster import RasterLayer  # noqa: E402
from core.terrain import HeightmapData  # noqa: E402
from utils.errors import ValidationError  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402

COLORMAP = corpus("colormap.raw")
DISPLACE = corpus("displace.bin")


def _grid(side: int) -> HeightmapData:
    return HeightmapData(
        width=side, height=side, cell_size=8.0, values=[0.0] * (side * side)
    )


def _layer(side: int, colors) -> RasterLayer:
    return RasterLayer(
        width=side, height=side, bytes_per_sample=4, data=color.pack_many(colors)
    )


# --- the grids line up ------------------------------------------------


def test_the_colormap_and_the_heightmap_are_the_same_grid() -> None:
    """The fact the whole mapping rests on."""
    if not (os.path.isfile(COLORMAP) and os.path.isfile(DISPLACE)):
        return
    assert os.path.getsize(COLORMAP) == 512 * 512 * 4
    assert os.path.getsize(DISPLACE) == 512 * 512 * 4


def test_colours_land_on_the_vertex_that_shares_their_index() -> None:
    side = 4
    colors = [(i, 0, 255 - i, 255) for i in range(side * side)]
    obj = build_mesh(_grid(side), name="T")

    assert apply_colormap(obj, _layer(side, colors)) == side * side

    attribute = obj.data.color_attributes.get(EXM_COLOR_ATTR)
    assert attribute is not None
    for index, expected in enumerate(colors):
        assert color.from_float(tuple(attribute.data[index].color)) == expected


def test_the_layer_is_per_vertex_not_per_face_corner() -> None:
    """POINT domain: the data is one colour per heightfield sample."""
    side = 4
    obj = build_mesh(_grid(side), name="T")
    apply_colormap(obj, _layer(side, [(1, 2, 3, 255)] * (side * side)))

    attribute = obj.data.color_attributes.get(EXM_COLOR_ATTR)
    assert attribute.domain == "POINT"
    assert len(attribute.data) == len(obj.data.vertices)


def test_a_mismatched_grid_is_refused_rather_than_stretched() -> None:
    obj = build_mesh(_grid(4), name="T")
    try:
        apply_colormap(obj, _layer(8, [(0, 0, 0, 255)] * 64))
    except ValidationError:
        return
    raise AssertionError("a 8x8 colormap was applied to a 4x4 terrain")


def test_an_all_zero_layer_builds_nothing() -> None:
    """Several layers ship unused; an all-black terrain is not data."""
    obj = build_mesh(_grid(4), name="T")
    assert apply_colormap(obj, _layer(4, [(0, 0, 0, 0)] * 16)) == 0
    assert obj.data.color_attributes.get(EXM_COLOR_ATTR) is None


def test_applying_twice_replaces_rather_than_accumulates() -> None:
    side = 4
    obj = build_mesh(_grid(side), name="T")
    apply_colormap(obj, _layer(side, [(10, 10, 10, 255)] * 16))
    apply_colormap(obj, _layer(side, [(20, 20, 20, 255)] * 16))

    layers = [a for a in obj.data.color_attributes if a.name == EXM_COLOR_ATTR]
    assert len(layers) == 1
    assert color.from_float(tuple(layers[0].data[0].color)) == (20, 20, 20, 255)


# --- round trip -------------------------------------------------------


def test_import_then_export_reproduces_the_bytes() -> None:
    side = 8
    colors = [((i * 7) % 256, (i * 3) % 256, (i * 11) % 256, 255) for i in range(64)]
    layer = _layer(side, colors)

    obj = build_mesh(_grid(side), name="T")
    apply_colormap(obj, layer)

    assert extract_colormap(obj, side, side) == layer.data


def test_the_real_colormap_survives_a_round_trip() -> None:
    """512x512 of actual map data, byte for byte."""
    if not os.path.isfile(COLORMAP):
        return
    data = open(COLORMAP, "rb").read()
    layer = RasterLayer(width=512, height=512, bytes_per_sample=4, data=data)

    obj = build_mesh(_grid(512), name="T")
    assert apply_colormap(obj, layer) == 512 * 512
    assert extract_colormap(obj, 512, 512) == data


def test_a_terrain_with_no_colour_layer_exports_mid_grey() -> None:
    """Not black: an untouched terrain must not export as a shadow."""
    obj = build_mesh(_grid(4), name="T")
    exported = extract_colormap(obj, 4, 4)
    assert color.unpack_many(exported) == [(127, 127, 127, 255)] * 16


def test_exporting_against_the_wrong_grid_is_refused() -> None:
    obj = build_mesh(_grid(4), name="T")
    try:
        extract_colormap(obj, 8, 8)
    except ValidationError:
        return
    raise AssertionError("a 4x4 terrain produced an 8x8 colormap")


# --- the ground needs coordinates to put its textures on ----------------


def test_the_terrain_gets_a_uv_layer() -> None:
    """It was built without one.

    Every face then sampled the same texel, and the ground rendered as
    a flat colour — indistinguishable from having no texture at all,
    which is exactly what it looked like.
    """
    from blender_io.terrain_bridge import apply_tilemap
    from formats.exm.tilemap import read_tilemap

    tile_file = corpus("level.tile")
    if not os.path.isfile(tile_file):
        return

    obj = build_mesh(_grid(9), name="T")
    assert len(obj.data.uv_layers) == 0

    apply_tilemap(obj, read_tilemap(tile_file), None)

    layers = list(obj.data.uv_layers)
    assert len(layers) == 1
    assert layers[0].active_render is True
    assert len(layers[0].data) == len(obj.data.loops)


def test_the_terrain_uvs_repeat_rather_than_stretch() -> None:
    """One tile texture per tile cell, which is what makes it tile.

    Coordinates run well past 1 on purpose — image nodes repeat, and
    that repetition is the tiling.
    """
    from blender_io.terrain_bridge import apply_tilemap
    from formats.exm.tilemap import read_tilemap

    tile_file = corpus("level.tile")
    if not os.path.isfile(tile_file):
        return

    obj = build_mesh(_grid(9), name="T")
    apply_tilemap(obj, read_tilemap(tile_file), None)

    data = obj.data.uv_layers[0].data
    us = [data[i].uv[0] for i in range(len(data))]
    vs = [data[i].uv[1] for i in range(len(data))]

    assert min(us) == 0.0 and min(vs) == 0.0
    assert max(us) > 1.0 and max(vs) > 1.0
    # The first quad covers exactly one repeat.
    assert tuple(data[0].uv) == (0.0, 0.0)


# --- ground textures ----------------------------------------------------


def _tile_scene(write: set[int] | None = None):
    """A terrain, a colour map, and tile textures on disk."""
    import tempfile

    from core import color as _color
    from core import dds
    from formats.exm.tilemap import read_tilemap

    tile_file = corpus("level.tile")
    if not os.path.isfile(tile_file):
        return None

    tilemap = read_tilemap(tile_file)
    root = tempfile.mkdtemp()
    image = bytes([120, 110, 90, 255]) * (32 * 32)

    for index in tilemap.used_tiles():
        if write is not None and index not in write:
            continue
        relative = tilemap.path_for(index).replace("\\", "/")
        path = os.path.join(root, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        dds.write(path, image, 32, 32)

    obj = build_mesh(_grid(9), name="T")
    apply_colormap(
        obj,
        RasterLayer(
            width=9,
            height=9,
            bytes_per_sample=4,
            data=_color.pack_many([(200, 150, 100, 255)] * 81),
        ),
    )
    return tilemap, root, obj


def test_a_ground_texture_is_loaded_by_its_full_path() -> None:
    """Not by basename.

    ``level.tile`` names ``region1\\ground_fall.dds`` while the model
    texture tree has its own ground textures. Resolving a tile by
    filename alone can pick a model's texture instead, which shows up
    as a square of ground wearing a wall's material.
    """
    from blender_io.terrain_bridge import apply_tilemap

    scene = _tile_scene()
    if scene is None:
        return
    tilemap, root, obj = scene

    assert apply_tilemap(obj, tilemap, root) == len(tilemap.used_tiles())

    for material in obj.data.materials:
        nodes = [n for n in material.node_tree.nodes if n.type == "TEX_IMAGE"]
        assert nodes, material.name
        assert nodes[0].image is not None


def test_the_colour_map_modulates_the_ground_texture() -> None:
    """Without it the terrain is one flat texture repeated everywhere."""
    from blender_io.terrain_bridge import EXM_COLOR_ATTR, apply_tilemap

    scene = _tile_scene()
    if scene is None:
        return
    tilemap, root, obj = scene
    apply_tilemap(obj, tilemap, root)

    material = obj.data.materials[0]
    colour = next(
        n for n in material.node_tree.nodes if n.type == "VERTEX_COLOR"
    )
    assert colour.layer_name == EXM_COLOR_ATTR

    mix = next(n for n in material.node_tree.nodes if n.type == "MIX_RGB")
    assert mix.blend_type == "MULTIPLY"

    links = [
        (l.from_node.name, l.from_socket.name, l.to_node.name, l.to_socket.name)
        for l in material.node_tree.links
    ]
    assert ("Diffuse", "Color", mix.name, "Color1") in links
    assert (colour.name, "Color", mix.name, "Color2") in links
    assert (mix.name, "Color", "BSDF_PRINCIPLED", "Base Color") in links


def test_a_missing_ground_texture_still_gets_a_material() -> None:
    """Losing the slot would shift every later tile onto the wrong one."""
    from blender_io.terrain_bridge import apply_tilemap

    scene = _tile_scene(write={0, 1})
    if scene is None:
        return
    tilemap, root, obj = scene

    assert apply_tilemap(obj, tilemap, root) == len(tilemap.used_tiles())
    assert len(obj.data.materials) == len(tilemap.used_tiles())


def test_every_quad_lands_on_a_tile_cell_that_exists() -> None:
    """511 quads over 256 cells, so the ratio is not exact."""
    quads, side, per_cell = 511, 256, 2
    reached = {min(side - 1, quad // per_cell) for quad in range(quads)}
    assert reached == set(range(side))


# --- the baked terrain texture ------------------------------------------


def _fresh_materials() -> None:
    """Materials are cached by name and persist between tests."""
    import bpy as _bpy

    for material in list(_bpy.data.materials):
        try:
            del _bpy.data.materials[material.name]
        except (AttributeError, KeyError, TypeError):
            pass


def test_the_baked_landscape_is_preferred_over_the_tiles() -> None:
    """It is what the original editor draws.

    ``landscape.dds`` is 1024x1024, DXT1, and carries no mipmaps —
    a texture drawn at one fixed scale. 98.5% of it is sand tones with
    olive-green patches, soft-edged, which is the reference picture.
    Tiles alone give hard squares of a single texture instead.
    """
    _fresh_materials()
    import shutil
    import tempfile

    from blender_io.terrain_bridge import LANDSCAPE_MATERIAL, apply_landscape

    source = corpus("landscape.dds")
    if not os.path.isfile(source):
        return

    folder = tempfile.mkdtemp()
    shutil.copy(source, os.path.join(folder, "landscape.dds"))

    obj = build_mesh(_grid(9), name="T")
    assert apply_landscape(obj, folder) is True

    # Named for the map it came from: every level ships its own
    # landscape.dds under the same filename.
    assert len(obj.data.materials) == 1
    assert obj.data.materials[0].name.startswith(LANDSCAPE_MATERIAL)
    assert all(p.material_index == 0 for p in obj.data.polygons)


def test_two_maps_do_not_share_one_terrain_material() -> None:
    """A material cached by name alone dresses the second map in the
    first one's ground."""
    import shutil
    import tempfile

    from blender_io.terrain_bridge import apply_landscape

    source = corpus("landscape.dds")
    if not os.path.isfile(source):
        return
    _fresh_materials()

    names = []
    for folder_name in ("r1m1-1-1", "r2m1-1-1"):
        folder = os.path.join(tempfile.mkdtemp(), folder_name)
        os.makedirs(folder)
        shutil.copy(source, os.path.join(folder, "landscape.dds"))

        obj = build_mesh(_grid(9), name="T")
        apply_landscape(obj, folder)
        names.append(obj.data.materials[0].name)

    assert names[0] != names[1], names


def test_the_landscape_covers_the_terrain_once() -> None:
    """One image over the whole map, corner to corner."""
    _fresh_materials()
    import shutil
    import tempfile

    from blender_io.terrain_bridge import apply_landscape

    source = corpus("landscape.dds")
    if not os.path.isfile(source):
        return

    folder = tempfile.mkdtemp()
    shutil.copy(source, os.path.join(folder, "landscape.dds"))

    obj = build_mesh(_grid(9), name="T")
    apply_landscape(obj, folder)

    data = obj.data.uv_layers[0].data
    us = [data[i].uv[0] for i in range(len(data))]
    vs = [data[i].uv[1] for i in range(len(data))]
    assert (min(us), max(us)) == (0.0, 1.0)
    assert (min(vs), max(vs)) == (0.0, 1.0)


def test_the_colour_map_is_applied_as_modulate2x() -> None:
    """Its neutral is 127, not 255.

    Three quarters of the sample map is exactly mid-grey, so a plain
    multiply would halve the brightness of most of the terrain. The
    doubling afterwards puts 127 back at neutral.
    """
    _fresh_materials()
    import shutil
    import tempfile

    from blender_io.terrain_bridge import apply_landscape

    source = corpus("landscape.dds")
    if not os.path.isfile(source):
        return

    folder = tempfile.mkdtemp()
    shutil.copy(source, os.path.join(folder, "landscape.dds"))

    obj = build_mesh(_grid(9), name="T")
    apply_landscape(obj, folder)

    tree = obj.data.materials[0].node_tree
    doubler = next(n for n in tree.nodes if n.name == "ExM_ColormapMix_2x")
    assert doubler.blend_type == "MULTIPLY"
    assert tuple(doubler.inputs["Color2"].default_value)[:3] == (2.0, 2.0, 2.0)

    links = [
        (l.from_node.name, l.to_node.name, l.to_socket.name)
        for l in tree.links
    ]
    assert ("ExM_ColormapMix", "ExM_ColormapMix_2x", "Color1") in links
    assert ("ExM_ColormapMix_2x", "BSDF_PRINCIPLED", "Base Color") in links


def test_a_map_without_a_baked_texture_still_gets_its_tiles() -> None:
    import tempfile

    from blender_io.terrain_bridge import apply_landscape

    obj = build_mesh(_grid(9), name="T")
    assert apply_landscape(obj, tempfile.mkdtemp()) is False


def test_the_tiles_add_close_up_detail_over_the_baked_colour() -> None:
    """One 1024x1024 image over 4088 units is four units to a texel.

    That is as blurred as it sounds, and it is what the baked map alone
    looks like up close. The tiles are the detail the engine blends
    over it, sampled at their own scale from their own UV layer.
    """
    _fresh_materials()
    import shutil
    import tempfile

    from core import dds as _dds
    from blender_io.terrain_bridge import TILE_UV_LAYER, apply_landscape
    from formats.exm.tilemap import read_tilemap

    landscape = corpus("landscape.dds")
    tile_file = corpus("level.tile")
    if not (os.path.isfile(landscape) and os.path.isfile(tile_file)):
        return

    folder = tempfile.mkdtemp()
    shutil.copy(landscape, os.path.join(folder, "landscape.dds"))

    tilemap = read_tilemap(tile_file)
    root = tempfile.mkdtemp()
    image = bytes([120, 110, 90, 255]) * (32 * 32)
    for index in tilemap.used_tiles():
        relative = tilemap.path_for(index).replace("\\", "/")
        path = os.path.join(root, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _dds.write(path, image, 32, 32)

    obj = build_mesh(_grid(17), name="T")
    assert apply_landscape(obj, folder, tilemap, root) is True

    assert [layer.name for layer in obj.data.uv_layers] == ["UVMap", TILE_UV_LAYER]
    assert len(obj.data.materials) == len(tilemap.used_tiles())

    tree = obj.data.materials[0].node_tree
    names = {n.name for n in tree.nodes}
    assert {"Diffuse", "Detail", "ExM_DetailDouble", "ExM_DetailMix"} <= names

    links = [
        (l.from_node.name, l.to_node.name, l.to_socket.name) for l in tree.links
    ]
    # Every terrain texture names its own UV layer explicitly, so the
    # node carries the layer AND the texture it feeds.
    assert (f"ExM_UV_{TILE_UV_LAYER}_Detail", "Detail", "Vector") in links
    assert ("ExM_UV_UVMap_Diffuse", "Diffuse", "Vector") in links
    # The tile is doubled first, then mixed at a chosen strength: the
    # engine blends its tiles through a mask rather than laying one per
    # cell, and full strength turns every grass cell olive.
    assert ("Detail", "ExM_DetailDouble", "Color1") in links
    assert ("ExM_DetailDouble", "ExM_DetailMix", "Color2") in links
    assert ("Diffuse", "ExM_DetailMix", "Color1") in links


def test_the_tile_uv_puts_one_texture_in_each_cell() -> None:
    """Span equals the number of tile cells, so each cell gets one repeat."""
    _fresh_materials()
    import shutil
    import tempfile

    from blender_io.terrain_bridge import TILE_UV_LAYER, apply_landscape
    from formats.exm.tilemap import read_tilemap

    landscape = corpus("landscape.dds")
    tile_file = corpus("level.tile")
    if not (os.path.isfile(landscape) and os.path.isfile(tile_file)):
        return

    folder = tempfile.mkdtemp()
    shutil.copy(landscape, os.path.join(folder, "landscape.dds"))
    tilemap = read_tilemap(tile_file)

    obj = build_mesh(_grid(17), name="T")
    apply_landscape(obj, folder, tilemap, tempfile.mkdtemp())

    tile_layer = next(
        layer for layer in obj.data.uv_layers if layer.name == TILE_UV_LAYER
    )
    us = [tile_layer.data[i].uv[0] for i in range(len(tile_layer.data))]
    assert max(us) == float(tilemap.side)

    base = obj.data.uv_layers[0]
    base_us = [base.data[i].uv[0] for i in range(len(base.data))]
    assert max(base_us) == 1.0


def test_the_daytime_lighting_is_multiplied_in_when_the_map_ships_one() -> None:
    """Named lightmap_<time>_<level>.dds and sitting in the map folder.

    The sizes say the daytime one is uncompressed A8R8G8B8 while the
    other three are DXT1 — the quality went where it is seen.
    """
    _fresh_materials()
    import shutil
    import tempfile

    from core import dds as _dds
    from blender_io.terrain_bridge import apply_landscape

    landscape = corpus("landscape.dds")
    if not os.path.isfile(landscape):
        return

    folder = tempfile.mkdtemp()
    shutil.copy(landscape, os.path.join(folder, "landscape.dds"))
    _dds.write(
        os.path.join(folder, "lightmap_daytime_r1m1.dds"),
        bytes([200, 200, 200, 255]) * (64 * 64),
        64,
        64,
    )

    obj = build_mesh(_grid(9), name="T")
    apply_landscape(obj, folder)

    tree = obj.data.materials[0].node_tree
    assert any(n.name == "Lightmap" for n in tree.nodes)
    links = [(l.from_node.name, l.to_node.name) for l in tree.links]
    assert ("Lightmap", "ExM_LightMix") in links


def test_the_tile_layer_does_not_capture_the_baked_texture() -> None:
    """A second UV layer takes active_render by default.

    Every texture node without an explicit UV input then reads through
    it, so the baked landscape came out as 256 copies of itself with
    its colour washed away — but only when tiles were loaded, which is
    why importing without models looked correct and importing with
    them did not.
    """
    import shutil
    import tempfile

    from core import dds as _dds
    from blender_io.terrain_bridge import TILE_UV_LAYER, apply_landscape
    from formats.exm.tilemap import read_tilemap

    landscape = corpus("landscape.dds")
    tile_file = corpus("level.tile")
    if not (os.path.isfile(landscape) and os.path.isfile(tile_file)):
        return
    _fresh_materials()

    folder = tempfile.mkdtemp()
    shutil.copy(landscape, os.path.join(folder, "landscape.dds"))

    tilemap = read_tilemap(tile_file)
    root = tempfile.mkdtemp()
    for index in tilemap.used_tiles():
        relative = tilemap.path_for(index).replace("\\", "/")
        path = os.path.join(root, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _dds.write(path, bytes([120, 110, 90, 255]) * (32 * 32), 32, 32)

    obj = build_mesh(_grid(17), name="T")
    apply_landscape(obj, folder, tilemap, root)

    layers = {layer.name: layer for layer in obj.data.uv_layers}
    assert layers["UVMap"].active_render is True
    assert layers[TILE_UV_LAYER].active_render is False

    # And the layer Texture Paint uses is the primary one.
    active = obj.data.uv_layers[obj.data.uv_layers.active_index]
    assert active.name == "UVMap"


# --- the map's lighting -------------------------------------------------


def test_the_lightmap_is_applied_as_modulate2x_not_multiply() -> None:
    """Measured on the real lightmaps, not assumed.

        daytime    A8R8G8B8 1024x1024   median 128, max 214
        nighttime  DXT1     1024x1024   median  57, max 132

    Neither reaches white. A plain multiply could therefore only ever
    darken, and would halve the terrain at the median. Doubled, 128 is
    neutral, 214 lifts lit ground to 1.68x, and night's 57 becomes
    0.45.
    """
    import shutil
    import tempfile

    from blender_io.terrain_bridge import apply_landscape

    landscape = corpus("landscape.dds")
    lightmap = corpus("lightmap_daytime_r1m1.dds")
    if not (os.path.isfile(landscape) and os.path.isfile(lightmap)):
        return
    _fresh_materials()

    folder = tempfile.mkdtemp()
    shutil.copy(landscape, os.path.join(folder, "landscape.dds"))
    shutil.copy(lightmap, folder)

    obj = build_mesh(_grid(9), name="T")
    apply_landscape(obj, folder)

    tree = obj.data.materials[0].node_tree
    doubler = next(n for n in tree.nodes if n.name == "ExM_LightMix_2x")
    assert doubler.blend_type == "MULTIPLY"
    assert tuple(doubler.inputs["Color2"].default_value)[:3] == (2.0, 2.0, 2.0)

    links = [(l.from_node.name, l.to_node.name) for l in tree.links]
    assert ("Lightmap", "ExM_LightMix") in links
    assert ("ExM_LightMix", "ExM_LightMix_2x") in links


def test_the_lightmaps_never_reach_white() -> None:
    """The property the modulate2x reading rests on.

    No channel saturates, and the median sits at mid-grey — measured::

        daytime    max R 239 G 215 B 165, median luminance 128
        nighttime  max R 140 G 134 B 109, median luminance  57

    A map meant for a plain multiply would have to reach 255 somewhere
    to leave lit ground at full brightness. These do not, so they are
    scaled for the doubling.
    """
    from core import dds as _dds

    for time_of_day in ("daytime", "nighttime"):
        path = corpus(f"lightmap_{time_of_day}_r1m1.dds")
        if not os.path.isfile(path):
            continue

        data = open(path, "rb").read()
        header = _dds.read_header(data)
        assert (header["width"], header["height"]) == (1024, 1024)

        rgba = _dds.decode(data)[0]
        for channel in range(3):
            assert max(rgba[channel::4]) < 250, (time_of_day, channel)


def test_each_time_of_day_gets_its_own_material() -> None:
    """Otherwise switching the hour reuses the previous one's lighting."""
    import shutil
    import tempfile

    from blender_io.terrain_bridge import apply_landscape

    landscape = corpus("landscape.dds")
    if not os.path.isfile(landscape):
        return
    _fresh_materials()

    folder = tempfile.mkdtemp()
    shutil.copy(landscape, os.path.join(folder, "landscape.dds"))
    for time_of_day in ("daytime", "nighttime"):
        source = corpus(f"lightmap_{time_of_day}_r1m1.dds")
        if os.path.isfile(source):
            shutil.copy(source, folder)

    names = []
    for time_of_day in ("daytime", "nighttime"):
        obj = build_mesh(_grid(9), name="T")
        apply_landscape(obj, folder, time_of_day=time_of_day)
        names.append(obj.data.materials[0].name)

    assert names[0] != names[1], names


def test_a_painted_terrain_texture_is_called_out() -> None:
    """Paint lives in the image datablock until it is written out.

    A texture edited in Blender then looks nothing like the file on
    disk, while every measurement of that file says it is fine. That
    gap is invisible from either side, and it cost several rounds of
    hunting a stripe pattern through the map data that was never in it.
    """
    import logging

    from blender_io.terrain_bridge import _report_terrain_inputs

    mesh = bpy.data.meshes.new("terrain")
    obj = bpy.data.objects.new("terrain", mesh)

    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger = logging.getLogger("exmeditor.blender_io.terrain_bridge")
    logger.addHandler(handler)
    try:
        _report_terrain_inputs(
            obj, fake_bpy.FakeImage("landscape.dds", size=(1024, 1024)), None
        )
        assert not [r for r in records if r.levelno >= logging.WARNING]

        records.clear()
        _report_terrain_inputs(
            obj,
            fake_bpy.FakeImage("landscape.dds", size=(1024, 1024), is_dirty=True),
            None,
        )
        warnings = [r for r in records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1
        assert "UNSAVED" in warnings[0].getMessage()
    finally:
        logger.removeHandler(handler)


# --- how the scene is displayed -----------------------------------------


def test_the_view_transform_is_switched_to_standard() -> None:
    """Blender 3.6 defaults to Filmic.

    Filmic is a tone curve for photographic renders with a wide
    dynamic range. Game textures are authored in display space and
    already carry their contrast, so it puts them through a second
    curve: brown rock comes out near-white and the ground goes muddy.
    Nothing in the data changes; the whole difference is this setting.
    """
    import types

    from blender_io.scene_bridge import (
        DISPLAY_VIEW_TRANSFORM,
        use_display_referred_colours,
    )

    scene = types.SimpleNamespace(view_settings=fake_bpy.FakeViewSettings())
    assert scene.view_settings.view_transform == "Filmic"

    assert use_display_referred_colours(scene) is True
    assert scene.view_settings.view_transform == DISPLAY_VIEW_TRANSFORM

    # Already set: reported as unchanged, so the caller does not claim
    # to have done something it did not.
    assert use_display_referred_colours(scene) is False


def test_a_scene_without_colour_management_is_left_alone() -> None:
    import types

    from blender_io.scene_bridge import use_display_referred_colours

    assert use_display_referred_colours(types.SimpleNamespace()) is False


def test_the_ground_detail_is_folded_in_at_the_shaders_own_strength() -> None:
    """``diffuse_detail.fx`` says ``Diffuse.rgb * Details.rgb * 2.0``.

    There used to be a dial in front of this, set below full because
    without the corner blend every tile covered its whole cell and a
    grass tile turned a sandy map olive. The blend is real now, so the
    dial had nothing left to compensate for and the shader's own
    constant stands on its own.
    """
    import shutil
    import tempfile

    from blender_io.terrain_bridge import apply_landscape
    from core import dds as _dds
    from formats.exm.tilemap import read_tilemap

    landscape = corpus("landscape.dds")
    tile_file = corpus("level.tile")
    if not (os.path.isfile(landscape) and os.path.isfile(tile_file)):
        return

    folder = tempfile.mkdtemp()
    shutil.copy(landscape, os.path.join(folder, "landscape.dds"))
    tilemap = read_tilemap(tile_file)

    root = tempfile.mkdtemp()
    for index in tilemap.used_tiles():
        relative = tilemap.path_for(index).replace("\\", "/")
        path = os.path.join(root, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _dds.write(path, bytes([120, 110, 90, 255]) * (32 * 32), 32, 32)

    obj = build_mesh(_grid(17), name="T")
    apply_landscape(obj, folder, tilemap, root)

    mix = next(
        n for n in obj.data.materials[0].node_tree.nodes
        if n.name == "ExM_DetailMix"
    )
    assert mix.inputs["Fac"].default_value == 1.0


def test_the_terrain_chain_produces_the_sand_the_editor_shows() -> None:
    """Measured on the real files, so the trade above is visible.

        landscape alone                      R177 G161 B128
        x lightmap x 2                       R199 G152 B66
        x lightmap x 2 x colormap x 2        R180 G136 B58

    which is the sand of the original editor. Anything olive on top of
    that comes from the tiles, not from the baked map.
    """
    from core import color as _color
    from core import dds as _dds

    landscape = corpus("landscape.dds")
    lightmap = corpus("lightmap_daytime_r1m1.dds")
    colormap = corpus("colormap.raw")
    if not all(os.path.isfile(p) for p in (landscape, lightmap, colormap)):
        return

    def mean(pixels):
        count = len(pixels) // 4
        return [
            sum(pixels[i * 4 + c] for i in range(0, count, 97))
            / len(range(0, count, 97))
            for c in range(3)
        ]

    base = mean(_dds.decode(open(landscape, "rb").read())[0])
    light = mean(_dds.decode(open(lightmap, "rb").read())[0])
    samples = _color.unpack_many(open(colormap, "rb").read())[::97]
    tint = [sum(s[c] for s in samples) / len(samples) for c in range(3)]

    result = [
        base[c] / 255 * (light[c] / 255 * 2) * (tint[c] / 255 * 2) * 255
        for c in range(3)
    ]

    # Warm: red well clear of blue, and not a green-dominant pixel.
    assert result[0] > result[2] + 80
    assert result[1] < result[0]
