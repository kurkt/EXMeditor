# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `formats/exm/ssl.py` and `core/manifest.py`.

Includes regression tests for two bugs that only surfaced when the
parser was first run against a REAL `.ssl` file rather than a
hand-written fixture:

1. `MINSAFEX`/`MAXSAFEX` are written as `"40.000"` — decimal-formatted
   despite being whole numbers. Parsing them as `int` raised on real
   data.
2. `.ssl` references filenames in CamelCase (`LevelRoads.xml`) while
   the shipped files are lower-case on disk. Case-sensitive lookup
   found only 21 of 31 files; case-insensitive lookup finds 28.

Both are the kind of bug a synthetic fixture written from the format
spec would never have caught, which is why the fixture below is a
verbatim excerpt of real map data rather than an idealized sample.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.manifest import FILE_REFERENCE_KEYS, FIXED_FILENAME_EXCEPTIONS  # noqa: E402
from formats.exm.ssl import read_manifest, resolve_in_folder, scan  # noqa: E402
from utils.errors import ParsingError  # noqa: E402

# Verbatim excerpt from a real map's `.ssl`, including the exact
# attribute-on-its-own-line formatting and decimal-formatted integers
# that broke the first implementation.
_REAL_SSL = """<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>
<Ini>
\t<Section
\t\tname="LEVEL">
\t\t<Key
\t\t\tname="HIGHMAP">displace.bin</Key>

\t\t<Key
\t\t\tname="ROADMAP">LevelRoads.xml</Key>

\t\t<Key
\t\t\tname="LEVELSIZE">32</Key>

\t\t<Key
\t\t\tname="WATERLEVEL">286.440</Key>

\t\t<Key
\t\t\tname="PASSMAPCELLSIZE">16</Key>

\t\t<Key
\t\t\tname="MINSAFEX">40.000</Key>

\t\t<Key
\t\t\tname="MAXSAFEX">4056.000</Key>

\t\t<Key
\t\t\tname="LOCALQUESTS"></Key>
\t</Section>
\t<Section
\t\tname="CAMERA">
\t\t<Key
\t\t\tname="X">1326.182</Key>
\t</Section>
</Ini>
"""


def _write_ssl(content: str = _REAL_SSL) -> str:
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "test.ssl")
    with open(path, "w", encoding="cp1251") as f:
        f.write(content)
    return path


def test_reads_all_level_keys() -> None:
    manifest = read_manifest(_write_ssl())
    assert manifest.raw_keys["HIGHMAP"] == "displace.bin"
    assert manifest.raw_keys["ROADMAP"] == "LevelRoads.xml"


def test_parses_typed_fields() -> None:
    manifest = read_manifest(_write_ssl())
    assert manifest.level_size == 32
    assert manifest.water_level == 286.44
    assert manifest.passmap_cell_size == 16


def test_safe_bounds_parse_as_float_not_int() -> None:
    """Regression: real files write these as "40.000"/"4056.000" —
    decimal-formatted whole numbers. int() raises on that."""
    manifest = read_manifest(_write_ssl())
    assert manifest.min_safe_x == 40.0
    assert manifest.max_safe_x == 4056.0


def test_empty_key_value_is_preserved_as_empty_string() -> None:
    """Several optional keys are present-but-blank in real maps
    (LOCALQUESTS, ENV_SKY_Y_NEG, ...). They must round-trip as empty,
    not vanish or raise."""
    manifest = read_manifest(_write_ssl())
    assert manifest.raw_keys["LOCALQUESTS"] == ""


def test_file_ref_returns_none_for_absent_key() -> None:
    manifest = read_manifest(_write_ssl())
    assert manifest.file_ref("HIGHMAP") == "displace.bin"
    assert manifest.file_ref("NO_SUCH_KEY") is None


def test_rejects_file_without_level_section() -> None:
    bad = """<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>
<Ini><Section name="CAMERA"><Key name="X">1.0</Key></Section></Ini>
"""
    try:
        read_manifest(_write_ssl(bad))
        raise AssertionError("expected ParsingError")
    except ParsingError as e:
        assert "LEVEL" in e.message


def test_rejects_malformed_xml() -> None:
    try:
        read_manifest(_write_ssl("<Ini><Section></Ini>"))
        raise AssertionError("expected ParsingError")
    except ParsingError:
        pass


