# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the asset library and map validation.

The library exists because three features — browsing, replacing and
validating — all need the same thing: models with metadata. These
tests pin down the metadata they rely on, and in particular that
categorisation copes with real catalogue data, which is messier than
it looks.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.assets import (  # noqa: E402
    EXTERNAL_CATEGORY,
    UNCATEGORISED,
    AssetInfo,
    AssetLibrary,
    build_library,
    categorise,
    display_name_for,
    measure_asset,
)
from core.map_validation import Severity, validate_map  # noqa: E402
from core.objects import ObjectInstance  # noqa: E402
from core.scene import MapScene  # noqa: E402
from formats.exm.model_catalog import ModelCatalog, ModelEntry  # noqa: E402
from utils.math import Vector3  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402


def _catalog(**entries: str) -> ModelCatalog:
    catalog = ModelCatalog()
    for model_id, path in entries.items():
        catalog.add(ModelEntry(model_id=model_id, file_path=path))
    return catalog


def _node(name, asset_id, node_class="SgAnimatedModelNode", org=None, scale=None):
    return ObjectInstance(
        name=name,
        node_class=node_class,
        asset_id=asset_id,
        org=org if org is not None else Vector3(100, 0, 100),
        org_rel=True,
        scale=scale,
    )


# --- categorisation ---


def test_categories_come_from_the_directory_structure() -> None:
    """Derived from where the game puts models, not from a list
    invented here — so a mod that adds folders still categorises."""
    assert categorise("data\\models\\buildings\\region1\\house3.gam") == ("Buildings", "")
    assert categorise("data\\models\\nature\\region1\\trees\\dub3.gam") == ("Nature", "Trees")
    assert categorise("data\\models\\vehicles\\ural\\cab.gam") == ("Vehicles", "Ural")


def test_region_folders_are_not_used_as_subcategories() -> None:
    """'region1' says nothing about what an asset is."""
    category, subcategory = categorise("data\\models\\buildings\\region2\\bar.gam")
    assert category == "Buildings"
    assert subcategory == ""


def test_absolute_paths_get_their_own_category() -> None:
    """Real catalogues contain models added by hand during modding,
    pointing at a working folder. Categorising by those folders
    produced a category called 'C:'."""
    assert categorise("C:\\Users\\Someone\\Desktop\\test.gam") == (EXTERNAL_CATEGORY, "")
    assert categorise("/home/someone/model.gam") == (EXTERNAL_CATEGORY, "")


def test_unknown_top_level_folders_become_their_own_category() -> None:
    """Better than discarding the information into a catch-all."""
    category, _sub = categorise("data\\models\\wares\\crate.gam")
    assert category == "Wares"


def test_a_pathless_entry_is_uncategorised() -> None:
    assert categorise("model.gam") == (UNCATEGORISED, "")


def test_display_names_are_readable() -> None:
    assert display_name_for("r1_Dub_Kust1") == "Dub Kust1"
    assert display_name_for("big_stone2") == "Big stone2"


# --- library ---


def test_builds_a_library_from_a_catalogue() -> None:
    catalog = _catalog(
        house3="data\\models\\buildings\\region1\\house3.gam",
        dub3="data\\models\\nature\\region1\\trees\\dub3.gam",
    )
    library = build_library(catalog)
    assert len(library) == 2
    assert library.get("house3").category == "Buildings"
    assert "dub3" in library


def test_usage_counts_come_from_the_map() -> None:
    catalog = _catalog(house3="data\\models\\buildings\\house3.gam")
    library = build_library(catalog, usage_counts={"house3": 13})
    assert library.get("house3").usage_count == 13


def test_search_orders_by_usage_not_alphabetically() -> None:
    """A search for 'stone' on a real map returns dozens of hits; the
    one placed 132 times is likelier to be wanted than the one placed
    once."""
    catalog = _catalog(
        stone_a="data\\models\\nature\\stones\\stone_a.gam",
        stone_b="data\\models\\nature\\stones\\stone_b.gam",
    )
    library = build_library(catalog, usage_counts={"stone_b": 100, "stone_a": 1})
    results = library.search("stone")
    assert [a.asset_id for a in results] == ["stone_b", "stone_a"]


