# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""A ``.ssl`` is four sections, and the lighting is not in the first one.

LEVEL carries SUN_AZIMUTH and the ascensions; ILLUMINATION carries
MODEL_AMBIENT, MODEL_DIFFUSE, LS_COLOR and LS_DIFFUSE. The reader took
only LEVEL, so every map was lit with a white sun and a grey ambient
however green its own numbers were — and the game's foliage textures,
which are greyscale in the shipped files, came out grey with it.

Measured: across the 41 manifests in the corpus no key name appears in
two sections, so merging them loses nothing.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.lighting import read_lighting  # noqa: E402
from formats.exm.ssl import read_manifest  # noqa: E402
from utils.errors import ParsingError  # noqa: E402

_MANIFEST = """<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>
<Ini>
    <Section name="LEVEL">
        <Key name="LEVELSIZE">32</Key>
        <Key name="SUN_AZIMUTH">45.000</Key>
        <Key name="SUN_DAY_ASCENTION">55.000</Key>
        <Key name="CURRENTDAYTIME">1</Key>
        <Key name="HIGHMAP">displace.bin</Key>
    </Section>
    <Section name="CAMERA">
        <Key name="CAM_FOV">60.000</Key>
    </Section>
    <Section name="ILLUMINATION">
        <Key name="MODEL_AMBIENT">101 116 44</Key>
        <Key name="MODEL_DIFFUSE">40 68 50</Key>
        <Key name="LS_COLOR">97 83 101</Key>
        <Key name="LS_DIFFUSE">101 108 112</Key>
        <Key name="LS_TFACTOR">18605364804791530000000000000000000.000</Key>
    </Section>
</Ini>
"""


def _write(text: str = _MANIFEST) -> str:
    path = os.path.join(tempfile.mkdtemp(), "map.ssl")
    with open(path, "wb") as handle:
        handle.write(text.encode("cp1251", errors="replace"))
    return path


def test_keys_outside_level_are_read() -> None:
    manifest = read_manifest(_write())

    assert manifest.file_ref("SUN_AZIMUTH") == "45.000"
    assert manifest.file_ref("MODEL_AMBIENT") == "101 116 44"
    assert manifest.file_ref("CAM_FOV") == "60.000"


def test_the_map_lights_itself_in_its_own_colours() -> None:
    """The regression, in the units the file writes them in."""
    lighting = read_lighting(read_manifest(_write()))

    assert lighting is not None
    assert [round(c * 255) for c in lighting.model_ambient] == [101, 116, 44]
    assert [round(c * 255) for c in lighting.model_diffuse] == [40, 68, 50]
    assert [round(c * 255) for c in lighting.landscape_ambient] == [97, 83, 101]
    assert [round(c * 255) for c in lighting.landscape_diffuse] == [101, 108, 112]

    # And the angles, which were never the problem, still come from LEVEL.
    assert lighting.azimuth == 45.0
    assert lighting.ascension == 55.0


def test_level_still_wins_a_name_it_shares() -> None:
    """No shipped manifest has a clash, so nothing depends on which
    section wins — but the one the reader has always used should."""
    text = _MANIFEST.replace(
        '<Key name="CAM_FOV">60.000</Key>',
        '<Key name="SUN_AZIMUTH">999.000</Key>',
    )
    manifest = read_manifest(_write(text))

    assert manifest.file_ref("SUN_AZIMUTH") == "45.000"


def test_a_manifest_with_no_level_section_is_still_refused() -> None:
    """Merging sections must not turn "this is not a manifest" into a
    partial read of whatever was in the file."""
    text = _MANIFEST.replace('name="LEVEL"', 'name="SOMETHINGELSE"')
    try:
        read_manifest(_write(text))
    except ParsingError:
        return
    raise AssertionError("a file with no LEVEL section was accepted")


def test_uninitialised_memory_is_still_ignored() -> None:
    """LS_TFACTOR is 1.86e34 on every map. Reading more of the file
    must not start believing it."""
    lighting = read_lighting(read_manifest(_write()))

    assert lighting is not None
    for value in lighting.model_ambient + lighting.model_diffuse:
        assert 0.0 <= value <= 1.0
