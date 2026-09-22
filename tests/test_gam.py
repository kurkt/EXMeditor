# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `.gam` model loading.

The vertex layout was reverse-engineered rather than documented, so
these tests pin down the exact field offsets that were verified against
real data — in particular that the file's own stored bounding box
matches the positions the parser reads, which is what confirmed the
layout was right in the first place.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.mesh_bridge import build_model_mesh  # noqa: E402
from core.coordinates import CoordinateTransform  # noqa: E402
from utils.math import Vector3  # noqa: E402
from formats.exm.gam import MAGIC, read_container, read_model  # noqa: E402
from formats.exm.model_catalog import read_model_catalog, resolve_model_file  # noqa: E402
from utils.errors import ParsingError  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402


def _make_gam(
    *,
    vertex_count: int = 3,
    triangle_count: int = 1,
    stride: int = 44,
    indices: list[int] | None = None,
    with_bounds: bool = True,
) -> str:
    """Build a minimal but structurally real .gam file."""
    header = bytearray(72)
    header[0:5] = b"mesh\x00"
    struct.pack_into("<I", header, 56, stride)
    struct.pack_into("<I", header, 60, 9)
    struct.pack_into("<I", header, 64, vertex_count)
    struct.pack_into("<I", header, 68, triangle_count)

    vertices = bytearray()
    for i in range(vertex_count):
        # Written to fill exactly `stride` bytes, so a non-44 stride
        # produces structurally valid data rather than a malformed file.
        vertex = bytearray(stride)
        struct.pack_into("<3f", vertex, 0, float(i), float(i) * 2, float(i) * 3)  # position
        if stride >= 24:
            struct.pack_into("<3f", vertex, 12, 0.0, 1.0, 0.0)                    # normal
        if stride >= 44:
            struct.pack_into("<4B", vertex, 24, 200, 200, 200, 255)               # colour
            struct.pack_into("<2f", vertex, 28, 0.25, 0.75)                       # uv
            struct.pack_into("<2f", vertex, 36, 0.5, 0.5)                         # uv2
        vertices += vertex

    if indices is None:
        indices = [0, 1, 2] * triangle_count
    index_data = struct.pack(f"<{len(indices)}H", *indices)

    chunk = bytes(header) + bytes(vertices) + index_data
    if with_bounds:
        last = vertex_count - 1
        chunk += struct.pack(
            "<6f", 0.0, 0.0, 0.0, float(last), float(last) * 2, float(last) * 3,
        )

    # container: magic(7) + subtype(1) + count(4) + TOC(16) + payload
    container = bytearray(MAGIC + b"\x00")
    container += struct.pack("<I", 1)
    container += struct.pack("<4I", 4, len(chunk), 12 + 16, 0)
    container += chunk

    path = os.path.join(tempfile.mkdtemp(), "model.gam")
    with open(path, "wb") as f:
        f.write(container)
    return path


# --- container ---


def test_reads_container_chunks() -> None:
    subtype, chunks = read_container(_make_gam())
    assert subtype == 0
    assert len(chunks) == 1
    assert chunks[0].chunk_id == 4


def test_rejects_wrong_magic() -> None:
    path = os.path.join(tempfile.mkdtemp(), "bad.gam")
    with open(path, "wb") as f:
        f.write(b"NOTAGAMFILE" + b"\x00" * 32)
    try:
        read_container(path)
        raise AssertionError("expected ParsingError")
    except ParsingError as e:
        assert "magic" in e.message


def test_rejects_missing_file() -> None:
    try:
        read_container("/no/such/model.gam")
        raise AssertionError("expected ParsingError")
    except ParsingError:
        pass


# --- mesh parsing ---


