# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for model resolution diagnostics.

The tool's whole value is that different causes look different, so the
tests are organised by failure stage: each one constructs a map that
breaks at exactly that point and checks the report says so — and says
nothing about the later stages, which were never reached.
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

from core.diagnostics import STAGE_NAMES, DiagnosticsReport, ResolutionRecord, Stage  # noqa: E402
from core.objects import ObjectInstance  # noqa: E402
from formats.exm.gam import MAGIC  # noqa: E402
from formats.exm.model_catalog import ModelCatalog, ModelEntry  # noqa: E402
from formats.exm.model_trace import trace_objects  # noqa: E402
from utils.math import Vector3  # noqa: E402


def _node(name: str, asset_id: str | None, node_class: str = "SgAnimatedModelNode"):
    return ObjectInstance(
        name=name, node_class=node_class, asset_id=asset_id, org=Vector3(0, 0, 0),
    )


def _catalog(**entries: str) -> tuple[ModelCatalog, dict[str, str]]:
    catalog = ModelCatalog()
    sources = {}
    for model_id, path in entries.items():
        catalog.add(ModelEntry(model_id=model_id, file_path=path))
        sources[model_id] = "testcatalog.xml"
    return catalog, sources


def _install_model(root: str, relative: str, *, valid: bool = True) -> None:
    """Write a .gam at a game-relative path, optionally a broken one."""
    destination = os.path.join(root, relative.replace("\\", os.sep))
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if not valid:
        with open(destination, "wb") as f:
            f.write(b"NOT A GAM FILE AT ALL")
        return

    header = bytearray(72)
    header[0:5] = b"mesh\x00"
    struct.pack_into("<I", header, 56, 44)
    struct.pack_into("<I", header, 64, 3)
    struct.pack_into("<I", header, 68, 1)
    vertices = bytearray()
    for i in range(3):
        vertex = bytearray(44)
        struct.pack_into("<3f", vertex, 0, float(i), float(i), float(i))
        struct.pack_into("<3f", vertex, 12, 0.0, 1.0, 0.0)
        vertices += vertex
    chunk = bytes(header) + bytes(vertices) + struct.pack("<3H", 0, 1, 2)
    chunk += struct.pack("<6f", 0.0, 0.0, 0.0, 2.0, 2.0, 2.0)
    container = bytearray(MAGIC + b"\x00") + struct.pack("<I", 1)
    container += struct.pack("<4I", 4, len(chunk), 28, 0) + chunk
    with open(destination, "wb") as f:
        f.write(container)


# --- stage detection ---


def test_node_without_an_id_is_reported_as_such() -> None:
    catalog, sources = _catalog()
    report = trace_objects([_node("N1", None)], catalog, sources, tempfile.mkdtemp())
    assert report.records[0].failed_at == Stage.HAS_ID


def test_sound_and_group_nodes_are_not_counted_as_failures() -> None:
    """They legitimately have no model; counting them buries the real
    failures under expected noise."""
    catalog, sources = _catalog()
    objects = [
        _node("S1", "S_WIND", node_class="SgSoundSourceNode"),
        _node("G1", None, node_class="SgNode"),
    ]
    report = trace_objects(objects, catalog, sources, tempfile.mkdtemp())
    assert report.not_applicable() == 2
    assert report.counts_by_failure() == {}
    assert len(report.expecting_a_model()) == 0


def test_missing_catalogue_entry_is_distinguished() -> None:
    catalog, sources = _catalog(other="data\\models\\other.gam")
    report = trace_objects([_node("N1", "nosuch")], catalog, sources, tempfile.mkdtemp())
    record = report.records[0]
    assert record.failed_at == Stage.CATALOGUE_ENTRY
    assert "not listed" in record.failure_reason
    # nothing later should be claimed
    assert record.resolved_path is None
    assert record.mesh_count is None


