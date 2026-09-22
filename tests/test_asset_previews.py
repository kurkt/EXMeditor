# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the model palette the Asset Browser draws.

A list of names is not a way to choose a model: picking anything from
it meant knowing what ``heavy_dot3`` looks like before you looked. What
these guard is that the palette is BUILDABLE and ORDERED — a capped run
has to produce the models the open map actually uses, not the
alphabetically early ones.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

import bpy  # noqa: E402

from blender_io.asset_library import (  # noqa: E402
    ASSET_COLLECTION,
    build_asset_previews,
    clear_asset_previews,
)
from core.asset_previews import (  # noqa: E402
    plan_previews,
    preview_tags,
    unresolved,
)
from core.assets import AssetInfo, AssetLibrary  # noqa: E402


def _library() -> AssetLibrary:
    library = AssetLibrary()
    for asset_id, category, usage, resolved in (
        ("aaa_unused", "buildings", 0, True),
        ("heavy_dot3", "buildings", 7, True),
        ("tube3", "misc", 5, True),
        ("zzz_popular", "buildings", 40, True),
        ("gone", "buildings", 99, False),      # catalogued, not on disk
    ):
        asset = AssetInfo(
            asset_id=asset_id,
            file_path=f"data\\models\\{category}\\{asset_id}.gam",
            category=category,
            display_name=asset_id.replace("_", " "),
            usage_count=usage,
        )
        if resolved:
            asset.resolved_path = f"/game/{asset_id}.gam"
        library.add(asset)
    return library


class _Provider:
    """Stands in for MeshProvider: a mesh for everything but 'broken'."""

    def __init__(self, broken=()):
        self.broken = set(broken)
        self.asked = []

    def get_mesh(self, model_id):
        self.asked.append(model_id)
        if model_id in self.broken:
            return None
        return bpy.data.meshes.new(f"mesh_{model_id}")


def _reset() -> None:
    for name in list(bpy.data.collections.keys()):
        del bpy.data.collections[name]
    for obj in list(bpy.data.objects):
        try:
            bpy.data.objects.remove(obj)
        except Exception:
            pass


# --- choosing what to build ---------------------------------------------


def test_the_most_used_models_are_built_first() -> None:
    """A cap has to bite on the models nobody places, not the ones the
    map is made of."""
    chosen = plan_previews(_library(), limit=2)

    assert [a.asset_id for a in chosen] == ["zzz_popular", "heavy_dot3"]


def test_a_model_with_no_file_is_never_offered() -> None:
    """A thumbnail cannot be made from a file that is not there, and
    carrying it into the build only to fail turns one clear message
    into a hundred."""
    chosen = plan_previews(_library(), limit=0)

    assert "gone" not in [a.asset_id for a in chosen]
    assert [a.asset_id for a in unresolved(_library())] == ["gone"]


def test_a_category_narrows_the_build() -> None:
    chosen = plan_previews(_library(), category="misc", limit=0)
    assert [a.asset_id for a in chosen] == ["tube3"]


def test_an_explicit_id_list_narrows_it_too() -> None:
    """The scope that matters: what the open map already places."""
    chosen = plan_previews(
        _library(), asset_ids={"heavy_dot3", "tube3", "gone"}, limit=0,
    )
    # 'gone' drops out for having no file, whatever the map asks for.
    assert [a.asset_id for a in chosen] == ["heavy_dot3", "tube3"]


def test_a_limit_of_zero_builds_everything_resolvable() -> None:
    assert len(plan_previews(_library(), limit=0)) == 4


def test_the_tags_are_what_the_browser_filters_by() -> None:
    library = _library()
    assert preview_tags(library.get("heavy_dot3")) == ["buildings", "used"]
    assert preview_tags(library.get("aaa_unused")) == ["buildings"]


# --- building it ---------------------------------------------------------


def test_every_built_model_is_marked_as_an_asset() -> None:
    """Marking is what makes it appear in the browser at all.

    ``asset_data`` being present is the whole signal — an object that
    was linked but not marked is invisible there, which looks exactly
    like a model that failed to import.
    """
    _reset()
    chosen = plan_previews(_library(), limit=0)
    result = build_asset_previews(chosen, _Provider())

    assert result.built == 4
    assert result.failed == []

    collection = bpy.data.collections.get(ASSET_COLLECTION)
    assert collection is not None
    assert len(collection.objects) == 4
    for obj in collection.objects:
        assert obj.asset_data is not None
        assert obj.preview_generated is True


def test_each_asset_keeps_the_id_the_map_format_writes() -> None:
    """So one dragged into the scene is already the right model, and
    Assign ExMachina Node has nothing left to ask."""
    _reset()
    build_asset_previews(plan_previews(_library(), limit=0), _Provider())

    ids = {
        obj.get("exm_asset_id")
        for obj in bpy.data.collections[ASSET_COLLECTION].objects
    }
    assert ids == {"aaa_unused", "heavy_dot3", "tube3", "zzz_popular"}


