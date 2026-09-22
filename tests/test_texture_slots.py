# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for texture slots and transparency in the material bridge.

The bush is the case that motivated all of this: it looked like a
broken texture and was a material never told the texture had alpha.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

import bpy  # noqa: E402

from blender_io import texture_bridge  # noqa: E402
from core import dds  # noqa: E402
from core.mesh import Material  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402

BUSH = os.path.join(CORPUS, "kustarnik_1.dds")
OPAQUE = os.path.join(CORPUS, "factory_box.dds")


def _fresh():
    bpy.data.materials.clear()
    bpy.data.images.clear()
    texture_bridge.reset_missing_textures()


def _game_root(*textures: str) -> str | None:
    """A throwaway game tree holding the named corpus textures."""
    if not all(os.path.isfile(t) for t in textures):
        return None
    root = tempfile.mkdtemp()
    folder = os.path.join(root, "data", "models", "textures")
    os.makedirs(folder)
    for texture in textures:
        shutil.copy(texture, folder)
    texture_bridge.build_texture_index(root, refresh=True)
    return root


def _linked(material, from_node, from_socket, to_node, to_socket) -> bool:
    """Whether a link exists, compared by name as bpy requires."""
    for link in material.node_tree.links:
        if (
            link.from_node.name == from_node
            and link.from_socket.name == from_socket
            and link.to_node.name == to_node
            and link.to_socket.name == to_socket
        ):
            return True
    return False


def _nodes(material):
    return {n.name: n for n in material.node_tree.nodes if n.type != "BSDF_PRINCIPLED"}


# --- slots ------------------------------------------------------------


def test_nodes_are_named_from_the_slot_not_the_position() -> None:
    """A material carrying only slot 1 must not call it Diffuse."""
    _fresh()
    material = Material(
        name="m", shader="bump", textures=["only_a_bump.dds"], slots=[1]
    )
    built = texture_bridge.build_material(material, None, name_prefix="test")
    assert "Bump" in _nodes(built)
    assert "Diffuse" not in _nodes(built)


def test_two_slots_land_on_their_own_nodes() -> None:
    _fresh()
    material = Material(
        name="m",
        shader="bump",
        textures=["base.dds", "bump.dds"],
        slots=[0, 1],
    )
    nodes = _nodes(texture_bridge.build_material(material, None, name_prefix="test"))
    # Labelled as missing, since no game folder was given to find them in.
    assert nodes["Diffuse"].label.startswith("base.dds")
    assert nodes["Bump"].label.startswith("bump.dds")


def test_without_slots_position_is_used_as_a_fallback() -> None:
    """The string-scan path recovers names and not slot numbers."""
    _fresh()
    material = Material(name="m", shader="diffuse", textures=["a.dds", "b.dds"])
    nodes = _nodes(texture_bridge.build_material(material, None, name_prefix="test"))
    assert nodes["Diffuse"].label.startswith("a.dds")
    assert nodes["Bump"].label.startswith("b.dds")


def test_an_unmeasured_slot_keeps_its_number() -> None:
    _fresh()
    material = Material(name="m", shader="x", textures=["odd.dds"], slots=[9])
    assert "Slot9" in _nodes(texture_bridge.build_material(material, None, name_prefix="test"))


# --- transparency -----------------------------------------------------


def test_the_bush_texture_makes_its_material_transparent() -> None:
    """DXT5 with real semi-transparency, not a cutout."""
    root = _game_root(BUSH)
    if root is None:
        return
    _fresh()

    material = Material(
        name="m", shader="diffuse", textures=["kustarnik_1.dds"], slots=[0]
    )
    built = texture_bridge.build_material(material, root, name_prefix="test")

    assert built.blend_method == texture_bridge.TRANSPARENT_BLEND_METHOD
    assert built.shadow_method == texture_bridge.TRANSPARENT_BLEND_METHOD

    principled = next(
        n for n in built.node_tree.nodes if n.type == "BSDF_PRINCIPLED"
    )
    diffuse = _nodes(built)["Diffuse"]
    assert _linked(built, "Diffuse", "Alpha", principled.name, "Alpha")
    assert _linked(built, "Diffuse", "Color", principled.name, "Base Color")


