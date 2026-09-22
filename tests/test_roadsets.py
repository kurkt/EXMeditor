# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for roads.xml and the road surfaces built from it."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

import bpy  # noqa: E402

from formats.exm.roadsets import (  # noqa: E402
    END_CAP,
    STRAIGHT,
    T_JUNCTION,
    X_JUNCTION,
    read_road_sets,
)
from corpus import CORPUS, corpus  # noqa: E402

ROADS_XML = os.path.join(CORPUS, "roads.xml")


def test_a_road_is_built_from_models_not_generated() -> None:
    """The finding that reframed this whole feature.

    ``roads.xml`` names a ``.gam`` per shape — a straight run, a T, an
    X and an end cap — and the engine lays them along the chain. So a
    road's width and surface are not something to invent: they are in
    the model.
    """
    if not os.path.isfile(ROADS_XML):
        return

    sets = read_road_sets(ROADS_XML)
    assert "road_country" in sets

    country = sets["road_country"]
    assert country.straight().lower().endswith("road_country.gam")
    for shape in (T_JUNCTION, X_JUNCTION, END_CAP):
        assert shape in country.models
    assert country.piece(STRAIGHT) == country.straight()


def test_an_untyped_item_is_a_skin_variant() -> None:
    """``skinNumber`` picks between them.

    ``rock_cliff`` names rock_clif_1 through rock_clif_4 with no type,
    and the sample map's skinNumber runs 0..3. Keeping only the last —
    which storing them by type does — collapses four different pieces
    of cliff into one.
    """
    if not os.path.isfile(ROADS_XML):
        return

    cliff = read_road_sets(ROADS_XML).get("rock_cliff")
    assert cliff is not None
    assert len(cliff.straights) == 4

    for skin in range(4):
        assert cliff.straight(skin).lower().endswith(f"rock_clif_{skin + 1}.gam")
    # Out of range wraps rather than failing: the file is the authority
    # on how many variants there are, not the node.
    assert cliff.straight(4) == cliff.straight(0)


def test_the_master_file_carries_every_set() -> None:
    """41 of them, cliffs included — they are built the same way."""
    if not os.path.isfile(ROADS_XML):
        return

    sets = read_road_sets(ROADS_XML)
    assert len(sets) > 30
    assert {"rock_cliff", "sand_cliff", "road_country", "r3_cross"} <= set(sets)


def test_every_set_the_sample_map_uses_is_present() -> None:
    """275 nodes on road_country, 212 on r3_country_dry, and two more."""
    level = os.path.join(CORPUS, "levelroads.xml")
    if not (os.path.isfile(ROADS_XML) and os.path.isfile(level)):
        return

    import re

    text = open(level, "rb").read().decode("cp1251", errors="replace")
    used = set(re.findall(r'roadset="([^"]+)"', text))
    sets = read_road_sets(ROADS_XML)

    assert used <= set(sets), used - set(sets)


def test_a_malformed_file_loses_nothing_that_parses() -> None:
    """Shipped XML carries Windows-1251 comments mid-element."""
    import tempfile

    path = os.path.join(tempfile.mkdtemp(), "roads.xml")
    open(path, "wb").write(
        '<?xml version="1.0" encoding="windows-1251"?>\n'
        '<Roads>\n'
        '  <Set name="good" scale="2.000 1.000 1.000">\n'
        '    <Item model="a\\b.gam"/>\n'
        '    <Item model="a\\c.gam" type="2"/> <!-- \xef\xe5\xf0\xe5\xea -->\n'
        "  </Set>\n"
        "</Roads>\n".encode("cp1251", errors="replace")
    )

    sets = read_road_sets(path)
    assert set(sets) == {"good"}
    assert sets["good"].scale == (2.0, 1.0, 1.0)
    assert sets["good"].models[X_JUNCTION].endswith("c.gam")
    assert sets["good"].straight().endswith("b.gam")


def test_a_missing_file_yields_no_sets_rather_than_raising() -> None:
    assert read_road_sets("/nowhere/roads.xml") == {}


