# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reading and writing DDS textures, with no external converter.

Why this exists
---------------

Blender 3.6 cannot write DDS. Every shipped texture measured is DDS,
and the engine does not accept Targa in its place, so an image that
lives only inside a ``.blend`` had nowhere to go: the SDK saved it as
``.tga`` and warned that the game would probably ignore it. This module
closes that gap without asking anyone to run a separate tool.

What the game actually uses
---------------------------

Two shipped textures, ``factory_box.dds`` and
``military_metal_walls.dds``, have **byte-identical 128-byte headers**:
DXT1, 256x256, nine mip levels down to 1x1, ``DDSD_LINEARSIZE`` with
the level-0 size in ``dwPitchOrLinearSize``, caps
``COMPLEX|TEXTURE|MIPMAP``. That header is reproduced field for field
below rather than assembled from what the DDS specification permits,
because what the specification permits and what a 2005 engine's loader
accepts are different sets.

Neither texture uses DXT1's one-bit alpha. Across 8192 blocks, not one
sits in the three-colour mode with a transparent index, and not one has
``c0 == c1``. So the encoder stays in four-colour mode, which is both
simpler and higher quality.

For images that do carry alpha the encoder switches to DXT5, following
the engine's own choice: the editor's startup log reports its RGBA
texture format as DXT5 and its uncompressed fallback as A8R8G8B8.

Quality and cost
----------------

Endpoints come from the block's colour bounding box, inset to pull them
off the extremes, then refined once by least squares against the
indices actually chosen. Measured against a synthetic source with
gradients and hard edges — deliberately not a source that was already
DXT1-quantised, which would flatter any encoder — this reaches roughly
39 dB PSNR, in the range good DXT1 encoders occupy.

