# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sidebar panel: the "EXMeditor" tab in the 3D viewport's N-panel.

The editor: map in and out, placing and editing objects, assets,
textures, and the one check a map needs before export. The *Research*
sub-panel of measurement tools lives in ``addon/research/`` — source
repository only, not in the install archive — and attaches itself
under this panel when that package is present.
"""

from __future__ import annotations

import bpy

CATEGORY = "EXMeditor"


class EXM_PT_main_panel(bpy.types.Panel):
    bl_label = "EXMeditor"
    bl_idname = "EXM_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    def draw(self, context) -> None:
        layout = self.layout

        col = layout.column(align=True)
        col.operator("exmachina.import_map", icon="IMPORT")
        col.operator("exmachina.export_map", icon="EXPORT")
        layout.operator("exmachina.create_map", icon="FILE_NEW")

        box = layout.box()
        box.label(text="Objects", icon="OBJECT_DATA")
        box.operator("exmachina.assign_node", icon="ADD")
        box.operator("exmachina.replace_model", icon="FILE_REFRESH")
        box.operator("exmachina.clear_node", icon="X")
        box.separator()
        box.operator("exmachina.create_model", icon="MESH_DATA")
        box.operator("exmachina.model_doctor", icon="MODIFIER")

        box = layout.box()
        box.label(text="Assets", icon="ASSET_MANAGER")
        box.operator("exmachina.build_asset_previews", icon="ASSET_MANAGER")
        box.operator("exmachina.clear_asset_previews", icon="TRASH")
        box.operator("exmachina.browse_assets", icon="VIEWZOOM")

        box = layout.box()
        box.label(text="Textures", icon="TEXTURE")
        box.operator("exmachina.edit_texture", icon="BRUSH_DATA")
        box.operator("exmachina.fork_texture", icon="DUPLICATE")
        box.operator("exmachina.save_texture", icon="IMAGE_DATA")
        box.operator("exmachina.save_all_textures", icon="FILE_TICK")

        layout.operator("exmachina.validate_map", icon="CHECKMARK")


_CLASSES = (EXM_PT_main_panel,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
