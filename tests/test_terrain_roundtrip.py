# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Main regression test: displace.bin -> import -> export -> import.

This is the test requested explicitly: take a real (synthetic, but
file-based — not an in-memory shortcut) displace.bin, run it through
the exact operator pipeline Blender would use for import, export it
back out, re-import the result, and numerically compare against the
original. Only float32-precision differences are tolerated. No visual
comparison anywhere in this file — everything here is numeric.
"""

from __future__ import annotations

import array
import math
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from addon import operators  # noqa: E402
from core.terrain import HeightmapData  # noqa: E402
from formats.exm.plugin import ExMachinaPlugin  # noqa: E402
from formats.exm.terrain import read_displace, write_displace  # noqa: E402
from formats.registry import PluginRegistry  # noqa: E402

_FLOAT32_REL_TOLERANCE = 1e-5
_FLOAT32_ABS_TOLERANCE = 1e-4
# Two independent float32 roundings are now expected on any test that
# uses a non-1.0 xy_scale/height_scale: WorldTransform.to_world()
# rounds once (materializing a new array.array('f', ...)), and
# to_game() rounds again on the way back — each up to ~1 ULP of
# relative error at the sample's magnitude. This is an accepted,
# understood cost of keeping world-unit conversion in its own
# separate, composable layer (see the architecture doc changelog)
# rather than a single combined read/scale/write step — NOT a
# precision regression to chase down. A default-scale (1.0/1.0) round
# trip still passes with effectively zero error, since to_world()/
# to_game() are no-ops at that point (see test_full_cycle_default_scale).


class _FakeWindowManager:
    def fileselect_add(self, op) -> None:
        pass


class _FakeContext:
    def __init__(self, active_object=None) -> None:
        self.collection = fake_bpy.FakeCollection("Map")
        self.active_object = active_object
        self.window_manager = _FakeWindowManager()
        self.scene = fake_bpy.FakeObject("Scene", None)


_MINIMAL_SSL = """<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>
<Ini>
\t<Section name="LEVEL">
\t\t<Key name="HIGHMAP">displace.bin</Key>
\t</Section>
</Ini>
"""


def _write_manifest(folder: str) -> None:
    """File discovery is manifest-driven, so a test map needs a .ssl."""
    with open(os.path.join(folder, "map.ssl"), "w", encoding="cp1251") as f:
        f.write(_MINIMAL_SSL)


def _terrain_object(collection):
    """Find the terrain mesh in the 'Terrain' sub-collection build_scene
    now creates (it used to be linked directly into the target)."""
    for child in collection.children:
        if child.name == "Terrain":
            return child.objects.linked[0]
    raise AssertionError("no Terrain sub-collection was created")


def _install_active_plugin() -> None:
    registry = PluginRegistry()
    registry.register(ExMachinaPlugin())
    operators.default_registry = registry


def _make_varied_heightmap(side: int, seed: int) -> HeightmapData:
    """A non-trivial, non-uniform heightmap — real terrain-like test
    data, not a flat placeholder (see the earlier lesson learned: a
    uniform file can't catch orientation/precision bugs)."""
    rng = random.Random(seed)
    values = array.array("f", [rng.uniform(0.0, 500.0) for _ in range(side * side)])
    return HeightmapData(width=side, height=side, cell_size=1.0, values=values)


def _run_full_cycle(side: int, xy_scale: float, height_scale: float, seed: int) -> None:
    _install_active_plugin()

    # 1. A real displace.bin file on disk (the actual starting point,
    # not an in-memory HeightmapData handed directly to blender_io).
    original_dir = tempfile.mkdtemp()
    original_path = os.path.join(original_dir, "displace.bin")
    original_heightmap = _make_varied_heightmap(side, seed)
    write_displace(original_path, original_heightmap)
    _write_manifest(original_dir)

    # 2. Import through the real operator.
    import_ctx = _FakeContext()
    import_op = operators.EXM_OT_import_map()
    import_op.directory = original_dir
    import_op.xy_scale = xy_scale
    import_op.height_scale = height_scale
    import_result = import_op.execute(import_ctx)
    assert import_result == {"FINISHED"}, f"import failed: {import_result}"
    terrain_obj = _terrain_object(import_ctx.collection)

    # 3. Export through the real operator, to a DIFFERENT folder (so
    # there's no risk of accidentally reading the original file back
    # instead of the freshly exported one).
    export_dir = tempfile.mkdtemp()
    # Same context as the import: the export operator reads the source
    # folder and scale from context.scene, which import recorded there.
    export_ctx = import_ctx
    export_ctx.active_object = terrain_obj
    export_op = operators.EXM_OT_export_map()
    export_op.directory = export_dir
    export_op.xy_scale = xy_scale
    export_op.height_scale = height_scale
    export_op.ground_level_offset = 0.0
    export_result = export_op.execute(export_ctx)
    assert export_result == {"FINISHED"}, f"export failed: {export_result}"

    exported_path = os.path.join(export_dir, "displace.bin")
    assert os.path.isfile(exported_path)

    # 4. Re-import the EXPORTED file (a second, independent read from
    # disk — not reusing anything held in memory from step 2/3).
    reimported_heightmap = read_displace(exported_path)

    # 5. Numeric comparison ONLY.
    assert reimported_heightmap.width == original_heightmap.width == side
    assert reimported_heightmap.height == original_heightmap.height == side
    assert len(reimported_heightmap.values) == len(original_heightmap.values) == side * side

    max_diff = 0.0
    for i, (original, reimported) in enumerate(zip(original_heightmap.values, reimported_heightmap.values)):
        diff = abs(original - reimported)
        max_diff = max(max_diff, diff)
        assert math.isclose(original, reimported, rel_tol=_FLOAT32_REL_TOLERANCE, abs_tol=_FLOAT32_ABS_TOLERANCE), (
            f"value mismatch at sample {i}: original={original}, "
            f"reimported={reimported}, diff={diff} (side={side}, "
            f"xy_scale={xy_scale}, height_scale={height_scale})"
        )


def test_full_cycle_default_scale() -> None:
    _run_full_cycle(side=16, xy_scale=1.0, height_scale=1.0, seed=1)


def test_full_cycle_confirmed_size_32() -> None:
    """32x32 is one of the empirically confirmed real grid sizes
    (from an '8x8' declared map) — see the architecture doc changelog."""
    _run_full_cycle(side=32, xy_scale=1.0, height_scale=1.0, seed=2)


def test_full_cycle_confirmed_size_64() -> None:
    _run_full_cycle(side=64, xy_scale=1.0, height_scale=1.0, seed=3)


def test_full_cycle_nontrivial_xy_scale() -> None:
    _run_full_cycle(side=16, xy_scale=2.5, height_scale=1.0, seed=4)


def test_full_cycle_nontrivial_height_scale() -> None:
    _run_full_cycle(side=16, xy_scale=1.0, height_scale=0.37, seed=5)


def test_full_cycle_nontrivial_both_scales() -> None:
    _run_full_cycle(side=24, xy_scale=1.7, height_scale=0.42, seed=6)


def test_full_cycle_small_grid() -> None:
    """Edge case: the smallest valid grid (2x2, one quad)."""
    _run_full_cycle(side=2, xy_scale=1.0, height_scale=1.0, seed=7)


def test_export_preserves_unparsed_files_and_leaves_source_untouched() -> None:
    """The snapshot strategy replaces the old .bak scheme: export copies
    the whole source folder into a separate destination, so files the
    SDK never parses survive verbatim AND the original map folder is
    never modified at all."""
    _install_active_plugin()
    side = 8
    original_heightmap = _make_varied_heightmap(side, seed=8)

    original_dir = tempfile.mkdtemp()
    write_displace(os.path.join(original_dir, "displace.bin"), original_heightmap)
    _write_manifest(original_dir)
    # A file this SDK has no parser for at all.
    opaque = b"OPAQUE BLOB THE SDK NEVER PARSES"
    with open(os.path.join(original_dir, "grass.xml"), "wb") as f:
        f.write(opaque)

    import_ctx = _FakeContext()
    import_op = operators.EXM_OT_import_map()
    import_op.directory = original_dir
    import_op.xy_scale = 1.0
    import_op.height_scale = 1.0
    import_op.execute(import_ctx)
    terrain_obj = _terrain_object(import_ctx.collection)

    export_dir = tempfile.mkdtemp()
    export_ctx = import_ctx
    export_ctx.active_object = terrain_obj
    export_op = operators.EXM_OT_export_map()
    export_op.directory = export_dir
    export_op.xy_scale = 1.0
    export_op.height_scale = 1.0
    export_op.ground_level_offset = 0.0
    assert export_op.execute(export_ctx) == {"FINISHED"}

    # The unparsed file came through byte-identically.
    with open(os.path.join(export_dir, "grass.xml"), "rb") as f:
        assert f.read() == opaque

    # The source folder was not modified.
    reread_source = read_displace(os.path.join(original_dir, "displace.bin"))
    for a, b in zip(original_heightmap.values, reread_source.values):
        assert math.isclose(a, b, rel_tol=_FLOAT32_REL_TOLERANCE, abs_tol=_FLOAT32_ABS_TOLERANCE)

    # And the exported terrain still matches numerically.
    reimported = read_displace(os.path.join(export_dir, "displace.bin"))
    for a, b in zip(original_heightmap.values, reimported.values):
        assert math.isclose(a, b, rel_tol=_FLOAT32_REL_TOLERANCE, abs_tol=_FLOAT32_ABS_TOLERANCE)


def test_full_cycle_survives_irregular_edit_between_import_and_export() -> None:
    """After Sculpt/Grab-style edits, vertex X/Y positions are no longer
    on a clean raster. Export must still work — grid indexing comes from
    the stored row/col attributes, never from position."""
    _install_active_plugin()
    side = 8
    original_heightmap = _make_varied_heightmap(side, seed=42)

    original_dir = tempfile.mkdtemp()
    write_displace(os.path.join(original_dir, "displace.bin"), original_heightmap)
    _write_manifest(original_dir)

    import_ctx = _FakeContext()
    import_op = operators.EXM_OT_import_map()
    import_op.directory = original_dir
    import_op.xy_scale = 2.0
    import_op.height_scale = 1.0
    import_op.execute(import_ctx)
    terrain_obj = _terrain_object(import_ctx.collection)

    edit_row, edit_col = 3, 5
    edit_delta = 77.7
    target_index = edit_row * side + edit_col
    for i, v in enumerate(terrain_obj.data.vertices):
        v.co.x += ((i * 0.353) % 1.0) - 0.5
        v.co.y += ((i * 0.617) % 1.0) - 0.5
        if i == target_index:
            v.co.z += edit_delta

    export_dir = tempfile.mkdtemp()
    export_ctx = import_ctx
    export_ctx.active_object = terrain_obj
    export_op = operators.EXM_OT_export_map()
    export_op.directory = export_dir
    export_op.xy_scale = 2.0
    export_op.height_scale = 1.0
    export_op.ground_level_offset = 0.0
    assert export_op.execute(export_ctx) == {"FINISHED"}

    reimported = read_displace(os.path.join(export_dir, "displace.bin"))
    for row in range(side):
        for col in range(side):
            expected = original_heightmap.get_height(col, row)
            if (row, col) == (edit_row, edit_col):
                expected += edit_delta
            actual = reimported.get_height(col, row)
            assert abs(actual - expected) < 1e-2, (
                f"mismatch at (row={row}, col={col}): expected {expected}, got {actual}"
            )


_ALL_TESTS = (
    test_full_cycle_default_scale,
    test_full_cycle_confirmed_size_32,
    test_full_cycle_confirmed_size_64,
    test_full_cycle_nontrivial_xy_scale,
    test_full_cycle_nontrivial_height_scale,
    test_full_cycle_nontrivial_both_scales,
    test_full_cycle_small_grid,
    test_export_preserves_unparsed_files_and_leaves_source_untouched,
    test_full_cycle_survives_irregular_edit_between_import_and_export,
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
