# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for map coverage reporting.

The report exists to separate two situations that look identical to a
user: a model that was never imported, and one that was imported but
can't be seen. Most of these tests are about that boundary being drawn
accurately — a false "complete" sends the investigation the wrong way,
and so does a false shortfall.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.coverage_bridge import annotate_with_scene  # noqa: E402
from core.coverage import compare_maps, count_expectations  # noqa: E402
from core.objects import ObjectInstance  # noqa: E402
from formats.exm.world import read_world  # noqa: E402
from utils.math import Vector3  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402


def _node(name, asset_id, node_class="SgAnimatedModelNode", children=None):
    return ObjectInstance(
        name=name,
        node_class=node_class,
        asset_id=asset_id,
        org=Vector3(0, 0, 0),
        children=list(children or []),
    )


def _mesh_object(name, asset_id=None, *, faces=True):
    mesh = fake_bpy.FakeMesh(name + "_mesh")
    mesh.from_pydata(
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)] if faces else [],
    )
    obj = fake_bpy.FakeObject(name, mesh)
    if asset_id:
        obj["exm_id"] = asset_id
    return obj


# --- expectations ---


def test_counts_node_classes() -> None:
    objects = [
        _node("N1", "rock"),
        _node("N2", "rock"),
        _node("S1", "S_WIND", node_class="SgSoundSourceNode"),
        _node("G1", None, node_class="SgNode"),
    ]
    report = count_expectations(objects)
    assert report.class_counts["SgAnimatedModelNode"] == 2
    assert report.class_counts["SgSoundSourceNode"] == 1
    assert report.total_nodes == 4


def test_counts_expected_instances_per_model_id() -> None:
    objects = [_node(f"N{i}", "rock") for i in range(5)] + [_node("N9", "tree")]
    report = count_expectations(objects)
    assert report.models["rock"].expected == 5
    assert report.models["tree"].expected == 1
    assert report.expected_models == 6


def test_nested_objects_are_counted() -> None:
    """Nested objects are as real as top-level ones; leaving them out
    understates what the map asks for."""
    tree = [_node("Parent", None, node_class="SgNode", children=[
        _node("Child", "rock"),
        _node("Deep", None, node_class="SgNode", children=[_node("Deeper", "rock")]),
    ])]
    report = count_expectations(tree)
    assert report.models["rock"].expected == 2


def test_sound_and_group_nodes_expect_no_model() -> None:
    objects = [
        _node("S1", "S_WIND", node_class="SgSoundSourceNode"),
        _node("G1", None, node_class="SgNode"),
    ]
    report = count_expectations(objects)
    assert report.models == {}
    assert report.expected_models == 0


def test_unknown_classes_are_carried_into_the_report() -> None:
    report = count_expectations([_node("N1", "rock")], {"SgSpriteNode": 12})
    assert report.unknown_classes == {"SgSpriteNode": 12}
    assert any("SgSpriteNode" in line for line in report.summary_lines())


# --- scene side ---


def test_imported_instances_are_counted_from_the_scene() -> None:
    report = count_expectations([_node("N1", "rock"), _node("N2", "rock")])
    collection = fake_bpy.FakeCollection("Map")
    collection.objects.link(_mesh_object("N1", "rock"))
    collection.objects.link(_mesh_object("N2", "rock"))

    annotate_with_scene(report, collection)
    assert report.models["rock"].imported == 2
    assert report.models["rock"].complete


def test_empties_do_not_count_as_imported() -> None:
    """An Empty means the model could not be loaded. Counting it would
    report full coverage for a map whose models are all missing — the
    exact opposite of the truth."""
    report = count_expectations([_node("N1", "rock")])
    collection = fake_bpy.FakeCollection("Map")
    empty = fake_bpy.FakeObject("N1", None)
    empty["exm_id"] = "rock"
    collection.objects.link(empty)

    annotate_with_scene(report, collection)
    assert report.models["rock"].imported == 0
    assert report.models["rock"].missing == 1