def test_the_description_names_the_id_not_the_pretty_name() -> None:
    """The display name is prettied up for reading; world.xml writes
    the id, and a browser that only shows the pretty one cannot be
    used to find the model a map names."""
    _reset()
    build_asset_previews(plan_previews(_library(), limit=1), _Provider())

    obj = list(bpy.data.collections[ASSET_COLLECTION].objects)[0]
    assert "zzz_popular" in obj.asset_data.description


def test_a_model_that_will_not_read_is_counted_not_fatal() -> None:
    _reset()
    provider = _Provider(broken={"heavy_dot3"})
    result = build_asset_previews(plan_previews(_library(), limit=0), provider)

    assert result.failed == ["heavy_dot3"]
    assert result.built == 3


def test_building_twice_does_not_build_twice() -> None:
    """Widening the selection should cost only the new models."""
    _reset()
    chosen = plan_previews(_library(), limit=0)
    build_asset_previews(chosen, _Provider())

    provider = _Provider()
    again = build_asset_previews(chosen, provider)

    assert again.built == 0
    assert again.reused == 4
    assert provider.asked == []          # no .gam re-read
    assert len(bpy.data.collections[ASSET_COLLECTION].objects) == 4


def test_the_palette_can_be_cleared() -> None:
    """It is derived from the game folder and must not outlive one."""
    _reset()
    build_asset_previews(plan_previews(_library(), limit=0), _Provider())

    assert clear_asset_previews() == 4
    assert len(bpy.data.collections[ASSET_COLLECTION].objects) == 0


def test_thumbnails_can_be_skipped() -> None:
    """Marking without rendering is much faster and much less useful,
    so it is a choice rather than the default."""
    _reset()
    result = build_asset_previews(
        plan_previews(_library(), limit=0), _Provider(), generate_previews=False,
    )

    assert result.built == 4
    assert result.previews == 0
    for obj in bpy.data.collections[ASSET_COLLECTION].objects:
        assert obj.asset_data is not None
        assert obj.preview_generated is False


# --- the palette has to be visible while it is being filled -------------


def test_previews_are_rendered_before_the_palette_is_hidden() -> None:
    """The crash. Excluding a layer collection drops its objects from
    the depsgraph, so there is no evaluated object left to render — and
    the first version excluded the palette on creation, then rendered a
    preview per model into that hole.

    Order is the fix: link, mark, render, and only then hide.
    """
    _reset()
    result = build_asset_previews(plan_previews(_library(), limit=0), _Provider())

    assert result.built == 4
    assert result.previews == 4, "a preview was skipped — was the palette hidden first?"
    assert result.failed == []


def test_the_palette_is_hidden_but_still_evaluated() -> None:
    """It is a palette, not part of the map, so it has to go out of
    sight — but with the EYE, not by excluding it.

    A preview is asynchronous: asset_generate_preview() queues a job
    and returns. Excluding the collection when the loop ended pulled
    the objects out from under jobs that had not run yet, and Blender
    died in wm_jobs_timer walking its notifier queue long after the
    operator had returned."""
    _reset()
    build_asset_previews(plan_previews(_library(), limit=0), _Provider())

    layer = next(
        c for c in bpy.context.view_layer.layer_collection.children
        if c.name == ASSET_COLLECTION
    )
    assert layer.hide_viewport is True
    assert layer.exclude is False


def test_a_hidden_palette_can_still_render_a_preview() -> None:
    """The property the crash turned on. Pinning the double too, since
    the first version of this bug lived in what it did not model: a
    fake that rendered previews regardless would have passed against
    the broken code."""
    _reset()
    build_asset_previews(plan_previews(_library(), limit=1), _Provider())

    obj = list(bpy.data.collections[ASSET_COLLECTION].objects)[0]
    # Hidden by now — and a late job must still be able to render it.
    obj.asset_generate_preview()


def test_an_excluded_object_still_cannot_be_previewed() -> None:
    """The fake must keep modelling the difference, or the test above
    proves nothing."""
    from blender_io.asset_library import _set_excluded

    _reset()
    build_asset_previews(plan_previews(_library(), limit=1), _Provider())
    _set_excluded(ASSET_COLLECTION, True)

    obj = list(bpy.data.collections[ASSET_COLLECTION].objects)[0]
    try:
        obj.asset_generate_preview()
    except RuntimeError:
        return
    raise AssertionError("a preview was rendered for an excluded object")


# --- the tree the browser groups by -------------------------------------


