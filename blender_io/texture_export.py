# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender materials -> texture files the engine can find.

The mirror of ``texture_bridge``, which builds Blender materials from a
model's textures. This puts them back.

Where they go
-------------

Beside the ``.gam``, because that is where the engine looks. Read off
the editor's own log rather than assumed::

    Couldn't load texture data\\models\\custom\\mininao.dds
    for model cube1111.gam

The model is ``data\\models\\custom\\cube1111.gam`` and the texture was
sought in that same folder. The skin chunk stores a bare filename, so
the folder comes from the model.

Format
------

The file is COPIED whenever the image already exists on disk, whatever
its format. A ``.dds`` picked out of the game's own textures arrives
byte-identical, which is both the common case and the only one where
the result is certainly loadable.

An image that exists only inside the .blend — painted, generated,
packed — is encoded to ``.dds`` by ``core/dds.py``. Blender 3.6 has no
DDS writer of its own, and Targa is not a substitute: it was tested
against the engine and did not load, which is what made writing our
own encoder necessary rather than merely tidy.
"""

from __future__ import annotations

import os
import shutil

import bpy

from blender_io.gam_export import TEXTURE_NODE_NAMES
from core import dds
from utils.logging import get_logger

logger = get_logger("blender_io.texture_export")

#: Written directly. Anything else Blender can open is re-saved.
COPYABLE_SUFFIXES = (".dds", ".tga", ".png", ".bmp", ".jpg", ".jpeg")

#: What an image with no file on disk becomes. Every shipped texture
#: measured is DDS and the engine rejected Targa in a controlled test,
#: so there is exactly one right answer here.
FALLBACK_SUFFIX = ".dds"


class TextureExportResult:
    """What happened, in enough detail to explain it to the user."""

    def __init__(self) -> None:
        self.copied: list[str] = []
        self.converted: list[str] = []
        self.skipped: list[str] = []

    @property
    def written(self) -> list[str]:
        return self.copied + self.converted

    def summary(self) -> str:
        parts = []
        if self.copied:
            parts.append(f"{len(self.copied)} copied")
        if self.converted:
            parts.append(f"{len(self.converted)} converted to {FALLBACK_SUFFIX}")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        return ", ".join(parts) if parts else "no textures"


def _image_nodes(obj):
    """Every image node the exporter will look at, per material.

    Matched the way HTAToolchain matches them — by node NAME — so the
    files written are exactly the ones whose names reach the skin
    chunk. Picking them by type instead would happily copy a roughness
    map the model never references.
    """
    mesh = getattr(obj, "data", None)
    for material in getattr(mesh, "materials", None) or []:
        if material is None:
            continue
        tree = getattr(material, "node_tree", None)
        nodes = getattr(tree, "nodes", None)
        if nodes is None:
            continue
        for node in nodes:
            if getattr(node, "type", "") != "TEX_IMAGE":
                continue
            if node.name not in TEXTURE_NODE_NAMES:
                continue
            image = getattr(node, "image", None)
            if image is not None:
                yield material, node, image


def _source_path(image) -> str:
    """The image's file on disk, or an empty string.

    A packed image reports a filepath it no longer reads from, so the
    file has to actually be there before it can be copied.
    """
    raw = getattr(image, "filepath_from_user", None)
    path = raw() if callable(raw) else (getattr(image, "filepath", "") or "")
    if not path:
        return ""
    blender_path = getattr(bpy, "path", None)
    if blender_path is not None and hasattr(blender_path, "abspath"):
        path = blender_path.abspath(path)
    return path if os.path.isfile(path) else ""


def export_textures_beside_model(objects, model_path: str) -> TextureExportResult:
    """Put every referenced texture in the model's own folder.

    Existing files are not overwritten: a game texture already sitting
    there is the one the rest of the game uses, and replacing it with a
    re-save from Blender would alter every other model that shares it.
    """
    result = TextureExportResult()
    folder = os.path.dirname(os.path.abspath(model_path))
    os.makedirs(folder, exist_ok=True)

    seen: set[str] = set()
    for obj in objects:
        for material, node, image in _image_nodes(obj):
            source = _source_path(image)
            name = os.path.basename(source) if source else ""

            # An image edited inside Blender must not be copied from
            # disk: the file on disk is the version BEFORE the edit, and
            # copying it silently discards the work. Blender marks an
            # edited image dirty until it is saved, and that flag is the
            # only thing that distinguishes the two cases.
            edited = bool(getattr(image, "is_dirty", False))
            if edited:
                source = ""
                if name and not name.lower().endswith(".dds"):
                    name = os.path.splitext(name)[0] + FALLBACK_SUFFIX

            if not name:
                base = (getattr(image, "name", "") or material.name or "texture")
                name = os.path.splitext(base)[0] + FALLBACK_SUFFIX
            elif not name.lower().endswith(COPYABLE_SUFFIXES):
                name = os.path.splitext(name)[0] + FALLBACK_SUFFIX
                source = ""

            if name.lower() in seen:
                continue
            seen.add(name.lower())

            target = os.path.join(folder, name)
            if os.path.exists(target) and not edited:
                # Deliberately not overwritten — see the docstring.
                logger.info("%s is already beside the model; left alone", name)
                result.copied.append(name)
                continue

            if edited:
                # The one case that DOES overwrite. The rule protects
                # shared game textures from being clobbered by an
                # accidental re-save; an image the user deliberately
                # painted is the opposite situation, and refusing to
                # write it is what "my changes do not transfer" means.
                logger.info("%s was edited in Blender; re-encoding it", name)

            if source:
                try:
                    shutil.copyfile(source, target)
                except OSError as exc:
                    logger.warning("could not copy %s: %s", name, exc)
                    result.skipped.append(name)
                    continue
                logger.info("copied %s next to the model", name)
                result.copied.append(name)
                continue

            if _save_image(image, target):
                logger.info(
                    "%s exists only inside the .blend, so it was encoded to %s",
                    getattr(image, "name", "?"), name,
                )
                result.converted.append(name)
            else:
                result.skipped.append(name)

    return result


def _save_image(image, target: str) -> bool:
    """Write a Blender image to disk, reporting failure rather than raising.

    A ``.dds`` target goes through this add-on's own encoder, since
    Blender cannot write the format. Anything else is left to Blender,
    which handles its own formats better than we would.
    """
    if target.lower().endswith(".dds"):
        return _save_as_dds(image, target)

    try:
        image.file_format = "TARGA"
        image.save_render(target)
        return True
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.warning(
            "could not save %s: %s", getattr(image, "name", "?"), exc
        )
        return False


def read_image_pixels(image):
    """Read a Blender image as top-down RGBA bytes, or None.

    Blender stores pixels bottom-up as floats; DDS stores them top-down
    as bytes, so this flips as it converts.

    ``foreach_get`` moves the whole buffer in one call. Building the
    list element by element instead takes minutes on a 1024x1024
    texture, and a texture that takes minutes to export is one nobody
    exports.

    A float image holds linear values while a byte image holds display
    ones, so a float image gets the sRGB transfer curve applied. Getting
    that backwards does not corrupt anything — it makes the texture
    uniformly too bright or too dark, which is worth knowing when
    judging a result.
    """
    try:
        width, height = int(image.size[0]), int(image.size[1])
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        logger.warning("could not read the size of %s: %s", getattr(image, "name", "?"), exc)
        return None

    if width < 1 or height < 1:
        logger.warning("%s reports %sx%s", getattr(image, "name", "?"), width, height)
        return None

    count = width * height * 4
    try:
        buffer = [0.0] * count
        image.pixels.foreach_get(buffer)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        try:
            buffer = list(image.pixels)
        except (AttributeError, RuntimeError, TypeError) as exc:
            logger.warning(
                "could not read pixels of %s: %s", getattr(image, "name", "?"), exc
            )
            return None

    if len(buffer) < count:
        logger.warning(
            "%s reports %sx%s but holds %s floats; not encoding it",
            getattr(image, "name", "?"), width, height, len(buffer),
        )
        return None

    linear = bool(getattr(image, "is_float", False))
    table = _SRGB_TABLE if linear else None

    rgba = bytearray(count)
    row = width * 4
    for y in range(height):
        source = (height - 1 - y) * row
        destination = y * row
        for i in range(row):
            value = buffer[source + i]
            if value <= 0.0:
                byte = 0
            elif value >= 1.0:
                byte = 255
            elif table is not None and i % 4 != 3:
                # Alpha is never gamma-encoded.
                byte = table[int(value * 4095.0)]
            else:
                byte = int(value * 255.0 + 0.5)
            rgba[destination + i] = byte

    return bytes(rgba), width, height


def _build_srgb_table() -> tuple[int, ...]:
    """Linear 0..1 to display-space bytes, sampled at 4096 points."""
    table = []
    for index in range(4096):
        value = index / 4095.0
        if value <= 0.0031308:
            encoded = value * 12.92
        else:
            encoded = 1.055 * (value ** (1.0 / 2.4)) - 0.055
        table.append(max(0, min(255, int(encoded * 255.0 + 0.5))))
    return tuple(table)


_SRGB_TABLE = _build_srgb_table()


def _save_as_dds(image, target: str) -> bool:
    """Encode a Blender image to DDS.

    Blender stores pixels bottom-up as floats in 0..1; DDS stores them
    top-down as bytes. Both conversions happen here, in one pass, so
    nothing downstream has to know which way up an image was.
    """
    result = read_image_pixels(image)
    if result is None:
        return False
    rgba, width, height = result

    try:
        written = dds.write(target, rgba, width, height)
    except (dds.DDSError, OSError) as exc:
        logger.warning(
            "could not encode %s to DDS: %s", getattr(image, "name", "?"), exc
        )
        return False

    logger.info(
        "encoded %s as %s (%sx%s, %s bytes)",
        getattr(image, "name", "?"), os.path.basename(target),
        width, height, written,
    )
    return True


# --- keeping the model's own texture names in step --------------------


def retarget_model_textures(model_path: str, objects) -> list[str]:
    """Rewrite the model's texture names to the images Blender is using.

    Copying a new image next to the model does nothing on its own: the
    ``.gam`` names its textures in the skin chunk, and until that name
    changes the game goes on loading the old file. Which is exactly
    what "I swapped the texture in the material and nothing happened"
    is.

    The skin chunk is patched in place, so the file keeps its size and
    every byte this add-on does not understand is preserved. Slots are
    matched by number — node ``Diffuse`` is slot 0, ``Bump`` slot 1 and
    so on — and materials by their order in the model, which is the
    order ``apply_materials`` puts them in the Blender slots.

    Returns one line per change, for reporting.
    """
    from formats.exm.gam import Chunk, read_container, write_container
    from formats.exm.skin import (
        SKIN_CHUNK_ID,
        TEXTURE_NAME_SIZE,
        parse_skin,
        set_texture,
    )

    wanted = _wanted_textures(objects)
    if not wanted:
        return []

    try:
        subtype, chunks = read_container(model_path)
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        logger.warning("could not reopen %s: %s", model_path, exc)
        return []

    changes: list[str] = []
    rebuilt: list[Chunk] = []

    for chunk in chunks:
        data = chunk.data
        if chunk.chunk_id == SKIN_CHUNK_ID:
            try:
                skin = parse_skin(data, source_file=model_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not read the skin chunk: %s", exc)
                rebuilt.append(chunk)
                continue

            for index, material in enumerate(skin.materials):
                for texture in material.textures:
                    replacement = wanted.get((index, texture.slot))
                    if not replacement or replacement == texture.filename:
                        continue
                    if len(replacement.encode("latin-1", "replace")) >= TEXTURE_NAME_SIZE:
                        logger.warning(
                            "%s is too long for the model's %s-byte name field",
                            replacement, TEXTURE_NAME_SIZE,
                        )
                        continue
                    try:
                        data = set_texture(
                            data, index, texture.slot, replacement, model_path
                        )
                    except (KeyError, IndexError, ValueError) as exc:
                        logger.warning("could not retarget slot: %s", exc)
                        continue
                    changes.append(
                        f"material {index} slot {texture.slot}: "
                        f"{texture.filename} -> {replacement}"
                    )

        rebuilt.append(Chunk(chunk_id=chunk.chunk_id, data=data))

    if changes:
        with open(model_path, "wb") as handle:
            handle.write(write_container(subtype, rebuilt))
        for line in changes:
            logger.info("retargeted %s", line)

    return changes


def _wanted_textures(objects) -> dict:
    """``(material index, slot) -> filename`` from the Blender materials.

    Material index is the slot on the Blender mesh, which
    ``apply_materials`` fills in the model's own order.
    """
    from blender_io.texture_bridge import TEXTURE_SLOT_NAMES

    slot_of_name = {name: index for index, name in enumerate(TEXTURE_SLOT_NAMES)}
    wanted: dict = {}

    for obj in objects:
        mesh = getattr(obj, "data", None)
        materials = list(getattr(mesh, "materials", []) or [])
        for index, material in enumerate(materials):
            tree = getattr(material, "node_tree", None)
            if tree is None:
                continue
            for node in tree.nodes:
                if getattr(node, "type", "") != "TEX_IMAGE":
                    continue
                slot = slot_of_name.get(getattr(node, "name", ""))
                if slot is None:
                    continue
                image = getattr(node, "image", None)
                if image is None:
                    continue
                source = _source_path(image)
                name = os.path.basename(source) if source else ""
                if not name:
                    name = os.path.splitext(getattr(image, "name", ""))[0] + FALLBACK_SUFFIX
                if getattr(image, "is_dirty", False) and not name.lower().endswith(".dds"):
                    name = os.path.splitext(name)[0] + FALLBACK_SUFFIX
                wanted.setdefault((index, slot), name)

    return wanted