def test_the_width_comes_from_the_model_that_makes_the_road() -> None:
    """Its narrower horizontal span is the width; the longer one is
    how far one piece runs."""
    from blender_io.roads_bridge import road_profile_from_model

    model = os.path.join(CORPUS, "factory_box.gam")
    if not os.path.isfile(model):
        return

    measured = road_profile_from_model(model)
    assert measured is not None
    half_width, textures = measured
    assert half_width > 0.0
    assert all(name.lower().endswith(".dds") for name in textures if name)

    assert road_profile_from_model("/nowhere/none.gam") is None


def test_skin_number_indexes_the_materials_inside_the_road_model() -> None:
    """Measured on ``road_country.gam``: one mesh, five materials.

        skin 0 -> country_1.dds
        skin 1 -> country_2.dds
        skin 2 -> country_3.dds
        skin 3 -> country_1.dds

    and skinNumber in the sample map runs 0..3. So it picks the
    material inside the model — the surface varies along a road — and
    is not a choice between different models.
    """
    from blender_io.roads_bridge import road_profile_from_model

    model = os.path.join(CORPUS, "road_country.gam")
    if not os.path.isfile(model):
        return

    half_width, textures = road_profile_from_model(model)

    # X is the width and Z the length, not "the narrower one is the
    # width". The X span is 19.77 on every piece of the set — it is the
    # road grid's step — while Z varies with the piece.
    assert abs(half_width * 2 - 19.77) < 0.1
    assert len(textures) == 5
    assert textures[0].lower() == "country_1.dds"
    assert textures[1].lower() == "country_2.dds"
    assert textures[2].lower() == "country_3.dds"


def test_a_road_surface_is_alpha_blended() -> None:
    """road.fx: "simple diffuse shader for roads (alpha-blended)".

    That is why a road in the editor melts into the ground at its edges
    rather than sitting on it as a slab.
    """
    from blender_io.roads_bridge import ROADSET_PROP, build_road_surfaces

    collection = bpy.data.collections.new("Roads")
    curve = bpy.data.curves.new("chain", type="CURVE")
    spline = curve.splines.new("POLY")
    spline.points.add(1)
    for index, point in enumerate(spline.points):
        point.co = (index * 30.0, 0.0, 0.0, 1.0)

    obj = bpy.data.objects.new("ExM_Road_0", curve)
    obj[ROADSET_PROP] = "road_country"

    assert build_road_surfaces([obj], None, collection=collection) == 1
    surface = next(o for o in collection.objects if o.name.endswith("_surface"))
    assert surface.data.materials[0].blend_method == "BLEND"


# --- placing the road's own models --------------------------------------


def _game_tree_with(model_name: str, under: str):
    """A stand-in game folder holding one model where a set names it."""
    import shutil
    import tempfile

    source = os.path.join(CORPUS, "factory_box.gam")
    if not os.path.isfile(source):
        return None

    root = tempfile.mkdtemp()
    folder = os.path.join(root, *under.split("/"))
    os.makedirs(folder)
    shutil.copy(source, os.path.join(folder, model_name))
    return root


def _chain_with_meta(points, roadset: str):
    import json

    from blender_io.roads_bridge import NODE_META_PROP

    curve = bpy.data.curves.new("chain", type="CURVE")
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, (x, y, z) in zip(spline.points, points):
        point.co = (x, y, z, 1.0)

    obj = bpy.data.objects.new("ExM_Road_0", curve)
    obj[NODE_META_PROP] = json.dumps(
        [{"roadset": roadset, "skin_number": 0, "model_num": 0}] * len(points)
    )
    return obj


def test_a_segment_is_placed_for_every_node() -> None:
    """The engine lays a model per node; so does this.

    A generated ribbon can only approximate a road — it guesses the
    width, guesses how the texture sits, and joins at junctions in a
    way nothing in the data describes.
    """
    from blender_io.roads_bridge import ROAD_SEGMENT_PROP, place_road_models

    root = _game_tree_with("road_country.gam", "data/models/roads/road_country")
    if root is None or not os.path.isfile(ROADS_XML):
        return

    collection = bpy.data.collections.new("Roads")
    obj = _chain_with_meta([(0, 0, 0), (20, 0, 0), (40, 0, 0)], "road_country")

    placed = place_road_models(
        [obj], root, collection=collection, road_sets=read_road_sets(ROADS_XML)
    )
    assert placed == 3
    assert all(
        o[ROAD_SEGMENT_PROP] == obj.name
        for o in collection.objects
        if o.name.startswith("ExM_Road_0_seg")
    )


