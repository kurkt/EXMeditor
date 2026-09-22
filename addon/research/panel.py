# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The *Research* sub-panel under the main EXMeditor panel.

Shown only when the preference is on — and the preference itself is
drawn only when this package is installed, so a release build never
shows either.
"""

from __future__ import annotations

import bpy

from addon.panels import CATEGORY, EXM_PT_main_panel
from addon.preferences import show_research_tools


class EXM_PT_research_panel(bpy.types.Panel):
    """Measurement tools: what the format holds, not how to edit it."""

    bl_label = "Research"
    bl_idname = "EXM_PT_research_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_parent_id = EXM_PT_main_panel.bl_idname
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context) -> bool:
        return show_research_tools(context)

    def draw(self, context) -> None:
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="Models", icon="FILE_BLANK")
        col.operator("exmachina.mesh_inspector", icon="MESH_DATA")
        col.operator("exmachina.model_forensics", icon="FILE_BLANK")
        col.operator("exmachina.format_census", icon="PRESET")
        col.operator("exmachina.registration_audit", icon="VIEWZOOM")

        col = layout.column(align=True)
        col.label(text="Map", icon="OUTLINER")
        col.operator("exmachina.diagnose_models", icon="ZOOM_ALL")
        col.operator("exmachina.analyze_scene", icon="MESH_DATA")
        col.operator("exmachina.coverage_report", icon="PRESET")
        col.operator("exmachina.world_census", icon="OUTLINER")
        layout.label(text="Reports: system console", icon="CONSOLE")


def register() -> None:
    bpy.utils.register_class(EXM_PT_research_panel)


def unregister() -> None:
    bpy.utils.unregister_class(EXM_PT_research_panel)