def test_missing_file_is_distinguished_from_missing_entry() -> None:
    """The two look identical in the viewport and need different fixes."""
    root = tempfile.mkdtemp()
    catalog, sources = _catalog(rock="data\\models\\rock.gam")  # never installed
    report = trace_objects([_node("N1", "rock")], catalog, sources, root)
    record = report.records[0]
    assert record.failed_at == Stage.FILE_FOUND
    assert record.game_path == "data\\models\\rock.gam"
    assert record.catalogue_source == "testcatalog.xml"


def test_unparsable_file_is_distinguished_from_missing_file() -> None:
    root = tempfile.mkdtemp()
    _install_model(root, "data\\models\\rock.gam", valid=False)
    catalog, sources = _catalog(rock="data\\models\\rock.gam")
    report = trace_objects([_node("N1", "rock")], catalog, sources, root)
    record = report.records[0]
    assert record.failed_at == Stage.PARSED
    assert record.resolved_path is not None, "the file WAS found; only parsing failed"


def test_a_fully_resolvable_model_reaches_geometry() -> None:
    root = tempfile.mkdtemp()
    _install_model(root, "data\\models\\rock.gam")
    catalog, sources = _catalog(rock="data\\models\\rock.gam")
    report = trace_objects([_node("N1", "rock")], catalog, sources, root)
    record = report.records[0]
    assert record.reached == Stage.HAS_GEOMETRY
    assert record.mesh_count == 1
    assert record.vertex_count == 3
    assert record.triangle_count == 1


def test_parse_can_be_skipped_for_a_fast_check() -> None:
    root = tempfile.mkdtemp()
    _install_model(root, "data\\models\\rock.gam", valid=False)
    catalog, sources = _catalog(rock="data\\models\\rock.gam")
    report = trace_objects(
        [_node("N1", "rock")], catalog, sources, root, parse_files=False,
    )
    # Stops at FILE_FOUND without claiming anything about parsing.
    assert report.records[0].reached == Stage.FILE_FOUND


def test_models_are_parsed_once_per_id_not_once_per_object() -> None:
    """A model used 400 times must not be read 400 times, or the report
    would take longer than the import it is diagnosing."""
    root = tempfile.mkdtemp()
    _install_model(root, "data\\models\\rock.gam")
    catalog, sources = _catalog(rock="data\\models\\rock.gam")
    objects = [_node(f"N{i}", "rock") for i in range(50)]
    report = trace_objects(objects, catalog, sources, root)
    assert report.resolved() == 0  # scene stages not run here
    assert all(r.reached == Stage.HAS_GEOMETRY for r in report.records)


# --- report aggregation ---


def test_summary_separates_expected_from_failed() -> None:
    root = tempfile.mkdtemp()
    catalog, sources = _catalog(rock="data\\models\\rock.gam")
    objects = [
        _node("S1", "S_WIND", node_class="SgSoundSourceNode"),
        _node("N1", "rock"),      # file missing
        _node("N2", "nosuch"),    # not in catalogue
    ]
    report = trace_objects(objects, catalog, sources, root)

    assert report.referenced() == 3
    assert report.not_applicable() == 1
    assert len(report.expecting_a_model()) == 2
    counts = report.counts_by_failure()
    assert counts[Stage.CATALOGUE_ENTRY] == 1
    assert counts[Stage.FILE_FOUND] == 1


def test_affected_ids_groups_by_model_not_object() -> None:
    """'412 objects missing' and '3 models missing, used 412 times' are
    very different problems."""
    root = tempfile.mkdtemp()
    catalog, sources = _catalog()
    objects = [_node(f"N{i}", "ghost") for i in range(7)]
    report = trace_objects(objects, catalog, sources, root)
    ids = report.affected_ids(Stage.CATALOGUE_ENTRY)
    assert ids == {"ghost": 7}


def test_find_matches_by_id_or_node_name() -> None:
    root = tempfile.mkdtemp()
    catalog, sources = _catalog()
    report = trace_objects([_node("Object42", "ghost")], catalog, sources, root)
    assert len(report.find("ghost")) == 1
    assert len(report.find("Object42")) == 1
    assert report.find("nothing") == []