def test_a_segment_faces_the_next_node() -> None:
    """Turned by the model's own long axis, not by an assumed one."""
    import math

    from blender_io.roads_bridge import place_road_models

    root = _game_tree_with("road_country.gam", "data/models/roads/road_country")
    if root is None or not os.path.isfile(ROADS_XML):
        return

    collection = bpy.data.collections.new("Roads")
    obj = _chain_with_meta([(0, 0, 0), (20, 0, 0), (20, 20, 0)], "road_country")
    place_road_models(
        [obj], root, collection=collection, road_sets=read_road_sets(ROADS_XML)
    )

    segments = {
        o.name: o for o in collection.objects if o.name.startswith("ExM_Road_0_seg")
    }
    assert tuple(segments["ExM_Road_0_seg0"].location) == (0, 0, 0)
    # The second node turns north, so the piece turns with it.
    assert math.isclose(
        segments["ExM_Road_0_seg1"].rotation_euler[2], math.pi / 2, abs_tol=1e-6
    )


def test_a_set_that_cannot_be_resolved_places_nothing() -> None:
    """Reported rather than guessed at: the ribbon stands in instead."""
    from blender_io.roads_bridge import place_road_models

    collection = bpy.data.collections.new("Roads")
    obj = _chain_with_meta([(0, 0, 0), (20, 0, 0)], "no_such_set")

    assert place_road_models([obj], "/nowhere", collection=collection) == 0
    assert not list(collection.objects)


def test_the_chain_survives_placement() -> None:
    """The export reads the curve, so placement may not touch it."""
    from blender_io.roads_bridge import place_road_models

    root = _game_tree_with("road_country.gam", "data/models/roads/road_country")
    if root is None or not os.path.isfile(ROADS_XML):
        return

    collection = bpy.data.collections.new("Roads")
    obj = _chain_with_meta([(0, 0, 0), (20, 0, 0), (40, 0, 0)], "road_country")
    before = [tuple(p.co) for p in obj.data.splines[0].points]

    place_road_models(
        [obj], root, collection=collection, road_sets=read_road_sets(ROADS_XML)
    )
    assert [tuple(p.co) for p in obj.data.splines[0].points] == before


def test_a_partial_placement_falls_back_rather_than_leaving_gaps() -> None:
    """Skipping the surface on a partial result is worse than either.

    A map came back with a handful of segments placed and the rest of
    its roads gone altogether, which is what "roads are broken" was.
    """
    from blender_io.scene_bridge import _expected_segments

    curve = bpy.data.curves.new("chain", type="CURVE")
    spline = curve.splines.new("POLY")
    spline.points.add(4)
    obj = bpy.data.objects.new("ExM_Road_0", curve)

    assert _expected_segments([obj]) == 5
    assert _expected_segments([]) == 0

    empty = bpy.data.objects.new("no curve", bpy.data.meshes.new("m"))
    assert _expected_segments([empty]) == 0


def test_placing_models_is_off_by_default() -> None:
    """It is what the engine does and is not yet established enough."""
    from addon.preferences import road_models

    class _Context:
        preferences = None

    assert road_models(_Context()) is False


# --- roads that follow their chain --------------------------------------


