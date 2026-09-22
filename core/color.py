# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The one place a colour changes representation.

The format, and how it was settled
----------------------------------

The engine stores a colour as a 32-bit value packed **ARGB**::

    bits 31..24  alpha
    bits 23..16  red
    bits 15..8   green
    bits 7..0    blue

Little-endian, so on disk the bytes run **B, G, R, A**. This is
Direct3D's ``D3DCOLOR`` / ``D3DFMT_A8R8G8B8``, which is what a 2005
DirectX 9 title would use, but "it would make sense" is not why this is
written down.

Two independent measurements settled it, after a long stretch where it
could not be settled at all:

**The engine names the format.** The editor's startup log reports its
uncompressed texture format as ``A8R8G8B8``.

**The terrain colour map has no counterexample.** ``colormap.raw`` is
512x512 packed colours, one per heightfield vertex — the same grid as
``displace.bin``. Across all 262144 samples:

===========================================  ========  ========
Relation                                     Samples   Share
===========================================  ========  ========
byte at bits 23..16 > byte at bits 7..0         58674    22.38%
byte at bits 7..0 > byte at bits 23..16             0     0.00%
equal                                          203470    77.62%
===========================================  ========  ========

Read as ARGB, red is never below blue: browns and olives, which is
what a wasteland terrain map contains. Read as ABGR the same bytes
give a terrain with no warm pixel anywhere, blue-shifted without a
single exception in a quarter of a million samples. Only one of those
is a real map.

Why this took so long, and why it is worth a module
---------------------------------------------------

Every earlier sample was **grey**. The one worked example in the
original tooling used ``r=0 g=255 b=0``, where red and blue are both
zero and ARGB and ABGR produce the identical integer. Every vertex
colour in ``civilhouse1`` — all 907 of them, 17 distinct values — is
greyscale too, because it is baked shading rather than paint. Two
independent sources, neither carrying one bit of information about the
question. That is not bad luck twice; it is what happens when the
thing being measured is only visible in data nobody had yet looked at.

So: one module, and no packing arithmetic anywhere else. The bug this
prevents is not hypothetical. ``gam.py`` read vertex colours with
``unpack_from("<4B")`` — disk order, B first — and handed the tuple to
Blender as ``(r, g, b, a)``, swapping red and blue on every model.
Nothing showed it, because every colour it had ever been given was
grey.

Signed or unsigned
------------------

