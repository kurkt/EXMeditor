# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Vertex Format Census operator.

Walks every ``.gam`` under the game folder and reports which vertex
formats, shaders and texture slots exist. The measured tables were
built from a handful of models; this says what the other thousand
contain.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import IntProperty, StringProperty

from core.format_census import format_census, take_census
from utils.logging import get_logger

logger = get_logger("addon.census")


class EXM_OT_format_census(bpy.types.Operator):
    """Tabulate vertex formats and shaders across the whole game."""

    bl_idname = "exmachina.format_census"
    bl_label = "Vertex Format Census"
    bl_description = (
        "Walk every .gam under a folder and report which vertex formats, "
        "shaders and texture slots it uses — including the ones this "
        "add-on has no measured layout for, which are the models it "
        "reads by guessing"
    )
    bl_options = {"REGISTER", "UNDO"}

    folder: StringProperty(
        name="Folder",
        description=(
            "Where to look. Leave empty to use the Game Folder from the "
            "add-on preferences"
        ),
        subtype="DIR_PATH",
    )

    limit: IntProperty(
        name="Limit",
        description="Stop after this many models. 0 reads all of them",
        default=0,
        min=0,
    )

    def execute(self, context):
        folder = bpy.path.abspath(self.folder) if self.folder else ""
        if not folder:
            from addon.preferences import get_game_root
            from formats.exm.model_catalog import normalise_game_root

            folder = normalise_game_root(get_game_root(context)) or ""

        if not folder or not os.path.isdir(folder):
            self.report({"ERROR"}, "Set Folder, or set the Game Folder in preferences")
            return {"CANCELLED"}

        try:
            census = take_census(folder, limit=self.limit or None)
            print(format_census(census))
        except OSError as exc:
            logger.error("census failed on %s: %s", folder, exc)
            self.report({"ERROR"}, f"Census failed: {exc}")
            return {"CANCELLED"}

        unknown = sum(
            count
            for (vertex_type, stride), count in census.vertex_formats.items()
            if not _is_measured(vertex_type, stride)
        )
        self.report(
            {"WARNING"} if unknown else {"INFO"},
            f"{census.models_read} model(s), {unknown} mesh(es) in unmeasured "
            "formats — report in the system console",
        )
        return {"FINISHED"}


def _is_measured(vertex_type: int, stride: int) -> bool:
    from formats.exm.gam import VERTEX_LAYOUTS

    known = VERTEX_LAYOUTS.get(vertex_type)
    return known is not None and known[0] == stride


_CLASSES = (EXM_OT_format_census,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