def test_the_tree_is_ex_machina_then_map_then_category() -> None:
    from core.asset_catalogs import ROOT, catalog_path

    assert catalog_path("r1m1", "buildings") == f"{ROOT}/r1m1/buildings"
    # A subcategory that says something the category does not.
    assert catalog_path("r1m1", "nature", "region1") == f"{ROOT}/r1m1/nature/region1"
    # And one that only repeats it does not earn a level.
    assert catalog_path("r1m1", "misc", "misc") == f"{ROOT}/r1m1/misc"


def test_a_catalogue_keeps_its_id_across_rebuilds() -> None:
    """An asset stores the id, not the path. An id drawn fresh each
    run would orphan everything filed under it last run."""
    from core.asset_catalogs import catalog_uuid

    first = catalog_uuid("Ex Machina/r1m1/buildings")
    assert catalog_uuid("Ex Machina/r1m1/buildings") == first
    assert catalog_uuid("Ex Machina/r1m1/nature") != first


def test_names_that_would_break_the_file_are_cleaned() -> None:
    """Blender splits the line on ":" and the path on "/", so neither
    can survive inside a name."""
    from core.asset_catalogs import catalog_path

    path = catalog_path("C:/maps/r1m1", "a/b")

    # Two separators: root, map, category. The slashes and the colon
    # inside the names given have to be gone, or they would add levels
    # and split the line.
    assert path.count("/") == 2
    assert ":" not in path
    assert path.startswith("Ex Machina/")
    # A drive letter brings a colon AND a slash, so it leaves two
    # dashes behind. Ugly, safe, and not worth collapsing: what matters
    # is that nothing in a name can add a level or split a line.
    assert path == "Ex Machina/C--maps-r1m1/a-b"


def test_every_level_of_the_tree_is_named_in_the_file() -> None:
    """Blender does not invent the parents: a file naming only the leaf
    shows one flat entry, not a tree."""
    from core.asset_catalogs import merge, parse

    text = merge("", ["Ex Machina/r1m1/buildings"])
    paths = set(parse(text))

    assert paths == {
        "Ex Machina",
        "Ex Machina/r1m1",
        "Ex Machina/r1m1/buildings",
    }
    assert text.splitlines()[-4:][0].startswith("VERSION 1") or "VERSION 1" in text


def test_merging_keeps_catalogues_that_were_already_there() -> None:
    """The file is shared with the user's own catalogues, and a rebuild
    that rewrote it would delete them."""
    from core.asset_catalogs import merge, parse

    mine = "VERSION 1\n\nabc-123:My Stuff/Rocks:My Stuff-Rocks\n"
    text = merge(mine, ["Ex Machina/r1m1/misc"])
    catalogs = parse(text)

    assert catalogs["My Stuff/Rocks"] == ("abc-123", "My Stuff-Rocks")
    assert "Ex Machina/r1m1/misc" in catalogs


def test_an_id_already_in_the_file_is_not_replaced() -> None:
    """Assets may already reference it."""
    from core.asset_catalogs import catalog_uuid, merge, parse

    path = "Ex Machina/r1m1/buildings"
    text = merge(f"VERSION 1\n\nkeep-me:{path}:whatever\n", [path])

    assert parse(text)[path][0] == "keep-me"
    assert parse(text)[path][0] != catalog_uuid(path)


def test_an_unsaved_file_gets_tags_and_says_why_it_has_no_tree() -> None:
    """Blender keeps catalogues beside the .blend. There is no beside
    for a file that has never been saved, and silence there looks like
    a broken build."""
    _reset()
    bpy.data.filepath = ""
    result = build_asset_previews(
        plan_previews(_library(), limit=0), _Provider(), map_name="r1m1",
    )

    assert result.built == 4
    assert result.catalog_file is None
    for obj in bpy.data.collections[ASSET_COLLECTION].objects:
        assert obj.asset_data.tags            # tags work either way


def test_a_saved_file_gets_the_tree_written_beside_it() -> None:
    import tempfile

    from core.asset_catalogs import catalog_path, parse

    _reset()
    folder = tempfile.mkdtemp()
    bpy.data.filepath = os.path.join(folder, "map.blend")
    try:
        result = build_asset_previews(
            plan_previews(_library(), limit=0), _Provider(), map_name="r1m1",
        )
        assert result.catalog_file == os.path.join(folder, "blender_assets.cats.txt")

        with open(result.catalog_file, encoding="utf-8") as handle:
            catalogs = parse(handle.read())
        assert catalog_path("r1m1", "buildings") in catalogs
        assert catalog_path("r1m1", "misc") in catalogs

        # And every asset points into it.
        for obj in bpy.data.collections[ASSET_COLLECTION].objects:
            assert obj.asset_data.catalog_id
    finally:
        bpy.data.filepath = ""
