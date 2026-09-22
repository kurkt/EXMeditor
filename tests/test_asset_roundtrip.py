# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""What happens to a model between the asset browser and the game.

Three separate faults, all found by comparing a model the user
exported back out of Blender against the one it came from:

    crag_boulder2.gam        shader='diffuse_detail_vc'
    Crag boulder2.001.gam    shader='bumpdiffuse_envalphagloss_spec'

with the same two textures in both. The shader was not preserved, and
``envalphagloss`` gives the diffuse alpha a meaning ``diffuse_detail_vc``
does not — which is a model that looks right in Blender and part
transparent in the game.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from blender_io.gam_export import (  # noqa: E402
    SHADER_PROP,
    _name_images_for_export,
    _name_materials_for_export,
    _restore_image_names,
    _restore_material_names,
)
from core.snapshot import contains_folder, is_same_folder  # noqa: E402


class _Image:
    def __init__(self, name):
        self.name = name
        self.filepath = ""


class _Node:
    type = "TEX_IMAGE"

    def __init__(self, image):
        self.image = image


class _Tree:
    def __init__(self, nodes):
        self.nodes = nodes


class _HtaSettings:
    """HTAToolchain's per-material property group, and its default."""

    def __init__(self):
        self.shader_name = "bumpdiffuse_envalphagloss_spec"


class _Material(dict):
    """Enough of a Blender material: a name, custom properties, the
    toolchain's settings group, and optionally an image node."""

    def __init__(self, name, image=None, **props):
        super().__init__(props)
        self.name = name
        self.htatools = _HtaSettings()
        self.node_tree = _Tree([_Node(image)] if image is not None else [])


class _Mesh:
    def __init__(self, materials):
        self.materials = materials


class _Object:
    def __init__(self, name, materials):
        self.name = name
        self.data = _Mesh(materials)


def test_the_shader_goes_where_the_exporter_reads_it() -> None:
    """CORRECTION to the previous fix. The exporter does NOT take the
    shader from the material name::

        HTAToolchain/__init__.py:891
            mtl.shader = material.htatools.shader_name

    a StringProperty defaulting to bumpdiffuse_envalphagloss_spec,
    which is what 27 of 31 exported models carry."""
    material = _Material(
        "ExM_diffuse_detail_vc_tropiccrag_9f21",
        **{SHADER_PROP: "diffuse_detail_vc"},
    )
    obj = _Object("Crag boulder2", [material])

    saved = _name_materials_for_export([obj])

    assert material.htatools.shader_name == "diffuse_detail_vc"
    # The NAME is what the exporter matches material indices against,
    # so it must not be touched.
    assert material.name == "ExM_diffuse_detail_vc_tropiccrag_9f21"

    _restore_material_names(saved)
    assert material.htatools.shader_name == "bumpdiffuse_envalphagloss_spec"


def test_a_material_with_no_recorded_shader_is_left_alone() -> None:
    """Built in Blender and never had one. The toolchain's default is a
    better answer than an invented shader."""
    material = _Material("Material.001")
    obj = _Object("Cube", [material])

    assert _name_materials_for_export([obj]) == []
    assert material.htatools.shader_name == "bumpdiffuse_envalphagloss_spec"


def test_the_image_is_renamed_to_the_file_it_came_from() -> None:
    """The exporter writes the DATABLOCK'S NAME as the texture
    filename::

        HTAToolchain/__init__.py:901
            texture.filename = pointer.image.name

    MEASURED: a model whose texture file had been renamed to `1` still
    wrote `tripo_image_ea527cc5-57ee-420f-a393-85d6`, because that is
    what the datablock was still called."""
    image = _Image("tripo_image_ea527cc5-57ee-420f-a393-85d62720b655.png")
    image.filepath = os.path.join("K:", os.sep, "textures", "1.dds")
    obj = _Object("Rock", [_Material("m", image=image)])

    saved = _name_images_for_export([obj])
    assert image.name == "1.dds"

    _restore_image_names(saved)
    assert image.name == "tripo_image_ea527cc5-57ee-420f-a393-85d62720b655.png"


def test_an_image_with_no_file_keeps_its_name() -> None:
    """Generated in Blender: there is no filename to prefer, and the
    datablock name is the only thing there is."""
    image = _Image("painted")
    image.filepath = ""
    obj = _Object("Rock", [_Material("m", image=image)])

    assert _name_images_for_export([obj]) == []
    assert image.name == "painted"


def test_exporting_into_the_folder_of_maps_is_refused() -> None:
    """MEASURED: it happened. data/maps ended up holding a complete copy
    of a map — 18 files byte-identical to the map's own — plus the two
    files the export rewrites, while the map folder stayed untouched.
    The file browser's Accept takes the folder it is SHOWING, so one
    click too few sends the whole export a level up."""
    maps = os.path.join("K:", os.sep, "GC", "data", "maps")
    one_map = os.path.join(maps, "NewMap2")

    assert contains_folder(maps, one_map)
    # In place is the normal case and must stay allowed.
    assert not contains_folder(one_map, one_map)
    assert is_same_folder(one_map, one_map)
    # A sibling map folder is a legitimate "save a copy as".
    assert not contains_folder(os.path.join(maps, "Other"), one_map)
    # And the other direction is not the same question.
    assert not contains_folder(one_map, maps)


def test_containment_ignores_spelling_and_trailing_separators() -> None:
    maps = os.path.join("K:", os.sep, "GC", "data", "maps")
    assert contains_folder(maps + os.sep, os.path.join(maps, "NewMap2"))
    # A folder whose name merely starts with the other's is not inside it.
    assert not contains_folder(maps, maps + "2")


def test_the_assign_dialog_reads_the_property_the_palette_writes() -> None:
    """The asset palette stamps exm_asset_id and the assign operator read
    exm_id, so a dragged asset came up with an empty model field — and
    the way out of an empty model field is to create a new model, which
    writes a new .gam with the wrong shader."""
    from addon.assign_operator import PALETTE_ID_PROP
    from blender_io.asset_library import build_asset_previews

    import inspect

    signature = inspect.signature(build_asset_previews)
    assert signature.parameters["asset_id_prop"].default == PALETTE_ID_PROP


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _source(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def test_the_three_fixes_are_actually_wired_in() -> None:
    """A helper nothing calls is the same as no helper.

    Each of these three was a working function beside a code path that
    did not use it: the material rename did not exist, the containment
    check did not exist, and the palette property was read by nobody.
    Testing the helpers alone would pass while the export kept doing
    exactly what it did before."""
    export = _source("blender_io", "gam_export.py")
    # Both export paths — in-process and background — and both restores.
    assert export.count("= _name_materials_for_export(objects)") == 2
    assert export.count("_restore_material_names(restore_materials)") == 2
    assert export.count("= _name_images_for_export(objects)") == 2
    assert export.count("_restore_image_names(restore_images)") == 2
    # Beside the node-name pass it mirrors, in both paths.
    assert export.count("= _name_texture_nodes_for_export(objects)") == 2

    operators = _source("addon", "operators.py")
    assert "contains_folder(folder, source_dir)" in operators
    # And the browser starts inside the map, so Accept saves in place.
    assert "self.directory = os.path.join(source_dir, \"\")" in operators

    assign = _source("addon", "assign_operator.py")
    assert "active.get(ASSET_ID_PROP) or active.get(PALETTE_ID_PROP)" in assign