The storage is 32 bits with no sign; alpha simply occupies the top
byte, so any colour with alpha above 127 has the high bit set and
prints as a negative number in a signed reader. Game tooling prints
these signed — ``-16711936`` for opaque green — and XML holds them the
same way, so both are accepted on input and :func:`to_signed` exists
for output. The sign is a display convention, not part of the format.
"""

from __future__ import annotations

import struct

#: Bit positions of each channel in the packed value.
ALPHA_SHIFT = 24
RED_SHIFT = 16
GREEN_SHIFT = 8
BLUE_SHIFT = 0

#: Byte order as written to disk, little-endian.
DISK_ORDER = ("blue", "green", "red", "alpha")

_MASK = 0xFFFFFFFF
_SIGN_BIT = 0x80000000


class ColorError(ValueError):
    """A channel value or packed colour outside what the format holds."""


def _check_channel(name: str, value: int) -> int:
    if not isinstance(value, int) or not 0 <= value <= 255:
        raise ColorError(f"{name} must be an integer in 0..255, got {value!r}")
    return value


def to_unsigned(value: int) -> int:
    """Normalise a packed colour to its unsigned 32-bit form.

    Accepts either convention, so callers reading XML or the output of
    the original tooling do not each have to remember which they got.
    """
    if not isinstance(value, int):
        raise ColorError(f"packed colour must be an integer, got {value!r}")
    value &= _MASK
    return value


def to_signed(value: int) -> int:
    """The same colour as a signed 32-bit integer.

    For writing values back in the form the game's own tools print.
    """
    value = to_unsigned(value)
    return value - (1 << 32) if value & _SIGN_BIT else value


def pack_color(red: int, green: int, blue: int, alpha: int = 255) -> int:
    """Channels in 0..255 to one unsigned ARGB integer."""
    _check_channel("red", red)
    _check_channel("green", green)
    _check_channel("blue", blue)
    _check_channel("alpha", alpha)
    return (
        (alpha << ALPHA_SHIFT)
        | (red << RED_SHIFT)
        | (green << GREEN_SHIFT)
        | (blue << BLUE_SHIFT)
    )


def unpack_color(value: int) -> tuple[int, int, int, int]:
    """One packed colour to ``(red, green, blue, alpha)``.

    Signed input is accepted; the return is always channel order, never
    disk order, so callers cannot accidentally treat one as the other.
    """
    value = to_unsigned(value)
    return (
        (value >> RED_SHIFT) & 0xFF,
        (value >> GREEN_SHIFT) & 0xFF,
        (value >> BLUE_SHIFT) & 0xFF,
        (value >> ALPHA_SHIFT) & 0xFF,
    )


# The names the original tooling used, kept so the two vocabularies
# do not drift apart.
rgba_to_game = pack_color
game_to_rgba = unpack_color


def pack_bytes(red: int, green: int, blue: int, alpha: int = 255) -> bytes:
    """Channels to the four bytes as they appear on disk: B, G, R, A."""
    return struct.pack(
        "<I", pack_color(red, green, blue, alpha)
    )


def unpack_bytes(data: bytes, offset: int = 0) -> tuple[int, int, int, int]:
    """Four bytes from a file to ``(red, green, blue, alpha)``.

    The whole point of this function is that the bytes are **not** in
    that order on disk. Reading them with ``unpack_from("<4B")`` and
    calling the result RGBA is the specific mistake this module exists
    to stop.
    """
    if len(data) - offset < 4:
        raise ColorError(f"need 4 bytes at offset {offset}, have {len(data) - offset}")
    return unpack_color(struct.unpack_from("<I", data, offset)[0])


def unpack_many(data: bytes, offset: int = 0, count: int | None = None):
    """Decode a run of packed colours.

    For raster layers, where doing this per-sample in a loop of Python
    calls is the difference between a fraction of a second and several.
    """
    available = (len(data) - offset) // 4
    if count is None:
        count = available
    if count > available:
        raise ColorError(f"asked for {count} colours, {available} available")

    values = struct.unpack_from(f"<{count}I", data, offset)
    return [
        (
            (v >> RED_SHIFT) & 0xFF,
            (v >> GREEN_SHIFT) & 0xFF,
            (v >> BLUE_SHIFT) & 0xFF,
            (v >> ALPHA_SHIFT) & 0xFF,
        )
        for v in values
    ]


def pack_many(colors) -> bytes:
    """Encode a sequence of ``(r, g, b, a)`` tuples to disk bytes."""
    values = [pack_color(*c) if len(c) == 4 else pack_color(*c, 255) for c in colors]
    return struct.pack(f"<{len(values)}I", *values)


# --- Blender's 0..1 floats -------------------------------------------


def to_float(color: tuple[int, int, int, int]) -> tuple[float, float, float, float]:
    """Channels in 0..255 to Blender's 0..1, channel order preserved."""
    return tuple(component / 255.0 for component in color)


def from_float(color) -> tuple[int, int, int, int]:
    """Blender's 0..1 back to 0..255, clamped and rounded.

    Clamping rather than raising: Blender's colour attributes are
    floats and can hold values outside 0..1 after arithmetic, and a
    value slightly over 1.0 should become 255, not an exception on
    export.
    """
    out = []
    for component in color:
        value = int(component * 255.0 + 0.5)
        out.append(0 if value < 0 else (255 if value > 255 else value))
    while len(out) < 4:
        out.append(255)
    return tuple(out[:4])


def describe(value: int) -> str:
    """A packed colour rendered for a diagnostic report.

    Shows the disk bytes alongside the channels, because when something
    is wrong with a colour the question is almost always which of the
    two is being looked at.
    """
    unsigned = to_unsigned(value)
    red, green, blue, alpha = unpack_color(unsigned)
    disk = struct.pack("<I", unsigned)
    return (
        f"0x{unsigned:08X} (signed {to_signed(unsigned)}) "
        f"R={red} G={green} B={blue} A={alpha} "
        f"disk bytes {' '.join(f'{b:02x}' for b in disk)}"
    )
