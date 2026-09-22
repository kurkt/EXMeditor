# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""A shader that reads a vertex colour, on a mesh that has none.

Reported from a real import as foliage that "loads more correctly —
transparent but colourless". Both halves are the same cause: the alpha
comes straight from the image and is unaffected, while the base colour
is multiplied by a Color Attribute node naming a layer that is not
there — which Blender answers with black.

Vertex types 7, 10, 11 and 15 carry no colour at all, so a mesh built
from one of them has no layer however loudly the shader name asks.
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

from blender_io.texture_bridge import (  # noqa: E402
    COLOR_LAYER,
    build_material,
    build_texture_index,
    reset_missing_textures,
)
from core import dds  # noqa: E402
from core.mesh import Material  # noqa: E402

TEXTURE = "kustarnik_1.dds"


def _root() -> str:
    root = tempfile.mkdtemp()
    path = os.path.join(root, "data", "models", "textures", "nature", TEXTURE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    dds.write(path, bytes([90, 140, 60, 255]) * (32 * 32), 32, 32)
    build_texture_index(root, refresh=True)
    return root


def _material() -> Material:
    """A bush: the shader reads a vertex colour, the mesh may not have one."""
    return Material(
        name="bush", shader="diffuse_vc", textures=[TEXTURE], slots=[0]
    )


def _reset() -> None:
    reset_missing_textures()
    for image in list(bpy.data.images):
        bpy.data.images.remove(image)
    bpy.data.images.refuse = set()
    for name in list(bpy.data.materials.keys()):
        del bpy.data.materials[name]


def _nodes(material) -> set:
    return {n.type for n in material.node_tree.nodes}


def _links(material):
    return [
        (l.from_node.name, l.from_socket.name, l.to_node.name, l.to_socket.name)
        for l in material.node_tree.links
    ]


def test_a_mesh_without_the_layer_does_not_get_a_vertex_colour_node() -> None:
    """The regression. The node was wired on the shader name alone."""
    _reset()
    built = build_material(_material(), _root(), vertex_color_available=False)

    assert "VERTEX_COLOR" not in _nodes(built)
    # And the texture still reaches the base colour, undimmed.
    assert ("Diffuse", "Color", "BSDF_PRINCIPLED", "Base Color") in _links(built)


def test_a_mesh_with_the_layer_still_gets_one() -> None:
    """The guard must not throw away the vertex colour where it exists —
    it is what makes one wall dirty and the next one clean."""
    _reset()
    built = build_material(_material(), _root(), vertex_color_available=True)

    assert "VERTEX_COLOR" in _nodes(built)
    node = next(
        n for n in built.node_tree.nodes if n.type == "VERTEX_COLOR"
    )
    assert node.layer_name == COLOR_LAYER


def test_the_two_are_not_the_same_material() -> None:
    """Materials are cached by name.

    Without this the first mesh to arrive decides for every later one,
    and a bush with no colour layer inherits the black-multiplying
    graph built for one that had it — the same bug by another road.
    """
    _reset()
    root = _root()
    with_layer = build_material(_material(), root, vertex_color_available=True)
    without = build_material(_material(), root, vertex_color_available=False)

    assert with_layer is not without
    assert with_layer.name != without.name
    assert without.name.endswith("_novc")


def test_a_shader_that_never_asked_is_named_as_before() -> None:
    """The suffix is only for shaders that read a vertex colour, so
    every other material keeps the name it has always had."""
    _reset()
    plain = Material(name="x", shader="diffuse", textures=[TEXTURE], slots=[0])
    built = build_material(plain, _root(), vertex_color_available=False)

    assert not built.name.endswith("_novc")
    assert "VERTEX_COLOR" not in _nodes(built)
