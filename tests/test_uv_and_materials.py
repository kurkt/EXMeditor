# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the three faults behind "the textures slide".

All three were invisible in the reference corpus until the right model
was looked at, so each test names the file that exposes it.
"""

from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

import bpy  # noqa: E402

import formats.exm.gam as gam  # noqa: E402
from blender_io import texture_export  # noqa: E402
from blender_io.mesh_bridge import _extend_aligned  # noqa: E402
from core.mesh import MeshData, Model  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402



def _model(name: str):
    path = os.path.join(CORPUS, f"{name}.gam")
    return gam.read_model(path) if os.path.isfile(path) else None


# --- the vertex layout is declared, not guessed ------------------------


def test_the_declared_type_gives_the_layout_for_every_measured_model() -> None:
    for name in ("civilhouse1", "big_flag01", "kustarnik1", "factory_box"):
        model = _model(name)
        if model is None:
            continue
        for mesh in model.meshes:
            assert len(mesh.uvs) == len(mesh.positions), (name, mesh.name)


def test_unapplied_scale_no_longer_destroys_the_uvs() -> None:
    """``cube1111`` is the case that made every polygon share one patch.

    Its normals carry the object scale, so the fallback found no
    unit-length field, decided there was no normal, and read the UVs
    from offset 12 — the normal. The header declares type 15, whose
    UVs are at 24, and reading that field gives coordinates inside the
    unit square.
    """
    model = _model("cube1111")
    if model is None:
        return

    uvs = model.meshes[0].uvs
    assert len(uvs) == 8
    assert all(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 for u, v in uvs), uvs
    assert len(set(uvs)) > 1, "every vertex landed on the same texel"


def test_the_fallback_is_what_got_it_wrong() -> None:
    """Kept as evidence, so the reason for preferring the header stands."""
    path = os.path.join(CORPUS, "cube1111.gam")
    if not os.path.isfile(path):
        return

    _subtype, chunks = gam.read_container(path)
    block = next(c.data for c in chunks if c.chunk_id == gam.MESH_CHUNK_ID)

    guessed = gam._detect_layout(block, 72, 48, 8)
    declared = gam._layout_from_vertex_type(15, 48)

    assert declared.uv == 24 and declared.normal == 12
    assert guessed.uv != declared.uv


def test_an_unknown_vertex_type_still_falls_back() -> None:
    assert gam._layout_from_vertex_type(99, 48) is None
    # A declared type whose stride disagrees describes something else.
    assert gam._layout_from_vertex_type(7, 48) is None


# --- meshes name their own material ------------------------------------


def test_each_mesh_carries_the_material_its_header_names() -> None:
    """The material index is at offset 52, not 44.

    ``machine_house1`` is what settles it: 30 meshes, 6 materials, and
    chunk 2 naming exactly 30 nodes. Offset 44 counts 0..29 — the node
    — and offset 52 stays inside the material count. Read from 44, 24
    of its meshes clamped onto material 0 and a building came out
    wearing its own antenna texture.
    """
    machine = _model("machine_house1")
    if machine is not None:
        assert len(machine.meshes) == 30
        assert len(machine.materials) == 6
        assert [m.node_index for m in machine.meshes] == list(range(30))
        assert sorted({m.material_index for m in machine.meshes}) == [0, 1, 2, 3, 4, 5]

    civil = _model("civilhouse1")
    if civil is not None:
        assert [m.material_index for m in civil.meshes] == [0, 1, 2]

    flag = _model("big_flag01")
    if flag is not None:
        # Five meshes all drawing with the first of nine materials —
        # which mesh order cannot express either.
        assert [m.material_index for m in flag.meshes] == [0, 0, 0, 0, 0]
        assert [m.node_index for m in flag.meshes] == [1, 2, 3, 4, 6]


def test_no_mesh_names_a_material_the_model_does_not_have() -> None:
    """The property that separates offset 52 from offset 44."""
    for name in (
        "machine_house1",
        "civilhouse1",
        "bridge_concrete",
        "big_flag01",
        "house2",
        "kustarnik1",
        "factory_box",
    ):
        model = _model(name)
        if model is None:
            continue
        for mesh in model.meshes:
            assert 0 <= mesh.material_index < len(model.materials), (
                name,
                mesh.name,
                mesh.material_index,
            )


def test_the_face_distribution_stops_piling_onto_one_material() -> None:
    """1336 of 1486 triangles landed on the antenna texture."""
    model = _model("machine_house1")
    if model is None:
        return

    per_material = {}
    for mesh in model.meshes:
        per_material[mesh.material_index] = per_material.get(
            mesh.material_index, 0
        ) + len(mesh.triangles)

    assert per_material[0] == 66, per_material
    assert sum(per_material.values()) == 1486
    assert max(per_material.values()) < 600


def test_a_single_material_model_points_every_mesh_at_zero() -> None:
    for name in ("factory_box", "cube1111"):
        model = _model(name)
        if model is None:
            continue
        assert all(m.material_index == 0 for m in model.meshes)


# --- per-vertex arrays stay aligned ------------------------------------


def test_a_mesh_without_uvs_does_not_shift_the_next_one() -> None:
    """The merge indexes these lists by vertex, so they must stay level."""
    uvs: list = []
    _extend_aligned(uvs, [], 3, (0.0, 0.0))
    _extend_aligned(uvs, [(0.5, 0.5)] * 2, 2, (0.0, 0.0))

    assert len(uvs) == 5
    assert uvs[3] == (0.5, 0.5), "the second mesh's UVs landed on the wrong vertex"


def test_more_values_than_vertices_are_trimmed() -> None:
    out: list = []
    _extend_aligned(out, [1, 2, 3, 4], 2, 0)
    assert out == [1, 2]


# --- edited textures reach the game ------------------------------------


def _object_with_image(image):
    material = bpy.data.materials.new("m")
    material.use_nodes = True
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.name = "Diffuse"
    node.image = image

    mesh = bpy.data.meshes.new("mesh")
    obj = bpy.data.objects.new("obj", mesh)
    obj.data.materials.append(material)
    return obj


def test_an_edited_image_is_re_encoded_not_copied_from_disk() -> None:
    """Copying the file on disk exports the version before the edit."""
    with tempfile.TemporaryDirectory() as folder:
        original = os.path.join(folder, "src.dds")
        shutil.copy(os.path.join(CORPUS, "factory_box.dds"), original) if os.path.isfile(
            os.path.join(CORPUS, "factory_box.dds")
        ) else open(original, "wb").write(b"stale")

        image = fake_bpy.FakeImage("t", filepath=original, size=(4, 4))
        image.is_dirty = True

        model_path = os.path.join(folder, "out", "model.gam")
        os.makedirs(os.path.dirname(model_path))
        result = texture_export.export_textures_beside_model(
            [_object_with_image(image)], model_path
        )

        written = os.path.join(os.path.dirname(model_path), "src.dds")
        assert os.path.isfile(written)
        assert open(written, "rb").read() != b"stale"
        assert "src.dds" in result.converted


def test_an_unedited_image_is_still_copied_untouched() -> None:
    """A game texture must arrive byte-identical."""
    with tempfile.TemporaryDirectory() as folder:
        original = os.path.join(folder, "game.dds")
        payload = b"pretend this is a shipped texture"
        open(original, "wb").write(payload)

        image = fake_bpy.FakeImage("t", filepath=original)
        model_path = os.path.join(folder, "out", "model.gam")
        os.makedirs(os.path.dirname(model_path))
        texture_export.export_textures_beside_model([_object_with_image(image)], model_path)

        written = os.path.join(os.path.dirname(model_path), "game.dds")
        assert open(written, "rb").read() == payload


# --- faces Blender declines to keep ------------------------------------


def test_uvs_survive_faces_being_dropped() -> None:
    """The fault behind "the material has the texture and shows a colour".

    Blender does not keep every face it is handed; duplicates are the
    ordinary case and models carry hundreds. Numbering the loops by
    walking the input face list then runs ahead of reality from the
    first dropped face, and every UV after it lands on the wrong
    vertex — the UVs collapse toward a point and the texture renders as
    a flat wash of its own average colour.
    """
    from blender_io.mesh_bridge import _apply_uv_layer

    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata(
        [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)],
        [],
        # The middle face repeats the first and is dropped.
        [(0, 1, 2), (0, 1, 2), (1, 2, 3)],
    )
    uvs = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
    _apply_uv_layer(mesh, "UVMap", uvs)

    layer = mesh.uv_layers[0]
    for loop_index, loop in enumerate(mesh.loops):
        u, v = uvs[loop.vertex_index]
        assert tuple(layer.data[loop_index].uv) == (u, 1.0 - v)


def test_face_materials_survive_faces_being_dropped() -> None:
    """Looked up through the polygon's own vertex, not its position."""
    from blender_io.mesh_bridge import _assign_face_materials

    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata(
        [(0, 0, 0), (1, 0, 0), (0, 1, 0), (5, 5, 0), (6, 5, 0), (5, 6, 0)],
        [],
        [(0, 1, 2), (0, 1, 2), (3, 4, 5)],
    )
    # First source mesh owns vertices 0..2 and uses material 0; the
    # second owns 3..5 and uses material 1.
    _assign_face_materials(mesh, [0, 0, 0, 1, 1, 1], slot_count=2)

    assert [p.material_index for p in mesh.polygons] == [0, 1]


