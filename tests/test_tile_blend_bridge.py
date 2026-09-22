# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the Blender side of ground tile blending.

Built on a synthetic tile map rather than on ``level.tile``, so these
run everywhere: the file format is already covered by
``test_tilemap.py`` and what is at stake here is the mesh attribute and
the node graph.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

import bpy  # noqa: E402

from blender_io.terrain_bridge import (  # noqa: E402
    TILE_BLEND_ATTR,
    apply_landscape,
    apply_tilemap,
    build_mesh,
)
from core import dds  # noqa: E402
from core.terrain import HeightmapData  # noqa: E402
from formats.exm.tilemap import TileMap  # noqa: E402

#: 4 tile cells across, 8 quads: two heightfield quads to a cell.
SIDE = 4
GRID = 9


def _grid(side: int) -> HeightmapData:
    return HeightmapData(
        width=side, height=side, cell_size=8.0, values=[0.0] * (side * side)
    )


def _tilemap(indices=None) -> TileMap:
    """Left half tile 0, right half tile 1, unless told otherwise."""
    if indices is None:
        indices = bytes(
            0 if x < SIDE // 2 else 1
            for _y in range(SIDE)
            for x in range(SIDE)
        )
    return TileMap(
        root="data\\tiles",
        tiles=["region1\\sand.dds", "region1\\rocks.dds", "region1\\grass.dds"],
        indices=indices,
        side=SIDE,
    )


