# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Create a map folder from nothing.

Everything here is written from the format, not copied from a template
folder — with one exception, ``roads.xml``, which is a 22 KB catalogue
of road sets identical in five of the six shipped maps and is copied
from an existing map rather than invented.

What a new map gets is the eighteen files every shipped map has (see
``core.map_template.REQUIRED_FILES``), plus ``NormalMap.bin`` and
``ShoreLine.bin``. The first attempt left those two out because they
are in only nine and seven of ten shipped maps — and the editor opened
the result, failed to load the normal map and died on an assert. See
``REQUIRED_FILES`` for why that inference was wrong.
"""

from __future__ import annotations

import os
import shutil
import struct

from core.map_template import (
    DEFAULT_TILE,
    DEFAULT_TILE_ROOT,
    FLAT_HEIGHT,
    NEUTRAL_COLOUR,
    camera_start,
    describe,
    level_size,
    normal_map_samples,
    raster_sizes,
    safe_bounds,
    terrain_vertices,
)
from formats.exm.gam import Chunk, write_container
from utils.errors import ErrorContext, ParsingError
from utils.logging import get_logger

logger = get_logger("formats.exm.new_map")

_HEADER = '<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>\n'

#: Bare LF, which is what the editor writes. MEASURED: all 41 ``.ssl``
#: files in the game use LF and none uses CRLF, and so does every small
#: XML file in the map folders the editor produced. (``roads.xml`` is
#: CRLF, so the parser plainly takes either — this is about a created
#: map diffing cleanly against one the editor rewrites, not about the
#: parser.)
_NEWLINE = "\n"

#: The small XML files, verbatim from the shipped templates. Their
#: comments are the game's own documentation of each file and are kept.
_BOILERPLATE = {
    "world.xml": _HEADER + '<World\n\tname="Object13"\n\tclass="SgNode"\n\tLastId="1" />\n',
    "dynamicscene.xml": _HEADER + (
        '<DynamicScene\n\tLastId="1">\n'
        '\t<TargetNamesForDestroy\n\t\tNames="u1 u2 u3 u4 u5 u6 u7 u8 u9" />\n\n'
        '\t<Object\n\t\tName="Player1"\n\t\tBelong="1100"\n\t\tPrototype="player" />\n'
        '</DynamicScene>\n'
    ),
    "levelroads.xml": _HEADER + "<Roads />\n",
    "static_obstacles.xml": _HEADER + "<Boxes>\n\n</Boxes>\n",
    "camera_paths.xml": _HEADER + "<Paths>\n\n</Paths>\n",
    "cinemaTriggers.xml": _HEADER + "<triggers>\n\n</triggers>\n",
    "external_paths.xml": _HEADER + "<Paths>\n\n</Paths>\n",
    "object_names.xml": _HEADER + "<ObjectNames>\n\n</ObjectNames>\n",
    "QuestStates.xml": _HEADER + "<quests>\n\n</quests>\n",
    # ``<resource>``, not ``<strings>``. The file is named strings.xml
    # and the root element is not: measured on ten maps — the five the
    # editor made and five shipped ones — and it is ``<resource>`` on
    # all ten.
    "strings.xml": _HEADER + "<resource>\n\n</resource>\n",
    "triggers.xml": _HEADER + "<triggers>\n\n</triggers>\n",
}

#: Manifest keys that are the same on every shipped map, in the order
#: the shipped files write them. Order is not known to matter and is
#: kept anyway: a diff against a map the editor rewrites is far easier
#: to read when only the values moved.
_LEVEL_CONSTANTS = (
    ("WEATHERDETAIL", "WeatherDetail.xml"),
    ("WEATHERTYPE", "0"),
    ("CURRENTDAYTIME", "0"),
    ("HIGHMAP", "displace.bin"),
    ("DETMAP", "water.raw"),
    ("CAMERAMAP", "cameramap.raw"),
    ("COLORMAP", "colormap.raw"),
    ("CLIFFMAP", "level.cliff"),
    ("CLIFFSET", "Cliffs.xml"),
    ("ROADMAP", "LevelRoads.xml"),
    ("ROADSET", "Roads.xml"),
    ("WAYPOINTS", "ways.xml"),
    ("BEACHSETS", "Beachsets.xml"),
    ("SHORELINE", "ShoreLine.bin"),
    ("NORMALMAP", "NormalMap.bin"),
    ("CUBEMAP", "data/models/textures/lobbycube.dds"),
    ("MAXHEIGHT", "2500.000"),
    ("WATERLEVEL", "0.000"),
    ("BASEWATERLEVEL", "0.000"),
    ("REFLECTIONTINT", "255 255 255"),
    ("REFRACTIONTINT", "255 255 255"),
    ("SKYDOMEDIVIDER", "8.000"),
    ("WATERSMALLTEX", "WaterSm.tga"),
    ("WATERBIGTEX", "Water.tga"),
    ("WATERABSRED", "0.050"),
    ("WATERABSGREEN", "0.040"),
    ("WATERABSBLUE", "0.032"),
    ("SERVERDYN", "DynamicScene.xml"),
    ("SERVERQUESTSTATES", "QuestStates.xml"),
    ("MODELNAMES", "data\\if\\diz\\model_names.xml"),
    ("OBJECTNAMES", "object_names.xml"),
    ("SERVEREXTERNALPATHS", "external_paths.xml"),
    ("STATICOBSTACLES", "static_obstacles.xml"),
    ("PLAYERPASSMAP", "player_passmap.bin"),
    ("SERVERS", "data\\models\\servers.xml"),
    ("STATICSERVERS", "data\\models\\commonservers.xml"),
    ("PASSMAP", "passmap.raw"),
    ("PASSMAPCELLSIZE", "16"),
    ("TRIGGERSNAME", "triggers.xml"),
    ("CINEMATRIGGERSNAME", "cinemaTriggers.xml"),
    ("DLGSTRINGS", "strings.xml"),
    ("LOCALQUESTS", "localQuests.xml"),
    ("LOCALQUESTINFO", "localQuestInfo.xml"),
    ("LOCALDIALOGS", "localDialogs.xml"),
    ("SKYTYPE", "1"),
    ("ENV_SKY_Z_POS", "data\\env\\day4.front.tga"),
    ("ENV_SKY_Z_NEG", "data\\env\\day4.back.tga"),
    ("ENV_SKY_Y_POS", "data\\env\\day4.up.tga"),
    ("ENV_SKY_Y_NEG", ""),
    ("ENV_SKY_X_POS", "data\\env\\day4.right.tga"),
    ("ENV_SKY_X_NEG", "data\\env\\day4.left.tga"),
    ("ENV_SKY_SCALE_S", "0.800"),
    ("ENV_SKY_SCALE_T", "0.400"),
    ("ENV_SKY_SCROLL_SPEED", "1.000"),
    ("ENV_SKY_ROTATE_SPEED", "2.000"),
    ("ENV_SKY_CLOUDS_ENABLE", "1"),
    ("SUN_AZIMUTH_SPEED", "0.000"),
    ("SUN_AZIMUTH", "45.000"),
    ("SUN_DAY_ASCENTION", "90.000"),
    ("SUN_RISE_ASCENTION", "53.000"),
    ("SUN_SET_ASCENTION", "127.000"),
    ("TILES", "level.tile"),
)

#: The lighting section, as the templates ship it. Green, because the
#: game's foliage and road textures carry no colour of their own — see
#: ``core/lighting.py``. LS_TFACTOR is uninitialised memory in every
#: shipped file and is reproduced rather than cleaned up, so a map this
#: writes looks like a map the editor wrote.
_ILLUMINATION = (
    ("MODEL_AMBIENT", "101 116 44"),
    ("MODEL_DIFFUSE", "40 68 50"),
    ("LS_COLOR", "97 83 101"),
    ("LS_DIFFUSE", "101 108 112"),
    ("LS_TFACTOR", "18605364804791530000000000000000000.000"),
    ("SKY_COLOR", "0 0 0"),
    ("FOG_COLOR", "0 0 0"),
)


def manifest_text(map_name: str, grid: int) -> str:
    """The ``.ssl`` for a new map of this size."""
    min_x, min_y, max_x, max_y = safe_bounds(grid)
    camera_x, camera_y, camera_z = camera_start(grid)

    level = list(_LEVEL_CONSTANTS)
    level.insert(3, ("PATH", f"data\\maps\\{map_name}"))
    level.insert(4, ("LEVELNAME", map_name))
    level += [
        ("LEVELSIZE", str(level_size(grid))),
        ("MINSAFEX", f"{min_x:.3f}"),
        ("MINSAFEY", f"{min_y:.3f}"),
        ("MAXSAFEX", f"{max_x:.3f}"),
        ("MAXSAFEY", f"{max_y:.3f}"),
    ]

    sections = (
        ("LEVEL", level),
        ("CAMERA", (
            ("X", f"{camera_x:.3f}"), ("Y", f"{camera_y:.3f}"),
            ("Z", f"{camera_z:.3f}"),
            ("A", "0.000"), ("B", "-0.785"), ("G", "0.000"),
        )),
        ("DEMO", (("STARTUP", "0"), ("FILE", "demo.rec"))),
        ("ILLUMINATION", _ILLUMINATION),
    )

    out = [_HEADER.rstrip("\n"), "<Ini>"]
    for name, keys in sections:
        out.append(f'\t<Section\n\t\tname="{name}">')
        for key, value in keys:
            out.append(f'\t\t<Key\n\t\t\tname="{key}">{value}</Key>\n')
        out.append("\t</Section>\n")
    out.append("</Ini>")
    return "\n".join(out) + "\n"


def flat_displace(grid: int) -> bytes:
    """A terrain flat at :data:`FLAT_HEIGHT`, as the templates ship."""
    side = terrain_vertices(grid)
    return struct.pack("<f", FLAT_HEIGHT) * (side * side)


def neutral_colormap(grid: int) -> bytes:
    """One neutral, opaque sample per terrain vertex."""
    side = terrain_vertices(grid)
    return bytes(NEUTRAL_COLOUR) * (side * side)


def empty_grass() -> bytes:
    """``grass.xml``: an ``ecbnt,t`` container with no grass in it.

    Reproduced chunk for chunk from the templates — 0x01 holds the two
    counts, both zero; 0x02 and 0x03 are empty; 0xF001 names the group
    and 0xF002 is 2 on every shipped file.
    """
    name = b"Grass" + b"\x00" * 25
    return write_container(0, [
        Chunk(0x01, b"\x00" * 8),
        Chunk(0x02, b""),
        Chunk(0x03, b""),
        Chunk(0xF001, name),
        Chunk(0xF002, struct.pack("<I", 2)),
    ])


def single_tile_map(grid: int) -> bytes:
    """``level.tile`` naming one ground texture, used by every cell.

    Three chunks, not one. The payload under ``0x0BADF00D``, a 30-byte
    name chunk reading ``TILEMAP``, and ``0xF002`` holding 1 — the same
    trailer every other container in this module already wrote, and the
    one this function was missing.

    Measured on 41 ``level.tile`` files: the shipped maps and the five
    the original editor produced itself. All 41 carry the name and
    version chunks; the file this function used to write carried
    neither, and it is the only structural difference between a map
    this SDK creates and one the editor loads. The editor died right
    after ``Normal map loaded in:`` — and the tile load is the next
    thing ``Landscape::Load`` does, by the order of the three log
    strings in ``M3DEditor.exe`` (0x545FA1 normal map, 0x546261 tiles,
    0x5464CB shoreline).
    """
    root = DEFAULT_TILE_ROOT.encode("cp1251")
    tile = DEFAULT_TILE.encode("cp1251")

    block = struct.pack("<I", grid)
    block += struct.pack("<I", len(root)) + root + b"\x00"
    block += struct.pack("<I", 1)
    block += struct.pack("<I", len(tile)) + tile + b"\x00"
    # Four bytes a cell: the tile in the low byte, the rest zero, which
    # is what every shipped map writes.
    block += b"\x00" * (4 * grid * grid)
    return write_container(0x0B, [
        Chunk(0x0BADF00D, block),
        Chunk(0xF001, b"TILEMAP" + b"\x00" * 23),
        Chunk(0xF002, struct.pack("<I", 1)),
    ])


def flat_normal_map(grid: int) -> bytes:
    """``NormalMap.bin`` for a terrain with no slope anywhere.

    Container subtype 16, three chunks: the payload under
    ``0x0BADF00D``, a 30-byte name chunk reading ``RIV``, and
    ``0xF002`` holding 1. All three are the same in every shipped file.

    The payload is a ``uint32`` sample count followed by that many
    bytes, ``(8G+2)^2`` of them — verified on five sizes, see
    ``core.map_template.normal_map_side``.

    What ONE BYTE per sample means is НЕИЗВЕСТНО. What is measured is
    that on the shipped flat templates it is **zero** over the whole
    interior — 66054 of 66564 samples on ``32x32``, and the rest sit in
    two columns at one edge and differ in character between templates
    (``16x16``'s are indistinguishable from uninitialised memory).
    Zero is therefore what a flat map's normal map is made of, and
    reproducing the edge litter would be reproducing litter.
    """
    samples = normal_map_samples(grid)
    payload = struct.pack("<I", samples) + b"\x00" * samples
    return write_container(16, [
        Chunk(0x0BADF00D, payload),
        Chunk(0xF001, b"RIV" + b"\x00" * 27),
        Chunk(0xF002, struct.pack("<I", 1)),
    ])


def empty_shoreline() -> bytes:
    """``ShoreLine.bin`` with no shore in it.

    Same container shape as the shipped ones: chunk 1 opens with a
    count — 2, 19, 4 and 8 on the four templates — then a name chunk
    reading ``SFF`` and ``0xF002`` holding 1. The record layout behind
    that count is НЕИЗВЕСТНО and is not needed here: a new map's
    ``water.raw`` is empty, so it has no water, so it has no shoreline,
    so the count is zero and there is nothing to lay out.

    Size is not derived from the map's: the four templates are 2614,
    66618, 21422 and 22606 bytes, which follows the water they contain
    and nothing else.
    """
    return write_container(0, [
        Chunk(0x01, struct.pack("<I", 0)),
        Chunk(0xF001, b"SFF" + b"\x00" * 27),
        Chunk(0xF002, struct.pack("<I", 1)),
    ])


def create_map(
    maps_folder: str,
    map_name: str,
    grid: int,
    *,
    roads_source: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Write ``<maps_folder>/<map_name>/`` and its ``.ssl``.

    Returns what was written. Raises rather than overwriting an
    existing map unless asked: a map folder is hours of work and this
    is not the place to lose one.
    """
    shape = describe(grid)
    folder = os.path.join(maps_folder, map_name)
    manifest = os.path.join(maps_folder, f"{map_name}.ssl")

    if not overwrite and (os.path.exists(folder) or os.path.exists(manifest)):
        raise ParsingError(
            "a map of that name is already there",
            context=ErrorContext(
                source_file=folder,
                extra={"manifest": manifest, "folder_exists": os.path.exists(folder)},
            ),
        )

    os.makedirs(folder, exist_ok=True)
    written = []

    def put(name: str, payload) -> None:
        path = os.path.join(folder, name)
        mode = "wb" if isinstance(payload, (bytes, bytearray)) else "w"
        if mode == "wb":
            with open(path, "wb") as handle:
                handle.write(payload)
        else:
            with open(path, "w", encoding="cp1251", errors="replace",
                      newline=_NEWLINE) as handle:
                handle.write(payload)
        written.append(name)

    sizes = raster_sizes(grid)
    put("displace.bin", flat_displace(grid))
    put("colormap.raw", neutral_colormap(grid))
    put("passmap.raw", b"\x00" * sizes["passmap.raw"])
    put("water.raw", b"\x00" * sizes["water.raw"])
    put("level.tile", single_tile_map(grid))
    put("grass.xml", empty_grass())
    # Without these the editor logs a load failure and asserts.
    put("NormalMap.bin", flat_normal_map(grid))
    put("ShoreLine.bin", empty_shoreline())
    for name, text in _BOILERPLATE.items():
        put(name, text)

    copied = _copy_road_sets(folder, maps_folder, roads_source)
    if copied:
        written.append("roads.xml")

    with open(manifest, "w", encoding="cp1251", errors="replace",
              newline=_NEWLINE) as handle:
        handle.write(manifest_text(map_name, grid))

    logger.info(
        "created %s: %s grid, LEVELSIZE %s, %.0f units across, %s file(s)",
        map_name, grid, shape["level_size"], shape["span"], len(written),
    )
    if not copied:
        logger.warning(
            "roads.xml was not copied — the new map has no road sets. Copy "
            "one from any existing map folder if you want roads."
        )
    return {
        "folder": folder,
        "manifest": manifest,
        "files": written,
        "shape": shape,
        "roads_copied": bool(copied),
    }


def _copy_road_sets(folder: str, maps_folder: str, source: str | None) -> bool:
    """Bring in ``roads.xml``, the one file not written from the format.

    22 KB of road-set definitions, byte-identical in five of the six
    shipped maps. Copied rather than reproduced because it is content,
    not structure — and content this SDK did not author.
    """
    candidates = []
    if source:
        candidates.append(source)
    for name in ("32x32", "16x16", "8x8", "64x64", "r1m1"):
        candidates.append(os.path.join(maps_folder, name, "roads.xml"))

    for path in candidates:
        if path and os.path.isfile(path):
            try:
                shutil.copyfile(path, os.path.join(folder, "roads.xml"))
                return True
            except OSError as exc:
                logger.debug("could not copy %s: %s", path, exc)
    return False