def test_the_v_axis_is_flipped_for_blender() -> None:
    from blender_io.mesh_bridge import _apply_uv_layer

    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    _apply_uv_layer(mesh, "UVMap", [(0.25, 0.75)] * 3)

    assert tuple(mesh.uv_layers[0].data[0].uv) == (0.25, 0.25)


# --- the phantom second UV layer ---------------------------------------


def _built(name: str):
    """Build a model's Blender mesh with no game folder."""
    from blender_io.mesh_bridge import build_model_mesh

    model = _model(name)
    return None if model is None else build_model_mesh(model, name)


def test_a_model_without_a_second_uv_set_gets_one_uv_layer() -> None:
    """Padding must not conjure a layer out of nothing.

    Per-vertex lists are padded so they stay indexable by vertex, which
    turned an absent second UV set into a full-length list of (0, 0).
    That built a second UV layer of zeros on every model — and being
    created second it claimed active_render, so Blender rendered with
    it. Every UV collapsed to one texel and each texture became a flat
    wash of its own average colour.
    """
    for name in ("factory_box", "kustarnik1", "civilhouse1"):
        mesh = _built(name)
        if mesh is None:
            continue
        names = [layer.name for layer in mesh.uv_layers]
        assert len(names) == 1, (name, names)


def test_only_the_primary_uv_layer_is_flagged_for_render() -> None:
    """Blender renders with whichever layer holds the flag, last wins."""
    mesh = _built("cube1111")
    if mesh is None:
        return
    flagged = [layer.name for layer in mesh.uv_layers if layer.active_render]
    assert flagged == ["UVMap"], flagged


