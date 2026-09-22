# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `formats/exm/collision.py` and `blender_io/collision_bridge.py`.

Static obstacles are oriented bounding boxes. The key behaviours pinned
here: absolute (not terrain-relative) heights, asymmetric extents
surviving a round trip, and boxes landing in the same Blender space as
every other spatial format.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.collision_bridge import (  # noqa: E402
    OBSTACLE_PROP,
    build_obstacle_boxes,
    extract_obstacles,
)
from core.collision import ObstacleBox, ObstacleSet  # noqa: E402
from core.coordinates import CoordinateTransform  # noqa: E402
from formats.exm.collision import read_obstacles, write_obstacles  # noqa: E402
from utils.errors import ParsingError  # noqa: E402
from utils.math import Vector3  # noqa: E402

_TWO_BOXES = """<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>
<Boxes>
\t<Box min="-10.000 -5.000 -20.000" max="10.000 5.000 20.000"
\t\torigin="100.000 300.000 200.000" rotation="0.0000 0.0000 0.0000 1.0000" />
\t<Box min="-9.235 -2.695 -18.223" max="6.587 8.895 18.184"
\t\torigin="1214.379 290.628 2818.083" rotation="0.0000 0.7071 0.0000 0.7071" />
</Boxes>
"""


def _write(content: str) -> str:
    path = os.path.join(tempfile.mkdtemp(), "static_obstacles.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write(content)
    return path


# --- codec ---


def test_reads_all_boxes() -> None:
    obstacles = read_obstacles(_write(_TWO_BOXES))
    assert len(obstacles) == 2


def test_parses_box_geometry() -> None:
    box = read_obstacles(_write(_TWO_BOXES)).boxes[0]
    assert box.min_corner == Vector3(-10.0, -5.0, -20.0)
    assert box.max_corner == Vector3(10.0, 5.0, 20.0)
    assert box.origin == Vector3(100.0, 300.0, 200.0)
    assert box.raw_rotation == (0.0, 0.0, 0.0, 1.0)


def test_size_and_local_centre() -> None:
    boxes = read_obstacles(_write(_TWO_BOXES)).boxes
    assert boxes[0].size() == Vector3(20.0, 10.0, 40.0)
    assert boxes[0].local_centre() == Vector3(0.0, 0.0, 0.0)  # symmetric
    # The asymmetric one: its visual centre is offset from its origin.
    assert abs(boxes[1].local_centre().x - (-1.324)) < 0.001


def test_rejects_missing_attribute() -> None:
    bad = '<?xml version="1.0"?><Boxes><Box min="0 0 0" max="1 1 1" /></Boxes>'
    try:
        read_obstacles(_write(bad))
        raise AssertionError("expected ParsingError")
    except ParsingError as e:
        assert "origin" in e.message


def test_rejects_malformed_numbers() -> None:
    bad = _TWO_BOXES.replace('min="-10.000 -5.000 -20.000"', 'min="-10.000 -5.000"')
    try:
        read_obstacles(_write(bad))
        raise AssertionError("expected ParsingError")
    except ParsingError:
        pass


def test_rejects_missing_file() -> None:
    try:
        read_obstacles("/no/such/static_obstacles.xml")
        raise AssertionError("expected ParsingError")
    except ParsingError:
        pass


def test_codec_round_trip() -> None:
    source = read_obstacles(_write(_TWO_BOXES))
    out = os.path.join(tempfile.mkdtemp(), "static_obstacles.xml")
    write_obstacles(out, source)
    reloaded = read_obstacles(out)

    assert len(reloaded) == len(source)
    for a, b in zip(source.boxes, reloaded.boxes):
        assert a.min_corner == b.min_corner
        assert a.max_corner == b.max_corner
        assert a.origin == b.origin
        for p, q in zip(a.raw_rotation, b.raw_rotation):
            assert abs(p - q) < 1e-4


# --- Blender bridge ---


def test_builds_one_empty_per_box() -> None:
    obstacles = read_obstacles(_write(_TWO_BOXES))
    collection = fake_bpy.FakeCollection("Collision")
    objects = build_obstacle_boxes(obstacles, collection, transform=CoordinateTransform())
    assert len(objects) == 2
    assert all(o.type == "EMPTY" for o in objects)
    assert all(o.empty_display_type == "CUBE" for o in objects)
    assert all(OBSTACLE_PROP in o for o in objects)


def test_box_placement_uses_the_shared_transform() -> None:
    """Boxes must land in the same Blender space as terrain, objects and
    roads: game Z -> Blender Y, game Y (height) -> Blender Z."""
    obstacles = ObstacleSet(boxes=[ObstacleBox(
        min_corner=Vector3(-1.0, -1.0, -1.0),
        max_corner=Vector3(1.0, 1.0, 1.0),
        origin=Vector3(10.0, 300.0, 20.0),
        raw_rotation=(0.0, 0.0, 0.0, 1.0),
    )])
    collection = fake_bpy.FakeCollection("Collision")
    transform = CoordinateTransform(xy_scale=10.0, height_scale=1.5)
    obj = build_obstacle_boxes(obstacles, collection, transform=transform)[0]

    assert obj.location[0] == 100.0   # game X * xy_scale
    assert obj.location[1] == 200.0   # game Z * xy_scale -> Blender Y
    assert obj.location[2] == 450.0   # game Y * height_scale -> Blender Z


def test_scale_reflects_half_extents() -> None:
    """A CUBE empty draws at +/- display_size * scale, so scale must be
    the box's half-extents for the drawn cube to match its real size."""
    obstacles = ObstacleSet(boxes=[ObstacleBox(
        min_corner=Vector3(-10.0, -5.0, -20.0),
        max_corner=Vector3(10.0, 5.0, 20.0),
        origin=Vector3(0.0, 0.0, 0.0),
        raw_rotation=(0.0, 0.0, 0.0, 1.0),
    )])
    collection = fake_bpy.FakeCollection("Collision")
    obj = build_obstacle_boxes(obstacles, collection, transform=CoordinateTransform())[0]
    # half-extents, axis-swapped: (x, z, y) of (20, 10, 40) / 2
    assert obj.scale == (10.0, 20.0, 5.0)


def test_bridge_round_trip_preserves_geometry() -> None:
    source = read_obstacles(_write(_TWO_BOXES))
    collection = fake_bpy.FakeCollection("Collision")
    transform = CoordinateTransform(xy_scale=10.0, height_scale=1.5)

    objects = build_obstacle_boxes(source, collection, transform=transform)
    back = extract_obstacles(objects, transform=transform)

    assert len(back) == len(source)
    for a, b in zip(source.boxes, back.boxes):
        for p, q in zip(a.origin.as_tuple(), b.origin.as_tuple()):
            assert abs(p - q) < 1e-3
        assert a.min_corner == b.min_corner
        assert a.max_corner == b.max_corner


def test_asymmetric_box_origin_survives_round_trip() -> None:
    """An asymmetric box's visual centre differs from its origin; the
    offset applied on import must be undone on export, or the box
    drifts a little further every save."""
    source = read_obstacles(_write(_TWO_BOXES))
    collection = fake_bpy.FakeCollection("Collision")
    transform = CoordinateTransform(xy_scale=10.0, height_scale=1.5)

    objects = build_obstacle_boxes(source, collection, transform=transform)
    once = extract_obstacles(objects, transform=transform)
    objects2 = build_obstacle_boxes(once, fake_bpy.FakeCollection("C2"), transform=transform)
    twice = extract_obstacles(objects2, transform=transform)

    asymmetric_original = source.boxes[1].origin
    asymmetric_twice = twice.boxes[1].origin
    for p, q in zip(asymmetric_original.as_tuple(), asymmetric_twice.as_tuple()):
        assert abs(p - q) < 1e-3, "origin drifted across repeated round trips"


def test_extract_skips_non_obstacle_objects() -> None:
    obstacles = read_obstacles(_write(_TWO_BOXES))
    collection = fake_bpy.FakeCollection("Collision")
    objects = build_obstacle_boxes(obstacles, collection, transform=CoordinateTransform())
    stranger = fake_bpy.FakeObject("UserEmpty", None)

    back = extract_obstacles(objects + [stranger], transform=CoordinateTransform())
    assert len(back) == 2


_ALL_TESTS = (
    test_reads_all_boxes,
    test_parses_box_geometry,
    test_size_and_local_centre,
    test_rejects_missing_attribute,
    test_rejects_malformed_numbers,
    test_rejects_missing_file,
    test_codec_round_trip,
    test_builds_one_empty_per_box,
    test_box_placement_uses_the_shared_transform,
    test_scale_reflects_half_extents,
    test_bridge_round_trip_preserves_geometry,
    test_asymmetric_box_origin_survives_round_trip,
    test_extract_skips_non_obstacle_objects,
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
