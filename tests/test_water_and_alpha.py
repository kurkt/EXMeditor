# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the water plane and for telling a cutout from a gloss mask."""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.terrain_bridge import (  # noqa: E402
    WATER_LEVEL_PROP,
    build_water,
)
from core import dds  # noqa: E402
from core.terrain import HeightmapData  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402

CUTOUT = os.path.join(CORPUS, "kustarnik_1.dds")
GLOSS = os.path.join(CORPUS, "metal_elements_roof.dds")
OPAQUE = os.path.join(CORPUS, "factory_box.dds")


# --- alpha that is not transparency ------------------------------------


def test_a_cutout_has_solid_parts_and_a_gloss_mask_has_none() -> None:
    """The measurement that separates them, and they are not close.

        kustarnik_1.dds          28.2% solid, 49.1% clear   cutout
        metal_elements_roof.dds   0.0% solid, 29.6% clear   gloss mask

    Not one texel of the roof texture is opaque. Read as transparency it
    makes a roof you can see straight through.
    """
    if os.path.isfile(CUTOUT):
        opaque, clear = dds.alpha_profile(open(CUTOUT, "rb").read())
        assert opaque > 0.2 and clear > 0.2
        assert dds.alpha_is_cutout(open(CUTOUT, "rb").read())

    if os.path.isfile(GLOSS):
        opaque, clear = dds.alpha_profile(open(GLOSS, "rb").read())
        assert opaque == 0.0
        assert clear > 0.2
        assert not dds.alpha_is_cutout(open(GLOSS, "rb").read())


def test_a_texture_with_no_alpha_is_not_a_cutout() -> None:
    if not os.path.isfile(OPAQUE):
        return
    data = open(OPAQUE, "rb").read()
    assert not dds.has_alpha(data)
    assert not dds.alpha_is_cutout(data)


def test_a_specular_shader_never_treats_its_alpha_as_transparency() -> None:
    """The convention of the era: gloss lives in the diffuse map's alpha."""
    from blender_io.texture_bridge import _image_has_alpha

    assert not _image_has_alpha(None, "anything.dds", None, "specular_vc")
    assert not _image_has_alpha(None, "anything.dds", None, "SpecularAO")


# --- the water plane ----------------------------------------------------


def _heightmap(side: int = 8, height: float = 0.0) -> HeightmapData:
    return HeightmapData(
        width=side, height=side, cell_size=8.0, values=[height] * (side * side)
    )


def test_the_plane_sits_at_the_level_the_manifest_states() -> None:
    obj = build_water(_heightmap(height=0.0), 286.44)
    zs = {round(v.co[2], 3) for v in obj.data.vertices}
    assert zs == {286.44}
    assert obj[WATER_LEVEL_PROP] == 286.44


def test_water_spans_the_terrain_where_the_terrain_is_submerged() -> None:
    """Grid positions come from the same expressions the terrain uses."""
    side, cell = 8, 8.0
    # Every sample below the line, so the surface covers the whole map.
    obj = build_water(_heightmap(side, height=0.0), 100.0)

    xs = [v.co[0] for v in obj.data.vertices]
    ys = [v.co[1] for v in obj.data.vertices]
    assert min(xs) == 0.0 and min(ys) == 0.0
    assert max(xs) == (side - 1) * cell
    assert max(ys) == (side - 1) * cell
    assert len(obj.data.polygons) == (side - 1) ** 2


def test_the_real_water_level_falls_inside_the_real_terrain() -> None:
    """286.44 against a heightfield running 219.78 to 540.66.

    A level outside that range would mean the two are measured in
    different units, and the plane would sit far above or below
    anything visible.
    """
    path = os.path.join(CORPUS, "displace.bin")
    if not os.path.isfile(path):
        return
    data = open(path, "rb").read()
    heights = struct.unpack(f"<{len(data) // 4}f", data)

    assert min(heights) < 286.44 < max(heights)
    below = sum(1 for h in heights if h < 286.44)
    assert 0 < below < len(heights)


def test_water_covers_only_the_ground_below_it() -> None:
    """A plane across the whole level puts water where the map is dry.

    The original editor shows water only in basins, so the surface is
    built cell by cell from the heightfield.
    """
    values = [300.0] * 16
    values[5] = 100.0  # one submerged sample, in the middle
    heightmap = HeightmapData(width=4, height=4, cell_size=8.0, values=values)

    obj = build_water(heightmap, 286.44)
    # The four cells touching that sample, and no others.
    assert len(obj.data.polygons) == 4
    assert len(obj.data.vertices) == 9


def test_a_map_entirely_above_the_line_gets_no_water() -> None:
    heightmap = HeightmapData(
        width=4, height=4, cell_size=8.0, values=[900.0] * 16
    )
    obj = build_water(heightmap, 286.44)
    assert len(obj.data.polygons) == 0


def test_the_real_map_floods_a_believable_share() -> None:
    """7.4% of cells, against 6.8% of vertices below the line."""
    path = os.path.join(CORPUS, "displace.bin")
    if not os.path.isfile(path):
        return
    data = open(path, "rb").read()
    heights = list(struct.unpack(f"<{len(data) // 4}f", data))
    heightmap = HeightmapData(
        width=512, height=512, cell_size=8.0, values=heights
    )

    obj = build_water(heightmap, 286.44)
    share = len(obj.data.polygons) / (511 * 511)
    assert 0.01 < share < 0.2, share


def test_a_gloss_mask_drives_roughness_instead_of_transparency() -> None:
    """The roof texture: DXT5, no opaque texel, shader says specular.

    Its alpha is what gives slate and metal their sheen, and much of
    what makes their surface detail read at all. Read as transparency
    it made a roof you could see through; ignored entirely it made a
    flat one.
    """
    import shutil
    import tempfile as _tempfile

    from blender_io import texture_bridge
    from core.mesh import Material

    gloss = os.path.join(CORPUS, "metal_elements_roof.dds")
    if not os.path.isfile(gloss):
        return

    root = _tempfile.mkdtemp()
    folder = os.path.join(root, "data", "models", "textures")
    os.makedirs(folder)
    shutil.copy(gloss, folder)
    texture_bridge.build_texture_index(root, refresh=True)

    material = Material(
        name="m",
        shader="specular_vc",
        textures=["metal_elements_roof.dds"],
        slots=[0],
    )
    built = texture_bridge.build_material(material, root, name_prefix="gloss")

    assert built.blend_method == "OPAQUE"
    assert any(n.name == "ExM_GlossToRoughness" for n in built.node_tree.nodes)


def test_a_cutout_gets_transparency_and_no_gloss_wiring() -> None:
    import shutil
    import tempfile as _tempfile

    from blender_io import texture_bridge
    from core.mesh import Material

    if not os.path.isfile(CUTOUT):
        return

    root = _tempfile.mkdtemp()
    folder = os.path.join(root, "data", "models", "textures")
    os.makedirs(folder)
    shutil.copy(CUTOUT, folder)
    texture_bridge.build_texture_index(root, refresh=True)

    material = Material(
        name="m", shader="diffuse", textures=["kustarnik_1.dds"], slots=[0]
    )
    built = texture_bridge.build_material(material, root, name_prefix="cut")

    assert built.blend_method == texture_bridge.TRANSPARENT_BLEND_METHOD
    assert not any(
        n.name == "ExM_GlossToRoughness" for n in built.node_tree.nodes
    )
