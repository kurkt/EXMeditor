# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The map's own lighting, from its manifest.

Every level states where its sun is and what colour its light is, and
none of it was being read. The scene was lit by whatever Blender
defaults to, which is why the two views differed in warmth even once
the textures matched::

    SUN_AZIMUTH          45.000    degrees
    SUN_DAY_ASCENTION    55.000    the sun's height at midday
    SUN_RISE_ASCENTION   45.000
    SUN_SET_ASCENTION   127.000
    CURRENTDAYTIME            1    which of those three applies

    MODEL_AMBIENT   101 116 44     ambient on models
    MODEL_DIFFUSE    40 68 50      the sun's own colour, on models
    LS_COLOR         97 83 101     ambient on the landscape
    LS_DIFFUSE      101 108 112    the sun's colour on the landscape

Models and the landscape are lit with different colours, which is a
choice rather than an oversight: the ground carries a baked lightmap
already, and its share of the sun has been taken out of these.

``LS_TFACTOR`` reads 1.86e34 on the sample map — uninitialised memory
written out as a float. It is ignored rather than interpreted.
"""

from __future__ import annotations

import dataclasses
import math

#: ``CURRENTDAYTIME`` to the ascension key that applies.
ASCENSION_KEYS = {
    0: "SUN_RISE_ASCENTION",
    1: "SUN_DAY_ASCENTION",
    2: "SUN_SET_ASCENTION",
}

#: A value this large is not a setting.
_IMPLAUSIBLE = 1.0e6


@dataclasses.dataclass
class Lighting:
    """Where the sun is and what colour the light is."""

    azimuth: float = 45.0
    ascension: float = 55.0
    model_ambient: tuple = (0.5, 0.5, 0.5)
    model_diffuse: tuple = (1.0, 1.0, 1.0)
    landscape_ambient: tuple = (0.5, 0.5, 0.5)
    landscape_diffuse: tuple = (1.0, 1.0, 1.0)

    def sun_rotation(self) -> tuple:
        """Euler angles for a Blender sun lamp.

        A lamp points down its own negative Z. Tilting it by
        ``90 - ascension`` from straight down puts it at the stated
        height above the horizon, and turning it by the azimuth aims it.
        """
        tilt = math.radians(90.0 - self.ascension)
        turn = math.radians(self.azimuth)
        return (tilt, 0.0, turn)

    def sun_strength(self) -> float:
        """How bright to make the lamp.

        Taken from the diffuse colour's own brightness, so a map that
        states a dim sun gets one. Normalised to the brightest channel
        so the colour carries the hue and this carries the amount.
        """
        return max(self.model_diffuse) if any(self.model_diffuse) else 1.0


def read_lighting(manifest) -> Lighting | None:
    """Read the lighting keys, or None when the map states none."""
    if manifest is None:
        return None

    lighting = Lighting()
    found = False

    daytime = _number(manifest, "CURRENTDAYTIME")
    key = ASCENSION_KEYS.get(int(daytime) if daytime is not None else 1, "SUN_DAY_ASCENTION")

    azimuth = _number(manifest, "SUN_AZIMUTH")
    if azimuth is not None:
        lighting.azimuth = azimuth
        found = True

    ascension = _number(manifest, key)
    if ascension is not None:
        lighting.ascension = ascension
        found = True

    for attribute, name in (
        ("model_ambient", "MODEL_AMBIENT"),
        ("model_diffuse", "MODEL_DIFFUSE"),
        ("landscape_ambient", "LS_COLOR"),
        ("landscape_diffuse", "LS_DIFFUSE"),
    ):
        colour = _colour(manifest, name)
        if colour is not None:
            setattr(lighting, attribute, colour)
            found = True

    return lighting if found else None


def _number(manifest, key: str) -> float | None:
    raw = manifest.file_ref(key)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # LS_TFACTOR-style rubbish: uninitialised memory as a float.
    return None if abs(value) > _IMPLAUSIBLE else value


def _colour(manifest, key: str) -> tuple | None:
    """``"101 116 44"`` to a 0..1 triple."""
    raw = manifest.file_ref(key)
    if not raw:
        return None
    parts = raw.split()
    if len(parts) != 3:
        return None
    try:
        values = [int(p) for p in parts]
    except ValueError:
        return None
    if any(v < 0 or v > 255 for v in values):
        return None
    return tuple(v / 255.0 for v in values)