It is pure Python and costs about 0.3 s per 256x256 level on an
ordinary machine, so a full mip chain is under half a second and a
1024x1024 texture takes several. That is slow next to a C encoder and
irrelevant next to the alternative, which was leaving the artist to run
one by hand.
"""

from __future__ import annotations

import struct

MAGIC = b"DDS "
HEADER_SIZE = 128

DXT1 = "DXT1"
DXT5 = "DXT5"
A8R8G8B8 = "A8R8G8B8"

#: Header flags carried by both shipped textures: CAPS, HEIGHT, WIDTH,
#: PIXELFORMAT, MIPMAPCOUNT, LINEARSIZE.
_FLAGS_COMPRESSED = 0x0A1007

#: The same, with PITCH instead of LINEARSIZE, for uncompressed data.
_FLAGS_UNCOMPRESSED = 0x0A1007 ^ 0x080000 | 0x000008

#: COMPLEX | TEXTURE | MIPMAP, as both shipped textures carry.
_CAPS_MIPMAPPED = 0x401008

#: TEXTURE alone, for a single-level image.
_CAPS_FLAT = 0x001000

_PF_FOURCC = 0x4
_PF_RGBA = 0x41


class DDSError(ValueError):
    """A DDS file this module cannot read, or an image it cannot write."""


# --- geometry --------------------------------------------------------


def _blocks(width: int, height: int) -> tuple[int, int]:
    return max(1, (width + 3) // 4), max(1, (height + 3) // 4)


def level_size(width: int, height: int, fmt: str) -> int:
    """Bytes occupied by one mip level."""
    if fmt == A8R8G8B8:
        return width * height * 4
    bx, by = _blocks(width, height)
    return bx * by * (8 if fmt == DXT1 else 16)


def mip_count(width: int, height: int) -> int:
    """Levels in a full chain down to 1x1, as both shipped files carry."""
    count = 1
    while width > 1 or height > 1:
        width = max(1, width // 2)
        height = max(1, height // 2)
        count += 1
    return count


def _halve(rgba: bytes, width: int, height: int) -> tuple[bytearray, int, int]:
    """Box-filter down one level.

    A 2x2 average. Odd dimensions clamp rather than skip, so a
    non-power-of-two texture still produces a complete chain instead of
    losing its last column.
    """
    new_w = max(1, width // 2)
    new_h = max(1, height // 2)
    out = bytearray(new_w * new_h * 4)

    for y in range(new_h):
        y0 = min(2 * y, height - 1)
        y1 = min(2 * y + 1, height - 1)
        for x in range(new_w):
            x0 = min(2 * x, width - 1)
            x1 = min(2 * x + 1, width - 1)
            base = (y * new_w + x) * 4
            for c in range(4):
                total = (
                    rgba[(y0 * width + x0) * 4 + c]
                    + rgba[(y0 * width + x1) * 4 + c]
                    + rgba[(y1 * width + x0) * 4 + c]
                    + rgba[(y1 * width + x1) * 4 + c]
                )
                out[base + c] = (total + 2) // 4

    return out, new_w, new_h


# --- colour packing --------------------------------------------------


def _to565(r: int, g: int, b: int) -> int:
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _from565(value: int) -> tuple[int, int, int]:
    r = (value >> 11) & 31
    g = (value >> 5) & 63
    b = value & 31
    return (r * 255 + 15) // 31, (g * 255 + 31) // 63, (b * 255 + 15) // 31


_REFIT_WEIGHTS = (1.0, 0.0, 2.0 / 3.0, 1.0 / 3.0)


def _encode_colour_block(pixels: list[tuple[int, int, int]]) -> bytes:
    """One 8-byte DXT colour block, always in four-colour mode.

    Three-colour mode buys a transparent index this codec never needs
    and costs an interpolated colour, so ``c0 > c1`` is maintained
    throughout. Where the block is flat the two endpoints coincide,
    which decodes correctly either way.
    """
    lo = [min(p[i] for p in pixels) for i in range(3)]
    hi = [max(p[i] for p in pixels) for i in range(3)]

    # Inset the bounding box. The extremes are usually one outlying
    # pixel, and spending an endpoint on it costs the other fifteen.
    for i in range(3):
        inset = (hi[i] - lo[i]) // 16
        lo[i] = min(255, lo[i] + inset)
        hi[i] = max(0, hi[i] - inset)

    c0 = _to565(*hi)
    c1 = _to565(*lo)
    if c0 < c1:
        c0, c1 = c1, c0

    best: tuple[int, int, int, list[int]] | None = None

    for _ in range(2):
        p0 = _from565(c0)
        p1 = _from565(c1)
        palette = (
            p0,
            p1,
            tuple((2 * p0[i] + p1[i]) // 3 for i in range(3)),
            tuple((p0[i] + 2 * p1[i]) // 3 for i in range(3)),
        )

        indices: list[int] = []
        error = 0
        for pixel in pixels:
            closest = 0
            closest_distance = None
            for k, entry in enumerate(palette):
                distance = (
                    (pixel[0] - entry[0]) ** 2
                    + (pixel[1] - entry[1]) ** 2
                    + (pixel[2] - entry[2]) ** 2
                )
                if closest_distance is None or distance < closest_distance:
                    closest_distance = distance
                    closest = k
            indices.append(closest)
            error += closest_distance or 0

        if best is None or error < best[0]:
            best = (error, c0, c1, list(indices))

        # Least-squares refit: given which palette entry each pixel
        # chose, solve for the endpoints that minimise the error, then
        # requantise and let the next pass reassign.
        a = b = ab = 0.0
        ax = [0.0, 0.0, 0.0]
        bx = [0.0, 0.0, 0.0]
        for k, pixel in zip(indices, pixels):
            w = _REFIT_WEIGHTS[k]
            a += w * w
            b += (1.0 - w) * (1.0 - w)
            ab += w * (1.0 - w)
            for i in range(3):
                ax[i] += w * pixel[i]
                bx[i] += (1.0 - w) * pixel[i]

        determinant = a * b - ab * ab
        if abs(determinant) < 1e-6:
            break

        new_hi = [
            max(0, min(255, round((b * ax[i] - ab * bx[i]) / determinant)))
            for i in range(3)
        ]
        new_lo = [
            max(0, min(255, round((a * bx[i] - ab * ax[i]) / determinant)))
            for i in range(3)
        ]
        n0 = _to565(*new_hi)
        n1 = _to565(*new_lo)
        if n0 < n1:
            n0, n1 = n1, n0
        if (n0, n1) == (c0, c1):
            break
        c0, c1 = n0, n1

    assert best is not None
    _error, c0, c1, indices = best

    if c0 == c1:
        # Equal endpoints put a DXT1 decoder into three-colour mode,
        # where index 3 means TRANSPARENT rather than a fourth colour.
        # This encoder never intends that, and a block that fell into
        # it would come back with see-through texels — which is what a
        # checkerboard showing through an edited texture looks like.
        # Nudging the lower endpoint down by one 565 step keeps the
        # block in four-colour mode and is invisible.
        if c1 > 0:
            c1 -= 1
        else:
            c0 += 1

    bits = 0
    for i, k in enumerate(indices):
        bits |= k << (2 * i)
    return struct.pack("<HHI", c0, c1, bits)


def _encode_alpha_block(alphas: list[int]) -> bytes:
    """One 8-byte DXT5 alpha block, eight-value interpolation mode."""
    a0 = max(alphas)
    a1 = min(alphas)
    if a0 == a1:
        # Flat alpha: endpoints equal, every index 0. Keeping a0 >= a1
        # stays in the eight-value mode and avoids the six-value one,
        # whose two extra entries are hard 0 and 255.
        return struct.pack("<BB", a0, a1) + b"\x00" * 6

    step = (a0 - a1) / 7.0
    indices: list[int] = []
    for alpha in alphas:
        position = round((a0 - alpha) / step)
        position = max(0, min(7, position))
        # Index 0 is a0 and index 1 is a1; 2..7 walk between them.
        indices.append(0 if position == 0 else (1 if position == 7 else position + 1))

    bits = 0
    for i, k in enumerate(indices):
        bits |= k << (3 * i)
    return struct.pack("<BB", a0, a1) + bits.to_bytes(6, "little")


def _gather_block(
    rgba, width: int, height: int, bx: int, by: int
) -> tuple[list[tuple[int, int, int]], list[int]]:
    """The 4x4 texels at a block position, clamped at the edges."""
    colours: list[tuple[int, int, int]] = []
    alphas: list[int] = []
    for y in range(4):
        sy = min(by + y, height - 1)
        for x in range(4):
            sx = min(bx + x, width - 1)
            o = (sy * width + sx) * 4
            colours.append((rgba[o], rgba[o + 1], rgba[o + 2]))
            alphas.append(rgba[o + 3])
    return colours, alphas


def encode_level(rgba, width: int, height: int, fmt: str) -> bytes:
    """Compress one mip level."""
    if fmt == A8R8G8B8:
        out = bytearray(width * height * 4)
        for i in range(0, len(out), 4):
            # A8R8G8B8 little-endian on disk is B, G, R, A.
            out[i] = rgba[i + 2]
            out[i + 1] = rgba[i + 1]
            out[i + 2] = rgba[i]
            out[i + 3] = rgba[i + 3]
        return bytes(out)

    out = bytearray()
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            colours, alphas = _gather_block(rgba, width, height, bx, by)
            if fmt == DXT5:
                out += _encode_alpha_block(alphas)
            out += _encode_colour_block(colours)
    return bytes(out)


def choose_format(rgba) -> str:
    """DXT1 for opaque images, DXT5 for anything with alpha.

    The engine's own choice, from the editor's startup log: it reports
    DXT5 as its RGBA texture format. Every shipped texture measured is
    opaque and DXT1.
    """
    return DXT1 if all(rgba[i] == 255 for i in range(3, len(rgba), 4)) else DXT5


def build_header(width: int, height: int, fmt: str, levels: int) -> bytes:
    """The 128-byte header, matching the shipped textures field for field."""
    compressed = fmt != A8R8G8B8
    flags = _FLAGS_COMPRESSED if compressed else _FLAGS_UNCOMPRESSED
    if levels <= 1:
        flags &= ~0x020000  # MIPMAPCOUNT

    header = bytearray(MAGIC)
    header += struct.pack(
        "<7I",
        124,
        flags,
        height,
        width,
        level_size(width, height, fmt) if compressed else width * 4,
        0,
        levels,
    )
    header += b"\0" * 44  # dwReserved1[11], zero in both shipped files

    if compressed:
        header += struct.pack("<2I", 32, _PF_FOURCC)
        header += fmt.encode("ascii")
        header += struct.pack("<5I", 0, 0, 0, 0, 0)
    else:
        header += struct.pack("<2I", 32, _PF_RGBA)
        header += b"\0" * 4
        header += struct.pack(
            "<5I", 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000
        )

    header += struct.pack(
        "<4I", _CAPS_MIPMAPPED if levels > 1 else _CAPS_FLAT, 0, 0, 0
    )
    header += struct.pack("<I", 0)

    assert len(header) == HEADER_SIZE
    return bytes(header)


def encode(
    rgba,
    width: int,
    height: int,
    fmt: str | None = None,
    mipmaps: bool = True,
) -> bytes:
    """Build a complete DDS file from top-left-origin RGBA bytes.

    ``rgba`` is row-major, four bytes per pixel, first row at the top —
    the same orientation DDS itself stores, so nothing is flipped here.
    Callers reading from Blender, whose images start at the bottom, flip
    before calling.
    """
    expected = width * height * 4
    if len(rgba) != expected:
        raise DDSError(
            f"{width}x{height} RGBA needs {expected} bytes, got {len(rgba)}"
        )
    if width < 1 or height < 1:
        raise DDSError(f"bad image size {width}x{height}")

    if fmt is None:
        fmt = choose_format(rgba)
    if fmt not in (DXT1, DXT5, A8R8G8B8):
        raise DDSError(f"unsupported format {fmt!r}")

    levels = mip_count(width, height) if mipmaps else 1
    out = bytearray(build_header(width, height, fmt, levels))

    level = rgba
    w, h = width, height
    for index in range(levels):
        if index:
            level, w, h = _halve(level, w, h)
        out += encode_level(level, w, h, fmt)

    return bytes(out)


def write(path: str, rgba, width: int, height: int, **kwargs) -> int:
    """Encode and write to disk. Returns the byte count."""
    data = encode(rgba, width, height, **kwargs)
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


# --- reading ---------------------------------------------------------


def has_alpha(data: bytes) -> bool:
    """Whether a DDS carries transparency worth wiring up.

    Asked of the file rather than of Blender's report, because the
    answer decides whether a material gets an alpha connection at all,
    and a texture judged opaque by mistake renders as a solid black
    quad where the game shows leaves.

    DXT3 and DXT5 store alpha explicitly and always count. DXT1 is the
    interesting case: it *can* carry one-bit punch-through, so the
    blocks are checked rather than assumed. Neither shipped DXT1
    texture measured uses it — across 8192 blocks, not one sits in the
    three-colour mode with a transparent index — but two samples do not
    make a rule, and the scan costs milliseconds.
    """
    header = read_header(data)
    fmt = header["format"]

    if fmt in (DXT5, "DXT3", "DXT2", "DXT4"):
        return True

    if fmt == A8R8G8B8:
        # Slicing the alpha bytes out runs at C speed; a Python loop
        # over a 1024x1024 level would not.
        size = header["width"] * header["height"] * 4
        alphas = data[HEADER_SIZE + 3 : HEADER_SIZE + size : 4]
        return bool(alphas) and min(alphas) != 255

    if fmt != DXT1:
        return False

    bx, by = _blocks(header["width"], header["height"])
    end = HEADER_SIZE + bx * by * 8
    for c0, c1, indices in struct.iter_unpack("<HHI", data[HEADER_SIZE:end]):
        if c0 > c1:
            continue  # four-colour mode: no transparent index exists
        if any((indices >> (2 * i)) & 3 == 3 for i in range(16)):
            return True
    return False


#: A texture whose alpha never reaches this is not a cutout: nothing in
#: it is meant to be solid.
_OPAQUE = 250

#: And one with no texel this low has nothing meant to be see-through.
_CLEAR = 5

#: How much of a texture must be solid before its alpha is read as
#: transparency. Well below the measured cutout and well above the
#: measured mask, so the two do not come near it.
_CUTOUT_OPAQUE_SHARE = 0.02


def alpha_profile(data: bytes) -> tuple[float, float]:
    """What share of a texture's alpha is solid, and what share is clear.

    Decoded from the top mip only — enough to characterise a mask, and
    a great deal cheaper than the whole chain.
    """
    header = read_header(data)
    if not has_alpha(data):
        return 1.0, 0.0

    rgba = decode_level(
        data, HEADER_SIZE, header["width"], header["height"], header["format"]
    )
    alphas = rgba[3::4]
    if not alphas:
        return 1.0, 0.0

    total = len(alphas)
    opaque = sum(1 for a in alphas if a >= _OPAQUE)
    clear = sum(1 for a in alphas if a <= _CLEAR)
    return opaque / total, clear / total


def alpha_is_cutout(data: bytes) -> bool:
    """Whether a texture's alpha means transparency rather than gloss.

    A cutout has solid parts and see-through parts: a leaf is opaque,
    the gap beside it is not. A gloss mask has neither — it is a
    continuous ramp that never commits to either end, because it is
    answering "how shiny" rather than "is this here".

    Measured on the two DXT5 textures in the corpus, and they do not sit
    near each other::

        kustarnik_1.dds          28.2% solid, 49.1% clear   cutout
        metal_elements_roof.dds   0.0% solid, 29.6% clear   gloss mask

    Not one texel of the roof texture is opaque. Read as transparency it
    makes a roof you can see straight through, which is exactly what it
    did.
    """
    opaque, clear = alpha_profile(data)
    return opaque >= _CUTOUT_OPAQUE_SHARE and clear > 0.0


def read_header(data: bytes) -> dict:
    """Parse the header, without decoding pixels."""
    if len(data) < HEADER_SIZE or data[:4] != MAGIC:
        raise DDSError("not a DDS file")

    size, flags, height, width, pitch, _depth, levels = struct.unpack_from(
        "<7I", data, 4
    )
    if size != 124:
        raise DDSError(f"unexpected header size {size}")

    pf_flags = struct.unpack_from("<I", data, 80)[0]
    fourcc = data[84:88]
    bit_count = struct.unpack_from("<I", data, 88)[0]

    if pf_flags & _PF_FOURCC:
        fmt = fourcc.decode("ascii", errors="replace")
    elif bit_count == 32:
        fmt = A8R8G8B8
    else:
        raise DDSError(f"unsupported pixel format (flags {pf_flags:#x})")

    return {
        "width": width,
        "height": height,
        "format": fmt,
        "levels": max(1, levels),
        "flags": flags,
        "linear_size": pitch,
    }


def decode_level(data: bytes, offset: int, width: int, height: int, fmt: str):
    """Decode one mip level into top-left-origin RGBA bytes."""
    out = bytearray(width * height * 4)

    if fmt == A8R8G8B8:
        for i in range(0, width * height * 4, 4):
            out[i] = data[offset + i + 2]
            out[i + 1] = data[offset + i + 1]
            out[i + 2] = data[offset + i]
            out[i + 3] = data[offset + i + 3]
        return out

    if fmt not in (DXT1, DXT5):
        raise DDSError(f"cannot decode {fmt}")

    block_size = 8 if fmt == DXT1 else 16
    at = offset

    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            alphas = None
            if fmt == DXT5:
                a0, a1 = data[at], data[at + 1]
                bits = int.from_bytes(data[at + 2 : at + 8], "little")
                if a0 > a1:
                    table = [a0, a1] + [
                        ((7 - k) * a0 + k * a1) // 7 for k in range(1, 7)
                    ]
                else:
                    table = (
                        [a0, a1]
                        + [((5 - k) * a0 + k * a1) // 5 for k in range(1, 5)]
                        + [0, 255]
                    )
                alphas = [table[(bits >> (3 * i)) & 7] for i in range(16)]

            colour_at = at + (block_size - 8)
            c0, c1, indices = struct.unpack_from("<HHI", data, colour_at)
            p0 = _from565(c0)
            p1 = _from565(c1)
            if c0 > c1:
                palette = (
                    p0,
                    p1,
                    tuple((2 * p0[i] + p1[i]) // 3 for i in range(3)),
                    tuple((p0[i] + 2 * p1[i]) // 3 for i in range(3)),
                )
                cutout = None
            else:
                palette = (
                    p0,
                    p1,
                    tuple((p0[i] + p1[i]) // 2 for i in range(3)),
                    (0, 0, 0),
                )
                cutout = 3

            for y in range(4):
                if by + y >= height:
                    break
                for x in range(4):
                    if bx + x >= width:
                        break
                    k = (indices >> (2 * (4 * y + x))) & 3
                    o = ((by + y) * width + (bx + x)) * 4
                    out[o : o + 3] = bytes(palette[k])
                    if alphas is not None:
                        out[o + 3] = alphas[4 * y + x]
                    else:
                        out[o + 3] = 0 if k == cutout else 255

            at += block_size

    return out


def decode(data: bytes):
    """Decode the top mip level. Returns ``(rgba, width, height)``."""
    header = read_header(data)
    rgba = decode_level(
        data, HEADER_SIZE, header["width"], header["height"], header["format"]
    )
    return rgba, header["width"], header["height"]
