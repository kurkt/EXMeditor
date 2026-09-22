# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Write an edited texture back to ``.dds``.

Blender 3.6 reads DDS and cannot write it. So an artist who paints on a
game texture has no way to save it from Blender: **Image > Save** either
refuses the format or writes something with a ``.dds`` name that is not
a DDS, and the result looks like a damaged texture rather than like a
failed save. That is a bad failure mode — it looks like the paint broke
the image.

This writes the image through ``core/dds.py``, which produces a header
byte-identical to the game's own and a payload the engine has been
observed to load.

It writes where the image already lives, so a texture edited in place
lands back where every model referencing it will find it. Overwriting is
the point here, unlike the model export, which deliberately refuses to
replace a shared game texture that was merely copied through.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from blender_io.texture_export import read_image_pixels
from core import dds
from utils.logging import get_logger

logger = get_logger("addon.save_texture")


class EXM_OT_save_texture(bpy.types.Operator):
    """Save an edited image as a game-format .dds."""

    bl_idname = "exmachina.save_texture"
    bl_label = "Save Texture As DDS"
    bl_description = (
        "Write the image back out as a .dds the game can load. Blender "
        "cannot write this format itself, so painting on a game texture "
        "and using Image > Save produces a file that is not a DDS"
    )
    bl_options = {"REGISTER"}

    image: StringProperty(
        name="Image",
        description="Which image to write. Empty uses the active one",
    )

    target: StringProperty(
        name="Save To",
        description=(
            "Where to write. Empty writes over the file the image came "
            "from, which is where the models referencing it look"
        ),
        subtype="FILE_PATH",
    )

    format: EnumProperty(
        name="Format",
        description="How to compress it",
        items=(
            (
                "AUTO",
                "Automatic",
                "DXT1 for an opaque image, DXT5 for one with alpha — "
                "what the game itself uses",
            ),
            ("DXT1", "DXT1", "Opaque, four bits per pixel"),
            ("DXT5", "DXT5", "With alpha, eight bits per pixel"),
            (
                "A8R8G8B8",
                "Uncompressed",
                "No compression and no loss, at four bytes per pixel",
            ),
        ),
        default="AUTO",
    )

    mipmaps: BoolProperty(
        name="Mipmaps",
        description=(
            "Write the full chain down to 1x1, as every shipped texture "
            "measured does"
        ),
        default=True,
    )

    def execute(self, context):
        image = self._image(context)
        if image is None:
            self.report(
                {"ERROR"},
                "No image found — type its name into Image in the panel "
                "below (F9), e.g. concrete.dds",
            )
            return {"CANCELLED"}

        target = self._target(image)
        if not target:
            self.report(
                {"ERROR"},
                f"{image.name} has nowhere sensible to go — set Save To, or "
                "use Fork Texture to put it beside the .gam",
            )
            return {"CANCELLED"}

        result = read_image_pixels(image)
        if result is None:
            self.report({"ERROR"}, f"Could not read the pixels of {image.name}")
            return {"CANCELLED"}

        rgba, width, height = result
        chosen = None if self.format == "AUTO" else self.format

        try:
            written = dds.write(
                target, rgba, width, height, fmt=chosen, mipmaps=self.mipmaps
            )
        except (dds.DDSError, OSError) as exc:
            logger.error("could not write %s: %s", target, exc)
            self.report({"ERROR"}, f"Could not write it: {exc}")
            return {"CANCELLED"}

        header = dds.read_header(open(target, "rb").read(dds.HEADER_SIZE + 4))
        logger.info(
            "wrote %s: %sx%s %s, %s level(s), %s bytes",
            target, width, height, header["format"], header["levels"], written,
        )

        # The image now matches the file again, so nothing downstream
        # should treat it as unsaved work.
        try:
            image.filepath = target
            image.reload()
        except (AttributeError, RuntimeError) as exc:
            logger.debug("could not reload %s: %s", image.name, exc)

        self.report(
            {"INFO"},
            f"Wrote {os.path.basename(target)} — {header['format']}, "
            f"{width}x{height}",
        )
        return {"FINISHED"}

    def _image(self, context):
        """Find the image to write.

        ``context.space_data`` is the space the operator was INVOKED
        from — the 3D View, when the button lives in a sidebar panel —
        so asking it for an image finds nothing however plainly the
        texture is open next door. Every area on screen is searched
        instead, and then the edited images, which is what an artist
        who has just painted actually has.
        """
        if self.image:
            named = bpy.data.images.get(self.image)
            if named is not None:
                return named

        space = getattr(context, "space_data", None)
        found = getattr(space, "image", None)
        if found is not None:
            return found

        for area in self._areas(context):
            if getattr(area, "type", "") != "IMAGE_EDITOR":
                continue
            for area_space in getattr(area, "spaces", []):
                found = getattr(area_space, "image", None)
                if found is not None:
                    return found

        edited = [
            image
            for image in bpy.data.images
            if getattr(image, "is_dirty", False)
        ]
        if len(edited) == 1:
            return edited[0]
        if len(edited) > 1:
            logger.info(
                "%s images have unsaved edits; name one in Image", len(edited)
            )
        return None

    @staticmethod
    def _areas(context):
        """Every area on screen, across windows."""
        areas = []
        screen = getattr(context, "screen", None)
        areas.extend(getattr(screen, "areas", []) or [])

        manager = getattr(getattr(bpy, "context", None), "window_manager", None)
        for window in getattr(manager, "windows", []) or []:
            window_screen = getattr(window, "screen", None)
            for area in getattr(window_screen, "areas", []) or []:
                if area not in areas:
                    areas.append(area)
        return areas

    def _target(self, image) -> str:
        if self.target:
            return bpy.path.abspath(self.target)

        existing = ""
        try:
            existing = bpy.path.abspath(image.filepath_from_user())
        except (AttributeError, RuntimeError):
            existing = bpy.path.abspath(getattr(image, "filepath", ""))

        if not existing:
            return ""

        # An image created inside Blender has a filepath like
        # "//name.dds", which resolves against the .blend and lands
        # wherever that happens to sit — the game root, in practice,
        # which is the one place a texture is proven NOT to resolve
        # from. Better to refuse and let the user say where.
        if not os.path.isdir(os.path.dirname(existing)):
            return ""
        if _looks_like_the_game_root(os.path.dirname(existing)):
            logger.warning(
                "%s would land in the game root, where the engine does not "
                "look for textures", os.path.basename(existing),
            )
            return ""

        stem, extension = os.path.splitext(existing)
        return existing if extension.lower() == ".dds" else f"{stem}.dds"


def _looks_like_the_game_root(folder: str) -> bool:
    """Whether this is the folder holding ``data``, rather than a
    folder inside it. Textures there are not found — measured."""
    return os.path.isdir(os.path.join(folder, "data"))


_CLASSES = (EXM_OT_save_texture,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