def test_a_road_is_its_own_model_arrayed_along_the_chain() -> None:
    """Blender arrays a mesh along a curve and bends it to follow.

    The result stays live: moving a chain point reshapes the road,
    which a mesh baked at import cannot do. It also settles the
    texture — a ribbon has to invent UVs, and stretching the whole of
    ``country_1.dds`` across the carriageway put the texture's dark
    border down the middle and its lane markings across it.
    """
    import shutil
    import tempfile

    from blender_io.roads_bridge import (
        ARRAY_MODIFIER,
        CURVE_MODIFIER,
        ROADSET_PROP,
        build_roads_from_models,
    )

    model = os.path.join(CORPUS, "road_country.gam")
    if not (os.path.isfile(model) and os.path.isfile(ROADS_XML)):
        return

    root = tempfile.mkdtemp()
    folder = os.path.join(root, "data", "models", "roads", "road_country")
    os.makedirs(folder)
    shutil.copy(model, os.path.join(folder, "road_country.gam"))

    collection = bpy.data.collections.new("Roads")
    curve = bpy.data.curves.new("chain", type="CURVE")
    spline = curve.splines.new("POLY")
    spline.points.add(3)
    for index, point in enumerate(spline.points):
        point.co = (index * 20.0, 0.0, 0.0, 1.0)

    obj = bpy.data.objects.new("ExM_Road_0", curve)
    obj[ROADSET_PROP] = "road_country"

    from core.coordinates import CoordinateTransform

    assert build_roads_from_models(
        [obj],
        root,
        collection=collection,
        road_sets=read_road_sets(ROADS_XML),
        transform=CoordinateTransform(),
    ) == 1

    road = next(o for o in collection.objects if o.name.endswith("_road"))
    # The model's own geometry and its own five materials.
    assert len(road.data.vertices) == 21
    assert len(road.data.materials) == 5

    # And it lies flat. Built without the coordinate transform it
    # stands on edge, 14.4 units tall, and the array offsets copies
    # straight up — one omission behind wrong orientation, wrong
    # position and far too many copies at once.
    xs = [v.co[0] for v in road.data.vertices]
    ys = [v.co[1] for v in road.data.vertices]
    zs = [v.co[2] for v in road.data.vertices]
    assert abs((max(xs) - min(xs)) - 19.770) < 0.01
    assert abs((max(ys) - min(ys)) - 14.447) < 0.01
    assert (max(zs) - min(zs)) < 1.0

    names = {m.name: m for m in road.modifiers}
    assert set(names) == {ARRAY_MODIFIER, CURVE_MODIFIER}

    array = names[ARRAY_MODIFIER]
    assert array.type == "ARRAY"
    assert array.fit_type == "FIT_CURVE"
    assert array.curve is obj
    # Offset along the axis the piece runs on, none across it.
    assert list(array.relative_offset_displace) == [0.0, 1.0, 0.0]

    deform = names[CURVE_MODIFIER]
    assert deform.type == "CURVE"
    assert deform.object is obj
    assert deform.deform_axis == "POS_Y"


def test_the_array_runs_before_the_curve() -> None:
    """Reversed, the copies are made from geometry already bent and the
    road doubles back on itself."""
    import shutil
    import tempfile

    from blender_io.roads_bridge import ROADSET_PROP, build_roads_from_models

    model = os.path.join(CORPUS, "road_country.gam")
    if not (os.path.isfile(model) and os.path.isfile(ROADS_XML)):
        return

    root = tempfile.mkdtemp()
    folder = os.path.join(root, "data", "models", "roads", "road_country")
    os.makedirs(folder)
    shutil.copy(model, os.path.join(folder, "road_country.gam"))

    collection = bpy.data.collections.new("Roads")
    curve = bpy.data.curves.new("chain", type="CURVE")
    curve.splines.new("POLY").points.add(2)
    obj = bpy.data.objects.new("ExM_Road_0", curve)
    obj[ROADSET_PROP] = "road_country"

    build_roads_from_models(
        [obj], root, collection=collection, road_sets=read_road_sets(ROADS_XML)
    )
    road = next(o for o in collection.objects if o.name.endswith("_road"))

    assert [m.type for m in road.modifiers] == ["ARRAY", "CURVE"]


def test_the_chain_is_not_touched() -> None:
    from blender_io.roads_bridge import ROADSET_PROP, build_roads_from_models

    collection = bpy.data.collections.new("Roads")
    curve = bpy.data.curves.new("chain", type="CURVE")
    spline = curve.splines.new("POLY")
    spline.points.add(2)
    before = [tuple(p.co) for p in spline.points]

    obj = bpy.data.objects.new("ExM_Road_0", curve)
    obj[ROADSET_PROP] = "nothing"

    assert build_roads_from_models([obj], None, collection=collection) == 0
    assert [tuple(p.co) for p in curve.splines[0].points] == before


