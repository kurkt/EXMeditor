# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Format-agnostic investigation tool for an unknown binary heightmap file.

This is a *developer tool*, not part of the shipped add-on — it has no
``bpy`` dependency but does depend on ``numpy``/``Pillow``, which is
why it lives in ``reverse/`` (the dev-tools package from the
architecture doc's roadmap) rather than in ``formats/`` or ``utils/``,
which must stay light enough to run inside Blender's bundled Python
without extra dependencies.

Purpose: stop *assuming* ``displace.bin`` is a 512x512 float32 grid
and instead let the file's own size, plus a handful of plausible
sample-type interpretations, suggest the answer. Run this against a
real ``displace.bin`` and look at the generated PNGs — recognizable
hills/plains vs. TV-static noise is usually obvious to the eye even
when the numeric stats alone are ambiguous.

Usage
-----
    python3 reverse/inspect_binary.py path/to/displace.bin [output_dir]
"""

from __future__ import annotations

import dataclasses
import math
import os
import struct
import sys

import numpy as np
from PIL import Image


@dataclasses.dataclass
class Candidate:
    """One "what if the file is actually this?" hypothesis."""

    label: str
    dtype: np.dtype
    itemsize: int


# The exact list requested: uint8, int16, uint16, float32 LE, float32 BE.
CANDIDATES: list[Candidate] = [
    Candidate("uint8", np.dtype("u1"), 1),
    Candidate("int16 (LE)", np.dtype("<i2"), 2),
    Candidate("uint16 (LE)", np.dtype("<u2"), 2),
    Candidate("float32 (LE)", np.dtype("<f4"), 4),
    Candidate("float32 (BE)", np.dtype(">f4"), 4),
]


def best_dimensions(count: int) -> tuple[int, int]:
    """Return the (width, height) factor pair of ``count`` closest to square.

    Determining resolution from the file's actual size (divided by the
    candidate's item size) rather than assuming a fixed 512x512 — a
    prime or near-prime ``count`` will come back as a very lopsided
    pair (e.g. ``(1, count)``), which is itself useful information: it
    suggests that dtype/endianness guess is probably wrong, since a
    real 2D grid dimension is unlikely to be prime.
    """
    best = (1, count)
    limit = math.isqrt(count)
    for w in range(1, limit + 1):
        if count % w == 0:
            h = count // w
            if abs(h - w) < abs(best[1] - best[0]):
                best = (w, h)
    return best


def roughness(grid: np.ndarray) -> float:
    """A cheap smoothness heuristic: mean absolute difference between
    horizontally + vertically adjacent samples, normalized by the
    data's own value range.

    Terrain tends to change gradually from one sample to the next
    (low roughness); noise (wrong dtype/endianness) tends to jump
    around randomly (high roughness). This is a *hint*, not proof —
    always look at the actual PNG too, since a genuinely rugged
    mountain range could score high here despite being real terrain.
    Returns ``float('nan')`` if the data has zero range (perfectly flat).
    """
    value_range = float(grid.max()) - float(grid.min())
    if value_range == 0:
        return float("nan")
    with np.errstate(invalid="ignore", over="ignore"):
        dx = np.abs(np.diff(grid.astype(np.float64), axis=1))
        dy = np.abs(np.diff(grid.astype(np.float64), axis=0))
        mean_diff = (dx.mean() + dy.mean()) / 2.0
    return mean_diff / value_range


def render_png(grid: np.ndarray, path: str) -> None:
    """Normalize ``grid`` to 0-255 grayscale and save it as a PNG."""
    finite = grid[np.isfinite(grid)]
    if finite.size == 0:
        # Every sample is NaN/Inf under this interpretation — nothing
        # meaningful to render; write a flat gray image as a placeholder
        # so the output file count still matches the candidate list.
        Image.new("L", (grid.shape[1], grid.shape[0]), 128).save(path)
        return
    lo, hi = float(finite.min()), float(finite.max())
    if hi == lo:
        normalized = np.full(grid.shape, 128, dtype=np.uint8)
    else:
        safe = np.nan_to_num(grid, nan=lo, posinf=hi, neginf=lo)
        normalized = ((safe - lo) / (hi - lo) * 255.0).astype(np.uint8)
    Image.fromarray(normalized, mode="L").save(path)


def inspect(filepath: str, output_dir: str) -> None:
    """Run every candidate interpretation against ``filepath`` and report."""
    file_size = os.path.getsize(filepath)
    print(f"File: {filepath}")
    print(f"Size: {file_size} bytes")
    print()

    os.makedirs(output_dir, exist_ok=True)

    for candidate in CANDIDATES:
        print(f"--- {candidate.label} ---")
        if file_size % candidate.itemsize != 0:
            print(
                f"  SKIPPED: file size {file_size} is not a multiple of "
                f"itemsize {candidate.itemsize} for this dtype"
            )
            print()
            continue

        count = file_size // candidate.itemsize
        raw = np.fromfile(filepath, dtype=candidate.dtype, count=count)

        width, height = best_dimensions(count)
        print(f"  sample count: {count}")
        print(f"  best-square dimensions: {width} x {height} (aspect diff: {abs(height - width)})")

        with np.errstate(invalid="ignore", over="ignore"):
            finite = raw[np.isfinite(raw.astype(np.float64))] if np.issubdtype(raw.dtype, np.floating) else raw
        if finite.size == 0:
            print("  all values non-finite (NaN/Inf) — likely the wrong interpretation")
            print()
            continue

        print(f"  min:  {finite.min()}")
        print(f"  max:  {finite.max()}")
        print(f"  mean: {finite.astype(np.float64).mean():.6f}")
        print(f"  first 8 values: {list(raw[:8])}")

        grid = raw.reshape(height, width)
        rough = roughness(grid)
        print(f"  roughness (lower = smoother/more terrain-like): {rough:.6f}")

        safe_label = candidate.label.replace(" ", "_").replace("(", "").replace(")", "")
        png_path = os.path.join(output_dir, f"{safe_label}.png")
        with np.errstate(invalid="ignore", over="ignore"):
            render_png(grid.astype(np.float64), png_path)
        print(f"  wrote: {png_path}")
        print()

    print(
        "Look at the PNGs in", output_dir, "— recognizable hills/plains in one "
        "of them (rather than uniform static) is the strongest signal here, "
        "stronger than any single number above. The 'roughness' score is a "
        "hint to check first, not a verdict."
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} path/to/displace.bin [output_dir]")
        sys.exit(1)
    target = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "displace_inspection_output"
    inspect(target, out_dir)