def test_an_opaque_texture_leaves_the_material_opaque() -> None:
    """Otherwise every material blends and the viewport crawls."""
    root = _game_root(OPAQUE)
    if root is None:
        return
    _fresh()

    material = Material(
        name="m", shader="diffuse", textures=["factory_box.dds"], slots=[0]
    )
    built = texture_bridge.build_material(material, root, name_prefix="test")

    assert built.blend_method == "OPAQUE"
    principled = next(
        n for n in built.node_tree.nodes if n.type == "BSDF_PRINCIPLED"
    )
    diffuse = _nodes(built)["Diffuse"]
    assert not _linked(built, "Diffuse", "Alpha", principled.name, "Alpha")


def test_alpha_is_judged_from_the_file_not_from_blenders_depth() -> None:
    """The two DDS files in the corpus, straight from the codec."""
    if not (os.path.isfile(BUSH) and os.path.isfile(OPAQUE)):
        return
    assert dds.has_alpha(open(BUSH, "rb").read()) is True
    assert dds.has_alpha(open(OPAQUE, "rb").read()) is False


def test_a_missing_texture_does_not_make_the_material_transparent() -> None:
    """No image means no evidence, and guessing would blend everything."""
    _fresh()
    material = Material(
        name="m", shader="diffuse", textures=["nowhere.dds"], slots=[0]
    )
    built = texture_bridge.build_material(material, None, name_prefix="test")
    assert built.blend_method == "OPAQUE"


# --- textures Blender itself cannot read -------------------------------


def test_a_dds_blender_declines_is_decoded_by_the_sdk() -> None:
    """A refused texture is indistinguishable from a missing one.

    The model arrives with a shader, no image and a flat colour, which
    reads as "the textures stopped working" rather than as one file
    Blender would not open. Since the SDK writes this format it can
    read it too.
    """
    from blender_io.texture_bridge import _load_dds_ourselves

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "t.dds")
        source = bytearray()
        for y in range(8):
            for x in range(8):
                source += bytes((x * 30 % 256, y * 30 % 256, 200, 255))
        dds.write(path, bytes(source), 8, 8)

        image = _load_dds_ourselves(path, "t.dds")
        assert image is not None
        assert tuple(image.size) == (8, 8)
        assert len(image.pixels) == 8 * 8 * 4


def test_the_fallback_flips_the_image_the_right_way_up() -> None:
    """DDS is top-down, Blender bottom-up."""
    from blender_io.texture_bridge import _load_dds_ourselves

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "t.dds")
        source = bytearray()
        for y in range(8):
            for x in range(8):
                # Green ramps down the file, so the last file row is
                # brightest and must land at Blender's first row.
                source += bytes((0, y * 36, 0, 255))
        dds.write(path, bytes(source), 8, 8, fmt=dds.A8R8G8B8, mipmaps=False)

        image = _load_dds_ourselves(path, "t.dds")
        first_row_green = image.pixels[1]
        last_row_green = image.pixels[(7 * 8 * 4) + 1]
        assert first_row_green > last_row_green


def test_something_that_is_not_a_dds_is_left_alone() -> None:
    from blender_io.texture_bridge import _load_dds_ourselves

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "t.png")
        open(path, "wb").write(b"not a dds")
        assert _load_dds_ourselves(path, "t.png") is None


# --- stale datablocks in the .blend ------------------------------------


def test_a_material_with_no_working_texture_is_rebuilt_not_reused() -> None:
    """Materials persist in the .blend and are looked up by name.

    An import that once produced a material whose texture would not
    load leaves that material behind. Every later import finds it by
    name and returns it, so the model stays flat-coloured however many
    times it is re-imported — and reinstalling the add-on changes
    nothing, because the material lives in the file.
    """
    root = _game_root(OPAQUE)
    if root is None:
        return
    _fresh()

    material = Material(
        name="m", shader="diffuse", textures=["factory_box.dds"], slots=[0]
    )
    built = texture_bridge.build_material(material, root, name_prefix="test")
    assert texture_bridge._is_complete(built, material)

    for node in [n for n in built.node_tree.nodes if n.type == "TEX_IMAGE"]:
        built.node_tree.nodes.remove(node)
    built.node_tree.links.clear()
    assert not texture_bridge._is_complete(built, material)

    again = texture_bridge.build_material(material, root, name_prefix="test")
    assert again is built, "a second datablock was made instead of a repair"
    assert texture_bridge._is_complete(again, material)