def test_parses_vertex_attributes() -> None:
    model = read_model(_make_gam(vertex_count=3))
    mesh = model.meshes[0]
    assert mesh.vertex_count == 3
    assert mesh.triangle_count == 1
    assert mesh.positions[1].as_tuple() == (1.0, 2.0, 3.0)
    assert mesh.normals[0].as_tuple() == (0.0, 1.0, 0.0)
    assert mesh.colors[0] == (200, 200, 200, 255)
    assert mesh.uvs[0] == (0.25, 0.75)
    assert mesh.uv2s[0] == (0.5, 0.5)
    assert mesh.triangles[0] == (0, 1, 2)


def test_stored_bounds_are_read() -> None:
    mesh = read_model(_make_gam()).meshes[0]
    assert mesh.stored_bounds is not None
    assert mesh.stored_bounds.max_corner.as_tuple() == (2.0, 4.0, 6.0)


def test_accepts_other_vertex_strides() -> None:
    """Models use several vertex formats — rocks are 44 bytes, other
    models are not. Rejecting everything else meant most of a real map's
    models failed to load. Geometry is recovered for any stride, checked
    against the mesh's own bounding box."""
    for stride in (32, 36, 40, 48, 56):
        model = read_model(_make_gam(stride=stride))
        mesh = model.meshes[0]
        assert mesh.vertex_count == 3
        assert mesh.triangle_count == 1
        assert len(mesh.normals) == 3  # detected by unit length


def test_rejects_a_layout_that_contradicts_the_stored_bounds() -> None:
    """The safety net that makes handling unknown strides sound: if the
    positions read don't reproduce the mesh's own bounding box, the
    layout is wrong and the mesh is refused rather than silently
    imported as garbage geometry."""
    path = _make_gam(vertex_count=3)
    with open(path, "rb") as f:
        data = bytearray(f.read())
    # Corrupt the stored bounds so they no longer describe the vertices.
    bounds_offset = len(data) - 24
    struct.pack_into("<6f", data, bounds_offset, 500.0, 500.0, 500.0, 900.0, 900.0, 900.0)
    with open(path, "wb") as f:
        f.write(data)

    try:
        read_model(path)
        raise AssertionError("expected ParsingError")
    except ParsingError as e:
        assert "bounding box" in e.message


def test_rejects_indices_that_match_no_valid_buffer() -> None:
    """Indices are located by validation (some meshes carry more than
    one vertex buffer, so the buffer's position isn't fixed). If no
    position yields all-valid indices, the mesh is refused rather than
    imported with broken topology."""
    try:
        read_model(_make_gam(vertex_count=3, triangle_count=1, indices=[0, 1, 99]))
        raise AssertionError("expected ParsingError")
    except ParsingError as e:
        assert "index buffer" in e.message


def test_rejects_truncated_chunk() -> None:
    """Declared counts that exceed the chunk's actual size must be
    caught, not read past the end.

    Built by writing a valid 3-vertex file then rewriting its header to
    claim 1000 vertices — the mismatch a corrupt or misidentified file
    would show."""
    path = _make_gam(vertex_count=3)
    with open(path, "rb") as f:
        data = bytearray(f.read())
    chunk_start = 12 + 16
    struct.pack_into("<I", data, chunk_start + 64, 1000)  # claim 1000 vertices
    with open(path, "wb") as f:
        f.write(data)

    try:
        read_model(path)
        raise AssertionError("expected ParsingError")
    except ParsingError as e:
        assert "index buffer" in e.message or "past the end" in e.message


# --- real file ---

_REAL_GAM = corpus("big_crag_11.gam")


def test_reads_a_mesh_with_two_vertex_buffers() -> None:
    """Some meshes store a second vertex buffer before the indices
    (3 of 9 in one real model). The index buffer is found by
    validation, so the extra buffer is skipped automatically."""
    real = corpus("oilmine1.gam")
    if not os.path.isfile(real):
        return
    model = read_model(real)
    assert len(model.meshes) == 9
    assert model.total_vertices() > 3000
    for mesh in model.meshes:
        for triangle in mesh.triangles:
            assert max(triangle) < mesh.vertex_count


