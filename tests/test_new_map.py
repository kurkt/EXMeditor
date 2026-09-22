# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for creating a map from nothing.

The sizes and every derived number are MEASURED off the six templates
the game ships in ``data/maps`` — see ``core/map_template.py``. What
these guard is that a created map is readable by the same readers that
read the shipped ones, and that its rasters are the exact sizes the
format demands: a map the editor opens and finds a byte short is worse
than one that was never written.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.map_template import (  # noqa: E402
    GRID_SIZES,
    REQUIRED_FILES,
    describe,
    grid_for_level_size,
    level_size,
    raster_sizes,
    safe_bounds,
    world_span,
)
from formats.exm.new_map import create_map, manifest_text  # noqa: E402
from formats.exm.ssl import read_manifest  # noqa: E402
from formats.exm.terrain import read_displace  # noqa: E402
from formats.exm.tilemap import read_tilemap  # noqa: E402
from utils.errors import ParsingError  # noqa: E402


def _make(grid: int = 8, name: str = "TestMap"):
    return create_map(tempfile.mkdtemp(), name, grid)


# --- the size table ------------------------------------------------------


def test_the_grid_is_four_times_the_level_size() -> None:
    """The two numbers are not the same, and swapping them makes a map
    a quarter of the size asked for.

    Measured on all six templates: 8x8 stores LEVELSIZE 2, 256x256
    stores 64.
    """
    assert level_size(8) == 2
    assert level_size(32) == 8
    assert level_size(256) == 64
    for grid in GRID_SIZES:
        assert grid_for_level_size(level_size(grid)) == grid


def test_the_rasters_are_the_sizes_the_shipped_maps_are() -> None:
    """64G^2, 64G^2, 4G^2, 2G^2 — checked against every template."""
    assert raster_sizes(8) == {
        "displace.bin": 4096, "colormap.raw": 4096,
        "passmap.raw": 256, "water.raw": 128,
    }
    assert raster_sizes(128)["displace.bin"] == 1048576   # r1m1
    assert raster_sizes(256)["water.raw"] == 131072       # r1m2


def test_the_playable_area_is_inset_forty_units() -> None:
    """40..984 on the shipped 32x32, 40..4056 on r1m1 — and 4056 was
    measured from the other direction long before this table existed."""
    assert safe_bounds(32) == (40.0, 40.0, 984.0, 984.0)
    assert safe_bounds(128) == (40.0, 40.0, 4056.0, 4056.0)
    assert world_span(128) == 4096.0


def test_an_unknown_size_is_refused() -> None:
    try:
        describe(100)
    except ValueError:
        return
    raise AssertionError("a size no shipped map uses was accepted")


# --- what gets written ---------------------------------------------------


def test_every_file_a_shipped_map_has_is_written() -> None:
    """Eighteen names, measured as the ones present in all ten map
    folders examined."""
    report = _make()

    for name in REQUIRED_FILES:
        if name == "roads.xml":
            continue          # copied, and there is nothing to copy from here
        assert os.path.isfile(os.path.join(report["folder"], name)), name


def test_the_two_files_the_editor_asserts_without_are_written() -> None:
    """The regression, and the reasoning error behind it.

    These are in only nine and seven of ten shipped maps, and the first
    version left them out on the strength of that. The editor opened
    the map, logged "Couldn't load normal map" and died on an assert —
    exception 0x80000003, BREAKPOINT.

    Presence in the shipped data is a statistic; being required is a
    property of the loader. The first says nothing about the second.
    """
    from core.map_template import EDITOR_REQUIRED_FILES

    report = _make()
    for name in EDITOR_REQUIRED_FILES:
        assert os.path.isfile(os.path.join(report["folder"], name)), name