def test_a_complete_material_is_reused_untouched() -> None:
    """Rebuilding everything every time would undo the user's edits."""
    root = _game_root(OPAQUE)
    if root is None:
        return
    _fresh()

    material = Material(
        name="m", shader="diffuse", textures=["factory_box.dds"], slots=[0]
    )
    first = texture_bridge.build_material(material, root, name_prefix="test")
    nodes_before = len(first.node_tree.nodes)

    second = texture_bridge.build_material(material, root, name_prefix="test")
    assert second is first
    assert len(second.node_tree.nodes) == nodes_before


def test_a_material_naming_no_texture_is_always_accepted() -> None:
    """Nothing to check, so nothing to rebuild."""
    _fresh()
    material = Material(name="m", shader="diffuse")
    built = texture_bridge.build_material(material, None, name_prefix="test")
    assert texture_bridge._is_complete(built, material)


def test_an_empty_image_datablock_is_not_mistaken_for_a_texture() -> None:
    assert not texture_bridge._has_pixels(
        fake_bpy.FakeImage("x", size=(0, 0), has_data=False)
    )
    assert texture_bridge._has_pixels(fake_bpy.FakeImage("x", size=(64, 64)))


# --- vertex-colour shaders ---------------------------------------------


def test_a_vc_shader_multiplies_the_texture_by_the_vertex_colour() -> None:
    """The split between the models that look right and the ones that don't.

    Every model using a ``_vc`` shader carries vertex colours and looks
    wrong in Blender; every model without one carries none and looks
    right. house2 runs its colours from 255 down to 0 — a third of them
    below white — so leaving them out shows a clean bright wall where
    the game shows a shaded one.
    """
    root = _game_root(OPAQUE)
    if root is None:
        return
    _fresh()

    material = Material(
        name="m", shader="diffuse_vc", textures=["factory_box.dds"], slots=[0]
    )
    built = texture_bridge.build_material(material, root, name_prefix="test")

    types = {n.type for n in built.node_tree.nodes}
    assert "VERTEX_COLOR" in types
    assert "MIX_RGB" in types

    mix = next(n for n in built.node_tree.nodes if n.type == "MIX_RGB")
    assert mix.blend_type == "MULTIPLY"

    assert _linked(built, "Diffuse", "Color", mix.name, "Color1")
    assert _linked(built, mix.name, "Color", "BSDF_PRINCIPLED", "Base Color")


def test_the_texture_does_not_also_bypass_the_mix() -> None:
    """Blender replaces an input's link silently; two would mislead."""
    root = _game_root(OPAQUE)
    if root is None:
        return
    _fresh()

    material = Material(
        name="m", shader="specular_vc", textures=["factory_box.dds"], slots=[0]
    )
    built = texture_bridge.build_material(material, root, name_prefix="test")
    assert not _linked(built, "Diffuse", "Color", "BSDF_PRINCIPLED", "Base Color")


def test_a_plain_shader_wires_the_texture_straight_through() -> None:
    root = _game_root(OPAQUE)
    if root is None:
        return
    _fresh()

    material = Material(
        name="m", shader="diffuse", textures=["factory_box.dds"], slots=[0]
    )
    built = texture_bridge.build_material(material, root, name_prefix="test")

    assert "VERTEX_COLOR" not in {n.type for n in built.node_tree.nodes}
    assert _linked(built, "Diffuse", "Color", "BSDF_PRINCIPLED", "Base Color")


def test_the_vc_suffix_is_what_marks_a_vertex_colour_shader() -> None:
    assert texture_bridge.uses_vertex_color("diffuse_vc")
    assert texture_bridge.uses_vertex_color("specular_vc")
    assert not texture_bridge.uses_vertex_color("diffuse")
    assert not texture_bridge.uses_vertex_color("bump")
    assert not texture_bridge.uses_vertex_color("")