def test_reads_a_pillbox_model() -> None:
    real = corpus("heavy_dot4.gam")
    if not os.path.isfile(real):
        return
    model = read_model(real)
    assert model.total_vertices() > 100
    assert model.total_triangles() > 100


def test_reads_a_multi_part_building() -> None:
    real = corpus("minin.gam")
    if not os.path.isfile(real):
        return
    model = read_model(real)
    assert len(model.meshes) > 1
    assert model.total_triangles() > 1000


def test_reads_multiple_meshes_in_one_chunk() -> None:
    """A chunk holds one mesh for a rock but 28 for a vehicle cab, laid
    end to end with a single bounding box after the last. Reading only
    the first meant most of a vehicle never appeared."""
    real = corpus("cab01.gam")
    if not os.path.isfile(real):
        return
    model = read_model(real)
    assert len(model.meshes) > 1
    assert model.total_vertices() > 1000
    assert all(m.triangle_count > 0 for m in model.meshes)


def test_reads_the_uv_only_vertex_format() -> None:
    """stride 32: position, normal, uv — no colour, no second uv set."""
    real = corpus("iron_dot.gam")
    if not os.path.isfile(real):
        return
    mesh = read_model(real).meshes[0]
    assert len(mesh.normals) == mesh.vertex_count
    assert len(mesh.uvs) == mesh.vertex_count
    assert mesh.colors == []      # this format has none
    assert mesh.uv2s == []


def test_reads_the_tangent_vertex_format() -> None:
    """stride 48: position, normal, uv, tangent. The tangent must not
    be mistaken for a second uv set."""
    real = corpus("cab01.gam")
    if not os.path.isfile(real):
        return
    mesh = read_model(real).meshes[0]
    assert len(mesh.uvs) == mesh.vertex_count
    assert mesh.uv2s == [], "tangent data was misread as a second uv set"


def test_real_model_stored_bounds_match_actual_positions() -> None:
    """The strongest confirmation the field offsets are right: the
    file's own bounding box agrees with the positions the parser
    extracts. A wrong offset would put arbitrary floats in `positions`
    and this would fail immediately."""
    if not os.path.isfile(_REAL_GAM):
        return  # real sample not available in this environment
    mesh = read_model(_REAL_GAM).meshes[0]
    stored = mesh.stored_bounds
    computed = mesh.computed_bounds()
    assert stored is not None and computed is not None
    for a, b in zip(stored.min_corner.as_tuple(), computed.min_corner.as_tuple()):
        assert abs(a - b) < 0.01
    for a, b in zip(stored.max_corner.as_tuple(), computed.max_corner.as_tuple()):
        assert abs(a - b) < 0.01


def test_real_model_normals_are_unit_length() -> None:
    if not os.path.isfile(_REAL_GAM):
        return
    mesh = read_model(_REAL_GAM).meshes[0]
    for normal in mesh.normals:
        assert abs(normal.length() - 1.0) < 0.01


# --- Blender bridge ---


def test_mesh_geometry_is_object_local_not_world_positioned() -> None:
    """Regression: mesh vertices are coordinates relative to the model's
    own origin, and the object that carries them is placed separately.
    Running them through the world-position conversion subtracted the
    map-centring offset from every vertex, throwing each model
    thousands of units away from its object."""
    model = read_model(_make_gam(vertex_count=3))
    transform = CoordinateTransform(
        xy_scale=1.25,
        height_scale=1.25,
        origin_offset=Vector3(2044.0, 0.0, 2044.0),
    )
    mesh = build_model_mesh(model, "test", transform=transform)

    # Source positions are (0,0,0), (1,2,3), (2,4,6) — small local
    # values. With the offset wrongly applied they would all land near
    # -2555.
    for vertex in mesh.vertices:
        assert abs(vertex.co.x) < 100, "map-centring offset leaked into local geometry"
        assert abs(vertex.co.y) < 100


