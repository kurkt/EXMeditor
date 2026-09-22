# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `core/raster.py` and `formats/exm/raster.py`.

Covers all four raster layers with one shared codec, plus the
confirmed `water.raw` height decoding (raw uint16 * 0.12 == the .ssl's
WATERLEVEL/BASEWATERLEVEL, matched to three decimals on real data).
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.raster import WATER_LEVEL_SCALE, RasterLayer  # noqa: E402
from formats.exm.raster import LAYER_GEOMETRY, read_raster, write_raster  # noqa: E402
from utils.errors import ParsingError  # noqa: E402


def _layer_file(width: int, height: int, bps: int, fill: bytes | None = None) -> str:
    """Write a layer file of the correct size for the given geometry.

    ``fill`` is a per-sample byte pattern and must be exactly ``bps``
    bytes long; it's repeated once per sample. Defaults to zeros.
    """
    if fill is None:
        fill = b"\x00" * bps
    if len(fill) != bps:
        raise ValueError(f"fill must be exactly {bps} bytes to be one sample, got {len(fill)}")
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "layer.raw")
    with open(path, "wb") as f:
        f.write(fill * (width * height))
    return path


# --- RasterLayer ---


def test_rejects_wrong_data_length() -> None:
    try:
        RasterLayer(width=4, height=4, bytes_per_sample=1, data=b"\x00" * 15)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_sample_u8_indexing() -> None:
    # 3x2 grid, values 0..5 laid out row-major
    layer = RasterLayer(width=3, height=2, bytes_per_sample=1, data=bytes([0, 1, 2, 3, 4, 5]))
    assert layer.sample_u8(0, 0) == 0
    assert layer.sample_u8(2, 0) == 2
    assert layer.sample_u8(0, 1) == 3
    assert layer.sample_u8(2, 1) == 5


def test_sample_u16_indexing_little_endian() -> None:
    # two samples: 0x0102 = 258, 0x0304 = 772 (little-endian)
    layer = RasterLayer(width=2, height=1, bytes_per_sample=2, data=bytes([0x02, 0x01, 0x04, 0x03]))
    assert layer.sample_u16(0, 0) == 258
    assert layer.sample_u16(1, 0) == 772


def test_sample_width_mismatch_raises() -> None:
    layer = RasterLayer(width=2, height=1, bytes_per_sample=1, data=b"\x01\x02")
    try:
        layer.sample_u16(0, 0)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_water_height_decodes_to_ssl_water_level() -> None:
    """Regression on the confirmed finding: raw 2387 and 2388 decode to
    exactly the WATERLEVEL (286.440) and BASEWATERLEVEL (286.560) keys
    from the same map's .ssl."""
    layer = RasterLayer(
        width=2, height=1, bytes_per_sample=2,
        data=(2387).to_bytes(2, "little") + (2388).to_bytes(2, "little"),
    )
    assert abs(layer.water_height(0, 0) - 286.440) < 0.001
    assert abs(layer.water_height(1, 0) - 286.560) < 0.001


def test_water_height_zero_means_no_water_not_altitude_zero() -> None:
    layer = RasterLayer(width=1, height=1, bytes_per_sample=2, data=b"\x00\x00")
    assert layer.water_height(0, 0) is None


def test_is_empty() -> None:
    assert RasterLayer(width=2, height=2, bytes_per_sample=1, data=b"\x00" * 4).is_empty()
    assert not RasterLayer(width=2, height=2, bytes_per_sample=1, data=b"\x00\x00\x01\x00").is_empty()


def test_water_level_scale_constant() -> None:
    assert WATER_LEVEL_SCALE == 0.12


# --- codec ---


def test_reads_each_known_layer_geometry() -> None:
    for key, (w, h, bps) in LAYER_GEOMETRY.items():
        path = _layer_file(w, h, bps)
        layer = read_raster(path, key)
        assert (layer.width, layer.height, layer.bytes_per_sample) == (w, h, bps)


def test_rejects_unknown_manifest_key() -> None:
    path = _layer_file(4, 4, 1)
    try:
        read_raster(path, "NOT_A_LAYER")
        raise AssertionError("expected ParsingError")
    except ParsingError as e:
        assert "not a known raster layer" in e.message


def test_rejects_size_mismatch() -> None:
    """A file that's the wrong size for its layer is rejected rather
    than reshaped — a mismatch means a different map-size variant or
    the wrong file entirely, and guessing between those risks silently
    misreading the data."""
    path = _layer_file(4, 4, 1)  # far too small for PASSMAP's 256x256
    try:
        read_raster(path, "PASSMAP")
        raise AssertionError("expected ParsingError")
    except ParsingError as e:
        assert "does not match" in e.message


def test_rejects_missing_file() -> None:
    try:
        read_raster("/no/such/file.raw", "PASSMAP")
        raise AssertionError("expected ParsingError")
    except ParsingError:
        pass


def test_round_trip_is_byte_identical() -> None:
    w, h, bps = LAYER_GEOMETRY["DETMAP"]
    src = _layer_file(w, h, bps, fill=b"\x53\x09")
    original = read_raster(src, "DETMAP")

    out = os.path.join(tempfile.mkdtemp(), "water.raw")
    write_raster(out, original)
    reread = read_raster(out, "DETMAP")
    assert reread.data == original.data


def test_write_rejects_missing_directory() -> None:
    layer = RasterLayer(width=2, height=2, bytes_per_sample=1, data=b"\x00" * 4)
    try:
        write_raster("/no/such/dir/out.raw", layer)
        raise AssertionError("expected ParsingError")
    except ParsingError:
        pass


_ALL_TESTS = (
    test_rejects_wrong_data_length,
    test_sample_u8_indexing,
    test_sample_u16_indexing_little_endian,
    test_sample_width_mismatch_raises,
    test_water_height_decodes_to_ssl_water_level,
    test_water_height_zero_means_no_water_not_altitude_zero,
    test_is_empty,
    test_water_level_scale_constant,
    test_reads_each_known_layer_geometry,
    test_rejects_unknown_manifest_key,
    test_rejects_size_mismatch,
    test_rejects_missing_file,
    test_round_trip_is_byte_identical,
    test_write_rejects_missing_directory,
)


if __name__ == "__main__":
    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            test_fn()
        except Exception as exc:  # noqa: BLE001 - test runner, want to catch everything
            failures += 1
            print(f"FAIL: {test_fn.__name__}: {exc}")
        else:
            print(f"PASS: {test_fn.__name__}")
    print(f"\n{len(_ALL_TESTS) - failures}/{len(_ALL_TESTS)} passed")
    sys.exit(1 if failures else 0)
