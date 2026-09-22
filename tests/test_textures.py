# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for material and texture import.

Models name textures as bare filenames with no path, so the work is in
finding them and in building a Blender material that survives being
exported again — HTAToolchain matches textures by node NAME, so a
material built with Blender's default names exports with no texture at
all.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.texture_bridge import (  # noqa: E402
    TEXTURE_SLOT_NAMES,
    build_material,
    build_texture_index,
    resolve_texture,
)
from core.mesh import Material  # noqa: E402
from formats.exm.gam import read_materials, read_model  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402


def _game_root(*textures: str) -> str:
    root = tempfile.mkdtemp()
    directory = os.path.join(root, "data", "models", "textures")
    os.makedirs(directory)
    for name in textures:
        with open(os.path.join(directory, name), "wb") as handle:
            handle.write(b"DDS ")
    return root


# --- reading materials from the model ---


def test_reads_shader_and_textures_from_a_real_model() -> None:
    path = corpus("big_crag_11.gam")
    if not os.path.isfile(path):
        return
    model = read_model(path)
    assert model.materials
    material = model.materials[0]
    assert material.shader == "diffuse_detail_vc"
    assert material.diffuse == "rock_clif2.dds"


def test_a_vehicle_material_carries_several_textures() -> None:
    path = corpus("cab01.gam")
    if not os.path.isfile(path):
        return
    model = read_model(path)
    first = model.materials[0]
    assert first.shader == "bumpdiffuse_envalphagloss_spec"
    assert len(first.textures) >= 2


def test_texture_extensions_are_what_separates_names_from_shaders() -> None:
    block = b"\x01\x00\x00\x00diffuse_vc\x00concrete.dds\x00"
    materials = read_materials(block)
    assert materials[0].shader == "diffuse_vc"
    assert materials[0].textures == ["concrete.dds"]


def test_a_model_without_materials_yields_none() -> None:
    assert read_materials(b"\x00" * 32) == []


# --- finding the files ---


def test_textures_are_found_by_name_anywhere_in_the_tree() -> None:
    root = _game_root("rock_clif2.dds")
    assert build_texture_index(root, refresh=True) == 1
    found = resolve_texture("rock_clif2.dds", root)
    assert found is not None and os.path.isfile(found)


def test_lookup_ignores_case_and_any_path_in_the_name() -> None:
    root = _game_root("Concrete.dds")
    build_texture_index(root, refresh=True)
    assert resolve_texture("concrete.dds", root) is not None
    # A Windows-style name: os.path.basename does not split backslashes
    # on Linux, so this would silently never match there.
    assert resolve_texture("some\\path\\CONCRETE.DDS", root) is not None


def test_a_missing_texture_resolves_to_none_rather_than_raising() -> None:
    """Models routinely reference files a partial install lacks, and
    refusing to build the material would lose the geometry too."""
    root = _game_root()
    build_texture_index(root, refresh=True)
    assert resolve_texture("nothing_here.dds", root) is None


# --- building the Blender material ---


def test_texture_nodes_are_named_for_the_exporter() -> None:
    """Not cosmetic: HTAToolchain matches textures by node NAME, so a
    material built with Blender's default names exports with no texture
    and the game shows a default."""
    root = _game_root("rock_clif2.dds", "coverrock_detail.dds")
    material = build_material(
        Material(
            name="rock",
            shader="diffuse_detail_vc",
            textures=["rock_clif2.dds", "coverrock_detail.dds"],
        ),
        root,
    )
    names = material.node_tree.nodes.keys()
    assert TEXTURE_SLOT_NAMES[0] in names
    assert TEXTURE_SLOT_NAMES[1] in names


def test_the_shader_name_is_preserved_on_the_material() -> None:
    """The shader decides how later textures are used, so it must
    survive even though this SDK does not interpret it."""
    material = build_material(
        Material(name="shader_only", shader="bumpdiffuse_envalphagloss_spec", textures=[]),
        None,
    )
    assert material["exm_shader"] == "bumpdiffuse_envalphagloss_spec"


def test_materials_are_reused_not_duplicated() -> None:
    """A map's models share textures heavily; one datablock per
    instance would multiply materials for no benefit."""
    root = _game_root("concrete.dds")
    spec = Material(name="shared", shader="diffuse_vc", textures=["concrete.dds"])
    first = build_material(spec, root)
    second = build_material(spec, root)
    assert first is second


def test_a_material_is_still_built_without_a_game_root() -> None:
    # A name of its own: materials are reused by name, so sharing one
    # with another test would return that test's material instead.
    material = build_material(
        Material(name="no_root", shader="diffuse_vc", textures=["missing.dds"]), None,
    )
    assert material is not None
    assert "Diffuse" in material.node_tree.nodes


def test_material_identity_includes_the_textures_not_just_the_shader() -> None:
    """Regression for a visible bug: shader names are shared by many
    unrelated materials — 'bump' is used by 16 across seven models —
    so reusing by shader alone meant the first model loaded claimed the
    name and every later one inherited its textures. An oil rig arrived
    wearing a bush's texture."""
    bush = Material(name="a", shader="diffuse_detail_vc", textures=["bush.dds"])
    rig = Material(name="b", shader="diffuse_detail_vc", textures=["rig.dds"])
    same = Material(name="c", shader="diffuse_detail_vc", textures=["bush.dds"])

    first = build_material(bush, None)
    second = build_material(rig, None)
    third = build_material(same, None)

    assert first is not second, "different textures shared one material"
    assert first is third, "identical materials were duplicated"


def test_missing_textures_are_recorded_for_reporting() -> None:
    """A model with a missing texture still imports, so the only sign
    would be a grey object among textured ones."""
    from blender_io.texture_bridge import (
        missing_textures,
        reset_missing_textures,
    )

    reset_missing_textures()
    root = _game_root()
    build_material(
        Material(name="gone", shader="diffuse_vc", textures=["absent.dds"]), root,
    )
    assert "absent.dds" in missing_textures()


_ALL_TESTS = (
    test_reads_shader_and_textures_from_a_real_model,
    test_a_vehicle_material_carries_several_textures,
    test_texture_extensions_are_what_separates_names_from_shaders,
    test_a_model_without_materials_yields_none,
    test_textures_are_found_by_name_anywhere_in_the_tree,
    test_lookup_ignores_case_and_any_path_in_the_name,
    test_a_missing_texture_resolves_to_none_rather_than_raising,
    test_texture_nodes_are_named_for_the_exporter,
    test_the_shader_name_is_preserved_on_the_material,
    test_materials_are_reused_not_duplicated,
    test_material_identity_includes_the_textures_not_just_the_shader,
    test_missing_textures_are_recorded_for_reporting,
    test_a_material_is_still_built_without_a_game_root,
)


if __name__ == "__main__":
    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            test_fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL: {test_fn.__name__}: {exc}")
        else:
            print(f"PASS: {test_fn.__name__}")
    print(f"\n{len(_ALL_TESTS) - failures}/{len(_ALL_TESTS)} passed")
    sys.exit(1 if failures else 0)