def test_the_normal_map_is_the_shape_the_shipped_ones_are() -> None:
    """MEASURED on five sizes: the payload is a uint32 count followed
    by (8G+2)^2 bytes, and the container is subtype 16 with a name
    chunk reading RIV."""
    from core.map_template import normal_map_samples, normal_map_side
    from formats.exm.gam import read_container

    assert normal_map_side(8) == 66
    assert normal_map_side(32) == 258
    assert normal_map_samples(32) == 66564

    report = _make(16)
    subtype, chunks = read_container(os.path.join(report["folder"], "NormalMap.bin"))

    assert subtype == 16
    assert [c.chunk_id for c in chunks] == [0x0BADF00D, 0xF001, 0xF002]

    payload = chunks[0].data
    import struct
    count = struct.unpack_from("<I", payload, 0)[0]
    assert count == normal_map_samples(16) == 130 * 130
    assert len(payload) == 4 + count
    # Flat terrain: zero is what the shipped templates hold over their
    # whole interior, and what one byte per sample MEANS is unknown.
    assert set(payload[4:]) == {0}

    assert chunks[1].data.split(bytes(1))[0] == b"RIV"


def test_the_shoreline_is_empty_because_the_map_has_no_water() -> None:
    """Its size follows the water a map contains, not the map's size —
    2614, 66618, 21422 and 22606 bytes on the four templates."""
    import struct

    from formats.exm.gam import read_container

    report = _make(8)
    subtype, chunks = read_container(os.path.join(report["folder"], "ShoreLine.bin"))

    assert [c.chunk_id for c in chunks] == [0x01, 0xF001, 0xF002]
    assert struct.unpack_from("<I", chunks[0].data, 0)[0] == 0
    assert chunks[1].data.split(bytes(1))[0] == b"SFF"


def test_the_rasters_come_out_the_exact_size() -> None:
    """A map the editor opens and finds a byte short is worse than one
    that was never written."""
    for grid in (8, 16, 32):
        report = _make(grid)
        for name, want in raster_sizes(grid).items():
            got = os.path.getsize(os.path.join(report["folder"], name))
            assert got == want, (grid, name, got, want)


def test_the_manifest_reads_back_through_the_shipped_reader() -> None:
    report = _make(32)
    manifest = read_manifest(report["manifest"])

    assert manifest.level_size == 8
    assert manifest.safe_bounds == (40.0, 40.0, 984.0, 984.0)
    # The lighting lives in ILLUMINATION, a different section from the
    # rest — a manifest that only wrote LEVEL would lose all of it.
    assert manifest.file_ref("MODEL_AMBIENT") == "101 116 44"
    assert manifest.file_ref("TILES") == "level.tile"


def test_the_terrain_is_flat_at_the_height_the_templates_use() -> None:
    """255, not 0: the shipped templates leave room to carve down."""
    report = _make(8)
    heightmap = read_displace(os.path.join(report["folder"], "displace.bin"))

    assert heightmap.width == heightmap.height == 32   # 4G
    assert min(heightmap.values) == max(heightmap.values) == 255.0


def test_the_tile_map_reads_back_with_one_ground_texture() -> None:
    report = _make(16)
    tilemap = read_tilemap(os.path.join(report["folder"], "level.tile"))

    assert tilemap.side == 16
    assert tilemap.tiles == ["region1\\GROUND_WASTE.dds"]
    assert len(tilemap.indices) == 16 * 16
    assert set(tilemap.indices) == {0}
    # Four bytes a cell, as every shipped map writes.
    assert len(tilemap.raw_cells) == 4 * 16 * 16


def test_the_manifest_names_the_map_it_belongs_to() -> None:
    text = manifest_text("Roadside", 32)

    assert 'name="PATH">data\\maps\\Roadside<' in text
    assert 'name="LEVELNAME">Roadside<' in text
    assert 'name="LEVELSIZE">8<' in text


def test_a_map_that_is_already_there_is_not_overwritten() -> None:
    """A map folder is hours of work and this is not the place to lose
    one."""
    folder = tempfile.mkdtemp()
    create_map(folder, "Twice", 8)
    try:
        create_map(folder, "Twice", 8)
    except ParsingError:
        return
    raise AssertionError("an existing map was overwritten")


def test_overwriting_is_possible_when_asked_for() -> None:
    folder = tempfile.mkdtemp()
    create_map(folder, "Twice", 8)
    report = create_map(folder, "Twice", 16, overwrite=True)

    assert read_manifest(report["manifest"]).level_size == 4