def test_mesh_scale_matches_terrain_scale() -> None:
    """Models and terrain must share one game-unit-to-Blender-unit
    ratio, or objects sit at a different scale from the ground."""
    model = read_model(_make_gam(vertex_count=3))
    transform = CoordinateTransform(xy_scale=1.25, height_scale=1.25)
    mesh = build_model_mesh(model, "test", transform=transform)

    # source vertex 2 is (2, 4, 6) in game units
    co = mesh.vertices[2].co
    assert abs(co.x - 2 * 1.25) < 1e-4
    assert abs(co.y - 6 * 1.25) < 1e-4   # game Z -> Blender Y
    assert abs(co.z - 4 * 1.25) < 1e-4   # game Y -> Blender Z


def test_builds_blender_mesh_with_all_layers() -> None:
    model = read_model(_make_gam(vertex_count=3))
    mesh = build_model_mesh(model, "test", transform=CoordinateTransform())
    assert len(mesh.vertices) == 3
    assert len(mesh.polygons) == 1
    assert len(mesh.uv_layers) == 2      # UV and UV2
    assert len(mesh.color_attributes) == 1
    assert mesh.custom_normals is not None


def test_mesh_uses_the_shared_coordinate_transform() -> None:
    """Model geometry must land in the same space as everything else:
    game Z -> Blender Y, game Y -> Blender Z."""
    model = read_model(_make_gam(vertex_count=3))
    transform = CoordinateTransform(xy_scale=10.0, height_scale=1.5)
    mesh = build_model_mesh(model, "test", transform=transform)

    # source vertex 1 is (1, 2, 3)
    co = mesh.vertices[1].co
    assert co.x == 10.0    # game X * xy_scale
    assert co.y == 30.0    # game Z * xy_scale -> Blender Y
    assert co.z == 3.0     # game Y * height_scale -> Blender Z


def test_normals_are_not_scaled() -> None:
    """Normals are directions: they get the axis swap but must not be
    scaled, or non-uniform factors would skew them off unit length."""
    model = read_model(_make_gam(vertex_count=3))
    transform = CoordinateTransform(xy_scale=10.0, height_scale=1.5)
    mesh = build_model_mesh(model, "test", transform=transform)
    # source normal (0, 1, 0) -> axis-swapped (0, 0, 1), still unit
    assert mesh.custom_normals[0] == (0.0, 0.0, 1.0)


# --- model catalogue ---

_REAL_CATALOG = corpus("animmodels.xml")


