# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Forensic analysis tools for ``displace.bin`` — no format hypotheses,
just measurements.

This is a *developer diagnostic tool*, like ``reverse/inspect_binary.py``
— not part of the shipped add-on, depends on ``numpy`` (fine here,
not fine in ``formats``/``blender_io``/``core``, which must stay
importable inside Blender's bundled Python without extra
dependencies). No ``bpy`` dependency; runs from a plain interpreter or
the CLI at the bottom of this file.

Two modes, matching the request:

``analyze(path)``
    A single-file report: file size, grid size, min/max/mean, unique
    height count, first/last 64 float32 values, CRC32, SHA1.

``compare(original_path, export_path)``
    A three-level diff between two files, intended to answer "what
    kind of bug is this" in minutes instead of guessing for weeks:

    1. Byte-level — exact hex diff, first differing offset, total
       differing byte count.
    2. float32-level — first differing sample index, original vs
       exported value, absolute and relative error.
    3. 2D grid orientation — tries all 8 symmetries of a square
       (identity, transpose, flip X, flip Y, rotate 90/180/270,
       anti-transpose) against the original grid and reports whether
       any of them exactly (or near-exactly, within float32 tolerance)
       equals the exported grid.

Nothing in this module changes, validates, or even imports the
exporter — it only reads two files and reports what it measures.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zlib
from math import isqrt

import numpy as np


def _read_float32_grid(path: str) -> tuple[np.ndarray, int]:
    """Read a displace.bin-shaped file into a (side, side) float32 array.

    Raises ``ValueError`` with a clear message if the file's size isn't
    a valid square float32 grid — callers decide whether that's fatal
    for what they're trying to do (analyze() tolerates it and just
    skips grid-shaped output; the grid-orientation check in compare()
    cannot proceed without it).
    """
    with open(path, "rb") as f:
        data = f.read()
    if len(data) % 4 != 0:
        raise ValueError(f"size {len(data)} bytes is not a multiple of 4")
    count = len(data) // 4
    side = isqrt(count)
    if side * side != count:
        raise ValueError(f"sample count {count} is not a perfect square")
    grid = np.frombuffer(data, dtype="<f4").reshape(side, side).copy()
    return grid, side


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


