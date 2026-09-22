# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `formats/exm/terrain.py` (the displace.bin codec).

Rewritten after the fixed-512x512 hypothesis was disproven by direct
experiment (multiple real maps of different declared sizes). No test
here assumes any particular grid size — dynamic size detection is the
thing under test.

Run with `pytest` once available in a dev environment; see the
`if __name__` block at the bottom for a no-pytest fallback runner.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.terrain import HeightmapData  # noqa: E402
from formats.exm.terrain import DEFAULT_CELL_SIZE, read_displace, write_displace  # noqa: E402
from utils.errors import EXMeditorError, ParsingError, ValidationError  # noqa: E402


@contextlib.contextmanager
def assert_raises(exc_type: type[BaseException]):
    """Tiny `pytest.raises`-alike so these tests don't require pytest to
    be installed to run (no network access in this sandbox to install
    it) while remaining fully valid, idiomatic pytest tests once it is."""
    try:
        yield
    except exc_type:
        return
    else:
        raise AssertionError(f"expected {exc_type.__name__} to be raised, but nothing was")


def _temp_path(name: str = "displace.bin") -> str:
    return os.path.join(tempfile.mkdtemp(), name)


# Confirmed empirically (real Ex Machina maps of different declared
# sizes) — see the architecture doc changelog. (file_size_bytes, grid_side).
CONFIRMED_SIZES = [
    (4096, 32),
    (16384, 64),
    (65536, 128),
    (262144, 256),
    (4194304, 1024),
]


def test_confirmed_size_table_round_trips_correctly() -> None:
    """Regression test tying the generic sqrt-based algorithm to the
    exact empirical (file_size -> grid_side) table that disproved the
    original fixed-512x512 hypothesis."""
    for file_bytes, expected_side in CONFIRMED_SIZES:
        heightmap = HeightmapData.filled(expected_side, expected_side, DEFAULT_CELL_SIZE, fill=1.0)
        path = _temp_path()
        write_displace(path, heightmap)
        assert os.path.getsize(path) == file_bytes

        reloaded = read_displace(path)
        assert reloaded.width == expected_side
        assert reloaded.height == expected_side


def test_round_trip_preserves_heights_for_a_small_map() -> None:
    """A small (8x8-declared-map-sized, 32x32 grid) map's exact height
    values must survive write -> read unchanged."""
    side = 32
    heightmap = HeightmapData.filled(side, side, DEFAULT_CELL_SIZE, fill=0.0)
    for i in range(0, side * side, 7):
        heightmap.values[i] = float(i) * 0.1

    path = _temp_path()
    write_displace(path, heightmap)
    reloaded = read_displace(path)

    assert reloaded.width == side
    assert reloaded.height == side
    assert list(reloaded.values) == list(heightmap.values)


def test_round_trip_preserves_heights_for_a_large_map() -> None:
    """Same as above but at the largest confirmed size (1024x1024), to
    catch any bug that only shows up at scale (e.g. an int overflow or
    an off-by-one that a tiny test grid wouldn't expose)."""
    side = 1024
    heightmap = HeightmapData.filled(side, side, DEFAULT_CELL_SIZE, fill=0.0)
    for i in range(0, side * side, 4099):  # a large, non-round stride
        heightmap.values[i] = float(i) * 0.001

    path = _temp_path()
    write_displace(path, heightmap)
    reloaded = read_displace(path)

    assert reloaded.width == side
    assert reloaded.height == side
    assert list(reloaded.values) == list(heightmap.values)


def test_read_rejects_size_not_a_multiple_of_4() -> None:
    path = _temp_path()
    with open(path, "wb") as f:
        f.write(b"\x00" * 4097)  # one byte over a multiple of 4
    with assert_raises(ParsingError):
        read_displace(path)


def test_read_rejects_non_perfect_square_sample_count() -> None:
    """4 * 1023 bytes = 1023 float32 samples, which has no integer
    square root — must be rejected, not silently reshaped into a
    lopsided/wrong grid."""
    path = _temp_path()
    with open(path, "wb") as f:
        f.write(b"\x00" * (4 * 1023))
    with assert_raises(ParsingError):
        read_displace(path)


def test_read_missing_file_raises() -> None:
    with assert_raises(EXMeditorError):
        read_displace("/no/such/directory/displace.bin")


def test_write_rejects_non_square_heightmap() -> None:
    """write_displace() must refuse a non-square grid — squareness is
    the one dimension rule actually confirmed, unlike a fixed size."""
    non_square = HeightmapData.filled(4, 8, 1.0, fill=0.0)
    path = _temp_path()
    with assert_raises(ValidationError):
        write_displace(path, non_square)
    assert not os.path.exists(path), "a rejected write must not create a partial file"


def test_write_rejects_non_finite_values() -> None:
    heightmap = HeightmapData.filled(32, 32, 1.0, fill=0.0)
    heightmap.set_height(5, 5, float("nan"))
    path = _temp_path()
    with assert_raises(ValidationError):
        write_displace(path, heightmap)
    assert not os.path.exists(path)


def test_custom_cell_size_is_preserved_on_the_python_object() -> None:
    """cell_size isn't stored in displace.bin itself (it's a flat float32
    grid with no header) — read_displace() takes it as a parameter."""
    heightmap = HeightmapData.filled(32, 32, 1.0, fill=5.0)
    path = _temp_path()
    write_displace(path, heightmap)
    reloaded = read_displace(path, cell_size=2.5)
    assert reloaded.cell_size == 2.5


def test_orientation_transforms_are_not_applied_by_default() -> None:
    """Sanity check on the SDK-wide 'no unverified default' rule: reading
    a displace.bin must never silently apply any orientation transform —
    that stays a manual, explicit step via core.terrain_transform."""
    side = 16
    heightmap = HeightmapData.filled(side, side, 1.0, fill=0.0)
    heightmap.set_height(0, 0, 42.0)
    path = _temp_path()
    write_displace(path, heightmap)
    reloaded = read_displace(path)
    assert reloaded.get_height(0, 0) == 42.0  # untouched — no implicit flip/transpose


_ALL_TESTS = (
    test_confirmed_size_table_round_trips_correctly,
    test_round_trip_preserves_heights_for_a_small_map,
    test_round_trip_preserves_heights_for_a_large_map,
    test_read_rejects_size_not_a_multiple_of_4,
    test_read_rejects_non_perfect_square_sample_count,
    test_read_missing_file_raises,
    test_write_rejects_non_square_heightmap,
    test_write_rejects_non_finite_values,
    test_custom_cell_size_is_preserved_on_the_python_object,
    test_orientation_transforms_are_not_applied_by_default,
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
