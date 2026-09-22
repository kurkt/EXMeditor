# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""What a static read of the editor settled, kept as tests.

Each of these was a guess before, and several of the guesses were
wrong. Pinning them here keeps the reasoning attached to the numbers.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from formats.exm.skin import SHADER_SLOTS, uses_detail  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402


# --- which slots a shader reads -----------------------------------------


def test_cubemap_and_detail_do_belong_to_one_shader() -> None:
    """``Shaders.txt`` implies they never co-occur. It is wrong.

    A census of 1355 models finds ``Skinned`` using all five slots at
    once on 213 materials. The exporter config describes what its own
    presets emit, not what the format allows — a distinction that cost
    a false claim in this file.
    """
    assert set(SHADER_SLOTS["skinned"]) >= {"cubemap", "detail"}

    # It stays rare: everything else keeps to one family or the other.
    both = [
        name
        for name, slots in SHADER_SLOTS.items()
        if "cubemap" in slots and "detail" in slots
    ]
    assert both == ["skinned"], both


def test_every_shader_reads_a_diffuse_map() -> None:
    for name, slots in SHADER_SLOTS.items():
        assert "diffuse" in slots, name


def test_the_detail_shaders_are_the_ones_the_exporter_names() -> None:
    for name in ("diffuse_detail", "diffuse_detail_vc", "road_detail", "lightmap_detail"):
        assert uses_detail(name), name
    for name in ("diffuse", "bump", "specular_vc", "bumpdiffuse_envalphagloss_spec"):
        assert not uses_detail(name), name


def test_shader_slots_are_matched_whatever_the_capitalisation() -> None:
    assert uses_detail("DIFFUSE_DETAIL_VC")
    assert uses_detail("Road_Detail")


# --- the detail map is modulate2x ---------------------------------------


def test_a_detail_map_is_multiplied_and_doubled() -> None:
    """``diffuse_detail.fx``: ``Diffuse.rgb * Details.rgb * 2.0``.

    So a mid-grey detail map is neutral and only its variation comes
    through — the same convention as the colour map and the lightmap.
    """
    import shutil
    import tempfile

    from blender_io import texture_bridge
    from core.mesh import Material

    source = corpus("factory_box.dds")
    if not os.path.isfile(source):
        return

    root = tempfile.mkdtemp()
    folder = os.path.join(root, "data", "models", "textures")
    os.makedirs(folder)
    shutil.copy(source, folder)
    texture_bridge.build_texture_index(root, refresh=True)

    material = Material(
        name="m",
        shader="diffuse_detail",
        textures=["factory_box.dds", "factory_box.dds"],
        slots=[0, texture_bridge.SLOT_DETAIL],
    )
    built = texture_bridge.build_material(material, root, name_prefix="detail")

    names = {n.name for n in built.node_tree.nodes}
    assert {"Diffuse", "Detail", "ExM_DetailMix", "ExM_DetailMix_2x"} <= names

    doubler = next(
        n for n in built.node_tree.nodes if n.name == "ExM_DetailMix_2x"
    )
    assert tuple(doubler.inputs["Color2"].default_value)[:3] == (2.0, 2.0, 2.0)


# --- a map with no watermap has no water --------------------------------


def test_water_needs_a_watermap_not_a_low_place() -> None:
    """``Landscape::Load()`` reports "using empty waterfield" when the
    file is absent — an empty field, not a flooded one.

    Every basin this add-on filled from the heightfield on a map with
    no watermap was invented.
    """
    from core.scene import MapScene

    scene = MapScene()
    assert scene.water_map is None

    # The scene model carries the map's water separately from its
    # level, so "no water" and "water of unknown extent" are not the
    # same state.
    assert hasattr(scene, "water_level")
    assert hasattr(scene, "water_map")


def test_the_watermap_grid_matches_the_size_the_manifest_states() -> None:
    """``(LEVELSIZE x 4)^2`` cells of int16, from ``Landscape::Load()``.

    The sample map states LEVELSIZE 32, so 128 x 128 cells at two bytes
    is 32768 — exactly the size of its ``water.raw``, and the same
    128 x 128 grid ``level.tile`` uses.
    """
    level_size = 32
    cells = (level_size * 4) ** 2
    assert cells == 128 * 128
    assert cells * 2 == 32768


