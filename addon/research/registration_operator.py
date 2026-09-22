# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Registration Audit operator.

Answers "why does my model not appear in the editor" by searching for
one that does, rather than by reasoning about which file ought to
matter. Read-only.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import StringProperty

from addon.preferences import get_game_root
from core.registration_audit import audit, compare_nodes, format_audit
from utils.logging import get_logger

logger = get_logger("addon.registration_audit")


class EXM_OT_registration_audit(bpy.types.Operator):
    """Find every file naming a working model, and which of them omit yours."""

    bl_idname = "exmachina.registration_audit"
    bl_label = "Registration Audit"
    bl_description = (
        "Search the whole Game Folder for two model ids — yours and one the "
        "editor already offers — and report which files name the working one "
        "and not yours"
    )

    # REGISTER *and* UNDO, or Blender offers no "Adjust Last Operation"
    # panel and the ids below can never be typed in.
    bl_options = {"REGISTER", "UNDO"}

    candidate: StringProperty(
        name="Your Model Id",
        description="The model id that does not appear, e.g. CubeV3",
    )

    reference: StringProperty(
        name="Working Model Id",
        description=(
            "A model the editor already lists, e.g. civilhouse1. Its "
            "registrations are the shape yours has to match"
        ),
        default="civilhouse1",
    )

    world_xml: StringProperty(
        name="world.xml",
        description=(
            "Optional: the map's world.xml. Given one, the node placing your "
            "model is printed beside a node the editor already draws"
        ),
        subtype="FILE_PATH",
    )

    def execute(self, context):
        game_root = get_game_root(context)
        if not game_root or not os.path.isdir(game_root):
            self.report(
                {"ERROR"},
                "Set the Game Folder in Preferences > Add-ons > EXMeditor",
            )
            return {"CANCELLED"}

        if not self.candidate:
            self.report(
                {"INFO"},
                "Enter Your Model Id in the 'Adjust Last Operation' panel at "
                "the bottom left, or press F9",
            )
            return {"FINISHED"}

        world = self.world_xml
        if world:
            blender_path = getattr(bpy, "path", None)
            if blender_path is not None and hasattr(blender_path, "abspath"):
                world = blender_path.abspath(world)

        result = audit(game_root, self.candidate, self.reference)

        print("")
        print("=== Registration Audit ===")
        for line in format_audit(result):
            print(line)

        if world and os.path.isfile(world):
            print("")
            print("=== Node Comparison ===")
            for line in compare_nodes(world, self.candidate, self.reference):
                print(line)

        missing = result.missing_from()
        if result.reference and not result.reference_mentions:
            self.report(
                {"ERROR"},
                f"{self.reference!r} was not found anywhere under the Game "
                "Folder — check the id, or the folder",
            )
            return {"FINISHED"}

        if missing:
            self.report(
                {"WARNING"},
                f"{len(missing)} file(s) list {self.reference!r} and not "
                f"{self.candidate!r} — full report in the console",
            )
            for path in missing[:4]:
                self.report({"WARNING"}, os.path.basename(path))
        else:
            self.report(
                {"INFO"},
                "No registration gap — every file naming the working model "
                "names yours too. The cause is elsewhere",
            )
        return {"FINISHED"}


_CLASSES = (EXM_OT_registration_audit,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
