# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Codec for the raw raster map layers.

One reader/writer for all four layers (``water.raw``, ``passmap.raw``,
``colormap.raw``, ``cameramap.raw``) — see ``core.raster`` for why
they share a type. These files have no header at all: the grid
dimensions are known per-layer, not stored, so this module holds the
confirmed geometry table and validates file size against it.
"""

from __future__ import annotations

import os

from core.raster import RasterLayer
from utils.errors import ErrorContext, ParsingError

#: Confirmed layer geometry: manifest key -> (width, height, bytes_per_sample).
#: Keyed by the ``.ssl`` manifest key rather than the filename, since
#: filenames vary per map but the manifest keys don't.
#:
#: CAMERAMAP's sample width is inferred from its file size matching
#: water.raw's exactly; its contents were all-zero in the only map seen,
#: so this is *Предположение*, not confirmed. It round-trips correctly
#: either way (the bytes are preserved regardless of how they're
#: grouped), so a wrong guess here can't corrupt an export — it would
#: only affect interpretation, which nothing currently does.
LAYER_GEOMETRY: dict[str, tuple[int, int, int]] = {
    "DETMAP": (128, 128, 2),      # water.raw
    "PASSMAP": (256, 256, 1),     # passmap.raw
    "COLORMAP": (512, 512, 4),    # colormap.raw
    "CAMERAMAP": (128, 128, 2),   # cameramap.raw
}


def read_raster(path: str, manifest_key: str) -> RasterLayer:
    """Read a raster layer file, validating its size against known geometry.

    Parameters
    ----------
    manifest_key:
        The ``.ssl`` key this file was referenced by (``"DETMAP"``,
        ``"PASSMAP"``, ...), which determines the expected geometry.

    Raises
    ------
    ParsingError
        If ``manifest_key`` isn't a known raster layer, the file can't
        be read, or its size doesn't match the expected geometry —
        a size mismatch means either a different map-size variant than
        any seen so far, or the wrong file, and guessing between those
        would risk silently misreading the data.
    """
    geometry = LAYER_GEOMETRY.get(manifest_key)
    if geometry is None:
        raise ParsingError(
            f"{manifest_key!r} is not a known raster layer",
            context=ErrorContext(source_file=path, extra={"known": sorted(LAYER_GEOMETRY)}),
        )
    width, height, bytes_per_sample = geometry

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise ParsingError(
            "could not read raster layer",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc

    expected = width * height * bytes_per_sample
    if len(data) != expected:
        raise ParsingError(
            f"raster layer size does not match the expected {width}x{height} "
            f"grid at {bytes_per_sample} bytes/sample",
            context=ErrorContext(
                source_file=path,
                extra={"expected_bytes": expected, "actual_bytes": len(data), "key": manifest_key},
            ),
        )

    return RasterLayer(width=width, height=height, bytes_per_sample=bytes_per_sample, data=data)


def write_raster(path: str, layer: RasterLayer) -> None:
    """Write a raster layer back out verbatim.

    Lossless by construction: ``RasterLayer.data`` is the original
    bytes, so this is a plain write with no re-encoding step that could
    introduce a difference.
    """
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        raise ParsingError(
            "output directory does not exist",
            context=ErrorContext(source_file=path, extra={"directory": directory}),
        )
    try:
        with open(path, "wb") as f:
            f.write(layer.data)
    except OSError as exc:
        raise ParsingError(
            "could not write raster layer",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc
