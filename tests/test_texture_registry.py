# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the texture registry the engine actually consults."""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from formats.exm.texture_registry import (  # noqa: E402
    find_texture_registry,
    read_texture_registry,
)

_SAMPLE = """<?xml version="1.0" encoding="windows-1251"?>
<Root>
 <Textures>
  <Texture name="concrete" file="data\\models\\textures\\all\\concrete.dds"/>
  <Texture name="minin_ao" file="data\\models\\textures\\mininao.dds"/>
  <Texture name="nothing"/>
 </Textures>
</Root>
"""


def _write(text: str = _SAMPLE) -> str:
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "ModelTextures.xml")
    open(path, "wb").write(text.encode("cp1251", errors="replace"))
    return path


def test_a_texture_is_found_by_registry_not_by_searching() -> None:
    """The engine performs no search.

    ``DraftModel.cpp`` opens data/models/ModelTextures.xml and
    registers each entry by name and file. That is why one folder
    resolved and another did not for no reason visible on disk.
    """
    registry = read_texture_registry(_write())

    assert registry["concrete.dds"].endswith("all\\concrete.dds")
    assert registry["mininao.dds"].endswith("textures\\mininao.dds")


def test_the_key_is_the_name_and_the_value_is_the_path() -> None:
    """Exactly two attributes, and no aliases.

    ``name`` is what a material writes in its skin chunk — a bare
    filename — and ``path`` is where that resolves.
    """
    registry = read_texture_registry(
        _write(
            '<Textures>'
            '<file name="0_glass.dds" path="data\\models\\vehicles\\shared\\0_glass.dds"/>'
            "</Textures>"
        )
    )
    assert registry["0_glass.dds"].endswith("shared\\0_glass.dds")


def test_an_entry_with_no_file_is_skipped() -> None:
    registry = read_texture_registry(_write())
    assert "nothing" not in registry


def test_lookups_ignore_case() -> None:
    """The chunk holds MininAO.dds and the editor's log says mininao.dds."""
    registry = read_texture_registry(
        _write(_SAMPLE.replace("mininao.dds", "MininAO.dds"))
    )
    assert "mininao.dds" in registry


def test_a_missing_registry_is_not_an_error() -> None:
    """Plenty of installs will not have one; the folder walk stands in."""
    assert read_texture_registry("/nowhere/ModelTextures.xml") == {}
    assert find_texture_registry("") == {}
    assert find_texture_registry(tempfile.mkdtemp()) == {}


def test_the_registry_is_found_whatever_its_capitalisation() -> None:
    root = tempfile.mkdtemp()
    folder = os.path.join(root, "data", "models")
    os.makedirs(folder)
    open(os.path.join(folder, "modeltextures.xml"), "wb").write(
        _SAMPLE.encode("cp1251")
    )

    assert "concrete.dds" in find_texture_registry(root)