def analyze(path: str) -> None:
    """Print the single-file forensic report requested: size, grid
    size, min/max/mean, unique-height count, first/last 64 float32
    values, CRC32, SHA1."""
    with open(path, "rb") as f:
        data = f.read()

    print(f"File: {path}")
    print(f"File size: {len(data)} bytes")

    grid: np.ndarray | None
    try:
        grid, side = _read_float32_grid(path)
    except ValueError as exc:
        print(f"Grid size: could not determine ({exc})")
        grid = None
    else:
        print(f"Grid size: {side} x {side}")

    if grid is not None:
        flat = grid.reshape(-1)
        print(f"Min: {flat.min()}")
        print(f"Max: {flat.max()}")
        print(f"Mean: {flat.mean():.6f}")
        print(f"Unique heights: {len(np.unique(flat))}")

    usable_len = (len(data) // 4) * 4
    floats = np.frombuffer(data[:usable_len], dtype="<f4")
    show = 64
    print(f"First {min(show, len(floats))} float32 values:")
    print(list(floats[:show]))
    print(f"Last {min(show, len(floats))} float32 values:")
    print(list(floats[-show:]) if len(floats) else [])

    print(f"CRC32: {zlib.crc32(data):08X}")
    print(f"SHA1: {hashlib.sha1(data).hexdigest()}")


# ---------------------------------------------------------------------------
# compare — level 1: byte diff
# ---------------------------------------------------------------------------


def _byte_diff(a: bytes, b: bytes) -> None:
    print("=== Level 1: byte-level diff ===")
    if len(a) != len(b):
        print(f"File sizes differ: original={len(a)} bytes, export={len(b)} bytes")

    n = min(len(a), len(b))
    first_offset = None
    diff_count = 0
    for i in range(n):
        if a[i] != b[i]:
            diff_count += 1
            if first_offset is None:
                first_offset = i
    diff_count += abs(len(a) - len(b))

    if diff_count == 0:
        print("Files are byte-identical.")
        return

    print(f"Total differing bytes: {diff_count} (of {max(len(a), len(b))} total)")
    if first_offset is not None:
        # Show the 4-byte-aligned word containing the first difference —
        # that's a float32 sample boundary, the unit that actually
        # matters for diagnosing endianness/value bugs.
        word_start = (first_offset // 4) * 4
        a_word = a[word_start:word_start + 4]
        b_word = b[word_start:word_start + 4]
        print(f"First differing offset: 0x{first_offset:06X} ({first_offset})")
        print(f"Offset 0x{word_start:06X}")
        print("Original:", " ".join(f"{byte:02X}" for byte in a_word))
        print("Export:  ", " ".join(f"{byte:02X}" for byte in b_word))


# ---------------------------------------------------------------------------
# compare — level 2: float32 diff
# ---------------------------------------------------------------------------


def _float_diff(a: bytes, b: bytes) -> None:
    print()
    print("=== Level 2: float32-level diff ===")
    n = min(len(a), len(b)) // 4
    if n == 0:
        print("Not enough data for a single float32 sample in one or both files.")
        return

    fa = np.frombuffer(a[:n * 4], dtype="<f4")
    fb = np.frombuffer(b[:n * 4], dtype="<f4")

    # NaN-safe inequality: NaN != NaN is True under normal comparison,
    # which would flag two identical NaN bit patterns as "different" —
    # treat identical bit patterns (including NaN) as equal.
    diff_mask = fa.view(np.uint32) != fb.view(np.uint32)
    diff_indices = np.nonzero(diff_mask)[0]

    if len(diff_indices) == 0:
        print("All float32 samples identical.")
        return

    first = int(diff_indices[0])
    orig_val = float(fa[first])
    export_val = float(fb[first])
    abs_err = abs(orig_val - export_val)
    rel_err = abs_err / abs(orig_val) if orig_val != 0 else float("inf")

    print(f"First differing sample: index {first}")
    print(f"Original: {orig_val}")
    print(f"Export:   {export_val}")
    print(f"Absolute error: {abs_err}")
    print(f"Relative error: {rel_err}")

    with np.errstate(invalid="ignore"):
        all_abs_err = np.abs(
            fa[diff_indices].astype(np.float64) - fb[diff_indices].astype(np.float64)
        )
    finite_err = all_abs_err[np.isfinite(all_abs_err)]
    print(f"Total differing samples: {len(diff_indices)} / {n}")
    if finite_err.size:
        print(f"Max absolute error: {finite_err.max()}")
        print(f"Mean absolute error (over differing samples): {finite_err.mean():.6f}")


# ---------------------------------------------------------------------------
# compare — level 3: 2D grid orientation
# ---------------------------------------------------------------------------


def _grid_orientations(grid: np.ndarray) -> dict[str, np.ndarray]:
    """The 8 symmetries of a square (the dihedral group D4): identity,
    the 3 non-trivial rotations, and the 4 reflections (2 axis-aligned
    flips + the 2 diagonal flips, one of which is a plain transpose)."""
    return {
        "identity": grid,
        "transpose": grid.T,
        "flip_x (mirror columns)": grid[:, ::-1],
        "flip_y (mirror rows)": grid[::-1, :],
        "rotate90_cw": np.rot90(grid, k=-1),
        "rotate180": np.rot90(grid, k=2),
        "rotate270_cw (== rotate90_ccw)": np.rot90(grid, k=1),
        "anti_transpose": grid[::-1, ::-1].T,
    }


def _grid_diff(original_path: str, export_path: str) -> None:
    print()
    print("=== Level 3: 2D grid orientation check (all 8 symmetries) ===")
    try:
        orig_grid, orig_side = _read_float32_grid(original_path)
        exp_grid, exp_side = _read_float32_grid(export_path)
    except ValueError as exc:
        print(f"Cannot perform grid check: {exc}")
        return

    if orig_side != exp_side:
        print(
            f"Grid sizes differ (original {orig_side}x{orig_side} vs "
            f"export {exp_side}x{exp_side}) — cannot compare orientations directly."
        )
        return

    any_match = False
    for name, transformed in _grid_orientations(orig_grid).items():
        exact = np.array_equal(transformed, exp_grid)
        close = exact or np.allclose(transformed, exp_grid, rtol=1e-5, atol=1e-5)
        if exact:
            print(f"  MATCH (exact bit-for-bit): {name}")
            any_match = True
        elif close:
            print(f"  MATCH (within float32 tolerance): {name}")
            any_match = True
        else:
            with np.errstate(invalid="ignore"):
                diff = np.abs(transformed.astype(np.float64) - exp_grid.astype(np.float64))
            finite = diff[np.isfinite(diff)]
            stat = f"mean abs diff: {finite.mean():.4f}" if finite.size else "non-finite values present"
            print(f"  no match: {name}  ({stat})")

    print()
    if any_match:
        print("=> The export matches one of the 8 standard orientations above.")
    else:
        print(
            "=> None of the 8 standard orientations match. The difference is "
            "likely NOT a simple transpose/flip/rotation — re-check the "
            "byte-level and float32-level sections above for scale, "
            "precision-loss, or endianness clues instead."
        )


def compare(original_path: str, export_path: str) -> None:
    """Run the full three-level diff between ``original_path`` and
    ``export_path`` and print the results. Read-only — never touches
    either file, never calls into the exporter or any other SDK code."""
    with open(original_path, "rb") as f:
        a = f.read()
    with open(export_path, "rb") as f:
        b = f.read()

    print(f"Original: {original_path} ({len(a)} bytes)")
    print(f"Export:   {export_path} ({len(b)} bytes)")
    print()
    _byte_diff(a, b)
    _float_diff(a, b)
    _grid_diff(original_path, export_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forensic analysis for displace.bin files")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze a single displace.bin")
    analyze_parser.add_argument("path")

    compare_parser = subparsers.add_parser("compare", help="Compare original vs exported displace.bin")
    compare_parser.add_argument("original")
    compare_parser.add_argument("export")

    args = parser.parse_args(argv)
    if args.mode == "analyze":
        analyze(args.path)
    elif args.mode == "compare":
        compare(args.original, args.export)
    return 0


if __name__ == "__main__":
    sys.exit(main())