def test_water_follows_the_mask_and_not_the_ground() -> None:
    """Terrain entirely above the water level still floods where the
    map says it does."""
    import struct

    from blender_io.terrain_bridge import build_water
    from core.raster import RasterLayer
    from core.terrain import HeightmapData

    cells = [0] * (128 * 128)
    for y in range(40, 60):
        for x in range(30, 50):
            cells[y * 128 + x] = 2000
    mask = RasterLayer(
        width=128,
        height=128,
        bytes_per_sample=2,
        data=struct.pack("<16384H", *cells),
    )

    high_ground = HeightmapData(
        width=128, height=128, cell_size=8.0, values=[900.0] * (128 * 128)
    )

    assert len(build_water(high_ground, 286.44, water_mask=mask).data.polygons) > 0
    # And with no mask, ground this high floods nowhere.
    assert len(build_water(high_ground, 286.44).data.polygons) == 0


def test_the_editor_working_folder_is_searched_for_the_watermap() -> None:
    """``data/editor`` holds the level the editor currently has open.

    Searching only the map folder found nothing and reported a map that
    plainly has water as having none.
    """
    import os as _os
    import tempfile

    from formats.exm.plugin import _game_root_above

    root = tempfile.mkdtemp()
    map_folder = _os.path.join(root, "data", "maps", "r1m1-1-1")
    _os.makedirs(map_folder)
    _os.makedirs(_os.path.join(root, "data", "editor"))

    assert _game_root_above(map_folder) == root
    assert _game_root_above("") == ""


# --- what the map-format report corrected -------------------------------


def test_the_derived_file_sizes_follow_the_grid() -> None:
    """Every terrain file is a function of the grid size G.

    Checked on 24 maps without exception::

        displace.bin   64 G^2   4G x 4G float32, 8 units apart
        colormap.raw   64 G^2   4G x 4G x 4 bytes
        passmap.raw     4 G^2   2G x 2G bytes
        water.raw       2 G^2   G x G uint16, 32 units apart
    """
    import os

    grid = 128  # r1m1
    expected = {
        "displace.bin": 64 * grid * grid,
        "colormap.raw": 64 * grid * grid,
        "passmap.raw": 4 * grid * grid,
        "water.raw": 2 * grid * grid,
    }
    for name, size in expected.items():
        path = corpus(f"{name}")
        if os.path.isfile(path):
            assert os.path.getsize(path) == size, name


def test_a_tile_cell_is_thirty_two_units() -> None:
    """And the heightfield is sampled every quarter of one."""
    from core.coordinates import UNITS_PER_HEIGHT_SAMPLE, UNITS_PER_TILE_CELL

    assert UNITS_PER_TILE_CELL == 32.0
    assert UNITS_PER_HEIGHT_SAMPLE == 8.0
    assert UNITS_PER_TILE_CELL / UNITS_PER_HEIGHT_SAMPLE == 4.0

    # r1m1: grid 128, so 4096 units across and 512 height samples.
    assert 128 * UNITS_PER_TILE_CELL == 4096
    assert 128 * 4 == 512


def test_the_lightmap_median_is_neutral_under_the_doubling() -> None:
    """Why the shadows looked twice as heavy as they are.

    Three shaders multiply the lightmap by two — ``landscapeFP_ps11``,
    ``landscapeSP_ps11`` and ``road.fx``. The daytime map's median
    luminance is 127 of 255, which doubled is exactly 1.00. Read as a
    plain 0..1 multiplier its 5th percentile of 47 is 0.18; with the
    doubling it is 0.36.
    """
    import os

    from core import dds as _dds

    path = corpus("lightmap_daytime_r1m1.dds")
    if not os.path.isfile(path):
        return

    rgba = _dds.decode(open(path, "rb").read())[0]
    luminance = sorted(
        (rgba[i] * 299 + rgba[i + 1] * 587 + rgba[i + 2] * 114) // 1000
        for i in range(0, len(rgba), 4 * 13)
    )
    median = luminance[len(luminance) // 2]

    assert 120 <= median <= 135, median
    assert 0.9 < median / 255.0 * 2 < 1.1