def test_search_matches_id_name_and_category() -> None:
    catalog = _catalog(house3="data\\models\\buildings\\region1\\house3.gam")
    library = build_library(catalog)
    assert library.search("house")
    assert library.search("HOUSE")          # case-insensitive
    assert library.search("buildings")      # by category
    assert library.search("nothing") == []


def test_empty_search_returns_everything() -> None:
    catalog = _catalog(a="data\\models\\objects\\a.gam", b="data\\models\\objects\\b.gam")
    assert len(build_library(catalog).search("")) == 2


def test_category_listing() -> None:
    catalog = _catalog(
        house3="data\\models\\buildings\\region1\\house3.gam",
        bar1="data\\models\\buildings\\region1\\bar1.gam",
        dub3="data\\models\\nature\\region1\\trees\\dub3.gam",
    )
    library = build_library(catalog)
    assert library.categories()["Buildings"] == 2
    assert len(library.in_category("Buildings")) == 2
    assert library.subcategories("Nature") == {"Trees": 1}


def test_geometry_is_absent_until_measured() -> None:
    """None means 'not read yet', which a browser must show differently
    from zero — an empty field would be indistinguishable from a model
    with no vertices."""
    asset = AssetInfo(asset_id="x", file_path="data\\models\\x.gam")
    assert asset.geometry is None
    assert not asset.is_measured
    assert AssetLibrary().unmeasured() == []


def test_measuring_a_missing_file_fails_without_raising() -> None:
    asset = AssetInfo(asset_id="x", file_path="data\\models\\x.gam")
    asset.resolved_path = "/no/such/file.gam"
    assert measure_asset(asset) is False
    assert asset.geometry is None


def test_measuring_a_real_model_records_its_geometry() -> None:
    real = corpus("big_crag_11.gam")
    if not os.path.isfile(real):
        return
    asset = AssetInfo(asset_id="big_crag_11", file_path="x.gam")
    asset.resolved_path = real
    assert measure_asset(asset) is True
    assert asset.geometry.vertex_count == 259
    assert asset.geometry.triangle_count == 152
    assert asset.geometry.dimensions is not None


def test_library_from_the_real_catalogue() -> None:
    from formats.exm.model_catalog import read_model_catalog

    real = corpus("animmodels.xml")
    if not os.path.isfile(real):
        return
    library = build_library(read_model_catalog(real))
    assert len(library) > 1000
    categories = library.categories()
    assert categories.get("Buildings", 0) > 100
    assert "C:" not in categories, "absolute paths leaked into the categories"


# --- validation ---


def test_a_clean_map_validates() -> None:
    library = build_library(_catalog(house3="data\\models\\buildings\\house3.gam"))
    scene = MapScene(objects=[_node("Object1", "house3")])
    assert validate_map(scene, library=library).is_clean


def test_unknown_model_is_an_error_with_suggestions() -> None:
    library = build_library(
        _catalog(
            house3="data\\models\\buildings\\house3.gam",
            house1="data\\models\\buildings\\house1.gam",
        )
    )
    scene = MapScene(objects=[_node("Object1", "house")])
    report = validate_map(scene, library=library)
    issue = report.errors[0]
    assert issue.code == "unknown_model"
    assert "house3" in issue.message or "house1" in issue.message


def test_model_ids_are_not_checked_without_a_library() -> None:
    """A user may legitimately work without the game installed, and a
    guess would be worse than no check."""
    scene = MapScene(objects=[_node("Object1", "anything_at_all")])
    assert validate_map(scene, library=None).is_clean


def test_missing_model_id_is_an_error() -> None:
    scene = MapScene(objects=[_node("Object1", None)])
    report = validate_map(scene)
    assert report.errors[0].code == "missing_model_id"


def test_zero_scale_is_an_error_and_negative_is_a_warning() -> None:
    scene = MapScene(objects=[
        _node("Zero", "x", scale=Vector3(1, 0, 1)),
        _node("Negative", "x", scale=Vector3(1, -1, 1)),
    ])
    report = validate_map(scene)
    assert any(i.code == "zero_scale" and i.severity == Severity.ERROR for i in report.issues)
    assert any(
        i.code == "negative_scale" and i.severity == Severity.WARNING
        for i in report.issues
    )


