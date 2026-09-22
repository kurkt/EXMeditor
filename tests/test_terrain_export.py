# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `blender_io/terrain_bridge.py`'s export path:
`validate_terrain_mesh()` and `extract_heightmap()`.

Companion to `test_terrain_bridge.py` (which covers `build_mesh`) and
`test_world_transform.py` (which covers game-unit <-> world-unit scale
composition/inversion). This file covers topology validation and the
purely mechanical mesh -> HeightmapData extraction — no scale math
lives in either `build_mesh` or `extract_heightmap` anymore, so none is
tested here.
"""

from __future__ import annotations

import array
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.terrain_bridge import (  # noqa: E402
    build_mesh,
    extract_heightmap,
    validate_terrain_mesh,
)
from core.terrain import HeightmapData  # noqa: E402
from utils.errors import ValidationError  # noqa: E402


def _grid(width: int, height: int, seed: float = 1.0, cell_size: float = 1.0) -> HeightmapData:
    values = array.array("f", [float(i) * seed for i in range(width * height)])
    return HeightmapData(width=width, height=height, cell_size=cell_size, values=values)


# --- validate_terrain_mesh: the happy path ---


def test_validate_accepts_a_freshly_built_mesh() -> None:
    obj = build_mesh(_grid(5, 5))
    assert validate_terrain_mesh(obj) == 5


def test_validate_accepts_non_square_but_valid_grid_shapes() -> None:
    # 2x2 through 6x6, sanity across a few sizes, not just one.
    for side in (2, 3, 4, 6):
        obj = build_mesh(_grid(side, side), name=f"grid_{side}")
        assert validate_terrain_mesh(obj) == side


# --- validate_terrain_mesh: rejection paths ---


def test_validate_rejects_none() -> None:
    try:
        validate_terrain_mesh(None)
        raise AssertionError("expected ValidationError")
    except ValidationError:
        pass


def test_validate_rejects_non_mesh_object() -> None:
    class NotAMesh:
        name = "empty_obj"
        type = "EMPTY"

    try:
        validate_terrain_mesh(NotAMesh())
        raise AssertionError("expected ValidationError")
    except ValidationError:
        pass


def test_validate_rejects_non_square_vertex_count() -> None:
    obj = build_mesh(_grid(3, 3))
    del obj.data.vertices[-1]  # 8 vertices, not a perfect square
    try:
        validate_terrain_mesh(obj)
        raise AssertionError("expected ValidationError")
    except ValidationError:
        pass


def test_validate_rejects_missing_face_hole() -> None:
    obj = build_mesh(_grid(3, 3))
    del obj.data.polygons[0]
    try:
        validate_terrain_mesh(obj)
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "hole" in e.message or "does not match" in e.message


def test_validate_rejects_triangle_face() -> None:
    obj = build_mesh(_grid(3, 3))
    old = obj.data.polygons[0]
    obj.data.polygons[0] = fake_bpy.FakeMeshPolygon(old.vertices[:3], old.index)
    try:
        validate_terrain_mesh(obj)
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "triangle" in e.message


def test_validate_rejects_ngon_face() -> None:
    obj = build_mesh(_grid(3, 3))
    old = obj.data.polygons[0]
    obj.data.polygons[0] = fake_bpy.FakeMeshPolygon(old.vertices + (old.vertices[0],), old.index)
    try:
        validate_terrain_mesh(obj)
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "n-gon" in e.message


def test_validate_rejects_duplicate_vertex_position() -> None:
    obj = build_mesh(_grid(3, 3))
    dupe = fake_bpy.FakeMeshVertex(obj.data.vertices[0].co, len(obj.data.vertices))
    obj.data.vertices.append(dupe)
    # Appending also breaks the perfect-square count first — still a
    # ValidationError either way, which is what matters here.
    try:
        validate_terrain_mesh(obj)
        raise AssertionError("expected ValidationError")
    except ValidationError:
        pass


def test_validate_rejects_broken_interior_topology() -> None:
    obj = build_mesh(_grid(4, 4))
    interior_index = 1 * 4 + 1  # grid cell (1,1), interior for a 4x4 grid
    target_edge = next(e for e in obj.data.edges if interior_index in e.vertices)
    obj.data.edges.remove(target_edge)
    try:
        validate_terrain_mesh(obj)
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "degree" in e.message.lower() or "4 connected edges" in e.message


def test_validate_rejects_missing_grid_attributes() -> None:
    """A mesh not built by build_mesh() (or one that lost its attributes
    to an operation like Remesh/Decimate) must be rejected with a clear,
    specific message — never by falling back to position-based guessing."""
    obj = build_mesh(_grid(3, 3))
    del obj.data.attributes._store["exm_row"]  # simulate the attribute never having existed
    try:
        validate_terrain_mesh(obj)
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "exm_row" in e.message and "re-import" in e.message


def test_validate_rejects_duplicate_grid_position() -> None:
    """Two vertices claiming the same stored (row, col) — e.g. from a
    corrupted attribute — must be rejected, not silently overwrite one
    sample with another."""
    obj = build_mesh(_grid(3, 3))
    obj.data.attributes["exm_row"].data[0].value = obj.data.attributes["exm_row"].data[1].value
    obj.data.attributes["exm_col"].data[0].value = obj.data.attributes["exm_col"].data[1].value
    try:
        validate_terrain_mesh(obj)
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "same stored grid position" in e.message


# --- extract_heightmap: purely mechanical, no scale math of any kind ---


def test_extract_round_trips_exactly() -> None:
    original = _grid(6, 6, seed=1.5)
    obj = build_mesh(original)
    extracted = extract_heightmap(obj)
    assert list(extracted.values) == list(original.values)
    assert (extracted.width, extracted.height) == (original.width, original.height)


def test_extract_auto_detects_cell_size_from_custom_property() -> None:
    original = _grid(5, 5, seed=1.5, cell_size=2.0)
    obj = build_mesh(original)
    # No cell_size passed explicitly — must be read from the custom
    # property build_mesh stored.
    extracted = extract_heightmap(obj)
    assert extracted.cell_size == 2.0


def test_extract_explicit_cell_size_overrides_custom_property() -> None:
    original = _grid(3, 3, cell_size=2.0)
    obj = build_mesh(original)
    extracted = extract_heightmap(obj, cell_size=9.0)
    assert extracted.cell_size == 9.0  # explicit argument wins


def test_extract_zero_cell_size_is_harmless() -> None:
    """cell_size is carried through only as metadata — extract_heightmap
    never divides by it (row/col come from the attribute, not position),
    so 0 is a valid, harmless value here."""
    obj = build_mesh(_grid(3, 3))
    result = extract_heightmap(obj, cell_size=0.0)
    assert result.cell_size == 0.0


def test_extract_ignores_position_entirely() -> None:
    """The core point of the attribute-based redesign: row/col comes
    from EXM_ROW_ATTR/EXM_COL_ATTR, never from X/Y position. Proof:
    scrambling every vertex's X/Y position (as Sculpt/Grab/Smooth would)
    must NOT affect the extracted heights or their grid placement at
    all."""
    original = _grid(4, 4, seed=2.0)
    obj = build_mesh(original)

    for i, v in enumerate(obj.data.vertices):
        # Irregular jitter — NOT a clean multiple of any grid spacing,
        # simulating a real sculpt/grab edit.
        v.co.x += (i * 0.137) % 1.0 - 0.5
        v.co.y += (i * 0.271) % 1.0 - 0.5
        # Z left untouched here so heights must still match exactly.

    extracted = extract_heightmap(obj)
    assert list(extracted.values) == list(original.values)


def test_full_import_export_import_cycle_matches_exactly() -> None:
    """The main regression test requested: build -> extract -> rebuild ->
    extract again must agree exactly (mesh-space round trip; the actual
    file I/O round trip is covered by test_terrain_roundtrip.py)."""
    original = _grid(10, 10, seed=0.73)

    obj1 = build_mesh(original)
    extracted1 = extract_heightmap(obj1)

    obj2 = build_mesh(extracted1)
    extracted2 = extract_heightmap(obj2)

    assert extracted1.width == extracted2.width == original.width
    assert extracted1.height == extracted2.height == original.height
    assert list(extracted1.values) == list(extracted2.values) == list(original.values)


def test_survives_irregular_edit_like_sculpt_or_grab() -> None:
    """The headline scenario the attribute-based redesign exists for:
    after Sculpt/Grab/Smooth-style edits, vertex X/Y positions are no
    longer on a clean regular raster. Export must still work perfectly
    — because indexing comes from the EXM_ROW_ATTR/EXM_COL_ATTR
    attribute, never from position — and must correctly reflect the Z
    (height) edits actually made, with zero cross-talk into row/col
    assignment."""
    side = 6
    original = _grid(side, side, seed=3.0)
    obj = build_mesh(original)

    # Simulate a Sculpt/Grab-style edit: irregular, non-grid-aligned
    # position jitter on every vertex, PLUS a deliberate, known height
    # edit on one specific vertex.
    height_edit_row, height_edit_col = 2, 3
    height_edit_delta = 123.456
    target_index = height_edit_row * side + height_edit_col

    for i, v in enumerate(obj.data.vertices):
        v.co.x += ((i * 0.611) % 1.0) - 0.5  # irregular, not aligned to any grid spacing
        v.co.y += ((i * 0.849) % 1.0) - 0.5
        if i == target_index:
            v.co.z += height_edit_delta

    extracted = extract_heightmap(obj)

    for row in range(side):
        for col in range(side):
            expected = original.get_height(col, row)
            if (row, col) == (height_edit_row, height_edit_col):
                expected += height_edit_delta
            actual = extracted.get_height(col, row)
            assert abs(actual - expected) < 1e-3, (
                f"mismatch at (row={row}, col={col}): expected {expected}, got {actual}"
            )


_ALL_TESTS = (
    test_validate_accepts_a_freshly_built_mesh,
    test_validate_accepts_non_square_but_valid_grid_shapes,
    test_validate_rejects_none,
    test_validate_rejects_non_mesh_object,
    test_validate_rejects_non_square_vertex_count,
    test_validate_rejects_missing_face_hole,
    test_validate_rejects_triangle_face,
    test_validate_rejects_ngon_face,
    test_validate_rejects_duplicate_vertex_position,
    test_validate_rejects_broken_interior_topology,
    test_validate_rejects_missing_grid_attributes,
    test_validate_rejects_duplicate_grid_position,
    test_extract_round_trips_exactly,
    test_extract_auto_detects_cell_size_from_custom_property,
    test_extract_explicit_cell_size_overrides_custom_property,
    test_extract_zero_cell_size_is_harmless,
    test_extract_ignores_position_entirely,
    test_full_import_export_import_cycle_matches_exactly,
    test_survives_irregular_edit_like_sculpt_or_grab,
)


if __name__ == "__main__":
    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            test_fn()
        except Exception as exc:  # noqa: BLE001 - test runner, want to catch everything
            failures += 1
            print(f"FAIL: {test_fn.__name__}: {exc}")
        else:
            print(f"PASS: {test_fn.__name__}")
    print(f"\n{len(_ALL_TESTS) - failures}/{len(_ALL_TESTS)} passed")
    sys.exit(1 if failures else 0)
