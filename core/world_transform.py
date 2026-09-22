# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The confirmed world-unit transform: game units <-> Blender world units.

Sits strictly between two other layers, and must not be duplicated
into either of them:

    formats/exm/terrain.py          (lossless, 1:1 with the game file)
              |
              v  HeightmapData, game units, untouched
    core/world_transform.py   <-- this module
              |
              v  HeightmapData, Blender world units
    blender_io/terrain_bridge.py    (mechanical mesh construction only)

Confirmed findings that motivate keeping this separate (see the
architecture doc changelog):

- ``displace.bin`` already stores real float32 heights, confirmed
  against the official Ex Machina conversion script. The "terrain too
  tall in Blender" problem is a units-conversion problem, not a codec
  bug — so the fix belongs in a units layer, not in the codec.
- "Ground Level = 255" is confirmed to be an in-game editor concept,
  never read or written by the official converter — it is NOT part of
  the file format, and therefore is deliberately NOT part of this
  transform's model (kept as a separate, optional, editor-level
  adjustment at the call site instead, since it isn't a "world unit"
  question at all).

This module builds on ``core.terrain_transform``'s existing
``scale_xy``/``scale_height`` rather than reimplementing the
arithmetic — this module is the *when/why* (confirmed game <-> Blender
unit conversion), ``terrain_transform`` is the *how* (generic,
independently-toggleable per-axis operations, also used for the
still-unresolved orientation question).

Deliberately game-agnostic — no ``bpy``, no Ex-Machina-specific
naming. The same ``WorldTransform`` type is meant to apply to a future
Command & Conquer terrain pipeline (RAW16 -> float32 -> Ex Machina, see
the architecture doc's roadmap) with a different calibrated
``xy_scale``/``height_scale``, reusing this module and
``blender_io/terrain_bridge.py`` completely unchanged — only the codec
producing the initial ``HeightmapData`` differs per game.
"""

from __future__ import annotations

import dataclasses

from core.terrain import HeightmapData
from core.terrain_transform import scale_height, scale_xy
from utils.errors import ValidationError


@dataclasses.dataclass(frozen=True)
class WorldTransform:
    """A confirmed-scope scale between a game's native terrain units and
    Blender world units.

    Neither field has a calibrated value yet — both default to ``1.0``
    (no-op), per the SDK-wide "no unverified default" rule. Determining
    the real values is the next research step (see the architecture
    doc), done by comparing ``blender_io.terrain_bridge.log_terrain_summary``'s
    output against a map's known real-world size — not something this
    class guesses.

    Parameters
    ----------
    xy_scale:
        Blender world units per game XY unit.
    height_scale:
        Blender world units per game height unit.
    """

    xy_scale: float = 1.0
    height_scale: float = 1.0

    def to_world(self, heightmap: HeightmapData) -> HeightmapData:
        """Game units -> Blender world units (the import direction).

        Called with the untouched output of ``formats/exm/terrain.py``'s
        ``read_displace()`` — before ``blender_io.terrain_bridge.build_mesh()``,
        never inside it.
        """
        return scale_height(scale_xy(heightmap, self.xy_scale), self.height_scale)

    def to_game(self, heightmap: HeightmapData) -> HeightmapData:
        """Blender world units -> game units (the export direction).

        The exact inverse of ``to_world``. Called with the raw output of
        ``blender_io.terrain_bridge.extract_heightmap()`` — before
        ``formats/exm/terrain.py``'s ``write_displace()``, never inside it.

        Raises
        ------
        ValidationError
            If either scale factor is 0 (cannot invert).
        """
        if self.xy_scale == 0:
            raise ValidationError("xy_scale is 0 — cannot invert (division by zero)")
        if self.height_scale == 0:
            raise ValidationError("height_scale is 0 — cannot invert (division by zero)")
        return scale_xy(scale_height(heightmap, 1.0 / self.height_scale), 1.0 / self.xy_scale)