def test_placement_outside_the_playable_area_is_a_warning() -> None:
    """A warning, not an error: the map still loads, and a deliberately
    distant object is a legitimate thing to build."""
    scene = MapScene(objects=[_node("Far", "x", org=Vector3(99999, 0, 99999))])
    report = validate_map(scene, bounds=(40, 40, 4056, 4056))
    issue = report.warnings[0]
    assert issue.code == "outside_playable_area"
    assert "99999" in issue.message


def test_nested_objects_are_not_bounds_checked() -> None:
    """A nested position is relative to its parent, so comparing it to
    world bounds would flag every child of a distant parent."""
    # The parent sits inside the bounds; the child's (5, 5) is an
    # offset from it, not a world position, and comparing it directly
    # would flag it as outside.
    parent = _node("Parent", None, node_class="SgNode", org=Vector3(1500, 0, 1500))
    child = _node("Child", "x", org=Vector3(5, 0, 5))
    child.org_rel = False
    parent.children.append(child)

    report = validate_map(MapScene(objects=[parent]), bounds=(1000, 1000, 2000, 2000))
    flagged = [i.node_name for i in report.issues if i.code == "outside_playable_area"]
    assert flagged == [], f"nested child was bounds-checked: {flagged}"


def test_a_model_on_a_group_node_is_flagged() -> None:
    scene = MapScene(objects=[_node("Group", "house3", node_class="SgNode")])
    report = validate_map(scene, library=build_library(_catalog()))
    assert any(i.code == "model_on_group_node" for i in report.issues)


def test_report_groups_issues_by_code() -> None:
    scene = MapScene(objects=[_node(f"N{i}", None) for i in range(5)])
    report = validate_map(scene)
    assert report.by_code()["missing_model_id"] == 5
    lines = report.summary_lines(limit_per_code=2)
    assert any("and 3 more" in line for line in lines)


def test_safe_bounds_come_from_the_manifest() -> None:
    """The validator has always accepted a playable area but never
    received one: it looked for a `safe_bounds` attribute the manifest
    did not have, so bounds checking silently never ran."""
    from core.manifest import LevelManifest

    manifest = LevelManifest(
        raw_keys={},
        min_safe_x=40.0,
        min_safe_y=40.0,
        max_safe_x=4056.0,
        max_safe_y=4056.0,
    )
    assert manifest.safe_bounds == (40.0, 40.0, 4056.0, 4056.0)


def test_a_partial_safe_area_is_treated_as_none() -> None:
    """Reporting objects outside a boundary the map never fully
    declared would be worse than not checking."""
    from core.manifest import LevelManifest

    assert LevelManifest(raw_keys={}, min_safe_x=40.0).safe_bounds is None


_ALL_TESTS = (
    test_categories_come_from_the_directory_structure,
    test_region_folders_are_not_used_as_subcategories,
    test_absolute_paths_get_their_own_category,
    test_unknown_top_level_folders_become_their_own_category,
    test_a_pathless_entry_is_uncategorised,
    test_display_names_are_readable,
    test_builds_a_library_from_a_catalogue,
    test_usage_counts_come_from_the_map,
    test_search_orders_by_usage_not_alphabetically,
    test_search_matches_id_name_and_category,
    test_empty_search_returns_everything,
    test_category_listing,
    test_geometry_is_absent_until_measured,
    test_measuring_a_missing_file_fails_without_raising,
    test_measuring_a_real_model_records_its_geometry,
    test_library_from_the_real_catalogue,
    test_safe_bounds_come_from_the_manifest,
    test_a_partial_safe_area_is_treated_as_none,
    test_a_clean_map_validates,
    test_unknown_model_is_an_error_with_suggestions,
    test_model_ids_are_not_checked_without_a_library,
    test_missing_model_id_is_an_error,
    test_zero_scale_is_an_error_and_negative_is_a_warning,
    test_placement_outside_the_playable_area_is_a_warning,
    test_nested_objects_are_not_bounds_checked,
    test_a_model_on_a_group_node_is_flagged,
    test_report_groups_issues_by_code,
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