def test_terrain_is_classified_separately_from_models() -> None:
    """The report exists partly because '1580 meshes for 1579 models'
    reads as an off-by-one until the extra is named as terrain."""
    report = count_expectations([_node("N1", "rock")])
    collection = fake_bpy.FakeCollection("Map")
    terrain = _mesh_object("ExM_Terrain")
    terrain["exm_cell_size"] = 8.0
    collection.objects.link(terrain)
    collection.objects.link(_mesh_object("N1", "rock"))

    annotate_with_scene(report, collection)
    assert report.composition.terrain == 1
    assert report.composition.imported_models == 1
    assert report.composition.total == 2


def test_diagnostic_markers_are_classified_separately() -> None:
    report = count_expectations([])
    collection = fake_bpy.FakeCollection("Map")
    marker = _mesh_object("DIAG_SUSPECT_X")
    marker["exm_diag_score"] = 70
    collection.objects.link(marker)

    annotate_with_scene(report, collection)
    assert report.composition.diagnostic_markers == 1
    assert report.composition.imported_models == 0


def test_unrelated_meshes_are_counted_as_other() -> None:
    report = count_expectations([])
    collection = fake_bpy.FakeCollection("Map")
    collection.objects.link(_mesh_object("UserCube"))

    annotate_with_scene(report, collection)
    assert report.composition.other == 1


def test_scene_walk_includes_sub_collections() -> None:
    report = count_expectations([_node("N1", "rock")])
    root = fake_bpy.FakeCollection("Map")
    child = fake_bpy.FakeCollection("Objects")
    root.children.link(child)
    child.objects.link(_mesh_object("N1", "rock"))

    annotate_with_scene(report, root)
    assert report.models["rock"].imported == 1


def test_game_unit_nodes_are_treated_like_model_nodes() -> None:
    """Tested because a plausible hypothesis said otherwise: that the
    SDK only builds geometry for SgAnimatedModelNode, which would
    explain why cities and bosses appear to be missing. It does not —
    both classes resolve models the same way, and this pins that down
    so the question does not have to be re-litigated."""
    objects = [
        _node("N1", "rock", node_class="SgAnimatedModelNode"),
        _node("N2", "city01", node_class="SgGameUnitNode"),
    ]
    report = count_expectations(objects)
    assert report.models["city01"].expected == 1
    assert report.by_class["SgGameUnitNode"].expecting_model == 1

    collection = fake_bpy.FakeCollection("Map")
    for name, asset_id, node_class in (
        ("N1", "rock", "SgAnimatedModelNode"),
        ("N2", "city01", "SgGameUnitNode"),
    ):
        obj = _mesh_object(name, asset_id)
        obj["exm_class"] = node_class
        collection.objects.link(obj)

    annotate_with_scene(report, collection)
    assert report.models["city01"].imported == 1
    assert report.by_class["SgGameUnitNode"].visualised == 1
    assert report.by_class["SgGameUnitNode"].without_visual == 0


def test_class_breakdown_separates_classes_that_expect_no_model() -> None:
    objects = [
        _node("S1", "S_WIND", node_class="SgSoundSourceNode"),
        _node("G1", None, node_class="SgNode"),
        _node("N1", "rock"),
    ]
    report = count_expectations(objects)
    assert report.by_class["SgSoundSourceNode"].expecting_model == 0
    assert report.by_class["SgNode"].expecting_model == 0
    assert report.by_class["SgAnimatedModelNode"].expecting_model == 1

    lines = report.class_visualisation_lines()
    assert any("no model expected" in line for line in lines)


def test_class_breakdown_names_classes_missing_their_visuals() -> None:
    """The measurement that turns 'the city is missing' from a claim
    into a number attributable to a specific node class."""
    objects = [_node(f"N{i}", "city01", node_class="SgGameUnitNode") for i in range(4)]
    report = count_expectations(objects)
    collection = fake_bpy.FakeCollection("Map")
    obj = _mesh_object("N0", "city01")
    obj["exm_class"] = "SgGameUnitNode"
    collection.objects.link(obj)

    annotate_with_scene(report, collection)
    entry = report.by_class["SgGameUnitNode"]
    assert entry.expecting_model == 4
    assert entry.visualised == 1
    assert entry.without_visual == 3
    assert any("3 WITHOUT a visual" in line for line in report.class_visualisation_lines())


# --- discrepancies ---