def test_a_material_behind_a_mix_node_still_counts_as_complete() -> None:
    """Otherwise every vertex-colour material is rebuilt on every import."""
    root = _game_root(OPAQUE)
    if root is None:
        return
    _fresh()

    material = Material(
        name="m", shader="diffuse_vc", textures=["factory_box.dds"], slots=[0]
    )
    built = texture_bridge.build_material(material, root, name_prefix="test")
    assert texture_bridge._is_complete(built, material)

    again = texture_bridge.build_material(material, root, name_prefix="test")
    assert again is built
    assert len([n for n in again.node_tree.nodes if n.type == "MIX_RGB"]) == 1


# --- the ambient occlusion slot ----------------------------------------


def test_a_lightmap_is_multiplied_into_the_base_colour() -> None:
    """Slot 2 on the ``DiffuseAO``/``SpecularAO`` shaders.

    TheTown carries MininAO.dds there on all six materials. Loaded and
    left unlinked, the diffuse map renders at full strength where the
    game shows it occluded.
    """
    root = _game_root(OPAQUE)
    if root is None:
        return
    _fresh()

    material = Material(
        name="m",
        shader="DiffuseAO",
        textures=["factory_box.dds", "factory_box.dds"],
        slots=[0, 2],
    )
    built = texture_bridge.build_material(
        material, root, name_prefix="test", uv2_available=True
    )

    assert "Lightmap" in _nodes(built)
    mix = next(
        n for n in built.node_tree.nodes if n.name == "ExM_LightmapMix"
    )
    assert mix.blend_type == "MULTIPLY"
    assert _linked(built, "Diffuse", "Color", mix.name, "Color1")
    assert _linked(built, "Lightmap", "Color", mix.name, "Color2")
    assert _linked(built, mix.name, "Color", "BSDF_PRINCIPLED", "Base Color")


def test_a_lightmap_and_a_vertex_colour_chain_in_order() -> None:
    """Both modulations apply, diffuse then AO then vertex colour."""
    root = _game_root(OPAQUE)
    if root is None:
        return
    _fresh()

    material = Material(
        name="m",
        shader="diffuse_vc",
        textures=["factory_box.dds", "factory_box.dds"],
        slots=[0, 2],
    )
    built = texture_bridge.build_material(
        material, root, name_prefix="test", uv2_available=True
    )

    assert _linked(built, "ExM_LightmapMix", "Color", "ExM_VertexColorMix", "Color1")
    assert _linked(
        built, "ExM_VertexColorMix", "Color", "BSDF_PRINCIPLED", "Base Color"
    )
    assert not _linked(built, "Diffuse", "Color", "BSDF_PRINCIPLED", "Base Color")


def test_a_lightmap_without_its_own_uvs_is_loaded_but_not_linked() -> None:
    """Sampling it with the diffuse UVs lays one image over another.

    TheTown carries an AO map on all six materials and no second UV
    set. Linked to the diffuse coordinates it looked visibly worse than
    leaving it out — which is the evidence this condition rests on.
    """
    root = _game_root(OPAQUE)
    if root is None:
        return
    _fresh()

    material = Material(
        name="m",
        shader="DiffuseAO",
        textures=["factory_box.dds", "factory_box.dds"],
        slots=[0, 2],
    )
    # A distinct prefix, so this cannot pick up the cached material the
    # test above built with a second UV set available.
    built = texture_bridge.build_material(
        material, root, name_prefix="nouv2", uv2_available=False
    )

    assert "Lightmap" in _nodes(built), "the map should still be loaded"
    assert not any(n.name == "ExM_LightmapMix" for n in built.node_tree.nodes)
    assert _linked(built, "Diffuse", "Color", "BSDF_PRINCIPLED", "Base Color")


def test_slot_two_is_named_lightmap() -> None:
    assert texture_bridge._slot_node_name(texture_bridge.SLOT_LIGHTMAP) == "Lightmap"


