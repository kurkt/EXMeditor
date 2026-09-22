# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `formats/exm/roads.py` and `blender_io/roads_bridge.py`.

The road format stores a flat node list linked by name
(`FwdZLink`/`BackZLink`); this SDK reconstructs those into ordered
chains, one per Blender curve. The tests below pin down that
reconstruction, the link regeneration on write, and the curve round
trip.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.roads_bridge import (  # noqa: E402
    ROAD_CHAIN_PROP,
    build_road_curves,
    extract_road_network,
)
from core.coordinates import CoordinateTransform  # noqa: E402
from core.roads import RoadChain, RoadNetwork, RoadNode  # noqa: E402
from core.terrain import HeightmapData  # noqa: E402
from formats.exm.roads import read_roads, write_roads  # noqa: E402
from utils.errors import ParsingError  # noqa: E402
from utils.math import Vector3  # noqa: E402

_THREE_NODE_ROAD = """<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>
<Roads>
\t<RoadNode class="RoadNode" name="A" roadset="road_country" skinNumber="1"
\t\tFwdZLink="B" org="100.000 0.000 200.000" ModelNum="0" />
\t<RoadNode class="RoadNode" name="B" roadset="road_country" skinNumber="1"
\t\tFwdZLink="C" BackZLink="A" org="110.000 0.000 210.000" ModelNum="0" />
\t<RoadNode class="RoadNode" name="C" roadset="road_country" skinNumber="0"
\t\tBackZLink="B" org="120.000 0.000 220.000" ModelNum="0" />
</Roads>
"""

_TWO_ROADS = """<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>
<Roads>
\t<RoadNode class="RoadNode" name="A1" roadset="road_country" FwdZLink="A2" org="0.000 0.000 0.000" />
\t<RoadNode class="RoadNode" name="A2" roadset="road_country" BackZLink="A1" org="10.000 0.000 0.000" />
\t<RoadNode class="RoadNode" name="B1" roadset="road_gravel" FwdZLink="B2" org="0.000 0.000 50.000" />
\t<RoadNode class="RoadNode" name="B2" roadset="road_gravel" BackZLink="B1" org="10.000 0.000 50.000" />
</Roads>
"""


