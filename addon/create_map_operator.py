# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Create a new map folder and its manifest.

The original editor asks for a size first and everything follows from
it, so this does the same: a size, a name, and the map exists.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from addon.preferences import get_game_root
from core.map_template import GRID_SIZES, describe
from formats.exm.model_catalog import normalise_game_root
from formats.exm.new_map import create_map
from utils.errors import EXMeditorError
from utils.logging import OperatorReportHandler, get_logger

logger = get_logger("addon.create_map")


def _size_items(self, context):
    """One entry per shipped map size, with what it means in units."""
    items = []
    for grid in GRID_SIZES:
        shape = describe(grid)
        items.append((
            str(grid),
            f"{grid} x {grid} tiles",
            f"{shape['span']:.0f} x {shape['span']:.0f} units, "
            f"{shape['vertices']} terrain samples per side, "
            f"LEVELSIZE {shape['level_size']}",
        ))
    return items


class EXM_OT_create_map(bpy.types.Operator):
    """Create an empty map: a folder of the eighteen files every
    shipped map has, and the .ssl beside it."""

    bl_idname = "exmachina.create_map"
    bl_label = "Create Map"
    bl_description = (
        "Create a new, empty map under data/maps — flat terrain, one "
        "ground texture, no objects. Pick a size first, as the original "
        "editor does"
    )
    bl_options = {"REGISTER"}

    map_name: StringProperty(
        name="Name",
        description=(
            "Folder and manifest name. This is what the game and the "
            "editor call the map"
        ),
        default="NewMap",
    )
    size: EnumProperty(
        name="Size",
        description="Tile grid. Everything else follows from it",
        items=_size_items,
        default=2,          # index of 32 in GRID_SIZES
    )
    open_after: BoolProperty(
        name="Import It",
        description="Import the map straight after creating it",
        default=True,
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "map_name")
        layout.prop(self, "size")

        shape = describe(int(self.size))
        box = layout.box()
        box.label(text=f"{shape['span']:.0f} x {shape['span']:.0f} units", icon="WORLD")
        box.label(text=f"LEVELSIZE {shape['level_size']}, "
                       f"{shape['vertices']} terrain samples per side")
        box.label(text=f"playable {shape['safe'][0]:.0f} .. {shape['safe'][2]:.0f}")
        layout.prop(self, "open_after")

    def execute(self, context):
        handler = OperatorReportHandler(self)
        logger.addHandler(handler)
        try:
            return self._run(context)
        finally:
            logger.removeHandler(handler)

    def _run(self, context):
        name = (self.map_name or "").strip()
        if not name:
            self.report({"ERROR"}, "The map needs a name")
            return {"CANCELLED"}
        if any(ch in name for ch in '\\/:*?"<>|'):
            self.report(
                {"ERROR"},
                "That name cannot be a folder name — no \\ / : * ? \" < > |",
            )
            return {"CANCELLED"}

        root = normalise_game_root(get_game_root(context))
        if not root:
            self.report(
                {"ERROR"},
                "Set the Game Folder in Preferences > Add-ons > ExMachina "
                "SDK — a map is created inside its data/maps",
            )
            return {"CANCELLED"}

        maps_folder = os.path.join(root, "data", "maps")
        if not os.path.isdir(maps_folder):
            self.report(
                {"ERROR"},
                f"No data/maps under the game folder ({maps_folder})",
            )
            return {"CANCELLED"}

        try:
            report = create_map(maps_folder, name, int(self.size))
        except EXMeditorError as exc:
            self.report({"ERROR"}, str(getattr(exc, "message", exc)))
            return {"CANCELLED"}
        except OSError as exc:
            self.report({"ERROR"}, f"Could not write the map: {exc}")
            return {"CANCELLED"}

        shape = report["shape"]
        self.report(
            {"INFO"},
            f"Created {name}: {shape['grid']} x {shape['grid']} tiles, "
            f"{shape['span']:.0f} units across, {len(report['files'])} files",
        )
        if not report["roads_copied"]:
            self.report(
                {"WARNING"},
                "No roads.xml was copied — the map has no road sets yet",
            )

        if self.open_after:
            try:
                bpy.ops.exmachina.import_map(filepath=report["folder"])
            except (AttributeError, RuntimeError, TypeError) as exc:
                logger.debug("could not import the new map: %s", exc)
                self.report(
                    {"INFO"},
                    f"Created at {report['folder']} — import it when ready",
                )
        return {"FINISHED"}


_CLASSES = (EXM_OT_create_map,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