def test_the_primary_layer_still_covers_every_loop() -> None:
    mesh = _built("factory_box")
    if mesh is None:
        return
    layer = mesh.uv_layers[0]
    assert len(layer.data) == len(mesh.loops)
    spread = max(abs(layer.data[i].uv[0]) for i in range(len(layer.data)))
    assert spread > 0.01, "the primary layer is collapsed"


def test_material_links_are_compared_by_name_not_identity() -> None:
    """bpy hands back a fresh socket wrapper on every access.

    Written with ``is``, this check passed against the test double and
    failed against every real material, so each import cleared and
    rebuilt materials that were already correct.
    """
    from blender_io.texture_bridge import _reaches_base_color

    class _Socket:
        def __init__(self, name):
            self.name = name

    class _Node:
        def __init__(self, name, socket):
            self.name = name
            self.socket = socket

    class _Link:
        def __init__(self, from_node, to_node, from_socket, to_socket):
            self.from_node = from_node
            self.to_node = to_node
            self.from_socket = from_socket
            self.to_socket = to_socket

    image_node = _Node("Diffuse", _Socket("Color"))
    principled = _Node("Principled BSDF", _Socket("Base Color"))

    class _Tree:
        links = [
            _Link(
                _Node("Diffuse", None),
                _Node("Principled BSDF", None),
                _Socket("Color"),
                _Socket("Base Color"),
            )
        ]

    assert _reaches_base_color(_Tree(), image_node, principled)


# --- vertex types the census turned up ---------------------------------


def test_every_layout_accounts_for_its_whole_stride() -> None:
    """A layout that does not sum to its stride is describing something else."""
    from formats.exm.gam import (
        CANDIDATE_VERTEX_LAYOUTS,
        VERTEX_FIELD_SIZES,
        VERTEX_LAYOUTS,
    )

    for table in (VERTEX_LAYOUTS, CANDIDATE_VERTEX_LAYOUTS):
        for vertex_type, (stride, fields) in table.items():
            total = sum(VERTEX_FIELD_SIZES[field] for field in fields)
            assert total == stride, (vertex_type, total, stride)


def test_measured_types_are_trusted_without_inspection() -> None:
    from formats.exm.gam import _layout_from_vertex_type

    layout = _layout_from_vertex_type(15, 48)
    assert layout is not None and layout.uv == 24 and layout.normal == 12

    layout = _layout_from_vertex_type(8, 36)
    assert layout is not None and layout.color == 24 and layout.uv == 28


def test_a_derived_layout_is_refused_when_the_data_disagrees() -> None:
    """Types 9, 10, 11 and 16 come from arithmetic, not from a file.

    The stride says what fields must be there; it does not prove where.
    So a derived layout is checked against the vertices before use, and
    a wrong one is dropped rather than applied.
    """
    from formats.exm.gam import _layout_from_vertex_type

    # A block of nonsense: no field of it reads as a unit normal.
    rubbish = struct.pack("<10f", *[9.0e30] * 10) * 8

    assert _layout_from_vertex_type(10, 40, rubbish, 0, 8) is None
    # And a type whose stride does not match its layout is not claimed.
    assert _layout_from_vertex_type(10, 48) is None


def test_a_derived_layout_is_accepted_when_the_normals_check_out() -> None:
    from formats.exm.gam import _layout_from_vertex_type

    vertex = struct.pack(
        "<3f3f2f2f",
        1.0, 2.0, 3.0,      # position
        0.0, 0.0, 1.0,      # normal, unit length
        0.25, 0.75,         # uv
        0.5, 0.5,           # uv2
    )
    block = vertex * 8
    layout = _layout_from_vertex_type(10, 40, block, 0, 8)

    assert layout is not None
    assert layout.normal == 12 and layout.uv == 24 and layout.uv2 == 32
