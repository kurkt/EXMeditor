# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for forking a shared texture onto one model.

The game shares textures across many models. An edit in place changes
every one of them, and the damage shows up somewhere the person editing
was not looking. Forking is the only safe edit the format allows.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

import bpy  # noqa: E402

from addon.fork_texture_operator import EXM_OT_fork_texture  # noqa: E402
from blender_io.mesh_provider import MODEL_PATH_PROP  # noqa: E402
from core import dds  # noqa: E402
from formats.exm.gam import read_container  # noqa: E402
from formats.exm.skin import parse_skin  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402



def _scene():
    """A model on disk, its texture, and a Blender object pointing at both."""
    source = os.path.join(CORPUS, "factory_box.gam")
    texture = os.path.join(CORPUS, "factory_box.dds")
    if not (os.path.isfile(source) and os.path.isfile(texture)):
        return None

    folder = tempfile.mkdtemp()
    model = os.path.join(folder, "factory_box.gam")
    shutil.copy(source, model)
    shutil.copy(texture, os.path.join(folder, "factory_box.dds"))

    material = bpy.data.materials.new("forkable")
    material.use_nodes = True
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.name = "Diffuse"
    node.image = bpy.data.images.load(os.path.join(folder, "factory_box.dds"))

    mesh = bpy.data.meshes.new("forkable")
    mesh.materials.append(material)
    mesh[MODEL_PATH_PROP] = model
    obj = bpy.data.objects.new("forkable", mesh)

    return folder, model, obj


def _run(obj, **properties):
    operator = EXM_OT_fork_texture()
    operator.model = ""
    operator.new_name = ""
    operator.slot = 0
    operator.material_index = -1
    operator.from_blender = False
    for key, value in properties.items():
        setattr(operator, key, value)
    operator.report = lambda *_args: None

    context = type("Context", (), {"active_object": obj})()
    return operator.execute(context)


def _textures(model_path: str) -> list[str]:
    block = next(
        c.data for c in read_container(model_path)[1] if c.chunk_id == 15
    )
    return [t.filename for m in parse_skin(block).materials for t in m.textures]


def test_forking_repoints_this_model_and_writes_the_copy() -> None:
    scene = _scene()
    if scene is None:
        return
    folder, model, obj = scene

    assert _textures(model) == ["factory_box.dds"]
    assert _run(obj) == {"FINISHED"}

    assert _textures(model) == ["factory_box_factory_box.dds"]
    assert os.path.isfile(os.path.join(folder, "factory_box_factory_box.dds"))


def test_the_shared_original_is_left_untouched() -> None:
    """The whole point: every other model keeps what it had."""
    scene = _scene()
    if scene is None:
        return
    folder, model, obj = scene

    original = os.path.join(folder, "factory_box.dds")
    before = open(original, "rb").read()

    _run(obj, new_name="mine.dds")

    assert open(original, "rb").read() == before
    assert _textures(model) == ["mine.dds"]


def test_the_copy_lands_beside_the_model() -> None:
    """Beside the .gam is the location shown by experiment to resolve."""
    scene = _scene()
    if scene is None:
        return
    folder, model, obj = scene

    _run(obj, new_name="beside.dds")
    assert os.path.isfile(os.path.join(os.path.dirname(model), "beside.dds"))


def test_the_material_follows_the_fork() -> None:
    """Otherwise the next save writes over the shared texture again."""
    scene = _scene()
    if scene is None:
        return
    _folder, _model, obj = scene

    _run(obj, new_name="followed.dds")

    node = next(
        n
        for n in obj.data.materials[0].node_tree.nodes
        if getattr(n, "name", "") == "Diffuse"
    )
    assert node.image.name.startswith("followed")


def test_an_existing_name_is_refused_rather_than_overwritten() -> None:
    scene = _scene()
    if scene is None:
        return
    folder, model, obj = scene

    open(os.path.join(folder, "taken.dds"), "wb").write(b"someone else's")
    assert _run(obj, new_name="taken.dds") == {"CANCELLED"}
    assert _textures(model) == ["factory_box.dds"]


