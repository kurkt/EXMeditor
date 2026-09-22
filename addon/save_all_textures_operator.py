# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Write every edited texture back, in one operation.

The gap this closes
-------------------

Saving one texture at a time works and is easy to half-finish. Blender
holds paint in the image datablock until it is written out, so an image
edited and not saved looks perfectly fine on screen and is gone when the
file closes — and nothing says which images are in that state.

An artist who has painted three textures on a building has to find each
one, name it, and pick its target. Miss one and the loss is silent.

So: find every image with unsaved edits, work out where each belongs,
write them all, and say plainly what could not be placed. Nothing here
guesses a location — an image with nowhere sensible to go is reported
rather than dropped somewhere the engine will not look.

What it does not do
-------------------

It does not touch images that are not edited. Rewriting a game texture
that was merely loaded would recompress it for no reason and change a
file every other model shares.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty

from blender_io.texture_export import read_image_pixels
from core import dds
from utils.logging import get_logger

logger = get_logger("addon.save_all_textures")


class EXM_OT_save_all_textures(bpy.types.Operator):
    """Write every texture with unsaved edits back to its .dds."""

    bl_idname = "exmachina.save_all_textures"
    bl_label = "Save All Edited Textures"
    bl_description = (
        "Write every image with unsaved edits back as a game-format "
        ".dds. Blender keeps paint in memory until it is saved, so an "
        "edited texture looks right on screen and is lost when the file "
        "closes"
    )
    bl_options = {"REGISTER"}

    dry_run: BoolProperty(
        name="List Only",
        description=(
            "Report what would be written and where, without writing "
            "anything"
        ),
        default=False,
    )

    def execute(self, context):
        edited = [
            image
            for image in bpy.data.images
            if getattr(image, "is_dirty", False)
        ]

        if not edited:
            self.report({"INFO"}, "No textures have unsaved edits")
            return {"FINISHED"}

        written: list[str] = []
        homeless: list[str] = []
        failed: list[str] = []

        print("=" * 68)
        print(f"Textures with unsaved edits: {len(edited)}")
        print("=" * 68)

        for image in edited:
            target = _target_for(image)
            name = getattr(image, "name", "?")

            if not target:
                homeless.append(name)
                print(f"  {name}: NOWHERE TO GO")
                print(
                    "      it has no file on disk, or its file sits in the "
                    "game root, where the engine does not look for textures."
                )
                print(
                    "      Use Fork Texture to put it beside "
                    "the .gam that uses it."
                )
                continue

            if self.dry_run:
                written.append(name)
                print(f"  {name} -> {target}")
                continue

            if _write(image, target):
                written.append(name)
                print(f"  {name} -> {target}")
            else:
                failed.append(name)
                print(f"  {name}: COULD NOT BE WRITTEN")

        verb = "would write" if self.dry_run else "wrote"
        summary = f"{verb} {len(written)}"
        if homeless:
            summary += f", {len(homeless)} with nowhere to go"
        if failed:
            summary += f", {len(failed)} failed"

        self.report(
            {"WARNING"} if (homeless or failed) else {"INFO"},
            f"{summary} — see the system console",
        )
        return {"FINISHED"}


def _target_for(image) -> str:
    """Where an edited image belongs, or an empty string.

    Its own file, if it has one and that file is somewhere the engine
    looks. Deliberately not the game root: a texture there is proven
    not to resolve, so writing one is worse than refusing.
    """
    try:
        existing = bpy.path.abspath(image.filepath_from_user())
    except (AttributeError, RuntimeError):
        existing = bpy.path.abspath(getattr(image, "filepath", ""))

    if not existing:
        return ""

    folder = os.path.dirname(existing)
    if not os.path.isdir(folder):
        return ""
    if os.path.isdir(os.path.join(folder, "data")):
        # The folder holding "data" is the game root.
        return ""

    stem, extension = os.path.splitext(existing)
    return existing if extension.lower() == ".dds" else f"{stem}.dds"


def _write(image, target: str) -> bool:
    result = read_image_pixels(image)
    if result is None:
        logger.warning("could not read the pixels of %s", getattr(image, "name", "?"))
        return False

    rgba, width, height = result
    try:
        dds.write(target, rgba, width, height)
    except (dds.DDSError, OSError) as exc:
        logger.warning("could not write %s: %s", target, exc)
        return False

    # Reload so the image matches the file again and nothing downstream
    # treats it as unsaved. Measured: re-encoding a DXT1 texture eight
    # times over moves it from 0.45 to 0.48 RMSE and stops there, so the
    # round trip costs one generation and does not accumulate.
    try:
        image.filepath = target
        image.reload()
    except (AttributeError, RuntimeError) as exc:
        logger.debug("could not reload %s: %s", image.name, exc)

    return True


_CLASSES = (EXM_OT_save_all_textures,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
