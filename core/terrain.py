# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Terrain domain model.

``HeightmapData`` is the in-memory representation of a terrain grid —
what ``formats/exm/terrain.py`` produces by reading ``displace.bin``,
and what ``blender_io/terrain_bridge.py`` turns into (and extracts
back out of) a Blender mesh. Nothing here knows about ``displace.bin``
specifically, or about ``bpy`` — see the architecture doc's
``core``/``formats``/``blender_io`` split.
"""

from __future__ import annotations

import array
import dataclasses
import math as _math

from utils.errors import ErrorContext, ValidationError
from utils.math import AABB, Vector3


@dataclasses.dataclass
class HeightmapData:
    """A regular-grid heightmap.

    Parameters
    ----------
    width, height:
        Grid dimensions, in number of samples (not world units).
    cell_size:
        World-space distance between two adjacent grid samples, along
        both axes. Assumed uniform (same spacing on both axes); a
        non-uniform cell size can be added if a future format needs it.
    values:
        Flat, row-major array of height samples: ``values[y * width + x]``
        is the height at grid cell ``(x, y)``. Kept as ``array.array('f')``
        (not a list) since this can be hundreds of thousands of floats
        for a large map — confirmed Ex Machina ``displace.bin`` grids
        are square but scale with the map's declared size (see
        ``formats/exm/terrain.py``), not a fixed dimension — and
        ``array`` is both more
        memory-compact and what ``utils.binary``'s bulk float32 read/write
        already produces/consumes directly.

    Note on coordinate convention: grid cell ``(x, y)`` maps to a local
    3D position via ``(x * cell_size, y * cell_size, height)`` — i.e.
    the height value becomes this type's own Z. This is a *local*,
    format-independent convention for this dataclass only; converting
    that into Blender's (or any other target's) actual axis convention
    is `utils.math.Transform`'s job, applied in `blender_io`, not here.
    """

    width: int
    height: int
    cell_size: float
    values: array.array

    def __post_init__(self) -> None:
        expected_len = self.width * self.height
        if len(self.values) != expected_len:
            raise ValidationError(
                "heightmap values length does not match width*height",
                context=ErrorContext(
                    extra={
                        "width": self.width,
                        "height": self.height,
                        "expected_length": expected_len,
                        "actual_length": len(self.values),
                    }
                ),
            )

    @classmethod
    def filled(cls, width: int, height: int, cell_size: float, fill: float = 0.0) -> "HeightmapData":
        """Build a ``width`` x ``height`` heightmap with every sample set to ``fill``.

        Convenient for tests and for "new terrain" creation before any
        file has been read.
        """
        return cls(width, height, cell_size, array.array("f", [fill]) * (width * height))

    def _index(self, x: int, y: int) -> int:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValidationError(
                "heightmap coordinate out of range",
                context=ErrorContext(
                    extra={"x": x, "y": y, "width": self.width, "height": self.height}
                ),
            )
        return y * self.width + x

    def get_height(self, x: int, y: int) -> float:
        """Return the height sample at grid cell ``(x, y)``."""
        return self.values[self._index(x, y)]

    def set_height(self, x: int, y: int, value: float) -> None:
        """Set the height sample at grid cell ``(x, y)``."""
        self.values[self._index(x, y)] = value

    def local_position(self, x: int, y: int) -> Vector3:
        """Return the local-space position (see class docstring) of cell ``(x, y)``."""
        return Vector3(x * self.cell_size, y * self.cell_size, self.get_height(x, y))

    def validate(
        self,
        *,
        expected_width: int | None = None,
        expected_height: int | None = None,
    ) -> None:
        """Raise ``ValidationError`` if this heightmap is not writable as-is.

        Checks performed:
        - dimensions match ``expected_width``/``expected_height`` if given
          (e.g. a format that requires a specific size — that check
          belongs to the calling ``formats/`` module, keeping any
          "must be exactly NxM" rule at the format layer rather than
          hardcoded into this generic type; Ex Machina's confirmed
          ``displace.bin`` rule is "square", not a fixed size — see
          ``formats/exm/terrain.py``).
        - every value is a finite float (no NaN/Infinity), since a
          heightmap containing either would produce a corrupt or
          crash-inducing binary export.

        Intended to run *before* handing this data to a binary writer —
        see the terrain export data flow in the architecture doc.
        """
        if expected_width is not None and self.width != expected_width:
            raise ValidationError(
                "heightmap width does not match the required size",
                context=ErrorContext(
                    extra={"width": self.width, "expected_width": expected_width}
                ),
            )
        if expected_height is not None and self.height != expected_height:
            raise ValidationError(
                "heightmap height does not match the required size",
                context=ErrorContext(
                    extra={"height": self.height, "expected_height": expected_height}
                ),
            )
        for i, value in enumerate(self.values):
            if not _math.isfinite(value):
                y, x = divmod(i, self.width)
                raise ValidationError(
                    "heightmap contains a non-finite value (NaN or Infinity)",
                    context=ErrorContext(extra={"x": x, "y": y, "value": value}),
                )

    def sample_at_world(self, x: float, y: float, cell_size: float | None = None) -> float:
        """Return the interpolated height at a continuous world position.

        ``x``/``y`` are game-space horizontal coordinates (not grid
        indices); ``cell_size`` defaults to this heightmap's own.
        Bilinear interpolation between the four surrounding samples,
        clamped at the grid edges so a position slightly outside the
        map returns the nearest edge height rather than raising.

        Needed to place ``world.xml`` objects: their ``org`` Y component
        is a height *offset above the terrain surface* (confirmed —
        object Y spans only ±82 while absolute terrain heights are
        227..541), so the absolute height is this sample plus that
        offset.
        """
        step = cell_size if cell_size is not None else self.cell_size
        if step == 0:
            raise ValidationError("cell_size is 0 — cannot sample terrain")

        gx = x / step
        gy = y / step
        # Clamp into the grid; positions outside the map get the edge value.
        gx = min(max(gx, 0.0), self.width - 1.0)
        gy = min(max(gy, 0.0), self.height - 1.0)

        x0 = int(gx)
        y0 = int(gy)
        x1 = min(x0 + 1, self.width - 1)
        y1 = min(y0 + 1, self.height - 1)
        fx = gx - x0
        fy = gy - y0

        h00 = self.get_height(x0, y0)
        h10 = self.get_height(x1, y0)
        h01 = self.get_height(x0, y1)
        h11 = self.get_height(x1, y1)

        top = h00 + (h10 - h00) * fx
        bottom = h01 + (h11 - h01) * fx
        return top + (bottom - top) * fy

    def local_bounds(self) -> AABB:
        """Return the AABB of this heightmap in its own local convention
        (see class docstring) — used by ``MapScene.bounding_box()``.

        Computed directly from grid extents + min/max height rather than
        via ``AABB.from_points`` over every sample, since that would be
        an unnecessary O(width*height) vector allocation for a shape
        that's already known to be a regular grid.
        """
        min_height = min(self.values)
        max_height = max(self.values)
        min_corner = Vector3(0.0, 0.0, min_height)
        max_corner = Vector3(
            (self.width - 1) * self.cell_size,
            (self.height - 1) * self.cell_size,
            max_height,
        )
        return AABB(min_corner, max_corner)
