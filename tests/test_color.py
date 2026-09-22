# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the packed colour format.

The corpus assertions are the evidence, not decoration. ``colormap.raw``
is the only data in this project that distinguishes ARGB from ABGR, and
if a future change breaks the reading, the terrain turning blue is
exactly the symptom that would be dismissed as a shading problem.
"""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import color  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402

COLORMAP = corpus("colormap.raw")


# --- the packing ------------------------------------------------------


def test_the_worked_example_from_the_original_tooling() -> None:
    """``rgba_to_dec -r 0 -g 255 -b 0 -a 255`` prints ``-16711936``.

    Necessary and not sufficient: red and blue are both zero here, so
    ABGR gives the same integer. Kept because it pins the alpha
    position and the sign convention, which it does settle.
    """
    assert color.to_signed(color.pack_color(0, 255, 0, 255)) == -16711936
    assert color.unpack_color(-16711936) == (0, 255, 0, 255)


def test_a_case_where_argb_and_abgr_differ() -> None:
    """Opaque red. ARGB gives -65536, ABGR would give -16776961."""
    assert color.to_signed(color.pack_color(255, 0, 0, 255)) == -65536
    assert color.to_signed(color.pack_color(0, 0, 255, 255)) == -16776961


def test_alpha_occupies_the_top_byte() -> None:
    assert color.pack_color(0, 0, 0, 255) == 0xFF000000
    assert color.pack_color(0, 0, 0, 0) == 0


def test_disk_order_is_blue_green_red_alpha() -> None:
    """Little-endian ARGB, which is not the order the channels read in."""
    assert color.pack_bytes(1, 2, 3, 4) == bytes((3, 2, 1, 4))
    assert color.unpack_bytes(bytes((3, 2, 1, 4))) == (1, 2, 3, 4)


def test_reading_disk_bytes_positionally_would_swap_red_and_blue() -> None:
    """The bug this module exists to prevent, stated as a test."""
    raw = color.pack_bytes(200, 100, 50)
    positional = struct.unpack("<4B", raw)
    correct = color.unpack_bytes(raw)

    assert correct == (200, 100, 50, 255)
    assert positional[0] == 50 and positional[2] == 200
    assert positional[:3] != correct[:3]


def test_signed_and_unsigned_are_the_same_colour() -> None:
    for signed in (-16711936, -65536, -1, 0, 16711935):
        assert color.unpack_color(signed) == color.unpack_color(
            color.to_unsigned(signed)
        )
    assert color.to_signed(0xFFFFFFFF) == -1
    assert color.to_unsigned(-1) == 0xFFFFFFFF


def test_channels_outside_the_range_are_refused() -> None:
    for bad in ((256, 0, 0), (-1, 0, 0), (0, 0, 0, 300)):
        try:
            color.pack_color(*bad)
        except color.ColorError:
            continue
        raise AssertionError(f"{bad} packed anyway")


# --- Blender's floats -------------------------------------------------


def test_float_conversion_round_trips() -> None:
    for sample in ((0, 0, 0, 0), (255, 255, 255, 255), (200, 100, 50, 128)):
        assert color.from_float(color.to_float(sample)) == sample


def test_floats_outside_zero_to_one_clamp_rather_than_raise() -> None:
    assert color.from_float((-0.5, 0.5, 1.5, 1.0)) == (0, 128, 255, 255)


def test_a_short_float_tuple_is_filled_with_opaque_alpha() -> None:
    assert color.from_float((1.0, 0.0, 0.0)) == (255, 0, 0, 255)


# --- the measurement that settled the format --------------------------


def test_the_terrain_colormap_has_no_counterexample() -> None:
    """512x512 packed colours, one per heightfield vertex.

    Read as ARGB: red never below blue — browns and olives. Read as
    ABGR the same bytes give a terrain with no warm pixel anywhere. A
    single sample bluer than it is red would break the finding, so the
    assertion is on zero, not on a proportion.
    """
    if not os.path.isfile(COLORMAP):
        return

    data = open(COLORMAP, "rb").read()
    assert len(data) == 512 * 512 * 4

    samples = color.unpack_many(data)
    warm = sum(1 for r, _g, b, _a in samples if r > b)
    cool = sum(1 for r, _g, b, _a in samples if b > r)

    assert cool == 0, f"{cool} samples are bluer than they are red"
    assert warm > 0, "no colour information at all"
    assert all(a == 255 for _r, _g, _b, a in samples)


def test_the_colormap_round_trips_byte_exact() -> None:
    if not os.path.isfile(COLORMAP):
        return
    data = open(COLORMAP, "rb").read()
    assert color.pack_many(color.unpack_many(data)) == data


def test_unpack_many_matches_unpack_bytes_one_at_a_time() -> None:
    raw = color.pack_many([(10, 20, 30, 40), (255, 0, 0, 255), (0, 0, 0, 0)])
    assert color.unpack_many(raw) == [
        color.unpack_bytes(raw, 0),
        color.unpack_bytes(raw, 4),
        color.unpack_bytes(raw, 8),
    ]


def test_asking_for_more_colours_than_exist_is_refused() -> None:
    try:
        color.unpack_many(b"\0" * 8, count=4)
    except color.ColorError:
        return
    raise AssertionError("read past the end of the buffer")