def _game_root(tilemap: TileMap) -> str:
    """A folder holding the tile textures the map names."""
    root = tempfile.mkdtemp()
    image = bytes([120, 110, 90, 255]) * (32 * 32)
    for index in set(tilemap.indices):
        relative = tilemap.path_for(index).replace("\\", "/")
        path = os.path.join(root, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        dds.write(path, image, 32, 32)
    return root


def _scene(indices=None):
    # Datablocks outlive a test in the fake exactly as they do in a
    # .blend, and a material cached by an earlier test would be handed
    # to this one. Clearing is what makes each test say something.
    for name in list(bpy.data.materials.keys()):
        del bpy.data.materials[name]
    for image in list(bpy.data.images):
        bpy.data.images.remove(image)

    tilemap = _tilemap(indices)
    return tilemap, _game_root(tilemap), build_mesh(_grid(GRID), name="T")


def _links(material):
    return [
        (l.from_node.name, l.from_socket.name, l.to_node.name, l.to_socket.name)
        for l in material.node_tree.links
    ]


def _material_of(obj, quad_x: int, quad_y: int):
    quads_across = GRID - 1
    polygon = obj.data.polygons[quad_y * quads_across + quad_x]
    return obj.data.materials[polygon.material_index]


def _textures(material):
    return [
        n for n in material.node_tree.nodes
        if n.type == "TEX_IMAGE" and n.name.startswith("Diffuse")
    ]


# --- the corner attribute -----------------------------------------------


def test_blending_writes_one_alpha_per_face_corner() -> None:
    tilemap, root, obj = _scene()
    apply_tilemap(obj, tilemap, root, blend=True)

    layer = obj.data.color_attributes.get(TILE_BLEND_ATTR)
    assert layer is not None
    assert layer.domain == "CORNER"
    assert len(layer.data) == len(obj.data.loops)


def test_a_uniform_patch_carries_no_blend_at_all() -> None:
    """Most of a map is one tile, and it must cost nothing there."""
    tilemap, root, obj = _scene()
    apply_tilemap(obj, tilemap, root, blend=True)

    layer = obj.data.color_attributes.get(TILE_BLEND_ATTR)
    polygon = obj.data.polygons[0]  # quad 0,0 — deep inside tile 0
    for loop in range(polygon.loop_start, polygon.loop_start + 4):
        assert tuple(layer.data[loop].color)[:3] == (0.0, 0.0, 0.0)


def test_the_boundary_quads_carry_a_ramp_from_nothing_to_everything() -> None:
    """Quads 3 and 4 straddle the tile boundary — see core/tile_blend.

    The second pass has to reach 0 at one end of the pair and 1 at the
    other, or the gradient is not a gradient.
    """
    tilemap, root, obj = _scene()
    apply_tilemap(obj, tilemap, root, blend=True)

    layer = obj.data.color_attributes.get(TILE_BLEND_ATTR)
    quads_across = GRID - 1
    alphas = []
    for quad_x in (3, 4):
        polygon = obj.data.polygons[quad_x]
        assert polygon.index == quad_x  # row 0
        for loop in range(polygon.loop_start, polygon.loop_start + 4):
            alphas.append(layer.data[loop].color[0])

    assert min(alphas) == 0.0
    assert max(alphas) == 1.0
    assert any(0.0 < a < 1.0 for a in alphas)
    assert quads_across == 8


def test_the_ramp_runs_along_x_only_when_the_map_splits_along_x() -> None:
    """A ramp in the wrong axis is the classic transposed-grid bug.

    Read across the blended band only. A corner attribute is per FACE
    corner, so the same vertex holds different alphas in the blended
    quad and in the uniform one next to it — that is not variation in
    y, it is two faces with different materials, and comparing across
    them would test nothing.
    """
    tilemap, root, obj = _scene()
    apply_tilemap(obj, tilemap, root, blend=True)

    layer = obj.data.color_attributes.get(TILE_BLEND_ATTR)
    quads_across = GRID - 1
    by_column: dict[int, set] = {}
    for row in range(quads_across):
        for quad_x in (3, 4):
            polygon = obj.data.polygons[row * quads_across + quad_x]
            for loop in range(polygon.loop_start, polygon.loop_start + 4):
                vertex = obj.data.loops[loop].vertex_index
                by_column.setdefault(vertex % GRID, set()).add(
                    round(layer.data[loop].color[0], 6)
                )

    assert sorted(by_column) == [3, 4, 5]
    for column, values in by_column.items():
        assert len(values) == 1, (column, values)
    assert [next(iter(by_column[c])) for c in (3, 4, 5)] == [0.0, 0.5, 1.0]


# --- materials -----------------------------------------------------------


def test_a_uniform_quad_gets_a_single_texture_material() -> None:
    """The graph the unblended path built, unchanged where nothing blends."""
    tilemap, root, obj = _scene()
    apply_tilemap(obj, tilemap, root, blend=True)

    material = _material_of(obj, 0, 0)
    textures = _textures(material)
    assert len(textures) == 1
    assert textures[0].name == "Diffuse"
    assert not [n for n in material.node_tree.nodes if n.type == "SEPARATE_COLOR"]


def test_a_boundary_quad_gets_both_tiles_and_mixes_them() -> None:
    tilemap, root, obj = _scene()
    apply_tilemap(obj, tilemap, root, blend=True)

    material = _material_of(obj, 3, 0)
    textures = _textures(material)
    assert [n.name for n in textures] == ["Diffuse", "Diffuse1"]
    assert all(n.image is not None for n in textures)

    links = _links(material)
    weights = next(
        n for n in material.node_tree.nodes
        if n.type == "VERTEX_COLOR" and n.name == "ExM_TileBlendAlpha"
    )
    assert weights.layer_name == TILE_BLEND_ATTR
    assert (weights.name, "Color", "ExM_TileBlendSplit", "Color") in links
    assert ("ExM_TileBlendSplit", "Red", "ExM_TileBlend1", "Fac") in links
    assert ("Diffuse", "Color", "ExM_TileBlend1", "Color1") in links
    assert ("Diffuse1", "Color", "ExM_TileBlend1", "Color2") in links


def test_the_base_pass_is_the_lower_tile_index() -> None:
    """Measured: the engine draws passes ascending by tile.

    Which one is the base decides which texture the blend starts from,
    so getting it backwards inverts every gradient on the map.
    """
    tilemap, root, obj = _scene()
    apply_tilemap(obj, tilemap, root, blend=True)

    material = _material_of(obj, 3, 0)
    assert "0-sand" in material.name
    assert material.name.index("0-sand") < material.name.index("1-rocks")


def test_one_material_per_combination_not_per_quad() -> None:
    tilemap, root, obj = _scene()
    apply_tilemap(obj, tilemap, root, blend=True)

    # Two uniform halves and one blended band: three materials.
    assert len(obj.data.materials) == 3
    for polygon in obj.data.polygons:
        assert 0 <= polygon.material_index < len(obj.data.materials)


def test_a_patch_of_three_tiles_stacks_three_passes() -> None:
    """Two textures is the easy case; the chain has to go further."""
    indices = bytearray(SIDE * SIDE)
    indices[SIDE + 1] = 1  # cell (1,1)
    indices[SIDE + 2] = 2  # cell (2,1)
    tilemap, root, obj = _scene(bytes(indices))
    apply_tilemap(obj, tilemap, root, blend=True)

    widest = max(obj.data.materials, key=lambda m: len(_textures(m)))
    assert [n.name for n in _textures(widest)] == ["Diffuse", "Diffuse1", "Diffuse2"]
    links = _links(widest)
    assert ("ExM_TileBlendSplit", "Green", "ExM_TileBlend2", "Fac") in links
    assert ("ExM_TileBlend1", "Color", "ExM_TileBlend2", "Color1") in links


def test_the_return_value_still_counts_ground_textures() -> None:
    """Not materials — the caller reports "N tiles", and blending does
    not change how many tiles the map uses."""
    tilemap, root, obj = _scene()
    assert apply_tilemap(obj, tilemap, root, blend=True) == len(tilemap.used_tiles())


# --- blending off --------------------------------------------------------


def test_with_blending_off_nothing_is_written_or_mixed() -> None:
    """The old path has to stay exactly what it was."""
    tilemap, root, obj = _scene()
    assert apply_tilemap(obj, tilemap, root) == len(tilemap.used_tiles())

    assert obj.data.color_attributes.get(TILE_BLEND_ATTR) is None
    assert len(obj.data.materials) == len(tilemap.used_tiles())
    for material in obj.data.materials:
        assert len(_textures(material)) == 1


# --- over the baked landscape --------------------------------------------


def _landscape_scene():
    tilemap, root, obj = _scene()
    folder = tempfile.mkdtemp()
    dds.write(
        os.path.join(folder, "landscape.dds"),
        bytes([200, 180, 140, 255]) * (32 * 32), 32, 32,
    )
    return tilemap, root, obj, folder


def test_the_baked_landscape_blends_its_detail_tiles_too() -> None:
    """The path shipped maps actually take.

    ``landscape.dds`` is present on every real map, so the tile-only
    path is the fallback — blending only there would leave the common
    case hard-edged.
    """
    tilemap, root, obj, folder = _landscape_scene()
    assert apply_landscape(obj, folder, tilemap, root, blend=True) is True

    assert obj.data.color_attributes.get(TILE_BLEND_ATTR) is not None

    material = _material_of(obj, 3, 0)
    details = [
        n for n in material.node_tree.nodes
        if n.type == "TEX_IMAGE" and n.name.startswith("Detail")
    ]
    assert [n.name for n in details] == ["Detail", "Detail1"]

    links = _links(material)
    # The blended detail, not one raw tile, is what gets doubled and
    # folded into the baked colour.
    assert ("ExM_TileBlend1", "Color", "ExM_DetailDouble", "Color1") in links
    assert ("ExM_DetailDouble", "Color", "ExM_DetailMix", "Color2") in links


def test_the_landscape_keeps_its_single_detail_wiring_when_unblended() -> None:
    """Named nodes other tests depend on must survive the refactor."""
    tilemap, root, obj, folder = _landscape_scene()
    assert apply_landscape(obj, folder, tilemap, root) is True

    material = obj.data.materials[0]
    links = _links(material)
    assert ("Detail", "Color", "ExM_DetailDouble", "Color1") in links
    assert ("Diffuse", "Color", "ExM_DetailMix", "Color1") in links
    assert obj.data.color_attributes.get(TILE_BLEND_ATTR) is None


# --- sampling the game's own mask atlas ---------------------------------


def _with_atlas():
    """A scene whose game folder also holds data/tiles/mask.dds."""
    tilemap, root, obj = _scene()
    path = os.path.join(root, "data", "tiles", "mask.dds")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 512x128 with alpha, so it encodes as DXT5 like the shipped file.
    dds.write(path, bytes([255, 255, 255, 128]) * (512 * 128), 512, 128)
    return tilemap, root, obj


def test_the_blend_reads_the_atlas_when_the_game_ships_it() -> None:
    from blender_io.terrain_bridge import MASK_UV_LAYERS

    tilemap, root, obj = _with_atlas()
    apply_tilemap(obj, tilemap, root, blend=True)

    material = _material_of(obj, 3, 0)
    names = {n.name for n in material.node_tree.nodes}
    assert "ExM_TileMask1" in names
    # And not the fallback.
    assert "ExM_TileBlendSplit" not in names

    links = _links(material)
    assert ("ExM_TileMask1", "Alpha", "ExM_TileBlend1", "Fac") in links
    assert (
        f"ExM_UV_{MASK_UV_LAYERS[0]}_ExM_TileMask1", "UV", "ExM_TileMask1", "Vector"
    ) in links


def test_without_the_atlas_it_falls_back_to_the_computed_ramp() -> None:
    """A partial install still gets blended ground, just not the
    game's own mask shapes."""
    tilemap, root, obj = _scene()
    apply_tilemap(obj, tilemap, root, blend=True)

    material = _material_of(obj, 3, 0)
    names = {n.name for n in material.node_tree.nodes}
    assert "ExM_TileBlendSplit" in names
    assert "ExM_TileMask1" not in names


def test_each_pass_gets_a_uv_set_of_its_own() -> None:
    from blender_io.terrain_bridge import MASK_UV_LAYERS

    tilemap, root, obj = _with_atlas()
    apply_tilemap(obj, tilemap, root, blend=True)

    for name in MASK_UV_LAYERS:
        layer = obj.data.uv_layers.get(name)
        assert layer is not None, name
        assert len(layer.data) == len(obj.data.loops)


def test_the_mask_uvs_land_in_the_cell_the_mask_calls_for() -> None:
    """The boundary band blends tiles 0 and 1, so its second pass has a
    two-corner mask and must read a two-corner cell — never cell 0."""
    from blender_io.terrain_bridge import MASK_UV_LAYERS

    tilemap, root, obj = _with_atlas()
    apply_tilemap(obj, tilemap, root, blend=True)

    layer = obj.data.uv_layers.get(MASK_UV_LAYERS[0])
    polygon = obj.data.polygons[3]  # a quad in the blended band
    for loop in range(polygon.loop_start, polygon.loop_start + 4):
        across, up = layer.data[loop].uv
        assert 0.0 < across < 1.0
        assert 0.0 < up < 1.0
        # Cell 0 is the full-cover cell and belongs to the base pass.
        assert not (across < 0.125 and up > 0.5)


def test_a_uniform_quad_asks_the_atlas_for_nothing() -> None:
    from blender_io.terrain_bridge import MASK_UV_LAYERS

    tilemap, root, obj = _with_atlas()
    apply_tilemap(obj, tilemap, root, blend=True)

    layer = obj.data.uv_layers.get(MASK_UV_LAYERS[0])
    polygon = obj.data.polygons[0]
    for loop in range(polygon.loop_start, polygon.loop_start + 4):
        assert tuple(layer.data[loop].uv) == (0.0, 0.0)


def test_the_mask_layers_do_not_steal_the_active_uv_set() -> None:
    """Blender hands ``active`` to the newest layer, and the active one
    is what Texture Paint and the viewport sample with. Three new
    layers here would put the ground's display on a mask lookup."""
    from blender_io.terrain_bridge import TERRAIN_UV_LAYER

    tilemap, root, obj = _with_atlas()
    apply_tilemap(obj, tilemap, root, blend=True)

    active = obj.data.uv_layers.active
    assert active is not None
    assert active.name == TERRAIN_UV_LAYER


# --- the layer references have to be live -------------------------------


def test_the_blend_attribute_is_actually_written() -> None:
    """It was not, and nothing said so.

    Adding a layer re-lays a mesh's custom data, and a reference taken
    before that reads as EMPTY — no exception, every write skipped, the
    layer left at its default. Which is white, so a real import came
    back with a full-length blend attribute of 1.0 everywhere and a
    terrain drawn with no blend at all.

    The order is the fix: create every layer, then look them all up.
    """
    tilemap, root, obj = _with_atlas()
    apply_tilemap(obj, tilemap, root, blend=True)

    layer = obj.data.color_attributes.get(TILE_BLEND_ATTR)
    assert layer is not None
    assert len(layer.data) == len(obj.data.loops)

    # White is what an unwritten FLOAT_COLOR reads as, so its absence is
    # the evidence that the writing happened at all.
    assert not all(
        tuple(entry.color)[:3] == (1.0, 1.0, 1.0) for entry in layer.data
    )

    # And the band either side of the tile boundary carries the ramp.
    polygon = obj.data.polygons[3]
    alphas = [
        layer.data[loop].color[0]
        for loop in range(polygon.loop_start, polygon.loop_start + 4)
    ]
    assert max(alphas) > 0.0


def test_a_stale_layer_reference_reads_as_empty() -> None:
    """Pinning the double's own behaviour, since the bug lived in it.

    A fake that kept every reference live could not reproduce this, and
    the test above would have passed against the broken importer.
    """
    tilemap, root, obj = _scene()
    mesh = obj.data

    first = mesh.uv_layers.new(name="ExM_Probe1")
    assert len(first.data) == len(mesh.loops)

    mesh.uv_layers.new(name="ExM_Probe2")
    assert len(first.data) == 0          # the reference went stale

    again = mesh.uv_layers.get("ExM_Probe1")
    assert len(again.data) == len(mesh.loops)   # looking it up revives it
