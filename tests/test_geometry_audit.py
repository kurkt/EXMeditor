# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for geometry auditing.

Two halves, matching the two modules: judging the source geometry
(`core/geometry_audit.py`) and judging what ended up in the scene
(`blender_io/scene_audit.py`).

The most important property under test is the absence of false
positives. A triage tool that flags healthy models is worse than none,
because it trains you to ignore it — so every anomaly test is paired
with a check that real, known-good models stay clean.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.scene_audit import audit_collection, audit_scene_object  # noqa: E402
from core.geometry_audit import (  # noqa: E402
    Severity,
    audit_model,
    audit_placement,
    measure,
    rank,
    verdict_for,
)
from core.mesh import MeshData, Model  # noqa: E402
from formats.exm.gam import read_model  # noqa: E402
from utils.math import Vector3  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402


def _mesh(positions, triangles, *, normals=True, uvs=True, name="m") -> MeshData:
    mesh = MeshData(name=name)
    mesh.positions = [Vector3(*p) for p in positions]
    mesh.triangles = list(triangles)
    if normals:
        mesh.normals = [Vector3(0.0, 1.0, 0.0) for _ in positions]
    if uvs:
        mesh.uvs = [(0.0, 0.0) for _ in positions]
    return mesh


def _model(*meshes) -> Model:
    model = Model()
    model.meshes = list(meshes)
    return model


def _cube(size: float = 10.0) -> Model:
    """A well-formed box: 8 corners, 12 triangles."""
    h = size / 2
    positions = [
        (-h, -h, -h), (h, -h, -h), (h, h, -h), (-h, h, -h),
        (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h),
    ]
    triangles = [
        (0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
        (0, 4, 5), (0, 5, 1), (2, 6, 7), (2, 7, 3),
        (0, 3, 7), (0, 7, 4), (1, 5, 6), (1, 6, 2),
    ]
    return _model(_mesh(positions, triangles))


# --- level 1: measurement ---


def test_measures_basic_statistics() -> None:
    stats = measure(_cube(10.0))
    assert stats.mesh_count == 1
    assert stats.vertex_count == 8
    assert stats.triangle_count == 12
    assert abs(stats.dimensions.x - 10.0) < 1e-6
    assert stats.has_normals and stats.has_uvs


def test_a_healthy_model_is_not_flagged() -> None:
    """The property that matters most: no false positives."""
    audit = audit_model("cube", _cube(10.0))
    assert audit.findings == []
    assert audit.verdict == "OK"
    assert audit.score == 0


def test_real_models_stay_clean() -> None:
    """Shipped assets must not trip the heuristics, or the ranking is
    useless."""
    for name in ("big_crag_11", "iron_dot", "heavy_dot4"):
        path = corpus(f"{name}.gam")
        if not os.path.isfile(path):
            continue
        audit = audit_model(name, read_model(path))
        assert audit.verdict == "OK", f"{name} was flagged: {audit.findings}"


# --- level 4: degenerate geometry ---


def test_detects_a_model_with_no_triangles() -> None:
    audit = audit_model("ghost", _model(_mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [])))
    codes = {f.code for f in audit.findings}
    assert "no_triangles" in codes
    assert audit.verdict == "SUSPECT"


def test_detects_a_collapsed_model() -> None:
    positions = [(5.0, 5.0, 5.0)] * 6
    audit = audit_model("dot", _model(_mesh(positions, [(0, 1, 2), (3, 4, 5)])))
    assert "collapsed" in {f.code for f in audit.findings}


def test_detects_empty_meshes_among_good_ones() -> None:
    cube = _cube(10.0)
    cube.meshes.append(_mesh([], [], name="empty"))
    audit = audit_model("partial", cube)
    assert "empty_meshes" in {f.code for f in audit.findings}


def test_a_few_degenerate_triangles_do_not_raise_the_score() -> None:
    """Shipped assets routinely carry a handful; flagging those put
    every large model on the list."""
    cube = _cube(10.0)
    # one repeated-index triangle among twelve good ones
    cube.meshes[0].triangles.append((0, 0, 1))
    audit = audit_model("cube", cube)
    finding = next(f for f in audit.findings if f.code == "degenerate_triangles")
    assert finding.severity == Severity.INFO
    assert audit.score == 0