def test_rejects_missing_file() -> None:
    try:
        read_manifest("/no/such/path/map.ssl")
        raise AssertionError("expected ParsingError")
    except ParsingError:
        pass


# --- case-insensitive resolution (regression test #2) ---


def test_resolve_finds_exact_match() -> None:
    folder = tempfile.mkdtemp()
    open(os.path.join(folder, "displace.bin"), "wb").close()
    assert resolve_in_folder(folder, "displace.bin") is not None


def test_resolve_finds_case_mismatched_file() -> None:
    """Regression: `.ssl` says "LevelRoads.xml", disk has
    "levelroads.xml". Windows doesn't care; Linux/macOS does."""
    folder = tempfile.mkdtemp()
    open(os.path.join(folder, "levelroads.xml"), "wb").close()
    resolved = resolve_in_folder(folder, "LevelRoads.xml")
    assert resolved is not None
    assert resolved.endswith("levelroads.xml")


def test_resolve_returns_none_when_genuinely_absent() -> None:
    folder = tempfile.mkdtemp()
    assert resolve_in_folder(folder, "nothing_here.xml") is None


def test_scan_reports_present_and_missing() -> None:
    folder = tempfile.mkdtemp()
    ssl_path = os.path.join(folder, "test.ssl")
    with open(ssl_path, "w", encoding="cp1251") as f:
        f.write(_REAL_SSL)
    # HIGHMAP present (exact case), ROADMAP present (WRONG case on disk)
    open(os.path.join(folder, "displace.bin"), "wb").close()
    open(os.path.join(folder, "levelroads.xml"), "wb").close()

    manifest = read_manifest(ssl_path)
    found = scan(folder, manifest)
    assert found["HIGHMAP"] is True
    assert found["ROADMAP"] is True  # found despite case mismatch


def test_scan_checks_fixed_filename_exceptions() -> None:
    """world.xml et al. are never referenced by any manifest key —
    scan() must check them by convention, or they'd never be found."""
    folder = tempfile.mkdtemp()
    ssl_path = os.path.join(folder, "test.ssl")
    with open(ssl_path, "w", encoding="cp1251") as f:
        f.write(_REAL_SSL)
    open(os.path.join(folder, "world.xml"), "wb").close()

    manifest = read_manifest(ssl_path)
    found = scan(folder, manifest)
    assert found["world.xml"] is True
    for name in FIXED_FILENAME_EXCEPTIONS:
        assert name in found, f"{name} should always be checked, present or not"


def test_scan_handles_windows_style_paths_in_values() -> None:
    """Some keys hold game-root paths with backslashes
    (SERVERS = data\\maps\\r1m1\\servers.xml). scan() checks the
    basename in the map folder; it must not choke on the separators."""
    folder = tempfile.mkdtemp()
    ssl = _REAL_SSL.replace(
        '\t\t\tname="HIGHMAP">displace.bin</Key>',
        '\t\t\tname="SERVERS">data\\maps\\r1m1\\servers.xml</Key>',
    )
    ssl_path = os.path.join(folder, "test.ssl")
    with open(ssl_path, "w", encoding="cp1251") as f:
        f.write(ssl)
    open(os.path.join(folder, "servers.xml"), "wb").close()

    manifest = read_manifest(ssl_path)
    found = scan(folder, manifest)
    assert found["SERVERS"] is True


def test_file_reference_keys_and_exceptions_do_not_overlap() -> None:
    """A file is either manifest-referenced or fixed-name, never both —
    if one ever appears in both lists it means a finding was recorded
    twice with contradictory meaning."""
    assert not (set(FILE_REFERENCE_KEYS) & set(FIXED_FILENAME_EXCEPTIONS))


_ALL_TESTS = (
    test_reads_all_level_keys,
    test_parses_typed_fields,
    test_safe_bounds_parse_as_float_not_int,
    test_empty_key_value_is_preserved_as_empty_string,
    test_file_ref_returns_none_for_absent_key,
    test_rejects_file_without_level_section,
    test_rejects_malformed_xml,
    test_rejects_missing_file,
    test_resolve_finds_exact_match,
    test_resolve_finds_case_mismatched_file,
    test_resolve_returns_none_when_genuinely_absent,
    test_scan_reports_present_and_missing,
    test_scan_checks_fixed_filename_exceptions,
    test_scan_handles_windows_style_paths_in_values,
    test_file_reference_keys_and_exceptions_do_not_overlap,
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