def _write(content: str) -> str:
    path = os.path.join(tempfile.mkdtemp(), "LevelRoads.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write(content)
    return path


# --- reading ---


def test_reconstructs_a_chain_in_traversal_order() -> None:
    network = read_roads(_write(_THREE_NODE_ROAD))
    assert len(network.chains) == 1
    assert [n.name for n in network.chains[0].nodes] == ["A", "B", "C"]


def test_separate_chains_stay_separate() -> None:
    network = read_roads(_write(_TWO_ROADS))
    assert len(network.chains) == 2
    assert network.node_count() == 4
    assert {c.roadset for c in network.chains} == {"road_country", "road_gravel"}


def test_parses_per_node_attributes() -> None:
    network = read_roads(_write(_THREE_NODE_ROAD))
    a, _b, c = network.chains[0].nodes
    assert a.roadset == "road_country"
    assert a.skin_number == 1
    assert a.model_num == 0
    assert a.org == Vector3(100.0, 0.0, 200.0)
    # skinNumber varies WITHIN a chain on real data — it must stay
    # per-node rather than being promoted to the chain.
    assert c.skin_number == 0


def test_absent_attributes_stay_none() -> None:
    """So a writer can reproduce the original rather than inventing
    defaults it never had."""
    network = read_roads(_write(_TWO_ROADS))
    node = network.chains[0].nodes[0]
    assert node.skin_number is None
    assert node.model_num is None
    assert node.as_cliff is None


def test_rejects_dangling_link() -> None:
    bad = _THREE_NODE_ROAD.replace('FwdZLink="C"', 'FwdZLink="NOPE"')
    try:
        read_roads(_write(bad))
        raise AssertionError("expected ParsingError")
    except ParsingError as e:
        assert "unknown node" in e.message


def test_rejects_node_without_name() -> None:
    bad = '<?xml version="1.0"?><Roads><RoadNode class="RoadNode" org="0 0 0" /></Roads>'
    try:
        read_roads(_write(bad))
        raise AssertionError("expected ParsingError")
    except ParsingError:
        pass


def test_rejects_malformed_org() -> None:
    bad = _THREE_NODE_ROAD.replace('org="100.000 0.000 200.000"', 'org="100.000 200.000"')
    try:
        read_roads(_write(bad))
        raise AssertionError("expected ParsingError")
    except ParsingError:
        pass


def test_closed_loop_is_not_dropped() -> None:
    """Every node has a BackZLink, so no chain start exists. The nodes
    must still be emitted rather than silently vanishing."""
    loop = """<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>
<Roads>
\t<RoadNode class="RoadNode" name="L1" FwdZLink="L2" BackZLink="L2" org="0.000 0.000 0.000" />
\t<RoadNode class="RoadNode" name="L2" FwdZLink="L1" BackZLink="L1" org="10.000 0.000 0.000" />
</Roads>
"""
    network = read_roads(_write(loop))
    assert network.node_count() == 2


# --- writing ---


def test_round_trip_preserves_chains_and_attributes() -> None:
    source = read_roads(_write(_THREE_NODE_ROAD))
    out = os.path.join(tempfile.mkdtemp(), "LevelRoads.xml")
    write_roads(out, source)
    reloaded = read_roads(out)

    assert len(reloaded.chains) == len(source.chains)
    for a, b in zip(source.all_nodes(), reloaded.all_nodes()):
        assert a.name == b.name
        assert a.roadset == b.roadset
        assert a.skin_number == b.skin_number
        assert a.org == b.org


def test_links_are_regenerated_from_chain_order() -> None:
    """Reordering a chain must produce correspondingly reordered links,
    not the stale ones from the source file."""
    source = read_roads(_write(_THREE_NODE_ROAD))
    source.chains[0].nodes.reverse()  # C, B, A

    out = os.path.join(tempfile.mkdtemp(), "LevelRoads.xml")
    write_roads(out, source)
    reloaded = read_roads(out)
    assert [n.name for n in reloaded.chains[0].nodes] == ["C", "B", "A"]


def test_extending_a_chain_round_trips() -> None:
    source = read_roads(_write(_THREE_NODE_ROAD))
    source.chains[0].nodes.append(
        RoadNode(name="D", org=Vector3(130.0, 0.0, 230.0), roadset="road_country")
    )
    out = os.path.join(tempfile.mkdtemp(), "LevelRoads.xml")
    write_roads(out, source)
    reloaded = read_roads(out)
    assert [n.name for n in reloaded.chains[0].nodes] == ["A", "B", "C", "D"]


# --- Blender bridge ---


def _flat_terrain(height: float = 100.0) -> HeightmapData:
    return HeightmapData.filled(8, 8, 8.0, fill=height)


def test_builds_one_curve_per_chain() -> None:
    network = read_roads(_write(_TWO_ROADS))
    collection = fake_bpy.FakeCollection("Roads")
    curves = build_road_curves(network, collection, transform=CoordinateTransform())
    assert len(curves) == 2
    assert all(c.type == "CURVE" for c in curves)
    assert len(curves[0].data.splines[0].points) == 2


def test_curve_points_use_the_shared_transform() -> None:
    """Roads must land in the same Blender space as terrain/objects:
    game Z becomes Blender Y, game Y becomes Blender Z."""
    network = RoadNetwork(chains=[RoadChain(nodes=[
        RoadNode(name="N", org=Vector3(10.0, 0.0, 20.0)),
    ])])
    collection = fake_bpy.FakeCollection("Roads")
    transform = CoordinateTransform(xy_scale=10.0, height_scale=1.5)
    curves = build_road_curves(network, collection, transform=transform)

    x, y, z, _w = curves[0].data.splines[0].points[0].co
    assert x == 100.0   # game X * xy_scale
    assert y == 200.0   # game Z * xy_scale -> Blender Y
    assert z == 0.0     # game Y * height_scale -> Blender Z


def test_curve_round_trip_without_terrain() -> None:
    network = read_roads(_write(_THREE_NODE_ROAD))
    collection = fake_bpy.FakeCollection("Roads")
    transform = CoordinateTransform(xy_scale=10.0, height_scale=1.5)

    curves = build_road_curves(network, collection, transform=transform)
    back = extract_road_network(curves, transform=transform)

    assert back.node_count() == network.node_count()
    for a, b in zip(network.all_nodes(), back.all_nodes()):
        assert a.name == b.name
        for p, q in zip(a.org.as_tuple(), b.org.as_tuple()):
            assert abs(p - q) < 1e-3


def test_curve_round_trip_with_terrain_relative_heights() -> None:
    """Node heights are stored relative to the ground; import resolves
    them against the terrain and export must un-resolve them."""
    network = read_roads(_write(_THREE_NODE_ROAD))
    terrain = _flat_terrain(height=100.0)
    collection = fake_bpy.FakeCollection("Roads")
    transform = CoordinateTransform(xy_scale=10.0, height_scale=1.5)

    curves = build_road_curves(network, collection, transform=transform, terrain=terrain)
    # On flat terrain at height 100, a node with org.y == 0 sits at
    # absolute height 100 -> Blender Z 150.
    assert abs(curves[0].data.splines[0].points[0].co[2] - 150.0) < 1e-3

    back = extract_road_network(curves, transform=transform, terrain=terrain)
    for a, b in zip(network.all_nodes(), back.all_nodes()):
        assert abs(a.org.y - b.org.y) < 1e-3  # back to 0, not 100


def test_extract_skips_objects_that_are_not_roads() -> None:
    network = read_roads(_write(_TWO_ROADS))
    collection = fake_bpy.FakeCollection("Roads")
    curves = build_road_curves(network, collection, transform=CoordinateTransform())
    stranger = fake_bpy.FakeObject("SomeCurveTheUserMade", None)

    back = extract_road_network(curves + [stranger], transform=CoordinateTransform())
    assert len(back.chains) == 2  # the stranger contributed nothing


def test_added_control_points_get_unique_names() -> None:
    """A point added in Blender must not collide with an existing node
    name — links are resolved by name, so a collision corrupts a chain."""
    network = read_roads(_write(_THREE_NODE_ROAD))
    collection = fake_bpy.FakeCollection("Roads")
    curves = build_road_curves(network, collection, transform=CoordinateTransform())

    curves[0].data.splines[0].points.add(1)  # user extends the road
    back = extract_road_network(curves, transform=CoordinateTransform())

    names = [n.name for n in back.all_nodes()]
    assert len(names) == len(set(names)), f"duplicate node names: {names}"
    assert len(names) == 4


def test_curve_objects_are_marked_as_roads() -> None:
    network = read_roads(_write(_TWO_ROADS))
    collection = fake_bpy.FakeCollection("Roads")
    curves = build_road_curves(network, collection, transform=CoordinateTransform())
    assert all(ROAD_CHAIN_PROP in c for c in curves)


_ALL_TESTS = (
    test_reconstructs_a_chain_in_traversal_order,
    test_separate_chains_stay_separate,
    test_parses_per_node_attributes,
    test_absent_attributes_stay_none,
    test_rejects_dangling_link,
    test_rejects_node_without_name,
    test_rejects_malformed_org,
    test_closed_loop_is_not_dropped,
    test_round_trip_preserves_chains_and_attributes,
    test_links_are_regenerated_from_chain_order,
    test_extending_a_chain_round_trips,
    test_builds_one_curve_per_chain,
    test_curve_points_use_the_shared_transform,
    test_curve_round_trip_without_terrain,
    test_curve_round_trip_with_terrain_relative_heights,
    test_extract_skips_objects_that_are_not_roads,
    test_added_control_points_get_unique_names,
    test_curve_objects_are_marked_as_roads,
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


# --- the frame a road piece is arrayed in -------------------------------


def test_a_chain_curve_keeps_its_up_vector_up() -> None:
    """Roads came in standing on edge, and a turn about Y set them right.

    The Curve modifier orients what it deforms by the curve's own
    frame. Blender's default twist is MINIMUM, which builds that frame
    from the first tangent and an arbitrary reference — on a horizontal
    road it lands a quarter turn over, and every arrayed piece stands
    on its edge.

    The ribbon path has set Z_UP since it was written, for this exact
    reason. The chains the models are arrayed along had not.
    """
    import fake_bpy

    fake_bpy.install()

    from blender_io.roads_bridge import build_road_curves
    from core.coordinates import CoordinateTransform
    from core.roads import RoadChain, RoadNetwork, RoadNode
    from utils.math import Vector3

    import bpy

    chain = RoadChain(
        nodes=[
            RoadNode(name="a", org=Vector3(0.0, 0.0, 0.0), roadset="road_country"),
            RoadNode(name="b", org=Vector3(64.0, 0.0, 0.0), roadset="road_country"),
        ],
    )
    collection = bpy.data.collections.new("roads")
    curves = build_road_curves(
        RoadNetwork(chains=[chain]), collection, transform=CoordinateTransform()
    )

    assert curves
    for obj in curves:
        assert obj.data.dimensions == "3D"
        assert obj.data.twist_mode == "Z_UP"


# --- the frame a road piece is laid down in -----------------------------


def test_the_roll_turns_about_the_length_not_the_height() -> None:
    """The easy way to get this wrong, and it looks like nothing.

    In MODEL space a piece is X across, Y up, Z along. Rolling about Y
    swaps the width with the LENGTH: the piece stays just as flat and
    is merely longer, which is indistinguishable from the roll never
    having been applied.
    """
    from core.roads import roll_about_length

    # A point one unit along each axis in turn.
    assert roll_about_length(1.0, 0.0, 0.0) == (0.0, -1.0, 0.0)   # across -> up
    assert roll_about_length(0.0, 1.0, 0.0) == (1.0, 0.0, 0.0)    # up -> across
    assert roll_about_length(0.0, 0.0, 1.0) == (0.0, 0.0, 1.0)    # along, untouched


def test_four_rolls_are_no_roll() -> None:
    from core.roads import roll_about_length

    for start in ((1.0, 2.0, 3.0), (-4.0, 0.5, 0.0)):
        point = start
        for _ in range(4):
            point = roll_about_length(*point)
        assert point == start


def test_rolling_a_piece_stands_it_on_edge_in_its_own_frame() -> None:
    """Which is what the Curve modifier then lays flat.

    Measured on road_country: 19.77 across, 14.45 along, 0.19 high. The
    roll has to leave the length alone and exchange the other two.
    """
    import fake_bpy

    fake_bpy.install()

    from blender_io.roads_bridge import _roll_piece
    from core.mesh import MeshData
    from utils.math import Vector3

    class _Model:
        pass

    mesh = MeshData(name="piece")
    mesh.positions = [
        Vector3(-9.885, 0.002, -7.223),
        Vector3(9.885, 0.190, 7.223),
    ]
    mesh.normals = [Vector3(0.0, 1.0, 0.0), Vector3(0.0, 1.0, 0.0)]
    model = _Model()
    model.meshes = [mesh]

    _roll_piece(model)

    xs = [p.x for p in mesh.positions]
    ys = [p.y for p in mesh.positions]
    zs = [p.z for p in mesh.positions]
    assert round(max(xs) - min(xs), 3) == 0.188     # the height, now across
    assert round(max(ys) - min(ys), 3) == 19.770    # the width, now up
    assert round(max(zs) - min(zs), 3) == 14.446    # the length, untouched


def test_the_normals_turn_with_the_geometry() -> None:
    """They are handed to Blender as custom split normals, so geometry
    rotated out from under them lights the road as if it were still
    standing on its edge — wrong, and only slightly wrong-looking."""
    import fake_bpy

    fake_bpy.install()

    from blender_io.roads_bridge import _roll_piece
    from core.mesh import MeshData
    from utils.math import Vector3

    class _Model:
        pass

    mesh = MeshData(name="piece")
    mesh.positions = [Vector3(1.0, 0.0, 0.0)]
    mesh.normals = [Vector3(0.0, 1.0, 0.0)]   # the road's up
    model = _Model()
    model.meshes = [mesh]

    _roll_piece(model)

    normal = mesh.normals[0]
    assert (round(normal.x, 6), round(normal.y, 6), round(normal.z, 6)) == (1.0, 0.0, 0.0)