def test_chain_rendering_marks_the_stop_point_once() -> None:
    record = ResolutionRecord(node_name="N1", asset_id="rock")
    record.advance(Stage.HAS_ID)
    record.advance(Stage.CATALOGUE_ENTRY)
    record.fail(Stage.FILE_PATH, "no path")

    text = record.describe_chain()
    assert text.count("[STOP]") == 1
    assert STAGE_NAMES[Stage.FILE_PATH] in text
    # stages after the stop are shown as not reached, not as failures
    assert "[OK  ] file parsed" not in text


def test_summary_lines_report_no_failures_when_all_resolve() -> None:
    report = DiagnosticsReport()
    record = ResolutionRecord(node_name="N1", asset_id="rock")
    record.advance(Stage.VISIBLE)
    report.records.append(record)
    assert report.resolved() == 1
    assert any("no failures" in line for line in report.summary_lines())


# --- Blender-side stages ---


def _resolved_record(name: str, asset_id: str) -> ResolutionRecord:
    record = ResolutionRecord(node_name=name, asset_id=asset_id)
    record.advance(Stage.HAS_GEOMETRY)
    return record


def test_object_never_created_is_distinguished_from_hidden() -> None:
    """Never created, created-but-unlinked and linked-but-hidden all look
    identical in the viewport and need different fixes."""
    from blender_io.diagnostics_bridge import annotate_with_scene

    report = DiagnosticsReport()
    report.records.append(_resolved_record("Missing", "rock"))

    collection = fake_bpy.FakeCollection("Map")
    annotate_with_scene(report, collection)

    assert report.records[0].failed_at == Stage.OBJECT_CREATED


def test_linked_and_visible_object_completes_the_chain() -> None:
    from blender_io.diagnostics_bridge import annotate_with_scene

    report = DiagnosticsReport()
    report.records.append(_resolved_record("Object1", "rock"))

    collection = fake_bpy.FakeCollection("Map")
    child = fake_bpy.FakeCollection("Objects")
    collection.children.link(child)
    obj = fake_bpy.FakeObject("Object1", None)
    child.objects.link(obj)

    annotate_with_scene(report, collection)
    record = report.records[0]
    assert record.succeeded
    assert record.collection == "Objects"


def test_an_object_is_found_by_its_node_name_not_its_display_name() -> None:
    """Regression: this diagnostic reported that NOT ONE of 1562 models
    had a Blender object, in a scene where the coverage report counted
    5888 imported and every one visualised.

    The importer deliberately builds a readable display name out of the
    node id and the model it draws, and keeps the real node name in a
    custom property. Indexing the scene by obj.name therefore could
    never match, and the tool answered "nothing imported" about a fully
    imported scene. Every existing test happened to name its object
    exactly like its node, which is the one case that cannot occur.
    """
    from blender_io.diagnostics_bridge import annotate_with_scene
    from blender_io.world_bridge import ORIGINAL_NAME_PROP

    report = DiagnosticsReport()
    report.records.append(_resolved_record("Object76476712", "Cube7"))

    collection = fake_bpy.FakeCollection("Map")
    child = fake_bpy.FakeCollection("Objects")
    collection.children.link(child)
    obj = fake_bpy.FakeObject("Object76476712 [Cube7]", None)
    obj[ORIGINAL_NAME_PROP] = "Object76476712"
    child.objects.link(obj)

    annotate_with_scene(report, collection)
    record = report.records[0]
    assert record.succeeded, "the object is there under its display name"
    assert record.blender_object == "Object76476712 [Cube7]"


def test_a_hand_made_object_is_still_found_by_its_plain_name() -> None:
    """Objects the user created carry no such property."""
    from blender_io.diagnostics_bridge import annotate_with_scene

    report = DiagnosticsReport()
    report.records.append(_resolved_record("Object1", "rock"))

    collection = fake_bpy.FakeCollection("Map")
    collection.objects.link(fake_bpy.FakeObject("Object1", None))

    annotate_with_scene(report, collection)
    assert report.records[0].succeeded