def test_a_lightmap_samples_the_second_uv_set_not_the_active_one() -> None:
    """An image node with an empty Vector input takes the active UV map.

    That is the diffuse one. A lightmap left to do it lays itself over
    the diffuse map in the diffuse map's own coordinates — two
    unrelated images on top of each other, which is what a lightmap
    linked without this looks like.
    """
    root = _game_root(OPAQUE)
    if root is None:
        return
    _fresh()

    material = Material(
        name="m",
        shader="diffuseAO",
        textures=["factory_box.dds", "factory_box.dds"],
        slots=[0, 2],
    )
    built = texture_bridge.build_material(
        material, root, name_prefix="uvmapped", uv2_available=True
    )

    uv_nodes = [n for n in built.node_tree.nodes if n.type == "UVMAP"]
    assert len(uv_nodes) == 1
    assert uv_nodes[0].uv_map == texture_bridge.UV2_LAYER
    assert _linked(built, uv_nodes[0].name, "UV", "Lightmap", "Vector")

    # And the diffuse map keeps the active layer.
    assert not _linked(built, uv_nodes[0].name, "UV", "Diffuse", "Vector")


def test_shader_names_are_matched_whatever_their_capitalisation() -> None:
    """1224 materials say BumpDiffuse_EnvAlphaGloss_Spec and 372 say it lowercase."""
    from formats.exm.skin import KNOWN_SHADERS, normalise_shader

    for spelling in ("DiffuseAO", "diffuseAO", "SpecularAO", "Diffuse_VC", "Bump"):
        assert normalise_shader(spelling) in KNOWN_SHADERS, spelling


# --- the diffuse UV must be the one Texture Paint uses -----------------


def test_the_diffuse_uv_layer_is_the_active_one() -> None:
    """``active`` and ``active_render`` are different flags.

    Renders follow active_render; Texture Paint and the viewport's
    texture display follow active, and Blender gives that to whichever
    layer was created last. A model with a lightmap unwrap then paints
    and previews through the lightmap's coordinates — every face
    sampling a small unrelated patch — which reads as coloured noise
    over a texture that is perfectly intact in the Image Editor.
    """
    from blender_io.mesh_bridge import UV_LAYER, build_model_mesh
    from formats.exm.gam import read_model

    path = os.path.join(CORPUS, "petrolstation.gam")
    if not os.path.isfile(path):
        return

    mesh = build_model_mesh(read_model(path), "petrolstation")
    names = [layer.name for layer in mesh.uv_layers]
    assert names == ["UVMap", "UVMap2"], names
    assert names[mesh.uv_layers.active_index] == UV_LAYER


def test_swapping_an_image_rewrites_the_models_texture_name() -> None:
    """Copying a new file beside the model changes nothing on its own.

    The .gam names its textures in the skin chunk, and until that name
    changes the game reloads the old file — which is what "I swapped
    the texture in the material and nothing happened" is.
    """
    import shutil
    import tempfile as _tempfile

    from blender_io.texture_export import retarget_model_textures
    from core import dds as _dds
    from formats.exm.gam import read_container
    from formats.exm.skin import parse_skin

    source = os.path.join(CORPUS, "factory_box.gam")
    if not os.path.isfile(source):
        return

    folder = _tempfile.mkdtemp()
    target = os.path.join(folder, "factory_box.gam")
    shutil.copy(source, target)
    _dds.write(os.path.join(folder, "new_wall.dds"), bytes([10, 20, 30, 255]) * 256, 16, 16)

    material = bpy.data.materials.new("swapped")
    material.use_nodes = True
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.name = "Diffuse"
    node.image = bpy.data.images.load(os.path.join(folder, "new_wall.dds"))

    mesh = bpy.data.meshes.new("swapped")
    mesh.materials.append(material)
    obj = bpy.data.objects.new("swapped", mesh)

    changes = retarget_model_textures(target, [obj])
    assert len(changes) == 1, changes

    skin = parse_skin(
        next(c.data for c in read_container(target)[1] if c.chunk_id == 15)
    )
    assert skin.materials[0].texture_for_slot(0) == "new_wall.dds"
    # In place: the chunk keeps its size, so every byte we do not
    # understand is still there.
    assert os.path.getsize(target) == os.path.getsize(source)