def test_mostly_degenerate_geometry_is_suspect() -> None:
    positions = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    triangles = [(0, 0, 0)] * 9 + [(0, 1, 2)]
    audit = audit_model("broken", _model(_mesh(positions, triangles)))
    finding = next(f for f in audit.findings if f.code == "degenerate_triangles")
    assert finding.severity == Severity.SUSPECT


# --- level 2: size anomalies ---


def test_detects_a_mis_scaled_tiny_model() -> None:
    audit = audit_model("tiny", _cube(0.0004))
    assert "tiny" in {f.code for f in audit.findings}
    assert audit.verdict == "SUSPECT"


def test_detects_an_implausibly_huge_model() -> None:
    audit = audit_model("giant", _cube(1200.0))
    assert "huge" in {f.code for f in audit.findings}


# --- level 6: orientation ---


def test_detects_a_building_lying_on_its_side() -> None:
    positions = [
        (-20.0, 0.0, -20.0), (20.0, 0.0, -20.0), (20.0, 0.3, 20.0), (-20.0, 0.3, 20.0),
    ]
    audit = audit_model("fallen", _model(_mesh(positions, [(0, 1, 2), (0, 2, 3)])))
    assert "flat" in {f.code for f in audit.findings}


# --- level 3: pivot ---


def test_detects_geometry_far_from_its_own_origin() -> None:
    positions = [(500.0, 500.0, 500.0), (505.0, 500.0, 500.0), (500.0, 505.0, 500.0)]
    audit = audit_model("offset", _model(_mesh(positions, [(0, 1, 2)])))
    assert "pivot_offset" in {f.code for f in audit.findings}


# --- level 5: terrain relation ---


def test_detects_a_model_floating_above_the_ground() -> None:
    audit = audit_model("hut", _cube(10.0))
    # cube spans -5..5 in Y, placed at height 350, ground at 286
    audit_placement(audit, Vector3(0.0, 350.0, 0.0), ground_height=286.0)
    assert "floating" in {f.code for f in audit.findings}


def test_detects_a_model_sunk_into_the_ground() -> None:
    audit = audit_model("hut", _cube(10.0))
    audit_placement(audit, Vector3(0.0, 200.0, 0.0), ground_height=286.0)
    assert "sunken" in {f.code for f in audit.findings}


def test_a_model_resting_on_the_ground_is_not_flagged() -> None:
    audit = audit_model("hut", _cube(10.0))
    # base at 286.0 exactly: placed at 291 with the cube spanning -5..5
    audit_placement(audit, Vector3(0.0, 291.0, 0.0), ground_height=286.0)
    assert audit.findings == []


def test_placement_check_is_skipped_without_terrain() -> None:
    audit = audit_model("hut", _cube(10.0))
    audit_placement(audit, Vector3(0.0, 9999.0, 0.0), ground_height=None)
    assert audit.findings == []


# --- scoring and ranking ---


def test_verdict_follows_the_worst_finding_not_the_point_total() -> None:
    """One definitive problem means broken, however few others there
    are. Scoring by points alone labelled 'no faces, nothing will be
    drawn' — a single 35-point finding — as a mere warning."""
    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [])
    audit = audit_scene_object(fake_bpy.FakeObject("Object1", mesh), "Objects")

    assert len([f for f in audit.findings if f.severity == Severity.SUSPECT]) == 1
    assert audit.score < 60, "a single finding scores below the old SUSPECT threshold"
    assert audit.verdict == "SUSPECT"


def test_verdict_for_empty_findings_is_ok() -> None:
    assert verdict_for([]) == "OK"


def test_ranking_puts_the_worst_first() -> None:
    clean = audit_model("clean", _cube(10.0))
    tiny = audit_model("tiny", _cube(0.0004))
    ordered = rank([clean, tiny])
    assert ordered[0].model_id == "tiny"


# --- scene audit ---


def test_scene_audit_reports_a_healthy_object() -> None:
    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = fake_bpy.FakeObject("Object1", mesh)

    audit = audit_scene_object(obj, "Objects")
    assert audit.exists and audit.has_mesh_data
    assert audit.vertex_count == 3
    assert audit.polygon_count == 1
    assert audit.verdict == "OK"


