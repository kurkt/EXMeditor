# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Edit a texture without changing every model that shares it.

The problem
-----------

The game shares textures aggressively. ``concrete.dds`` dresses
buildings across a map; ``brick+metall.dds`` more. A material names a
bare filename and the engine has no per-model override, so painting on
one of them changes every model that names it — silently, and somewhere
the person editing was not looking.

Forking is the only safe edit the format allows: write the image under
a new name, and re-point this model's skin chunk at it. That is three
things which have to agree — a file where the engine looks, a name in
the ``.gam``, and the image in Blender following — and getting one of
them wrong looks exactly like the edit not working.

So it is one button. Select the object, press it, paint.

What it does
------------

1. Works out which texture the object actually uses.
2. If that texture is shared, copies it beside the model under a name
   of its own and re-points the model at the copy.
3. Points Blender's material at the copy, so the next save writes there
   and not over the original.
4. Leaves the file open and ready to paint.

A texture already unique to this model is left alone: forking it again
would only add a second copy of the same thing.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from utils.logging import get_logger

logger = get_logger("addon.edit_texture")


class EXM_OT_edit_texture(bpy.types.Operator):
    """Detach this object's texture if it is shared, then edit it."""

    bl_idname = "exmachina.edit_texture"
    bl_label = "Edit Texture"
    bl_description = (
        "Give this model its own copy of the texture before you paint. "
        "The game shares textures between models, so editing one in "
        "place changes every model that names it"
    )
    bl_options = {"REGISTER", "UNDO"}

    slot: IntProperty(
        name="Slot",
        description="Which texture to edit. 0 is the diffuse map",
        default=0,
        min=0,
        max=4,
    )

    new_name: StringProperty(
        name="New Name",
        description=(
            "Filename for the copy. Empty appends the model's own name "
            "to the texture's"
        ),
    )

    always_fork: BoolProperty(
        name="Always Copy",
        description=(
            "Copy even when the texture is already unique to this model. "
            "Off skips the copy in that case, since there is nothing to "
            "protect"
        ),
        default=False,
    )

    def execute(self, context):
        obj = context.active_object
        if obj is None:
            self.report({"ERROR"}, "Select the object first")
            return {"CANCELLED"}

        image, node = _texture_of(obj, self.slot)
        if image is None:
            self.report(
                {"ERROR"},
                f"This object has no texture in slot {self.slot}",
            )
            return {"CANCELLED"}

        shared, sharing = _is_shared(context, obj, image)
        if shared or self.always_fork:
            logger.info(
                "%s is named by %s model file(s) in the game; giving this "
                "one its own copy", image.name, sharing,
            )
            result = bpy.ops.exmachina.fork_texture(
                slot=self.slot,
                new_name=self.new_name,
                from_blender=True,
            )
            if "FINISHED" not in result:
                return {"CANCELLED"}
            image, node = _texture_of(obj, self.slot)
            if image is None:
                self.report({"ERROR"}, "The copy did not take")
                return {"CANCELLED"}
        else:
            logger.info(
                "%s is named by no other model in the game; editing it in "
                "place", image.name,
            )

        _open_for_painting(context, image)

        self.report(
            {"INFO"},
            f"Editing {image.name} — paint, then Save Texture As DDS",
        )
        return {"FINISHED"}


def _texture_of(obj, slot: int):
    """The image in a slot, and the node holding it."""
    from blender_io.texture_bridge import TEXTURE_SLOT_NAMES

    if not 0 <= slot < len(TEXTURE_SLOT_NAMES):
        return None, None
    wanted = TEXTURE_SLOT_NAMES[slot]

    mesh = getattr(obj, "data", None)
    for material in list(getattr(mesh, "materials", []) or []):
        tree = getattr(material, "node_tree", None)
        if tree is None:
            continue
        for node in tree.nodes:
            if getattr(node, "name", "") != wanted:
                continue
            image = getattr(node, "image", None)
            if image is not None:
                return image, node
    return None, None


def _is_shared(context, obj, image):
    """Whether editing this image in place would reach another model.

    Asked of the GAME, not of the open scene. The scene answer was
    wrong in the dangerous direction: 53% of the game's 1006 texture
    names are used by more than one of its 1391 models — concrete.dds
    by 43 — while a map places only a fraction of those models. A
    texture shared forty-three ways reads as "used by one model here"
    as often as not, and the edit then went in place and changed the
    other forty-two.

    Returns ``(shared, how many models name it)``. Falls back to the
    scene count when the game folder is unknown, and says so: a wrong
    answer that announces itself is survivable.
    """
    from addon.preferences import get_game_root
    from blender_io.mesh_provider import MODEL_PATH_PROP
    from formats.exm.model_catalog import normalise_game_root
    from formats.exm.texture_usage import models_naming

    name = os.path.basename(getattr(image, "name", "") or "")
    root = normalise_game_root(get_game_root(context))
    if root:
        mesh = getattr(obj, "data", None)
        own = (mesh.get(MODEL_PATH_PROP) if mesh is not None else "") or ""
        users = models_naming(name, root)
        if users:
            mine = os.path.normcase(own) if own else None
            others = {
                path for path in users
                if mine is None or os.path.normcase(path) != mine
            }
            return bool(others), len(users)
        # Never heard of: an unknown is not evidence of safety.
        logger.info(
            "%s is not named by any model under the game folder; treating "
            "it as shared", name,
        )
        return True, 0

    logger.warning(
        "no game folder set, so %s can only be checked against this scene "
        "— which is not the game. Copying to be safe.", name,
    )
    return True, _scene_sharing(image)


def _scene_sharing(image) -> int:
    """How many distinct models in the OPEN SCENE use this image.

    Kept for the message only. It is not the question — see
    :func:`_is_shared` — and nothing decides on it any more.
    """
    from blender_io.mesh_provider import MODEL_PATH_PROP

    models = set()
    for other in bpy.data.objects:
        mesh = getattr(other, "data", None)
        if mesh is None or not hasattr(mesh, "materials"):
            continue
        if not _uses(mesh, image):
            continue
        models.add(mesh.get(MODEL_PATH_PROP) or getattr(mesh, "name", ""))

    return len(models) or 1


def _uses(mesh, image) -> bool:
    for material in list(getattr(mesh, "materials", []) or []):
        tree = getattr(material, "node_tree", None)
        if tree is None:
            continue
        for node in tree.nodes:
            if getattr(node, "image", None) is image:
                return True
    return False


def _open_for_painting(context, image) -> None:
    """Show the image somewhere the person can paint on it.

    Best effort: an Image Editor already on screen takes it, otherwise
    the message says where to find it. Rearranging somebody's workspace
    uninvited is worse than one more click.
    """
    try:
        screen = getattr(context, "screen", None)
        for area in getattr(screen, "areas", []) or []:
            if getattr(area, "type", "") != "IMAGE_EDITOR":
                continue
            for space in getattr(area, "spaces", []) or []:
                if hasattr(space, "image"):
                    space.image = image
                    return
    except (AttributeError, TypeError) as exc:
        logger.debug("could not show %s: %s", image.name, exc)


_CLASSES = (EXM_OT_edit_texture,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