def test_a_road_piece_is_flat_after_the_transform() -> None:
    """The engine's Y is up; Blender's is Z.

    ``road_country.gam`` is 19.770 x 0.188 x 14.447 in its own space
    and has to become 19.770 x 14.447 x 0.188 in Blender's. Skipping
    the swap leaves a slab standing on edge, and the debug overlay of
    the original editor puts the whole visible road at 1352 triangles —
    56 pieces, not the hundreds an upward array produces.
    """
    from blender_io.roads_bridge import SEGMENT_LENGTH_AXIS
    from core.coordinates import CoordinateTransform
    from formats.exm.gam import read_model
    from utils.math import Vector3

    model_path = os.path.join(CORPUS, "road_country.gam")
    if not os.path.isfile(model_path):
        return

    model = read_model(model_path)
    points = [(v.x, v.y, v.z) for m in model.meshes for v in m.positions]

    def span(values, axis):
        return max(v[axis] for v in values) - min(v[axis] for v in values)

    assert abs(span(points, 1) - 0.188) < 0.01, "thin axis is Y in model space"

    transform = CoordinateTransform()
    moved = [
        transform.game_to_blender_position(Vector3(*p)) for p in points
    ]
    moved = [(v.x, v.y, v.z) for v in moved]

    assert abs(span(moved, 2) - 0.188) < 0.01, "thin axis is Z in Blender"
    assert abs(span(moved, 1) - 14.447) < 0.01
    assert SEGMENT_LENGTH_AXIS == "Y"


# --- which piece a node is drawn with -----------------------------------


def test_the_piece_comes_from_the_node_degree() -> None:
    """MEASURED on r1m2: 371 ends, 3599 straights, 9 tees, 6 crossings,
    and 371 + 3599 + 9 + 6 is exactly the 3985 nodes in the file."""
    from formats.exm.roadsets import (
        END_CAP, STRAIGHT, T_JUNCTION, X_JUNCTION, piece_for_degree,
    )

    assert piece_for_degree(1) == END_CAP
    assert piece_for_degree(2) == STRAIGHT
    assert piece_for_degree(3) == T_JUNCTION
    assert piece_for_degree(4) == X_JUNCTION
    # An unfamiliar node still gets a road rather than nothing.
    assert piece_for_degree(0) == STRAIGHT
    assert piece_for_degree(7) == STRAIGHT


def test_the_degree_is_the_count_of_links_a_node_names() -> None:
    from formats.exm.roadsets import node_degree

    assert node_degree({"FwdZLink": "a", "BackZLink": "b"}) == 2
    assert node_degree({"BackZLink": "b"}) == 1
    # The two combinations the map actually contains for junctions.
    assert node_degree(
        {"FwdZLink": "a", "FwdXLink": "c", "BackXLink": "d"}
    ) == 3
    assert node_degree(
        {"FwdZLink": "a", "BackZLink": "b", "FwdXLink": "c", "BackXLink": "d"}
    ) == 4
    # An empty attribute is not a link.
    assert node_degree({"FwdZLink": "", "BackZLink": "   "}) == 0


def test_model_num_is_not_the_piece_type() -> None:
    """It reads 0 on all 3985 nodes measured.

    A reader that trusts it draws every junction and every dead end as
    a straight run — which looked like working roads because 90% of a
    map is straight.
    """
    from formats.exm.roadsets import RoadSet, STRAIGHT, T_JUNCTION

    road = RoadSet(name="r2_cross")
    road.straights.append("straight.gam")
    road.models[T_JUNCTION] = "crossT.gam"

    # What the data says, taken at face value: always the straight.
    assert road.piece(0) == "straight.gam"
    # What the links say for the same node.
    assert road.piece_for_node(
        {"FwdZLink": "a", "FwdXLink": "c", "BackXLink": "d"}
    ) == "crossT.gam"