def test_the_edited_image_can_be_the_source() -> None:
    """Paint first, fork second — the copy carries the paint."""
    scene = _scene()
    if scene is None:
        return
    folder, _model, obj = scene

    node = next(
        n
        for n in obj.data.materials[0].node_tree.nodes
        if getattr(n, "name", "") == "Diffuse"
    )
    node.image.size = (8, 8)
    node.image.pixels = [1.0, 0.0, 0.0, 1.0] * 64

    assert _run(obj, new_name="painted.dds", from_blender=True) == {"FINISHED"}

    written = os.path.join(folder, "painted.dds")
    rgba, width, height = dds.decode(open(written, "rb").read())
    assert (width, height) == (8, 8)
    assert rgba[0] > 200 and rgba[1] < 60


def test_a_model_that_was_never_imported_is_reported() -> None:
    mesh = bpy.data.meshes.new("nowhere")
    obj = bpy.data.objects.new("nowhere", mesh)
    assert _run(obj) == {"CANCELLED"}


def test_the_write_is_verified_by_reading_it_back() -> None:
    """Reporting success without checking hides a write that went astray.

    A change the game appears to ignore and a change that never reached
    the file look identical from the outside, and only one of them is
    worth investigating in the game.
    """
    scene = _scene()
    if scene is None:
        return
    _folder, model, obj = scene

    assert EXM_OT_fork_texture._verify(model, "factory_box.dds") is True
    assert EXM_OT_fork_texture._verify(model, "not_in_there.dds") is False

    _run(obj, new_name="verified.dds")
    assert EXM_OT_fork_texture._verify(model, "verified.dds") is True


