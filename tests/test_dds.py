# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the DDS codec.

The header assertions are the important ones. Quality can be argued
about; a header the engine's loader rejects makes the rest moot, and
the only evidence available for what it accepts is the two shipped
textures, whose 128-byte headers are byte-identical to each other.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import dds  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402

SHIPPED = ("factory_box.dds", "military_metal_walls.dds")


def _shipped(name: str) -> bytes | None:
    path = os.path.join(CORPUS, name)
    return open(path, "rb").read() if os.path.isfile(path) else None


def _gradient(width: int, height: int, alpha: bool = False) -> bytes:
    """A source with smooth ramps and hard edges, not already quantised."""
    out = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            o = (y * width + x) * 4
            r = int(127 + 120 * math.sin(x / 23.0) * math.cos(y / 31.0))
            if (x // 32 + y // 32) % 2 == 0:
                r += 40
            out[o] = max(0, min(255, r))
            out[o + 1] = y * 255 // max(1, height - 1)
            out[o + 2] = max(0, min(255, int(127 + 120 * math.sin((x + y) / 17.0))))
            out[o + 3] = (x * 255 // max(1, width - 1)) if alpha else 255
    return bytes(out)


# --- the header the game carries -------------------------------------


def test_our_header_is_the_shipped_header() -> None:
    """Byte-for-byte, for the case both shipped textures represent."""
    for name in SHIPPED:
        data = _shipped(name)
        if data is None:
            continue
        assert dds.build_header(256, 256, dds.DXT1, 9) == data[:128], name


def test_shipped_textures_read_as_expected() -> None:
    for name in SHIPPED:
        data = _shipped(name)
        if data is None:
            continue
        header = dds.read_header(data)
        assert header["format"] == dds.DXT1
        assert (header["width"], header["height"]) == (256, 256)
        assert header["levels"] == 9
        assert header["linear_size"] == 32768


def test_a_full_chain_is_the_size_the_game_file_is() -> None:
    """43704 bytes of payload for 256x256 DXT1 with nine levels."""
    data = _shipped("factory_box.dds")
    if data is None:
        return
    rgba, width, height = dds.decode(data)
    assert len(dds.encode(rgba, width, height, fmt=dds.DXT1)) == len(data)


def test_mip_chains_run_down_to_one_by_one() -> None:
    assert dds.mip_count(256, 256) == 9
    assert dds.mip_count(1, 1) == 1
    assert dds.mip_count(8, 4) == 4


# --- round trips ------------------------------------------------------


def test_re_encoding_a_shipped_texture_barely_moves_it() -> None:
    """A source already in DXT1's palette should survive re-encoding.

    Weak as a quality measure and strong as a correctness one: block
    layout, index packing or endpoint ordering wrong anywhere and this
    collapses.
    """
    data = _shipped("factory_box.dds")
    if data is None:
        return
    rgba, width, height = dds.decode(data)
    again, _w, _h = dds.decode(dds.encode(rgba, width, height, fmt=dds.DXT1))

    squared = sum(
        (rgba[i] - again[i]) ** 2 for i in range(len(rgba)) if i % 4 != 3
    )
    rmse = math.sqrt(squared / (len(rgba) * 3 // 4))
    assert rmse < 2.0, rmse


def test_quality_on_a_source_that_was_never_quantised() -> None:
    """Roughly 39 dB on gradients and edges; assert well clear of it."""
    source = _gradient(128, 128)
    decoded, _w, _h = dds.decode(dds.encode(source, 128, 128, fmt=dds.DXT1))

    squared = sum(
        (source[i] - decoded[i]) ** 2 for i in range(len(source)) if i % 4 != 3
    )
    rmse = math.sqrt(squared / (len(source) * 3 // 4))
    assert 20 * math.log10(255 / rmse) > 32.0, rmse


def test_alpha_survives_dxt5() -> None:
    source = _gradient(64, 64, alpha=True)
    assert dds.choose_format(source) == dds.DXT5

    decoded, _w, _h = dds.decode(dds.encode(source, 64, 64))
    worst = max(
        abs(source[i] - decoded[i]) for i in range(3, len(source), 4)
    )
    assert worst <= 20, worst


def test_uncompressed_is_lossless() -> None:
    source = _gradient(32, 32, alpha=True)
    encoded = dds.encode(source, 32, 32, fmt=dds.A8R8G8B8, mipmaps=False)
    decoded, _w, _h = dds.decode(encoded)
    assert bytes(decoded) == source


def test_an_opaque_image_picks_dxt1() -> None:
    assert dds.choose_format(_gradient(16, 16)) == dds.DXT1


# --- shapes and edges -------------------------------------------------


def test_sizes_that_are_not_multiples_of_four() -> None:
    """Blocks clamp at the edge rather than reading past the image."""
    for width, height in ((37, 19), (1, 1), (8, 4), (3, 5)):
        source = _gradient(width, height)
        encoded = dds.encode(source, width, height)
        header = dds.read_header(encoded)

        expected = dds.HEADER_SIZE
        w, h = width, height
        for _ in range(header["levels"]):
            expected += dds.level_size(w, h, dds.DXT1)
            w, h = max(1, w // 2), max(1, h // 2)

        assert len(encoded) == expected, (width, height)

        decoded, dw, dh = dds.decode(encoded)
        assert (dw, dh) == (width, height)
        assert len(decoded) == width * height * 4


def test_a_wrong_sized_buffer_is_refused() -> None:
    try:
        dds.encode(b"\0" * 10, 16, 16)
    except dds.DDSError:
        return
    raise AssertionError("a short buffer was encoded anyway")


def test_writing_produces_a_readable_file() -> None:
    source = _gradient(16, 16)
    with tempfile.TemporaryDirectory() as folder:
        target = os.path.join(folder, "out.dds")
        written = dds.write(target, source, 16, 16)
        assert written == os.path.getsize(target)
        assert dds.read_header(open(target, "rb").read())["format"] == dds.DXT1


def test_something_that_is_not_a_dds_is_reported() -> None:
    try:
        dds.read_header(b"not a dds file at all, really not" * 8)
    except dds.DDSError:
        return
    raise AssertionError("random bytes parsed as a DDS header")


# --- editing a texture and writing it back ------------------------------


def test_a_small_edit_does_not_damage_the_rest_of_the_image() -> None:
    """Re-encoding after a one-pixel change must not disturb other blocks.

    Measured on the corpus: the worst 4x4 block moves by under 4 of 255
    after a change to a single texel, on both DXT1 and DXT5.
    """
    for name in ("kustarnik_1.dds", "metal_elements_roof.dds", "factory_box.dds"):
        path = os.path.join(CORPUS, name)
        if not os.path.isfile(path):
            continue

        rgba, width, height = dds.decode(open(path, "rb").read())
        edited = bytearray(rgba)
        edited[0] = min(255, edited[0] + 10)

        again, _w, _h = dds.decode(dds.encode(bytes(edited), width, height))

        worst = 0
        for by in range(0, height, 4):
            for bx in range(0, width, 4):
                total = 0
                for y in range(4):
                    for x in range(4):
                        offset = ((by + y) * width + (bx + x)) * 4
                        for channel in range(4):
                            total += abs(rgba[offset + channel] - again[offset + channel])
                worst = max(worst, total / 64.0)

        assert worst < 8.0, (name, worst)


def test_no_block_falls_into_dxt1_three_colour_mode() -> None:
    """Equal endpoints make index 3 mean TRANSPARENT, not a fourth colour.

    A block that fell into it would come back with see-through texels,
    which is what a checkerboard showing through an edited texture
    looks like. Flat and near-flat blocks are where it would happen.
    """
    for colour in ((120, 120, 120), (120, 120, 121), (0, 0, 0), (255, 255, 255)):
        source = bytes(list(colour) + [255]) * 16
        encoded = dds.encode(source, 4, 4, fmt=dds.DXT1, mipmaps=False)
        c0, c1, _bits = struct.unpack_from("<HHI", encoded, dds.HEADER_SIZE)
        assert c0 > c1, (colour, c0, c1)

        decoded, _w, _h = dds.decode(encoded)
        assert set(decoded[3::4]) == {255}, colour


def test_a_flat_block_still_decodes_to_its_own_colour() -> None:
    """The nudge that keeps four-colour mode must be invisible."""
    source = bytes([120, 90, 60, 255]) * 16
    decoded, _w, _h = dds.decode(
        dds.encode(source, 4, 4, fmt=dds.DXT1, mipmaps=False)
    )
    for channel, expected in enumerate((120, 90, 60)):
        assert abs(decoded[channel] - expected) <= 4, (channel, decoded[channel])
