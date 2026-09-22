# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""What a diffuse texture's alpha means: transparency, or a gloss mask.

Decided by the shader. It used to be decided by counting texels — a
cutout was supposed to have more than 2% of its area fully opaque — and
a census of the whole corpus retired that test:

    265 textures the texel test called "not a cutout"
    175 used ONLY with gloss/specular/bump shaders, which the shader
        name already catches — including metal_elements_roof, the case
        the texel test existed for
     90 used with diffuse, diffuse_vc or road: d_grass, antens_alfa,
        resetka, the faction emblems. Plainly see-through, denied their
        alpha, and drawn as solid rectangles.

Soft-edged foliage is what it got wrong: tomato.dds is 0.2% solid and
dry_grass_1.dds 1.1%, because antialiased edges through DXT5 rarely
land on exactly 255.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.texture_bridge import (  # noqa: E402
    _alpha_is_gloss,
    _image_has_alpha,
    alpha_is_gloss_shader,
    build_texture_index,
)
from core import dds  # noqa: E402

SIDE = 32


def _texture(name: str, alphas) -> str:
    """A DDS whose alpha follows ``alphas(index)``. Returns a game root."""
    root = tempfile.mkdtemp()
    path = os.path.join(root, "data", "models", "textures", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    pixels = bytearray()
    for index in range(SIDE * SIDE):
        pixels += bytes([90, 140, 60, alphas(index)])
    dds.write(path, bytes(pixels), SIDE, SIDE)
    build_texture_index(root, refresh=True)
    return root


def _soft_foliage(index: int) -> int:
    """Like tomato.dds: a lot of clear, almost nothing fully opaque."""
    if index % 3 == 0:
        return 0
    return 250 if index % 97 == 0 else 200


def _hard_cutout(index: int) -> int:
    return 0 if index % 2 else 255


def _opaque(_index: int) -> int:
    return 255


# --- the shader is what decides -----------------------------------------


def test_a_gloss_shader_is_named_as_one() -> None:
    for shader in (
        "specular", "specular_vc", "SpecularAO",
        "bump", "bump_vc", "BumpDiffuse_EnvAlphaGloss_Spec",
    ):
        assert alpha_is_gloss_shader(shader) is True, shader

    for shader in (
        "diffuse", "diffuse_vc", "diffuse_detail", "tree_nolights",
        "road", "road_detail", "DiffuseAO", "Skinned",
    ):
        assert alpha_is_gloss_shader(shader) is False, shader


def test_soft_edged_foliage_is_transparent() -> None:
    """The regression. 0.2% of tomato.dds is fully opaque, and the old
    test wanted 2% — so the grass came in as solid grey rectangles."""
    root = _texture("tomato.dds", _soft_foliage)

    opaque, clear = dds.alpha_profile(
        open(os.path.join(root, "data", "models", "textures", "tomato.dds"), "rb").read()
    )
    assert opaque < 0.02          # exactly what the old test rejected
    assert clear > 0.1

    assert _image_has_alpha(None, "tomato.dds", root, "diffuse") is True


def test_a_gloss_mask_stays_a_gloss_mask() -> None:
    """The case the retired test was written for. It must not come back.

    metal_elements_roof is a continuous mask, and read as transparency
    it made a roof you could see through. Its shader is specular_vc,
    which is what catches it now.
    """
    root = _texture("metal_elements_roof.dds", _soft_foliage)

    assert _image_has_alpha(None, "metal_elements_roof.dds", root, "specular_vc") is False
    assert _alpha_is_gloss("metal_elements_roof.dds", root, "specular_vc") is True


def test_a_hard_cutout_on_a_gloss_shader_is_still_gloss() -> None:
    """The two halves have to agree.

    They did not: one counted texels and the other named the shader, so
    a gloss texture that happened to look like a cutout got neither
    transparency nor gloss and its alpha went nowhere.
    """
    root = _texture("shiny.dds", _hard_cutout)

    assert _image_has_alpha(None, "shiny.dds", root, "bump_vc") is False
    assert _alpha_is_gloss("shiny.dds", root, "bump_vc") is True


def test_a_texture_with_no_alpha_channel_is_opaque() -> None:
    """DXT1 is what an image with nothing to hide encodes as."""
    root = _texture("wall.dds", _opaque)

    assert _image_has_alpha(None, "wall.dds", root, "diffuse") is False
    assert _alpha_is_gloss("wall.dds", root, "diffuse") is False


def test_a_diffuse_shader_never_carries_gloss() -> None:
    """A plain diffuse shader has nowhere to put a mask — the game has
    specular, bump and bumpdiffuse_envalphagloss_spec for that."""
    root = _texture("emblem.dds", _soft_foliage)

    assert _alpha_is_gloss("emblem.dds", root, "diffuse") is False
    assert _image_has_alpha(None, "emblem.dds", root, "diffuse") is True
