# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The parsed `.ssl` level manifest.

Confirmed format: an Ini-like document wrapped in XML tags
(`<Ini><Section name="LEVEL"><Key name="...">value</Key>...`),
`windows-1251` encoded — see the architecture doc's forensic report on
`r1m1-1-1.ssl`. This is the SDK's entry point for auto-discovering the
rest of a map's files (see `formats/exm/ssl.py`), which is why it's
modeled as its own small domain type rather than folded into
`core/scene.py`.
"""

from __future__ import annotations

import dataclasses

# Keys confirmed to name another map file, in `.ssl`'s own casing.
# `formats/exm/ssl.py` uses this list to know which raw_keys entries
# are "file references" worth resolving during scan()/load() (see the
# architecture doc's §2) — every other key is calibration data or
# something not yet given SDK-level meaning.
FILE_REFERENCE_KEYS = frozenset({
    "HIGHMAP", "ROADMAP", "ROADSET", "OBJECTNAMES", "WAYPOINTS",
    "PASSMAP", "COLORMAP", "DETMAP", "CAMERAMAP", "CLIFFMAP", "CLIFFSET",
    "NORMALMAP", "TILES", "PLAYERPASSMAP", "STATICOBSTACLES",
    "SERVERDYN", "SERVERQUESTSTATES", "MODELNAMES", "SERVEREXTERNALPATHS",
    "SERVERS", "STATICSERVERS", "TRIGGERSNAME", "CINEMATRIGGERSNAME",
    "DLGSTRINGS", "SHORELINE", "WEATHERDETAIL",
})

# Confirmed NOT present in any `.ssl` key, resolved by the engine via a
# fixed filename in the map folder instead — see the architecture
# doc's file-relationship report. `formats/exm/ssl.py`'s scan() checks
# these explicitly, in addition to whatever FILE_REFERENCE_KEYS yields.
FIXED_FILENAME_EXCEPTIONS = (
    "world.xml",
    "camera_paths.xml",
    "grass.xml",
    "prefabs.xml",
    "dialogs.xml",
)


@dataclasses.dataclass
class LevelManifest:
    """The parsed contents of a map's `.ssl` file.

    Named calibration fields are pulled out for convenient typed
    access; everything else (including every file reference) stays in
    ``raw_keys`` as plain strings — this type doesn't try to model the
    full `.ssl` schema, only the parts the SDK currently has a use for.
    Adding a new named field later is a small, additive change; nothing
    needs the full key list modeled up front.
    """

    raw_keys: dict[str, str] = dataclasses.field(default_factory=dict)

    level_size: int | None = None
    water_level: float | None = None
    base_water_level: float | None = None
    passmap_cell_size: int | None = None
    # Stored as float, not int: real maps write these as "40.000" /
    # "4056.000" — decimal-formatted even though the values happen to be
    # whole numbers. Parsing them as int fails on real data.
    min_safe_x: float | None = None
    min_safe_y: float | None = None
    max_safe_x: float | None = None
    max_safe_y: float | None = None

    @property
    def safe_bounds(self) -> tuple[float, float, float, float] | None:
        """The playable area as ``(min_x, min_y, max_x, max_y)``.

        ``None`` unless the manifest declares all four corners — a
        partial declaration would be worse than none, since a validator
        would report objects outside a boundary the map never set.

        On the reference map this is 40..4056, against a terrain that
        spans 0..4088: the playable area stops one grid cell short of
        the edge on each side.
        """
        corners = (self.min_safe_x, self.min_safe_y, self.max_safe_x, self.max_safe_y)
        if any(value is None for value in corners):
            return None
        return corners

    def file_ref(self, key: str) -> str | None:
        """Return the raw filename/path for a manifest key, or ``None``
        if that key isn't present in this map's `.ssl` — the caller
        decides whether an absent key is optional or a problem."""
        return self.raw_keys.get(key)
