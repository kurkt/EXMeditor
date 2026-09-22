# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Mesh Inspector operator.

Runs on the selected object and prints what it actually contains. Give
it the source ``.gam`` as well and it compares the two, which is where
the useful answers are — a UV that disagrees with the file is a
different fault from a UV that matches it and still looks wrong.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import StringProperty

from blender_io.mesh_inspector import inspect_object
from utils.logging import get_logger

logger = get_logger("addon.inspector")


class EXM_OT_mesh_inspector(bpy.types.Operator):
    """Report what an imported object actually contains."""

    bl_idname = "exmachina.mesh_inspector"
    bl_label = "Mesh Inspector"
    bl_description = (
        "Report the real state of the selected imported object — whether "
        "Blender kept every face, whether each loop carries its own "
        "vertex's UV, what range the UVs cover, and whether each "
        "material's image reaches the shader. Give it the source .gam to "
        "compare against"
    )
    bl_options = {"REGISTER", "UNDO"}

    model: StringProperty(
        name="Source Model",
        description=(
            "The .gam this object was imported from. Optional, and worth "
            "setting: without it the UVs cannot be checked against anything"
        ),
        subtype="FILE_PATH",
    )

    def execute(self, context):
        obj = context.active_object
        if obj is None:
            self.report({"ERROR"}, "Select the imported object first")
            return {"CANCELLED"}

        path = bpy.path.abspath(self.model) if self.model else ""
        if path and not os.path.isfile(path):
            self.report({"ERROR"}, f"No such file: {path}")
            return {"CANCELLED"}

        try:
            for line in inspect_object(obj, path or None):
                print(line)
        except (AttributeError, OSError, ValueError) as exc:
            logger.error("mesh inspector failed on %s: %s", obj.name, exc)
            self.report({"ERROR"}, f"Mesh Inspector failed: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"{obj.name}: report in the system console")
        return {"FINISHED"}


_CLASSES = (EXM_OT_mesh_inspector,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
