# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for saving every edited texture at once.

Blender holds paint in the image datablock until it is written out, so
an edited texture looks right on screen and is gone when the file
closes — and nothing says which images are in that state. Saving one at
a time works and is easy to half-finish.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

import bpy  # noqa: E402

from addon.save_all_textures_operator import (  # noqa: E402
    EXM_OT_save_all_textures,
    _target_for,
)
from core import dds  # noqa: E402


def _scene():
    """A game tree with an edited texture, a homeless one and a clean one."""
    root = tempfile.mkdtemp()
    folder = os.path.join(root, "data", "models", "textures", "all")
    os.makedirs(folder)

    image = bytes([120, 110, 90, 255]) * (64 * 64)
    for name in ("wall.dds", "clean.dds"):
        dds.write(os.path.join(folder, name), image, 64, 64)

    edited = bpy.data.images.load(os.path.join(folder, "wall.dds"))
    edited.is_dirty = True
    edited.size = (8, 8)
    edited.pixels = [0.9, 0.1, 0.1, 1.0] * 64

    homeless = fake_bpy.FakeImage(
        "painted.dds",
        filepath=os.path.join(root, "painted.dds"),
        size=(8, 8),
        is_dirty=True,
    )
    bpy.data.images["painted.dds"] = homeless

    bpy.data.images.load(os.path.join(folder, "clean.dds"))
    return root, folder


def _run(**properties):
    operator = EXM_OT_save_all_textures()
    operator.dry_run = False
    for key, value in properties.items():
        setattr(operator, key, value)
    reported = []
    operator.report = lambda level, message: reported.append(message)
    operator.execute(None)
    return reported


def test_an_edited_texture_is_written_back_to_its_own_file() -> None:
    bpy.data.images.clear()
    _root, folder = _scene()

    _run()

    rgba, width, height = dds.decode(
        open(os.path.join(folder, "wall.dds"), "rb").read()
    )
    assert (width, height) == (8, 8)
    assert rgba[0] > 200 and rgba[1] < 60


def test_a_texture_that_was_not_edited_is_left_alone() -> None:
    """Rewriting a loaded game texture recompresses it for no reason
    and changes a file every other model shares."""
    bpy.data.images.clear()
    _root, folder = _scene()

    before = open(os.path.join(folder, "clean.dds"), "rb").read()
    _run()
    assert open(os.path.join(folder, "clean.dds"), "rb").read() == before


def test_a_texture_with_nowhere_to_go_is_reported_not_dropped() -> None:
    """The game root is the one place a texture is proven not to
    resolve from, so writing one there is worse than refusing."""
    bpy.data.images.clear()
    root, _folder = _scene()

    reported = _run()
    assert any("nowhere to go" in message for message in reported), reported
    assert not os.path.isfile(os.path.join(root, "painted.dds"))


def test_listing_only_writes_nothing() -> None:
    bpy.data.images.clear()
    _root, folder = _scene()

    before = open(os.path.join(folder, "wall.dds"), "rb").read()
    _run(dry_run=True)
    assert open(os.path.join(folder, "wall.dds"), "rb").read() == before


def test_nothing_edited_is_said_plainly() -> None:
    bpy.data.images.clear()
    reported = _run()
    assert reported == ["No textures have unsaved edits"]


def test_the_game_root_is_refused_as_a_target() -> None:
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "data"))

    at_root = fake_bpy.FakeImage("x.dds", filepath=os.path.join(root, "x.dds"))
    assert _target_for(at_root) == ""

    inside = os.path.join(root, "data", "models")
    os.makedirs(inside)
    proper = fake_bpy.FakeImage("y.dds", filepath=os.path.join(inside, "y.dds"))
    assert _target_for(proper) == os.path.join(inside, "y.dds")


def test_a_non_dds_target_becomes_a_dds() -> None:
    root = tempfile.mkdtemp()
    folder = os.path.join(root, "textures")
    os.makedirs(folder)

    image = fake_bpy.FakeImage("z.png", filepath=os.path.join(folder, "z.png"))
    assert _target_for(image).endswith("z.dds")


def test_the_image_collection_iterates_images_not_names() -> None:
    """A dict subclass yields keys, and code walking every image for
    unsaved edits then finds strings and reports nothing to save."""
    bpy.data.images.clear()
    root = tempfile.mkdtemp()
    path = os.path.join(root, "iter.dds")
    dds.write(path, bytes([1, 2, 3, 255]) * (8 * 8), 8, 8)
    bpy.data.images.load(path)

    for item in bpy.data.images:
        assert hasattr(item, "name"), type(item)