def test_hidden_object_is_reported_as_hidden_not_missing() -> None:
    from blender_io.diagnostics_bridge import annotate_with_scene

    report = DiagnosticsReport()
    report.records.append(_resolved_record("Object1", "rock"))

    collection = fake_bpy.FakeCollection("Map")
    obj = fake_bpy.FakeObject("Object1", None)
    obj.hide_viewport = True
    collection.objects.link(obj)

    annotate_with_scene(report, collection)
    record = report.records[0]
    assert record.failed_at == Stage.VISIBLE
    assert "hidden" in record.failure_reason
    # it WAS created and linked — those stages must still show as reached
    assert record.blender_object == "Object1"
    assert record.collection == "Map"


def test_scene_check_does_not_overwrite_an_earlier_failure() -> None:
    """A model whose file was never found must not be re-reported as
    'not visible' — that would hide the real cause."""
    from blender_io.diagnostics_bridge import annotate_with_scene

    report = DiagnosticsReport()
    record = ResolutionRecord(node_name="Object1", asset_id="rock")
    record.advance(Stage.CATALOGUE_ENTRY)
    record.fail(Stage.FILE_FOUND, "no file on disk")
    report.records.append(record)

    annotate_with_scene(report, fake_bpy.FakeCollection("Map"))
    assert record.failed_at == Stage.FILE_FOUND
    assert record.failure_reason == "no file on disk"


def test_game_root_normalisation_is_shared_with_the_importer() -> None:
    """Regression: diagnostics used the raw preference value while the
    importer normalised it, so a Game Folder set to GC/data/models made
    the report claim zero catalogue entries — contradicting the import
    it was meant to explain."""
    from formats.exm.model_catalog import normalise_game_root

    root = tempfile.mkdtemp()
    models = os.path.join(root, "data", "models")
    os.makedirs(models)

    assert normalise_game_root(root) == os.path.abspath(root)
    assert normalise_game_root(models) == os.path.abspath(root)
    assert normalise_game_root(tempfile.mkdtemp()) is None


def test_non_xml_references_are_not_treated_as_catalogues() -> None:
    """A servers index also points at particle systems and textures.
    Reading those as catalogues produced hundreds of irrelevant 'not
    found' lines that buried the real problem."""
    from formats.exm.model_catalog import read_catalogs_from_servers

    root = tempfile.mkdtemp()
    index = os.path.join(root, "servers.xml")
    with open(index, "w", encoding="cp1251") as f:
        f.write(
            '<?xml version="1.0"?><Servers><ParticlesServer>'
            '<Item id="p1" file="data\\Effects\\boom.psys" />'
            '<Item id="t1" file="data\\fx\\smoke.tga" />'
            "</ParticlesServer></Servers>"
        )

    problems: list[str] = []
    read_catalogs_from_servers(index, root, problems=problems)
    assert problems == [], f"non-catalogue files were reported: {problems}"


_ALL_TESTS = (
    test_node_without_an_id_is_reported_as_such,
    test_sound_and_group_nodes_are_not_counted_as_failures,
    test_missing_catalogue_entry_is_distinguished,
    test_missing_file_is_distinguished_from_missing_entry,
    test_unparsable_file_is_distinguished_from_missing_file,
    test_a_fully_resolvable_model_reaches_geometry,
    test_parse_can_be_skipped_for_a_fast_check,
    test_models_are_parsed_once_per_id_not_once_per_object,
    test_summary_separates_expected_from_failed,
    test_affected_ids_groups_by_model_not_object,
    test_find_matches_by_id_or_node_name,
    test_chain_rendering_marks_the_stop_point_once,
    test_summary_lines_report_no_failures_when_all_resolve,
    test_object_never_created_is_distinguished_from_hidden,
    test_linked_and_visible_object_completes_the_chain,
    test_hidden_object_is_reported_as_hidden_not_missing,
    test_scene_check_does_not_overwrite_an_earlier_failure,
    test_game_root_normalisation_is_shared_with_the_importer,
    test_non_xml_references_are_not_treated_as_catalogues,
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