def test_missing_instances_are_reported_per_id() -> None:
    report = count_expectations([_node(f"N{i}", "rock") for i in range(6)])
    collection = fake_bpy.FakeCollection("Map")
    for i in range(4):
        collection.objects.link(_mesh_object(f"N{i}", "rock"))

    annotate_with_scene(report, collection)
    coverage = report.models["rock"]
    assert coverage.expected == 6
    assert coverage.imported == 4
    assert coverage.missing == 2
    assert not coverage.complete


def test_extra_instances_are_reported_too() -> None:
    """More imported than expected usually means a duplicate import,
    which is as wrong as a shortfall but reads as success otherwise."""
    report = count_expectations([_node("N1", "rock")])
    collection = fake_bpy.FakeCollection("Map")
    collection.objects.link(_mesh_object("N1", "rock"))
    collection.objects.link(_mesh_object("N1_dup", "rock"))

    annotate_with_scene(report, collection)
    assert report.models["rock"].unexpected == 1
    assert report.incomplete()


def test_a_fully_covered_map_reports_no_discrepancies() -> None:
    report = count_expectations([_node("N1", "rock"), _node("N2", "tree")])
    collection = fake_bpy.FakeCollection("Map")
    collection.objects.link(_mesh_object("N1", "rock"))
    collection.objects.link(_mesh_object("N2", "tree"))

    annotate_with_scene(report, collection)
    assert report.incomplete() == []
    assert "No per-model discrepancies." in report.mismatch_lines()


def test_expected_nodes_are_recorded_for_tracing() -> None:
    """A shortfall must be traceable back to specific map objects, not
    just a count."""
    report = count_expectations([_node("Object42", "rock")])
    assert report.models["rock"].expected_nodes == ["Object42"]


# --- comparison ---


def test_comparison_finds_ids_unique_to_each_map() -> None:
    left = count_expectations([_node("A", "rock"), _node("B", "tree")])
    right = count_expectations([_node("C", "rock"), _node("D", "fuelstation")])

    comparison = compare_maps(left, "mapA", right, "mapB")
    assert set(comparison.shared) == {"rock"}
    assert set(comparison.only_left) == {"tree"}
    assert set(comparison.only_right) == {"fuelstation"}


def test_comparison_keeps_usage_counts() -> None:
    left = count_expectations([_node(f"N{i}", "rock") for i in range(3)])
    right = count_expectations([_node("X", "rock")])
    comparison = compare_maps(left, "a", right, "b")
    assert comparison.shared["rock"] == (3, 1)


def test_comparison_of_real_world_files() -> None:
    real = corpus("world.xml")
    if not os.path.isfile(real):
        return
    objects, _root = read_world(real)
    report = count_expectations(objects)
    assert report.expected_models > 1000
    assert len(report.models) > 50

    smaller = """<?xml version="1.0" encoding="windows-1251"?>
<World name="O1" class="SgNode" LastId="9">
\t<Node name="O2" class="SgAnimatedModelNode" org="1 2 3" orgRel="1" id="only_here" />
</World>
"""
    path = os.path.join(tempfile.mkdtemp(), "world.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write(smaller)
    other_objects, _r = read_world(path)
    comparison = compare_maps(
        report, "real", count_expectations(other_objects), "small",
    )
    assert "only_here" in comparison.only_right


_ALL_TESTS = (
    test_counts_node_classes,
    test_counts_expected_instances_per_model_id,
    test_nested_objects_are_counted,
    test_sound_and_group_nodes_expect_no_model,
    test_unknown_classes_are_carried_into_the_report,
    test_imported_instances_are_counted_from_the_scene,
    test_empties_do_not_count_as_imported,
    test_terrain_is_classified_separately_from_models,
    test_diagnostic_markers_are_classified_separately,
    test_unrelated_meshes_are_counted_as_other,
    test_scene_walk_includes_sub_collections,
    test_game_unit_nodes_are_treated_like_model_nodes,
    test_class_breakdown_separates_classes_that_expect_no_model,
    test_class_breakdown_names_classes_missing_their_visuals,
    test_missing_instances_are_reported_per_id,
    test_extra_instances_are_reported_too,
    test_a_fully_covered_map_reports_no_discrepancies,
    test_expected_nodes_are_recorded_for_tracing,
    test_comparison_finds_ids_unique_to_each_map,
    test_comparison_keeps_usage_counts,
    test_comparison_of_real_world_files,
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