def test_the_forked_file_is_a_valid_dds_of_the_right_size() -> None:
    """Structure as well as content: the game reads the header first."""
    scene = _scene()
    if scene is None:
        return
    folder, _model, obj = scene

    _run(obj, new_name="structural.dds", from_blender=False)

    data = open(os.path.join(folder, "structural.dds"), "rb").read()
    header = dds.read_header(data)

    expected = dds.HEADER_SIZE
    width, height = header["width"], header["height"]
    for _level in range(header["levels"]):
        expected += dds.level_size(width, height, header["format"])
        width, height = max(1, width // 2), max(1, height // 2)
    assert len(data) == expected


# --- one model against one placement -----------------------------------


def test_forking_the_texture_alone_changes_the_shared_model() -> None:
    """Worth stating: the .gam is shared by every placement of it.

    A map can hold dozens of one wall. Editing the model edits all of
    them, which is correct and is not always what was wanted.
    """
    scene = _scene()
    if scene is None:
        return
    _folder, model, obj = scene

    before = os.path.getmtime(model)
    _run(obj, new_name="shared.dds")

    # The same file was edited, not a copy.
    assert os.path.isfile(model)
    assert os.path.getmtime(model) >= before
    assert _textures(model) == ["shared.dds"]


def test_forking_the_model_leaves_the_original_untouched() -> None:
    """Scoping to one placement means giving it a model of its own."""
    scene = _scene()
    if scene is None:
        return
    folder, model, obj = scene

    original = open(model, "rb").read()
    assert _run(obj, new_name="mine.dds", fork_model=True) == {"FINISHED"}

    assert open(model, "rb").read() == original, "the shared model changed"

    copies = [
        name
        for name in os.listdir(folder)
        if name.endswith(".gam") and name != "factory_box.gam"
    ]
    assert len(copies) == 1, copies
    assert _textures(os.path.join(folder, copies[0])) == ["mine.dds"]


def test_the_object_is_pointed_at_its_new_model() -> None:
    """A copy nothing references is a copy the game never loads."""
    scene = _scene()
    if scene is None:
        return
    _folder, _model, obj = scene

    _run(obj, new_name="pointed.dds", fork_model=True)
    assert obj.get("exm_id", "").startswith("factory_box_")


# --- forking a fork ----------------------------------------------------


def test_repeated_forks_do_not_lengthen_the_name() -> None:
    """Appending the model name each time overruns the 44-byte field.

    ``concrete.dds`` became ``concrete_heavy_bigwall1_heavy_bigwall1.dds``
    and then a name that did not fit, which refused halfway and left
    the model partly edited.
    """
    operator = EXM_OT_fork_texture()
    operator.new_name = ""

    folder = tempfile.mkdtemp()
    model = os.path.join(folder, "heavy_bigwall1.gam")
    open(model, "wb").write(b"placeholder")

    name = "concrete.dds"
    for _round in range(5):
        name = operator._new_name(name, model)
        open(os.path.join(folder, name), "wb").write(b"placeholder")
        assert len(name.encode("latin-1")) < 44, name

    assert name == "concrete_heavy_bigwall1_5.dds"


def test_a_name_that_does_not_fit_leaves_the_model_alone() -> None:
    scene = _scene()
    if scene is None:
        return
    _folder, model, obj = scene

    before = open(model, "rb").read()
    assert _run(obj, new_name="x" * 60 + ".dds") == {"CANCELLED"}
    assert open(model, "rb").read() == before


def test_a_missing_texture_does_not_destroy_its_material() -> None:
    """Rebuilding a material whose texture cannot be found is pointless.

    It produces the same empty material, drops the last user of the
    image, and lets Blender purge that image from the file — a texture
    that could not be located turning into a destroyed material.
    """
    import bpy as _bpy

    from blender_io import texture_bridge
    from core.mesh import Material

    texture_bridge.reset_missing_textures()
    material = Material(
        name="m", shader="diffuse", textures=["nowhere_at_all.dds"], slots=[0]
    )

    first = texture_bridge.build_material(material, None, name_prefix="gone")
    nodes_before = [n.name for n in first.node_tree.nodes]

    second = texture_bridge.build_material(material, None, name_prefix="gone")
    assert second is first
    assert [n.name for n in second.node_tree.nodes] == nodes_before
    assert any(n.name == "Diffuse" for n in second.node_tree.nodes)


def test_a_name_that_already_repeats_the_suffix_is_folded_back_down() -> None:
    """A model edited by an earlier version carries the doubled name.

    Stripping one occurrence was not enough: the result still ended in
    the suffix, so the counter pushed it over the 44-byte field and the
    fork refused on a model it had itself produced.
    """
    from addon.fork_texture_operator import _base_name

    for stem in (
        "concrete",
        "concrete_heavy_bigwall1",
        "concrete_heavy_bigwall1_heavy_bigwall1",
        "concrete_heavy_bigwall1_heavy_bigwall1_heavy_bigwall1",
        "concrete_heavy_bigwall1_heavy_bigwall1_2",
    ):
        assert _base_name(stem, "heavy_bigwall1") == "concrete_heavy_bigwall1"


def test_a_long_name_is_trimmed_rather_than_refused() -> None:
    """Refusing leaves the user with whatever an earlier attempt wrote."""
    from addon.fork_texture_operator import _fit

    long_name = "x" * 80 + ".dds"
    fitted = _fit(long_name, 44)
    assert len(fitted.encode("latin-1")) < 44
    assert fitted.endswith(".dds")

    short = "fine.dds"
    assert _fit(short, 44) == short


def test_forking_a_doubled_name_now_succeeds() -> None:
    from addon.fork_texture_operator import EXM_OT_fork_texture

    operator = EXM_OT_fork_texture()
    operator.new_name = ""

    folder = tempfile.mkdtemp()
    model = os.path.join(folder, "heavy_bigwall1.gam")
    open(model, "wb").write(b"placeholder")

    name = "concrete_heavy_bigwall1_heavy_bigwall1.dds"
    for _round in range(4):
        name = operator._new_name(name, model)
        open(os.path.join(folder, name), "wb").write(b"placeholder")
        assert len(name.encode("latin-1")) < 44, name