def test_scene_audit_detects_vertices_without_faces() -> None:
    """The exact case that is invisible in the viewport and impossible
    to distinguish from 'model missing' without this check."""
    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [])
    obj = fake_bpy.FakeObject("Object1", mesh)

    audit = audit_scene_object(obj, "Objects")
    assert audit.vertex_count == 3
    assert audit.polygon_count == 0
    assert "no_faces_in_scene" in {f.code for f in audit.findings}
    assert audit.verdict == "SUSPECT"


def test_scene_audit_detects_a_collapsed_scale_axis() -> None:
    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = fake_bpy.FakeObject("Object1", mesh)
    obj.scale = (1.0, 0.0, 1.0)

    audit = audit_scene_object(obj, "Objects")
    assert "collapsed_scale" in {f.code for f in audit.findings}


def test_scene_audit_detects_negative_scale() -> None:
    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = fake_bpy.FakeObject("Object1", mesh)
    obj.scale = (1.0, -1.0, 1.0)

    audit = audit_scene_object(obj, "Objects")
    assert "negative_scale" in {f.code for f in audit.findings}


def test_scene_audit_detects_an_unlinked_object() -> None:
    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = fake_bpy.FakeObject("Object1", mesh)

    audit = audit_scene_object(obj, None)
    assert "unlinked" in {f.code for f in audit.findings}


def test_scene_audit_reports_hidden_objects() -> None:
    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = fake_bpy.FakeObject("Object1", mesh)
    obj.hide_viewport = True

    audit = audit_scene_object(obj, "Objects")
    assert not audit.visible
    assert "hidden" in {f.code for f in audit.findings}


def test_empties_are_not_judged_as_broken_geometry() -> None:
    """An Empty is the expected result when a model could not be
    loaded; the resolution diagnostics explains that separately, and
    flagging it here would double-report a known problem."""
    obj = fake_bpy.FakeObject("Object1", None)
    audit = audit_scene_object(obj, "Objects")
    assert not audit.has_mesh_data
    assert audit.findings == []


def test_world_position_accounts_for_parenting() -> None:
    parent = fake_bpy.FakeObject("Parent", None)
    parent.location = (100.0, 200.0, 0.0)
    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    child = fake_bpy.FakeObject("Child", mesh)
    child.location = (5.0, 5.0, 0.0)
    child.parent = parent

    audit = audit_scene_object(child, "Objects")
    assert audit.world_position.x == 105.0
    assert audit.world_position.y == 205.0


def test_audit_collection_walks_sub_collections() -> None:
    root = fake_bpy.FakeCollection("Map")
    child = fake_bpy.FakeCollection("Objects")
    root.children.link(child)
    mesh = fake_bpy.FakeMesh("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    child.objects.link(fake_bpy.FakeObject("Deep", mesh))
    root.objects.link(fake_bpy.FakeObject("Shallow", None))

    results = audit_collection(root)
    assert {a.name for a in results} == {"Deep", "Shallow"}


_ALL_TESTS = (
    test_measures_basic_statistics,
    test_a_healthy_model_is_not_flagged,
    test_real_models_stay_clean,
    test_detects_a_model_with_no_triangles,
    test_detects_a_collapsed_model,
    test_detects_empty_meshes_among_good_ones,
    test_a_few_degenerate_triangles_do_not_raise_the_score,
    test_mostly_degenerate_geometry_is_suspect,
    test_detects_a_mis_scaled_tiny_model,
    test_detects_an_implausibly_huge_model,
    test_detects_a_building_lying_on_its_side,
    test_detects_geometry_far_from_its_own_origin,
    test_detects_a_model_floating_above_the_ground,
    test_detects_a_model_sunk_into_the_ground,
    test_a_model_resting_on_the_ground_is_not_flagged,
    test_placement_check_is_skipped_without_terrain,
    test_verdict_follows_the_worst_finding_not_the_point_total,
    test_verdict_for_empty_findings_is_ok,
    test_ranking_puts_the_worst_first,
    test_scene_audit_reports_a_healthy_object,
    test_scene_audit_detects_vertices_without_faces,
    test_scene_audit_detects_a_collapsed_scale_axis,
    test_scene_audit_detects_negative_scale,
    test_scene_audit_detects_an_unlinked_object,
    test_scene_audit_reports_hidden_objects,
    test_empties_are_not_judged_as_broken_geometry,
    test_world_position_accounts_for_parenting,
    test_audit_collection_walks_sub_collections,
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