def test_catalogue_recovers_from_malformed_xml() -> None:
    """Reported from real use: commonservers.xml failed a strict parse,
    and refusing it cost every model it listed — whole categories
    (petrol stations, towns, pillboxes, fences) vanished from imports.
    An unescaped '&' is enough to trigger it."""
    bad = (
        '<?xml version="1.0" encoding="windows-1251"?>\n'
        "<AnimatedModels>\n"
        '\t<model id="petrolstation" file="data\\models\\petrolstation.gam" />\n'
        '\t<model id="heavy_dot4" file="data\\models\\dot4.gam" name="Bunker & Wall" />\n'
        "</AnimatedModels>"
    )
    path = os.path.join(tempfile.mkdtemp(), "commonservers.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write(bad)

    catalog = read_model_catalog(path)
    assert len(catalog) == 2
    assert catalog.get("petrolstation") is not None
    assert catalog.get("heavy_dot4") is not None


def test_catalogue_reads_non_cp1251_encodings() -> None:
    """Shipped catalogues are mostly windows-1251 but not uniformly; a
    decode error would otherwise lose the whole file."""
    content = (
        '<?xml version="1.0"?>\n<AnimatedModels>\n'
        '\t<model id="tree" file="data/models/tree.gam" />\n'
        "</AnimatedModels>"
    )
    path = os.path.join(tempfile.mkdtemp(), "catalog.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    assert read_model_catalog(path).get("tree") is not None


def test_well_formed_catalogue_still_keeps_full_attributes() -> None:
    """The lenient path recovers only id and file, so it must not be
    taken for files that parse normally."""
    content = (
        '<?xml version="1.0"?>\n<AnimatedModels>\n'
        '\t<model id="rock" file="rock.gam" shadow="1" passable="1" />\n'
        "</AnimatedModels>"
    )
    path = os.path.join(tempfile.mkdtemp(), "catalog.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write(content)
    entry = read_model_catalog(path).get("rock")
    assert entry.shadow == "1"
    assert entry.passable == "1"


def test_catalogue_maps_ids_to_gam_paths() -> None:
    if not os.path.isfile(_REAL_CATALOG):
        return
    catalog = read_model_catalog(_REAL_CATALOG)
    assert len(catalog) > 100
    entry = catalog.get("big_crag_11")
    assert entry is not None
    assert entry.file_path.lower().endswith(".gam")


def test_catalogue_returns_none_for_unknown_id() -> None:
    if not os.path.isfile(_REAL_CATALOG):
        return
    assert read_model_catalog(_REAL_CATALOG).get("no_such_model_id") is None


def test_resolve_model_file_finds_an_installed_model() -> None:
    if not (os.path.isfile(_REAL_CATALOG) and os.path.isfile(_REAL_GAM)):
        return
    import shutil

    catalog = read_model_catalog(_REAL_CATALOG)
    entry = catalog.get("big_crag_11")
    root = tempfile.mkdtemp()
    destination = os.path.join(root, entry.file_path.replace("\\", os.sep))
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copy2(_REAL_GAM, destination)

    resolved = resolve_model_file(entry, root)
    assert resolved is not None and os.path.isfile(resolved)


def test_resolve_model_file_returns_none_when_not_installed() -> None:
    if not os.path.isfile(_REAL_CATALOG):
        return
    catalog = read_model_catalog(_REAL_CATALOG)
    assert resolve_model_file(catalog.get("big_crag_11"), tempfile.mkdtemp()) is None


_ALL_TESTS = (
    test_reads_container_chunks,
    test_rejects_wrong_magic,
    test_rejects_missing_file,
    test_parses_vertex_attributes,
    test_stored_bounds_are_read,
    test_accepts_other_vertex_strides,
    test_rejects_a_layout_that_contradicts_the_stored_bounds,
    test_rejects_indices_that_match_no_valid_buffer,
    test_rejects_truncated_chunk,
    test_reads_a_mesh_with_two_vertex_buffers,
    test_reads_a_pillbox_model,
    test_reads_a_multi_part_building,
    test_reads_multiple_meshes_in_one_chunk,
    test_reads_the_uv_only_vertex_format,
    test_reads_the_tangent_vertex_format,
    test_real_model_stored_bounds_match_actual_positions,
    test_real_model_normals_are_unit_length,
    test_mesh_geometry_is_object_local_not_world_positioned,
    test_mesh_scale_matches_terrain_scale,
    test_builds_blender_mesh_with_all_layers,
    test_mesh_uses_the_shared_coordinate_transform,
    test_normals_are_not_scaled,
    test_catalogue_recovers_from_malformed_xml,
    test_catalogue_reads_non_cp1251_encodings,
    test_well_formed_catalogue_still_keeps_full_attributes,
    test_catalogue_maps_ids_to_gam_paths,
    test_catalogue_returns_none_for_unknown_id,
    test_resolve_model_file_finds_an_installed_model,
    test_resolve_model_file_returns_none_when_not_installed,
)


if __name__ == "__main__":
    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            test_fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL: {test_fn.__name__}: {exc}")
        else:
            print(f"PASS: {test_fn.__name__}")
    print(f"\n{len(_ALL_TESTS) - failures}/{len(_ALL_TESTS)} passed")
    sys.exit(1 if failures else 0)
