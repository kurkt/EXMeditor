# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `reverse/terrain_forensics.py`.

Verifies the forensic tool correctly identifies each class of
discrepancy it's meant to diagnose — not just that it runs without
crashing. Captures stdout and asserts on the actual diagnostic
conclusions, since a forensics tool that runs cleanly but reports the
wrong answer is worse than one that crashes.
"""

from __future__ import annotations

import io
import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from reverse import terrain_forensics as tf  # noqa: E402


def _make_grid(side: int = 8, seed: int = 1) -> np.ndarray:
    rng = np.random.RandomState(seed)
    return rng.uniform(0.0, 500.0, size=(side, side)).astype("<f4")


def _write(grid: np.ndarray, path: str | None = None) -> str:
    path = path or tempfile.mktemp()
    grid.astype("<f4").tofile(path)
    return path


def _capture_compare(original: str, export: str) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        tf.compare(original, export)
    return buf.getvalue()


def _capture_analyze(path: str) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        tf.analyze(path)
    return buf.getvalue()


# --- analyze() ---


def test_analyze_reports_correct_grid_size_and_stats() -> None:
    grid = _make_grid(side=8, seed=2)
    path = _write(grid)
    output = _capture_analyze(path)
    assert "Grid size: 8 x 8" in output
    assert f"Min: {grid.min()}" in output
    assert f"Max: {grid.max()}" in output
    assert "CRC32:" in output
    assert "SHA1:" in output


def test_analyze_handles_non_square_file_without_crashing() -> None:
    path = tempfile.mktemp()
    with open(path, "wb") as f:
        f.write(b"\x00" * 4097)  # not a multiple of 4
    output = _capture_analyze(path)
    assert "could not determine" in output


# --- compare(): level 1/2 exact-match and mismatch detection ---


def test_compare_identical_files_reports_no_differences() -> None:
    grid = _make_grid()
    path_a = _write(grid)
    path_b = _write(grid.copy())
    output = _capture_compare(path_a, path_b)
    assert "Files are byte-identical." in output
    assert "All float32 samples identical." in output
    assert "MATCH (exact bit-for-bit): identity" in output


def test_compare_reports_file_size_mismatch() -> None:
    grid = _make_grid(side=8)
    path_a = _write(grid)
    path_b = _write(_make_grid(side=4))
    output = _capture_compare(path_a, path_b)
    assert "File sizes differ" in output


def test_compare_reports_first_differing_sample_index() -> None:
    grid = _make_grid(side=4, seed=3)
    modified = grid.copy()
    modified[1, 2] = modified[1, 2] + 100.0  # sample index 1*4+2 = 6
    path_a = _write(grid)
    path_b = _write(modified)
    output = _capture_compare(path_a, path_b)
    assert "First differing sample: index 6" in output


# --- compare(): level 3, all 8 orientations, each individually verified ---


def _orientation_match_line(output: str, name: str) -> bool:
    return f"MATCH (exact bit-for-bit): {name}" in output


def test_detects_transpose() -> None:
    grid = _make_grid(side=10, seed=4)
    output = _capture_compare(_write(grid), _write(grid.T))
    assert _orientation_match_line(output, "transpose")


def test_detects_flip_x() -> None:
    grid = _make_grid(side=10, seed=5)
    output = _capture_compare(_write(grid), _write(grid[:, ::-1]))
    assert _orientation_match_line(output, "flip_x (mirror columns)")


def test_detects_flip_y() -> None:
    grid = _make_grid(side=10, seed=6)
    output = _capture_compare(_write(grid), _write(grid[::-1, :]))
    assert _orientation_match_line(output, "flip_y (mirror rows)")


def test_detects_rotate90() -> None:
    grid = _make_grid(side=10, seed=7)
    output = _capture_compare(_write(grid), _write(np.rot90(grid, k=-1)))
    assert _orientation_match_line(output, "rotate90_cw")


def test_detects_rotate180() -> None:
    grid = _make_grid(side=10, seed=8)
    output = _capture_compare(_write(grid), _write(np.rot90(grid, k=2)))
    assert _orientation_match_line(output, "rotate180")


def test_detects_rotate270() -> None:
    grid = _make_grid(side=10, seed=9)
    output = _capture_compare(_write(grid), _write(np.rot90(grid, k=1)))
    assert _orientation_match_line(output, "rotate270_cw (== rotate90_ccw)")


def test_detects_anti_transpose() -> None:
    grid = _make_grid(side=10, seed=10)
    output = _capture_compare(_write(grid), _write(grid[::-1, ::-1].T))
    assert _orientation_match_line(output, "anti_transpose")


def test_no_orientation_matches_a_scaled_grid() -> None:
    """A pure height-scale bug (e.g. leftover height_scale=2.0) is NOT
    an orientation issue — must correctly report no match, not a false
    positive on one of the 8."""
    grid = _make_grid(side=8, seed=11)
    output = _capture_compare(_write(grid), _write(grid * 2.0))
    for name in (
        "identity", "transpose", "flip_x (mirror columns)", "flip_y (mirror rows)",
        "rotate90_cw", "rotate180", "rotate270_cw (== rotate90_ccw)", "anti_transpose",
    ):
        assert not _orientation_match_line(output, name), f"false positive match: {name}"
    assert "None of the 8 standard orientations match" in output


def test_no_orientation_matches_an_endian_swapped_grid() -> None:
    grid = _make_grid(side=8, seed=12)
    be_path = tempfile.mktemp()
    grid.astype(">f4").tofile(be_path)
    output = _capture_compare(_write(grid), be_path)
    assert "None of the 8 standard orientations match" in output


def test_grid_check_handles_mismatched_sizes_gracefully() -> None:
    grid_a = _make_grid(side=8)
    grid_b = _make_grid(side=4)
    output = _capture_compare(_write(grid_a), _write(grid_b))
    assert "Grid sizes differ" in output


_ALL_TESTS = (
    test_analyze_reports_correct_grid_size_and_stats,
    test_analyze_handles_non_square_file_without_crashing,
    test_compare_identical_files_reports_no_differences,
    test_compare_reports_file_size_mismatch,
    test_compare_reports_first_differing_sample_index,
    test_detects_transpose,
    test_detects_flip_x,
    test_detects_flip_y,
    test_detects_rotate90,
    test_detects_rotate180,
    test_detects_rotate270,
    test_detects_anti_transpose,
    test_no_orientation_matches_a_scaled_grid,
    test_no_orientation_matches_an_endian_swapped_grid,
    test_grid_check_handles_mismatched_sizes_gracefully,
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
