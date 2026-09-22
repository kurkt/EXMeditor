# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Give one model its own copy of a texture, so editing it is safe.

The problem this exists for
---------------------------

The game shares textures aggressively. ``concrete.dds`` is used by
buildings across the map; ``brick+metall.dds`` by more. Painting on one
of them in Blender and saving changes **every model that references
it**, which is almost never what someone editing one wall wants. The
damage is silent and shows up somewhere else entirely.

The engine gives no way to scope a texture to a model — a material
names a bare filename and that is all. So the only safe edit is to
fork: write the image under a new name, and re-point this model's skin
chunk at it. Every other model keeps the original, byte for byte.

That is one operation and three things that have to agree, which is
why it is a button rather than a note in the documentation:

* the new file has to land where the engine looks — beside the ``.gam``
* the model's skin chunk has to name it
* the image in Blender has to follow, or the next save writes to the
  old file again and undoes the point of the exercise
"""

from __future__ import annotations

import os
import re
import shutil

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from blender_io.texture_export import read_image_pixels
from core import dds
from formats.exm.gam import Chunk, read_container, write_container
from formats.exm.skin import SKIN_CHUNK_ID, TEXTURE_NAME_SIZE, parse_skin, set_texture
from utils.logging import get_logger

logger = get_logger("addon.fork_texture")

from blender_io.mesh_provider import MODEL_PATH_PROP


def _base_name(stem: str, model: str) -> str:
    """Fold a texture stem down to one copy of the model suffix.

    Forking a fork used to append the model name again every time::

        concrete
        concrete_heavy_bigwall1
        concrete_heavy_bigwall1_heavy_bigwall1
        ... and then a name too long for the 44-byte field

    Stripping one occurrence was not enough, because a model already
    carrying two of them stayed over the limit. Every trailing
    repetition goes, and the counter with it.
    """
    suffix = f"_{model}"
    root = re.sub(r"_\d+$", "", stem)

    while root.endswith(suffix + suffix):
        root = root[: -len(suffix)]
    if not root.endswith(suffix):
        root = f"{root}{suffix}"
    return root


def _fit(name: str, limit: int) -> str:
    """Shorten a filename to fit a fixed field, keeping its extension.

    The field holds ``limit`` bytes including the terminating NUL, so
    the name itself has to come in under it.
    """
    if len(name.encode("latin-1", "replace")) < limit:
        return name

    stem, extension = os.path.splitext(name)
    room = limit - 1 - len(extension.encode("latin-1", "replace"))
    while len(stem.encode("latin-1", "replace")) > room and stem:
        stem = stem[:-1]
    return f"{stem}{extension}"


class EXM_OT_fork_texture(bpy.types.Operator):
    """Copy a texture under a new name and point only this model at it."""

    bl_idname = "exmachina.fork_texture"
    bl_label = "Fork Texture"
    bl_description = (
        "Write the selected object's texture under a new name beside its "
        ".gam and re-point that model at it. The game shares textures "
        "between models, so editing one in place changes every model "
        "using it — this scopes the edit to one"
    )
    bl_options = {"REGISTER"}

    model: StringProperty(
        name="Model",
        description=(
            "The .gam to re-point. Empty looks it up from the selected "
            "object's model id"
        ),
        subtype="FILE_PATH",
    )

    new_name: StringProperty(
        name="New Name",
        description=(
            "Filename for the copy, without a folder. Empty appends the "
            "model's own name to the texture's"
        ),
    )

    slot: IntProperty(
        name="Slot",
        description="Which texture slot to fork. 0 is the diffuse map",
        default=0,
        min=0,
        max=4,
    )

    material_index: IntProperty(
        name="Material",
        description="Which material to re-point. -1 does every material using this slot",
        default=-1,
        min=-1,
    )

    fork_model: BoolProperty(
        name="Fork The Model Too",
        description=(
            "Copy the .gam as well, so only THIS placement changes. Off "
            "edits the model in place, which changes every object on the "
            "map built from it — the map may hold dozens"
        ),
        default=False,
    )

    catalogue: StringProperty(
        name="Catalogue",
        description=(
            "The AnimModels.xml to register a forked model in. Empty "
            "uses the one beside the original"
        ),
        subtype="FILE_PATH",
    )

    from_blender: BoolProperty(
        name="Use The Edited Image",
        description=(
            "Encode what is in Blender, including unsaved paint. Off "
            "copies the original file untouched, which is the right "
            "starting point when the painting has not been done yet"
        ),
        default=True,
    )

    def execute(self, context):
        obj = context.active_object
        if obj is None:
            self.report({"ERROR"}, "Select the object first")
            return {"CANCELLED"}

        model_path = self._model_path(context, obj)
        if not model_path:
            self.report(
                {"ERROR"},
                "Could not find this object's .gam — set Model in the panel below",
            )
            return {"CANCELLED"}

        try:
            subtype, chunks = read_container(model_path)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            self.report({"ERROR"}, f"Could not read {os.path.basename(model_path)}: {exc}")
            return {"CANCELLED"}

        skin_data = next(
            (c.data for c in chunks if c.chunk_id == SKIN_CHUNK_ID), None
        )
        if skin_data is None:
            self.report({"ERROR"}, "That model has no skin chunk")
            return {"CANCELLED"}

        skin = parse_skin(skin_data, source_file=model_path)
        targets = [
            index
            for index, material in enumerate(skin.materials)
            if material.texture_for_slot(self.slot) is not None
            and (self.material_index < 0 or index == self.material_index)
        ]
        if not targets:
            self.report({"ERROR"}, f"No material carries a texture in slot {self.slot}")
            return {"CANCELLED"}

        original = skin.materials[targets[0]].texture_for_slot(self.slot)

        # Fork the model first, so everything below writes to the copy
        # and the shared original is never opened for writing.
        if self.fork_model:
            forked_model = self._fork_model_file(obj, model_path)
            if forked_model is None:
                return {"CANCELLED"}
            model_path = forked_model

        new_name = self._new_name(original, model_path)
        if len(new_name.encode("latin-1", "replace")) >= TEXTURE_NAME_SIZE:
            # Refused before anything is written, so a name that does
            # not fit leaves the model exactly as it was.
            self.report(
                {"ERROR"},
                f"{new_name} does not fit the model's {TEXTURE_NAME_SIZE}-byte name field",
            )
            return {"CANCELLED"}

        folder = os.path.dirname(model_path)
        destination = os.path.join(folder, new_name)

        if not self._write_copy(obj, original, destination, folder):
            return {"CANCELLED"}

        # Patch every material that named the old texture in this slot.
        for index in targets:
            try:
                skin_data = set_texture(
                    skin_data, index, self.slot, new_name, model_path
                )
            except (KeyError, IndexError, ValueError) as exc:
                self.report({"ERROR"}, f"Could not re-point material {index}: {exc}")
                return {"CANCELLED"}

        rebuilt = [
            Chunk(
                chunk_id=chunk.chunk_id,
                data=skin_data if chunk.chunk_id == SKIN_CHUNK_ID else chunk.data,
            )
            for chunk in chunks
        ]
        with open(model_path, "wb") as handle:
            handle.write(write_container(subtype, rebuilt))

        # Read it back. Reporting success without checking is how a
        # write that went somewhere else, or to a file the game does not
        # open, gets mistaken for a change the game ignored.
        confirmed = self._verify(model_path, new_name)
        if not confirmed:
            self.report(
                {"ERROR"},
                f"Wrote {os.path.basename(model_path)} but it does not name "
                f"{new_name} when read back",
            )
            return {"CANCELLED"}

        self._point_blender_at(obj, destination)

        logger.info("forked %s -> %s", original, new_name)
        logger.info("  model:   %s", model_path)
        logger.info("  texture: %s", destination)
        logger.info("  material(s) %s, slot %s — verified on re-read", targets, self.slot)
        scope = (
            "this object only"
            if self.fork_model
            else "EVERY object on the map built from this model"
        )
        self.report(
            {"INFO"},
            f"{os.path.basename(model_path)} now names {new_name} — "
            f"affects {scope}",
        )
        return {"FINISHED"}

    # --- pieces -------------------------------------------------------

    def _model_path(self, context, obj) -> str:
        """Where this object's ``.gam`` lives.

        Read from the mesh, which the importer stamps with it. Looking
        it up again would mean finding the map manifest and walking the
        catalogue from a context that has neither.
        """
        if self.model:
            return bpy.path.abspath(self.model)

        mesh = getattr(obj, "data", None)
        stored = mesh.get(MODEL_PATH_PROP) if mesh is not None else None
        if stored and os.path.isfile(str(stored)):
            return str(stored)
        if stored:
            logger.warning("%s is recorded at %s, which is gone", obj.name, stored)
        return ""

    def _new_name(self, original: str, model_path: str) -> str:
        """The forked filename.

        Forking a fork must not lengthen the name each time. Appending
        the model name blindly turned ``concrete.dds`` into
        ``concrete_heavy_bigwall1_heavy_bigwall1.dds`` and then into a
        name too long for the 44-byte field, leaving the model half
        edited. The suffix is added once and a number after it
        afterwards.
        """
        if self.new_name:
            name = os.path.basename(self.new_name)
            stem, extension = os.path.splitext(name)
            return name if extension.lower() == ".dds" else f"{stem}.dds"

        stem = os.path.splitext(original)[0]
        model = os.path.splitext(os.path.basename(model_path))[0]

        base = _base_name(stem, model)

        folder = os.path.dirname(model_path)
        candidate = f"{base}.dds"
        counter = 2
        while os.path.exists(os.path.join(folder, candidate)):
            candidate = f"{base}_{counter}.dds"
            counter += 1

        # Trim rather than refuse. A name that does not fit is this
        # function's problem to solve, not the user's to work around by
        # inventing one — and refusing at this point leaves them with a
        # model already carrying a name from an earlier attempt.
        return _fit(candidate, TEXTURE_NAME_SIZE)

    def _write_copy(self, obj, original: str, destination: str, folder: str) -> bool:
        """Write the forked texture, from Blender or from the original file."""
        if os.path.exists(destination):
            self.report({"ERROR"}, f"{os.path.basename(destination)} already exists")
            return False

        image = self._image_for_slot(obj)

        if self.from_blender and image is not None:
            result = read_image_pixels(image)
            if result is None:
                self.report({"ERROR"}, f"Could not read the pixels of {image.name}")
                return False
            rgba, width, height = result
            try:
                dds.write(destination, rgba, width, height)
            except (dds.DDSError, OSError) as exc:
                self.report({"ERROR"}, f"Could not write it: {exc}")
                return False
            return True

        source = self._find_original(image, original, folder)
        if not source:
            self.report(
                {"ERROR"},
                f"Could not find {original} to copy — turn on Use The Edited Image",
            )
            return False
        try:
            shutil.copyfile(source, destination)
        except OSError as exc:
            self.report({"ERROR"}, f"Could not copy it: {exc}")
            return False
        return True

    def _image_for_slot(self, obj):
        """The Blender image sitting in the node for this slot."""
        from blender_io.texture_bridge import TEXTURE_SLOT_NAMES

        if not 0 <= self.slot < len(TEXTURE_SLOT_NAMES):
            return None
        wanted = TEXTURE_SLOT_NAMES[self.slot]

        mesh = getattr(obj, "data", None)
        for material in list(getattr(mesh, "materials", []) or []):
            tree = getattr(material, "node_tree", None)
            if tree is None:
                continue
            for node in tree.nodes:
                if getattr(node, "name", "") == wanted:
                    image = getattr(node, "image", None)
                    if image is not None:
                        return image
        return None

    def _fork_model_file(self, obj, model_path: str) -> str | None:
        """Copy the ``.gam`` under a new id and point this object at it.

        Editing a model in place changes every object built from it, and
        a map holds many: one wall model can be placed dozens of times.
        The engine has no per-placement material, so scoping an edit to
        one object means giving it a model of its own.

        Three things have to happen together or the copy is invisible:
        the file, an entry in the catalogue — the game finds models by
        id and never by scanning the disk — and the object's own id.
        """
        stem = os.path.splitext(os.path.basename(model_path))[0]
        suffix = f"_{obj.name}"
        model_id = stem if stem.endswith(suffix) else f"{stem}{suffix}"
        model_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in model_id)
        destination = os.path.join(os.path.dirname(model_path), f"{model_id}.gam")

        if os.path.exists(destination):
            self.report({"ERROR"}, f"{os.path.basename(destination)} already exists")
            return None

        try:
            shutil.copyfile(model_path, destination)
        except OSError as exc:
            self.report({"ERROR"}, f"Could not copy the model: {exc}")
            return None

        catalogue = (
            bpy.path.abspath(self.catalogue)
            if self.catalogue
            else self._catalogue_beside(model_path)
        )
        if not catalogue:
            self.report(
                {"WARNING"},
                f"Wrote {os.path.basename(destination)} but found no "
                "AnimModels.xml to register it in — set Catalogue",
            )
        else:
            try:
                from formats.exm.model_catalog import register_model

                register_model(catalogue, model_id, destination)
                logger.info("registered %s in %s", model_id, catalogue)
            except Exception as exc:  # noqa: BLE001 - reported, not fatal
                self.report({"WARNING"}, f"Could not register it: {exc}")

        obj["exm_id"] = model_id
        logger.info("forked model %s -> %s", model_path, destination)
        return destination

    @staticmethod
    def _catalogue_beside(model_path: str) -> str:
        """The AnimModels.xml nearest the model, walking up."""
        folder = os.path.dirname(model_path)
        while True:
            for name in os.listdir(folder):
                if name.lower() == "animmodels.xml":
                    return os.path.join(folder, name)
            parent = os.path.dirname(folder)
            if parent == folder or os.path.basename(folder).lower() == "models":
                return ""
            folder = parent

    @staticmethod
    def _verify(model_path: str, expected: str) -> bool:
        """Re-read the model and check the new name is really in it."""
        try:
            _subtype, chunks = read_container(model_path)
            block = next(
                c.data for c in chunks if c.chunk_id == SKIN_CHUNK_ID
            )
            skin = parse_skin(block, source_file=model_path)
        except Exception as exc:  # noqa: BLE001 - a failed check is a failure
            logger.warning("could not verify %s: %s", model_path, exc)
            return False

        return any(
            texture.filename == expected
            for material in skin.materials
            for texture in material.textures
        )

    @staticmethod
    def _find_original(image, original: str, folder: str) -> str:
        beside = os.path.join(folder, original)
        if os.path.isfile(beside):
            return beside
        if image is not None:
            try:
                path = bpy.path.abspath(image.filepath_from_user())
            except (AttributeError, RuntimeError):
                path = ""
            if path and os.path.isfile(path):
                return path
        return ""

    def _point_blender_at(self, obj, destination: str) -> None:
        """Make the material use the new file, not the shared one.

        Without this the node still holds the shared image, and the next
        save writes over the very texture this was meant to protect.
        """
        from blender_io.texture_bridge import TEXTURE_SLOT_NAMES

        if not 0 <= self.slot < len(TEXTURE_SLOT_NAMES):
            return
        wanted = TEXTURE_SLOT_NAMES[self.slot]

        try:
            forked = bpy.data.images.load(destination, check_existing=True)
        except RuntimeError as exc:
            logger.debug("could not load %s: %s", destination, exc)
            return

        mesh = getattr(obj, "data", None)
        for material in list(getattr(mesh, "materials", []) or []):
            tree = getattr(material, "node_tree", None)
            if tree is None:
                continue
            for node in tree.nodes:
                if getattr(node, "name", "") == wanted:
                    node.image = forked
                    node.label = os.path.basename(destination)


_CLASSES = (EXM_OT_fork_texture,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
