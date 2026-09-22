# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generic raster map layers (``water.raw``, ``passmap.raw``,
``colormap.raw``, ``cameramap.raw``).

All four are the same shape of thing — a fixed-size grid of
fixed-width samples with no header — so they share one type and one
codec rather than getting four near-identical modules (see the v0.2
plan's §6.1). What differs is only the grid size, sample width, and
how a caller chooses to *interpret* the samples; none of that requires
separate parsing code.

Confirmed layer geometry, from real map data:

===============  ============  ==========  ===================================
File             Size (bytes)  Grid        Sample
===============  ============  ==========  ===================================
``water.raw``    32768         128 x 128   uint16 LE (see ``WATER_LEVEL_SCALE``)
``passmap.raw``  65536         256 x 256   uint8
``colormap.raw`` 1048576       512 x 512   packed ARGB (see ``core.color``)
``cameramap.raw``32768         128 x 128   uint16 LE *(Предположение — same
                                            shape as water.raw; contents were
                                            all-zero in the sample map, so the
                                            sample width is inferred from size
                                            alone, not observed)*
===============  ============  ==========  ===================================

No ``bpy`` import — usable from a CLI, tests, or a converter.
"""

from __future__ import annotations

from core import color as color_codec

import dataclasses

#: Multiplier converting a raw ``water.raw`` uint16 sample to a world
#: height in the same units as ``displace.bin``.
#:
#: **Подтверждено** against real map data: the two distinct nonzero
#: values in a real ``water.raw`` decode exactly to the two water-level
#: keys in the same map's ``.ssl``, to three decimal places —
#: ``2387 * 0.12 = 286.440`` = ``WATERLEVEL`` and
#: ``2388 * 0.12 = 286.560`` = ``BASEWATERLEVEL``. Two independent
#: values matching two independent manifest keys through one shared
#: constant is not a coincidence.
WATER_LEVEL_SCALE = 0.12


@dataclasses.dataclass
class RasterLayer:
    """A fixed-size grid of fixed-width samples, stored verbatim.

    ``data`` is kept as raw bytes rather than decoded into a list of
    numbers: for round-trip export the bytes are what matter, and for
    the layers whose per-sample meaning isn't needed (colormap,
    cameramap) decoding would be wasted work. Callers that need actual
    values use the accessors below, which decode on demand.
    """

    width: int
    height: int
    bytes_per_sample: int
    data: bytes

    def __post_init__(self) -> None:
        expected = self.width * self.height * self.bytes_per_sample
        if len(self.data) != expected:
            raise ValueError(
                f"raster data length {len(self.data)} does not match "
                f"{self.width}x{self.height} at {self.bytes_per_sample} bytes/sample "
                f"(expected {expected})"
            )

    @property
    def sample_count(self) -> int:
        return self.width * self.height

    def sample_u8(self, x: int, y: int) -> int:
        """Read a 1-byte sample at grid cell ``(x, y)``."""
        if self.bytes_per_sample != 1:
            raise ValueError(f"layer has {self.bytes_per_sample}-byte samples, not 1")
        return self.data[y * self.width + x]

    def sample_u16(self, x: int, y: int) -> int:
        """Read a 2-byte little-endian sample at grid cell ``(x, y)``."""
        if self.bytes_per_sample != 2:
            raise ValueError(f"layer has {self.bytes_per_sample}-byte samples, not 2")
        offset = (y * self.width + x) * 2
        return int.from_bytes(self.data[offset:offset + 2], "little")

    def water_height(self, x: int, y: int) -> float | None:
        """Decode a ``water.raw`` sample into a world height.

        Returns ``None`` where the sample is 0 — confirmed to mean "no
        water here" rather than "water at height zero" (a real map's
        water mask is ~6.6% covered, and the zero region forms the
        land, not a sea at altitude 0).
        """
        raw = self.sample_u16(x, y)
        if raw == 0:
            return None
        return raw * WATER_LEVEL_SCALE

    def color_at(self, x: int, y: int) -> tuple[int, int, int, int]:
        """Read a packed colour sample at grid cell ``(x, y)``.

        Returns ``(red, green, blue, alpha)`` in 0..255 — channel
        order, which is not the order the bytes sit in. See
        ``core.color``: the file stores B, G, R, A.
        """
        if self.bytes_per_sample != 4:
            raise ValueError(
                f"layer has {self.bytes_per_sample}-byte samples, not 4"
            )
        return color_codec.unpack_bytes(self.data, (y * self.width + x) * 4)

    def colors(self) -> list[tuple[int, int, int, int]]:
        """Every sample decoded, row-major.

        Decoded in one pass rather than per-sample: a 512x512 colormap
        is 262144 samples, and a Python call each would dominate the
        import.
        """
        if self.bytes_per_sample != 4:
            raise ValueError(
                f"layer has {self.bytes_per_sample}-byte samples, not 4"
            )
        return color_codec.unpack_many(self.data)

    def is_empty(self) -> bool:
        """True if every byte is zero — i.e. the layer is unused on this map.

        Worth checking before building anything from a layer: several
        layers ship all-zero on maps that don't use them, and silently
        importing an all-zero layer as geometry produces a confusing
        flat artifact rather than nothing.
        """
        return not any(self.data)
