# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the lighting every map states and nothing was reading."""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from core.lighting import ASCENSION_KEYS, read_lighting  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402

MANIFEST = corpus("r1m1-1-1.ssl")


class _Manifest:
    def __init__(self, text: str) -> None:
        self._keys = dict(re.findall(r'name="(\w+)">([^<]*)</Key>', text))

    def file_ref(self, key):
        return self._keys.get(key)


def _sample():
    if not os.path.isfile(MANIFEST):
        return None
    text = open(MANIFEST, "rb").read().decode("cp1251", errors="replace")
    return read_lighting(_Manifest(text))


def test_the_map_states_where_its_sun_is() -> None:
    lighting = _sample()
    if lighting is None:
        return
    assert lighting.azimuth == 45.0
    assert lighting.ascension == 55.0


def test_the_time_of_day_picks_which_ascension_applies() -> None:
    """``CURRENTDAYTIME`` 1 on the sample map, so the midday figure."""
    assert ASCENSION_KEYS[1] == "SUN_DAY_ASCENTION"

    text = '<Key name="CURRENTDAYTIME">2</Key><Key name="SUN_SET_ASCENTION">127.000</Key>'
    lighting = read_lighting(_Manifest(text))
    assert lighting is not None
    assert lighting.ascension == 127.0


def test_models_and_the_landscape_get_different_colours() -> None:
    """Deliberate: the ground carries a baked lightmap already, so its
    share of the sun has been taken out of the ``LS_*`` figures."""
    lighting = _sample()
    if lighting is None:
        return
    assert lighting.model_ambient != lighting.landscape_ambient
    assert lighting.model_diffuse != lighting.landscape_diffuse


def test_rubbish_written_out_as_a_float_is_ignored() -> None:
    """``LS_TFACTOR`` reads 1.86e34 — uninitialised memory."""
    lighting = read_lighting(
        _Manifest('<Key name="SUN_AZIMUTH">1.86e34</Key>')
    )
    assert lighting is None


def test_a_manifest_with_no_lighting_states_none() -> None:
    assert read_lighting(_Manifest("")) is None
    assert read_lighting(None) is None


def test_a_malformed_colour_is_refused_rather_than_guessed() -> None:
    for bad in ("101 116", "101 116 44 7", "a b c", "300 0 0", "-1 0 0"):
        lighting = read_lighting(
            _Manifest(f'<Key name="MODEL_AMBIENT">{bad}</Key>')
        )
        assert lighting is None, bad


def test_the_sun_points_where_the_map_says() -> None:
    """A lamp aims down its own negative Z, so a sun 55 degrees above
    the horizon is tilted 35 from straight down."""
    import math

    lighting = _sample()
    if lighting is None:
        return

    tilt, roll, turn = lighting.sun_rotation()
    assert math.isclose(math.degrees(tilt), 35.0, abs_tol=0.01)
    assert roll == 0.0
    assert math.isclose(math.degrees(turn), 45.0, abs_tol=0.01)


def test_the_lamp_takes_its_colour_and_strength_from_the_map() -> None:
    from blender_io.scene_bridge import build_lighting

    lighting = _sample()
    if lighting is None:
        return

    import bpy

    collection = bpy.data.collections.new("World")
    sun = build_lighting(lighting, collection)

    assert sun is not None
    assert tuple(sun.data.color) == lighting.model_diffuse
    assert sun.data.energy > 0.0