def test_the_grass_file_is_a_container_with_no_grass_in_it() -> None:
    """grass.xml is not XML: it is an ecbnt,t container, and a reader
    that took the name at face value would find nothing at all."""
    from formats.exm.gam import read_container

    report = _make(8)
    subtype, chunks = read_container(os.path.join(report["folder"], "grass.xml"))

    assert subtype == 0
    assert [c.chunk_id for c in chunks] == [0x01, 0x02, 0x03, 0xF001, 0xF002]
    assert chunks[0].data == b"\x00" * 8      # no types, no patches


def test_the_tile_map_carries_its_name_and_version_chunks() -> None:
    """MEASURED on 41 files — every shipped map plus the five the
    original editor wrote itself — level.tile ends with a 30-byte name
    chunk reading TILEMAP and 0xF002 holding 1. It shipped without
    them, and the editor died on the load right after the normal map,
    which is where the tile load is."""
    import struct

    from formats.exm.gam import read_container

    report = _make(8)
    subtype, chunks = read_container(os.path.join(report["folder"], "level.tile"))

    assert subtype == 0x0B
    assert [c.chunk_id for c in chunks] == [0x0BADF00D, 0xF001, 0xF002]
    assert len(chunks[1].data) == 30
    assert chunks[1].data.split(bytes(1))[0] == b"TILEMAP"
    assert struct.unpack("<I", chunks[2].data)[0] == 1

    # And the whole file, byte for byte. The five files the editor
    # wrote are 402, 1170, 4242, 16530 and 262290 bytes — 4*G*G + 146,
    # where the 146 is the 60-byte table, the header naming one tile,
    # and this trailer. single_tile_map now reproduces all five
    # EXACTLY; that was checked against the game folder and cannot be
    # checked here, because game data is never committed.
    from formats.exm.new_map import single_tile_map

    for size, expected in ((8, 402), (16, 1170), (32, 4242),
                           (64, 16530), (256, 262290)):
        assert len(single_tile_map(size)) == expected, size


def test_every_container_written_ends_with_a_name_and_a_version() -> None:
    """The general form of the bug above.

    Four of this module's five container writers appended the 0xF001 /
    0xF002 trailer and one did not, and nothing compared them. No
    container the game reads has ever been seen without it, so the
    check belongs on all of them at once rather than on each new one
    as it is remembered."""
    from formats.exm.gam import read_container

    report = _make(8)
    seen = 0
    for name in sorted(os.listdir(report["folder"])):
        path = os.path.join(report["folder"], name)
        with open(path, "rb") as handle:
            if handle.read(7) != b"ecbnt,t":
                continue
        seen += 1
        _subtype, chunks = read_container(path)
        assert [c.chunk_id for c in chunks[-2:]] == [0xF001, 0xF002], name
        assert len(chunks[-2].data) == 30, name
    assert seen >= 4, f"only {seen} container(s) found — the sweep found nothing"


def test_the_strings_file_is_rooted_at_resource() -> None:
    """The file is called strings.xml and its root element is not
    <strings>. MEASURED on ten maps, five of them written by the
    editor itself: <resource> on all ten."""
    report = _make(8)
    with open(os.path.join(report["folder"], "strings.xml"), encoding="cp1251") as fh:
        text = fh.read()

    assert "<resource>" in text and "</resource>" in text
    assert "<strings>" not in text


def test_the_text_files_use_the_line_ending_the_editor_uses() -> None:
    """All 41 .ssl files in the game are LF and none is CRLF, and so is
    every XML file in a map folder the editor wrote. This is about a
    created map diffing cleanly against one the editor has rewritten;
    roads.xml is CRLF, so the parser plainly takes either."""
    report = _make(8)
    paths = [report["manifest"]] + [
        os.path.join(report["folder"], n)
        for n in os.listdir(report["folder"]) if n.endswith(".xml")
    ]
    for path in paths:
        with open(path, "rb") as handle:
            raw = handle.read()
        if not raw.startswith(b"<?xml"):
            continue          # grass.xml is a container despite the name
        assert b"\r\n" not in raw, os.path.basename(path)
        assert b"\n" in raw, os.path.basename(path)
