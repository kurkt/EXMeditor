# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for `core/snapshot.py`.

The snapshot strategy is what keeps the ~25 map files this SDK doesn't
parse from being lost on export, so its guarantees are worth pinning
down explicitly: everything copies, declared-regenerated files are
allowed to differ, undeclared changes get reported, and the obvious
footguns (same folder, non-empty target) are refused.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.snapshot import (  # noqa: E402
    backup_files,
    copy_map_folder,
    is_same_folder,
    verify_preserved,
)
from utils.errors import ValidationError  # noqa: E402


def _make_map_folder(files: dict[str, bytes]) -> str:
    folder = tempfile.mkdtemp()
    for name, content in files.items():
        with open(os.path.join(folder, name), "wb") as f:
            f.write(content)
    return folder


def _sample_folder() -> str:
    return _make_map_folder({
        "displace.bin": b"terrain-data",
        "world.xml": b"<World/>",
        "grass.xml": b"binary-blob-we-never-parse",
        "level.tile": b"another-opaque-file",
    })


def test_copies_every_file() -> None:
    source = _sample_folder()
    target = tempfile.mkdtemp()
    copied = copy_map_folder(source, target, overwrite=True)
    assert sorted(copied) == ["displace.bin", "grass.xml", "level.tile", "world.xml"]
    for name in copied:
        assert os.path.isfile(os.path.join(target, name))


def test_copied_files_are_byte_identical() -> None:
    source = _sample_folder()
    target = tempfile.mkdtemp()
    copy_map_folder(source, target, overwrite=True)
    for name in os.listdir(source):
        with open(os.path.join(source, name), "rb") as a, open(os.path.join(target, name), "rb") as b:
            assert a.read() == b.read()


def test_refuses_non_empty_target_by_default() -> None:
    source = _sample_folder()
    target = _make_map_folder({"something.txt": b"pre-existing"})
    try:
        copy_map_folder(source, target)
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "not empty" in e.message


def test_allows_non_empty_target_with_overwrite() -> None:
    source = _sample_folder()
    target = _make_map_folder({"something.txt": b"pre-existing"})
    copied = copy_map_folder(source, target, overwrite=True)
    assert len(copied) == 4


def test_in_place_export_is_supported_not_an_error() -> None:
    """Exporting back into the map's own folder is the normal
    save-and-test-in-game workflow, not a mistake. Nothing needs
    copying since the files are already there."""
    source = _sample_folder()
    copied = copy_map_folder(source, source, overwrite=True)
    assert copied == []
    # every original file still present and untouched
    assert set(os.listdir(source)) == {"displace.bin", "world.xml", "grass.xml", "level.tile"}


def test_is_same_folder_handles_equivalent_spellings() -> None:
    source = _sample_folder()
    assert is_same_folder(source, source)
    assert is_same_folder(source, os.path.join(source, "."))
    assert not is_same_folder(source, tempfile.mkdtemp())


def test_backup_files_creates_bak_copies() -> None:
    source = _sample_folder()
    created = backup_files(source, {"displace.bin", "world.xml"})
    assert sorted(created) == ["displace.bin.bak", "world.xml.bak"]
    with open(os.path.join(source, "displace.bin.bak"), "rb") as f:
        assert f.read() == b"terrain-data"


def test_backup_files_skips_missing_files() -> None:
    source = _sample_folder()
    created = backup_files(source, {"displace.bin", "not_here.xml"})
    assert created == ["displace.bin.bak"]


def test_verify_is_a_no_op_for_in_place_export() -> None:
    """Comparing a folder against itself proves nothing, so it reports
    nothing rather than flagging every regenerated file."""
    source = _sample_folder()
    assert verify_preserved(source, source, regenerated=set()) == []


def test_refuses_missing_source() -> None:
    try:
        copy_map_folder("/no/such/folder", tempfile.mkdtemp(), overwrite=True)
        raise AssertionError("expected ValidationError")
    except ValidationError:
        pass


def test_verify_reports_nothing_when_untouched() -> None:
    source = _sample_folder()
    target = tempfile.mkdtemp()
    copy_map_folder(source, target, overwrite=True)
    assert verify_preserved(source, target, regenerated=set()) == []


def test_verify_allows_declared_regenerated_files_to_differ() -> None:
    """The whole point: a file the SDK deliberately rewrote is expected
    to differ and must not be reported as a problem."""
    source = _sample_folder()
    target = tempfile.mkdtemp()
    copy_map_folder(source, target, overwrite=True)
    with open(os.path.join(target, "displace.bin"), "wb") as f:
        f.write(b"newly-exported-terrain")

    assert verify_preserved(source, target, regenerated={"displace.bin"}) == []


def test_verify_catches_undeclared_change() -> None:
    """A file nobody claimed to rewrite must not silently differ."""
    source = _sample_folder()
    target = tempfile.mkdtemp()
    copy_map_folder(source, target, overwrite=True)
    with open(os.path.join(target, "grass.xml"), "wb") as f:
        f.write(b"corrupted somehow")

    problems = verify_preserved(source, target, regenerated={"displace.bin"})
    assert len(problems) == 1
    assert "grass.xml" in problems[0]


def test_verify_catches_missing_file() -> None:
    source = _sample_folder()
    target = tempfile.mkdtemp()
    copy_map_folder(source, target, overwrite=True)
    os.remove(os.path.join(target, "level.tile"))

    problems = verify_preserved(source, target, regenerated=set())
    assert len(problems) == 1
    assert "level.tile" in problems[0]
    assert "missing" in problems[0]


def test_verify_regenerated_matching_is_case_insensitive() -> None:
    """Map files are referenced in mixed case (LevelRoads.xml vs
    levelroads.xml), so declaring one casing must match the other."""
    source = _make_map_folder({"LevelRoads.xml": b"original"})
    target = tempfile.mkdtemp()
    copy_map_folder(source, target, overwrite=True)
    with open(os.path.join(target, "LevelRoads.xml"), "wb") as f:
        f.write(b"regenerated")

    assert verify_preserved(source, target, regenerated={"levelroads.xml"}) == []


_ALL_TESTS = (
    test_copies_every_file,
    test_copied_files_are_byte_identical,
    test_refuses_non_empty_target_by_default,
    test_allows_non_empty_target_with_overwrite,
    test_in_place_export_is_supported_not_an_error,
    test_is_same_folder_handles_equivalent_spellings,
    test_backup_files_creates_bak_copies,
    test_backup_files_skips_missing_files,
    test_verify_is_a_no_op_for_in_place_export,
    test_refuses_missing_source,
    test_verify_reports_nothing_when_untouched,
    test_verify_allows_declared_regenerated_files_to_differ,
    test_verify_catches_undeclared_change,
    test_verify_catches_missing_file,
    test_verify_regenerated_matching_is_case_insensitive,
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
