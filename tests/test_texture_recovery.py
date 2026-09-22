# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for recovering a texture whose datablock carries no pixels.

The failure these guard looked like this in a real import: the same
handful of textures reported "exists in this .blend with no pixel data;
reloading it" over and over, hundreds of lines of it, and the models
stayed grey. Every reload was a no-op and every repair produced the
datablock it was repairing.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

import bpy  # noqa: E402

from blender_io import texture_bridge  # noqa: E402
from blender_io.texture_bridge import (  # noqa: E402
    _has_pixels,
    _load_image,
    build_texture_index,
    missing_textures,
    reset_missing_textures,
    unreadable_textures,
)
from core import dds  # noqa: E402

TEXTURE = "concrete.dds"


def _game_root(refuse: bool = False) -> str:
    """A game folder with one real DDS in it. Returns the root."""
    root = tempfile.mkdtemp()
    path = os.path.join(root, "data", "models", "textures", "all", TEXTURE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    dds.write(path, bytes([120, 110, 90, 255]) * (32 * 32), 32, 32)

    build_texture_index(root, refresh=True)
    bpy.data.images.refuse = {path} if refuse else set()
    return root


def _reset() -> None:
    reset_missing_textures()
    for image in list(bpy.data.images):
        bpy.data.images.remove(image)
    bpy.data.images.refuse = set()


def _empty_datablock(name: str = TEXTURE):
    """What an import that could not read the file leaves behind.

    Saved in the .blend, so it comes back with the file and outlives
    any amount of reinstalling the add-on.
    """
    image = bpy.data.images.new(name, width=0, height=0)
    image.has_data = False
    image.size = (0, 0)
    image._unreadable = True  # nothing will ever revive this one
    return image


# --- the double has to behave like bpy before any of this means anything


def test_check_existing_matches_on_the_filepath() -> None:
    """The reason the bug survived every test that existed.

    ``images.load(path, check_existing=True)`` returns the datablock
    already pointing at that path. The fake used to ignore the flag and
    always produce a fresh image — so the repair below, which sets the
    dead block's filepath and then asks for the same path, looked like
    it escaped and in Blender never did.
    """
    _reset()
    root = _game_root()
    path = os.path.join(root, "data", "models", "textures", "all", TEXTURE)

    first = bpy.data.images.load(path, check_existing=True)
    second = bpy.data.images.load(path, check_existing=True)
    assert first is second

    fresh = bpy.data.images.load(path, check_existing=False)
    assert fresh is not first


def test_a_name_that_is_taken_suffixes_the_newcomer() -> None:
    """Blender never lets two datablocks share a name, and never
    silently replaces the holder."""
    _reset()
    first = bpy.data.images.new("shared.dds", width=4, height=4)
    second = bpy.data.images.new("shared.dds", width=4, height=4)
    assert first.name == "shared.dds"
    assert second.name == "shared.dds.001"
    assert bpy.data.images.get("shared.dds") is first


# --- not loaded yet is not the same as empty ---------------------------


def test_an_image_nobody_has_looked_at_yet_is_not_condemned() -> None:
    """Blender fills the buffer on demand.

    ``has_data`` is False for a perfectly good file until something
    asks for it. Reading that as "broken" would rebuild every material
    on every import and replace working textures — the repair doing
    the damage it exists to undo.
    """
    _reset()
    root = _game_root()
    path = os.path.join(root, "data", "models", "textures", "all", TEXTURE)

    lazy = bpy.data.images.load(path)
    lazy.has_data = False

    assert _has_pixels(lazy) is True
    assert lazy.has_data is True


def test_an_image_that_will_never_load_is_still_reported_empty() -> None:
    _reset()
    assert _has_pixels(_empty_datablock()) is False


# --- the repair --------------------------------------------------------


def test_an_empty_datablock_is_replaced_not_handed_back() -> None:
    """The regression. Before the fix this returned the same empty
    datablock it started with, for ever."""
    _reset()
    root = _game_root()
    dead = _empty_datablock()

    image = _load_image(TEXTURE, root)

    assert image is not None
    assert image is not dead
    assert _has_pixels(image) is True
    # And it holds the canonical name, so the next import finds the
    # working one rather than the corpse.
    assert image.name == TEXTURE
    assert bpy.data.images.get(TEXTURE) is image


def test_the_replaced_datablock_is_named_for_what_it_is() -> None:
    """Not deleted: anything still pointing at it keeps pointing at
    something that says why it is blank."""
    _reset()
    root = _game_root()
    dead = _empty_datablock()

    _load_image(TEXTURE, root)

    assert dead.name == f"{TEXTURE}.unreadable"


def test_a_second_import_reuses_the_repaired_image() -> None:
    """The repair must not add a datablock per import."""
    _reset()
    root = _game_root()
    _empty_datablock()

    first = _load_image(TEXTURE, root)
    before = len(bpy.data.images)
    second = _load_image(TEXTURE, root)

    assert first is second
    assert len(bpy.data.images) == before


def test_a_reload_that_works_is_enough_on_its_own() -> None:
    """The cheap path stays the first one tried."""
    _reset()
    root = _game_root()
    revivable = bpy.data.images.new(TEXTURE, width=0, height=0)
    revivable.has_data = False
    revivable.size = (0, 0)

    image = _load_image(TEXTURE, root)

    assert image is revivable
    assert _has_pixels(image) is True


# --- loaded without complaint and without pixels ------------------------


def test_a_dds_blender_declines_goes_through_the_sdk_decoder() -> None:
    """Blender does not raise for a DDS it cannot decode.

    It returns a datablock with nothing in it, so an exception was
    never the common failure — and the SDK's own decoder, which only
    ran on an exception, never got its turn.
    """
    _reset()
    root = _game_root(refuse=True)

    image = _load_image(TEXTURE, root)

    assert image is not None
    assert _has_pixels(image) is True
    assert image.size[0] == 32 and image.size[1] == 32


def test_a_file_that_nothing_can_read_is_counted_apart_from_a_missing_one() -> None:
    """Two different problems with two different fixes.

    "not under the game folder" is a path or an install; "there and
    unreadable" is the file itself. Counting them together is what let
    a folder full of present, unreadable textures be reported as
    nothing at all.
    """
    _reset()
    root = tempfile.mkdtemp()
    path = os.path.join(root, "data", "models", "textures", "all", TEXTURE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(b"DDS not really")
    build_texture_index(root, refresh=True)
    bpy.data.images.refuse = {path}

    assert _load_image(TEXTURE, root) is None
    assert TEXTURE in unreadable_textures()
    assert TEXTURE not in missing_textures()

    _reset()
    build_texture_index(tempfile.mkdtemp(), refresh=True)
    assert _load_image("nowhere.dds", tempfile.mkdtemp()) is None
    assert "nowhere.dds" in missing_textures()
    assert "nowhere.dds" not in unreadable_textures()


# --- the log has to stay readable ---------------------------------------


def test_an_empty_datablock_is_reported_once_not_once_per_material() -> None:
    """A map names one texture in two hundred materials.

    Reported per material, the one line that mattered was buried in
    hundreds of copies of itself.
    """
    _reset()
    root = _game_root()
    _empty_datablock()

    records: list[str] = []

    class Collector(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Collector()
    logger = logging.getLogger(texture_bridge.logger.name)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        for _ in range(5):
            _load_image(TEXTURE, root)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    reloading = [line for line in records if "no pixel data" in line]
    assert len(reloading) == 1, records
