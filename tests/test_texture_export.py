# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for putting a model's textures where the engine looks.

Measured, not assumed: the editor's log names the path it tries::

    Couldn't load texture data\\models\\custom\\mininao.dds
    for model cube1111.gam

The model is in that folder, so the texture must be too. A model that
is otherwise perfect but whose texture is elsewhere shows whatever is
already bound — which reads as a random texture and cost this project
several rounds of investigating the model format instead.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.texture_export import (  # noqa: E402
    FALLBACK_SUFFIX,
    export_textures_beside_model,
)


def _object_with_texture(*, filepath: str = "", node_name: str = "Diffuse"):
    material = fake_bpy.FakeMaterial("Material")
    node = material.node_tree.nodes.new("TEX_IMAGE")
    node.name = node_name
    node.image = fake_bpy.FakeImage("brick+metall.dds", filepath=filepath)

    mesh = fake_bpy.FakeMesh("m")
    mesh.materials = [material]
    obj = fake_bpy.FakeObject("Cube", mesh)
    return obj, node.image


def _a_texture_file(name: str = "brick+metall.dds") -> str:
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, name)
    with open(path, "wb") as handle:
        handle.write(b"DDS fake payload")
    return path


def test_a_texture_on_disk_is_copied_beside_the_model() -> None:
    source = _a_texture_file()
    obj, _ = _object_with_texture(filepath=source)
    model = os.path.join(tempfile.mkdtemp(), "custom", "Cube33.gam")

    result = export_textures_beside_model([obj], model)

    target = os.path.join(os.path.dirname(model), "brick+metall.dds")
    assert os.path.isfile(target), "the engine looks in the model's own folder"
    assert open(target, "rb").read() == open(source, "rb").read()
    assert result.copied == ["brick+metall.dds"]


def test_an_existing_file_is_not_overwritten() -> None:
    """A game texture already there is used by every other model that
    references it; a re-save from Blender would alter all of them."""
    source = _a_texture_file()
    obj, _ = _object_with_texture(filepath=source)
    folder = tempfile.mkdtemp()
    model = os.path.join(folder, "Cube33.gam")
    target = os.path.join(folder, "brick+metall.dds")
    with open(target, "wb") as handle:
        handle.write(b"the game's own copy")

    export_textures_beside_model([obj], model)
    assert open(target, "rb").read() == b"the game's own copy"


def test_only_nodes_the_exporter_reads_are_written() -> None:
    """HTAToolchain matches by node NAME, so a node it ignores must not
    have its file copied — the skin chunk will never mention it."""
    source = _a_texture_file()
    obj, _ = _object_with_texture(filepath=source, node_name="Roughness")
    model = os.path.join(tempfile.mkdtemp(), "Cube33.gam")

    result = export_textures_beside_model([obj], model)
    assert result.written == []


def test_an_image_with_no_file_is_saved_and_reported() -> None:
    """Blender 3.6 cannot write .dds, and every shipped model
    references one, so this must not happen quietly."""
    obj, _ = _object_with_texture(filepath="")
    model = os.path.join(tempfile.mkdtemp(), "Cube33.gam")

    result = export_textures_beside_model([obj], model)

    assert len(result.converted) == 1
    assert result.converted[0].endswith(FALLBACK_SUFFIX)
    assert os.path.isfile(os.path.join(os.path.dirname(model), result.converted[0]))


def test_the_same_texture_on_two_materials_is_written_once() -> None:
    source = _a_texture_file()
    first, _ = _object_with_texture(filepath=source)
    second, _ = _object_with_texture(filepath=source)
    model = os.path.join(tempfile.mkdtemp(), "Cube33.gam")

    result = export_textures_beside_model([first, second], model)
    assert result.copied == ["brick+metall.dds"]


def test_a_missing_source_file_is_reported_not_raised() -> None:
    obj, image = _object_with_texture(filepath="/nonexistent/gone.dds")
    model = os.path.join(tempfile.mkdtemp(), "Cube33.gam")

    result = export_textures_beside_model([obj], model)
    # No file on disk, so it falls through to a save rather than a copy.
    assert result.copied == []


def test_the_summary_reads_as_a_sentence() -> None:
    source = _a_texture_file()
    obj, _ = _object_with_texture(filepath=source)
    model = os.path.join(tempfile.mkdtemp(), "Cube33.gam")

    result = export_textures_beside_model([obj], model)
    assert "copied" in result.summary()
    assert export_textures_beside_model([], model).summary() == "no textures"


_ALL_TESTS = (
    test_a_texture_on_disk_is_copied_beside_the_model,
    test_an_existing_file_is_not_overwritten,
    test_only_nodes_the_exporter_reads_are_written,
    test_an_image_with_no_file_is_saved_and_reported,
    test_the_same_texture_on_two_materials_is_written_once,
    test_a_missing_source_file_is_reported_not_raised,
    test_the_summary_reads_as_a_sentence,
)


if __name__ == "__main__":
    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            test_fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL: {test_fn.__name__}: {exc}")
    print(f"{len(_ALL_TESTS) - failures}/{len(_ALL_TESTS)} passed")
