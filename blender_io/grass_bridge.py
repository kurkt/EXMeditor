# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Put a map's grass in the scene.

72529 tufts on the sample map, across six models. That count is the
whole design problem: a separate object each is more than Blender wants
to carry, and the difference between a map that opens and one that does
not.

So every tuft of one type shares that type's mesh datablock. Blender
holds one copy of the geometry and one object per placement, which is
what a linked duplicate is. There is still a cap, because a map is
worth opening even when its grass is not.
"""

from __future__ import annotations

import math

import bpy

from utils.logging import get_logger

logger = get_logger("blender_io.grass_bridge")

#: Collection the tufts are linked into.
GRASS_COLLECTION = "ExM_Grass"

#: Custom property naming which grass model a tuft came from.
GRASS_TYPE_PROP = "exm_grass_type"


def build_grass(
    field,
    collection,
    game_root: str | None,
    *,
    transform=None,
) -> int:
    """Place a map's grass. Returns how many tufts were placed.

    All of it. There used to be a cap here, and it was the wrong shape
    of control: a map's grass is either wanted or it is not, and a
    partial field is neither the map nor a lighter version of it — it
    is a map with holes in it, in whichever places the stride happened
    to skip. Whether to import grass at all is now the switch.
    """
    if field is None or not field.total:
        return 0

    meshes = _load_models(field, game_root, transform)
    if not meshes:
        logger.warning("no grass model could be loaded; nothing to place")
        return 0

    placed = 0

    for type_index, placements in sorted(field.by_type.items()):
        mesh = meshes.get(type_index)
        if mesh is None:
            continue

        for index in range(len(placements)):
            tuft = placements[index]
            obj = bpy.data.objects.new(f"{GRASS_COLLECTION}_{type_index}_{index}", mesh)
            obj[GRASS_TYPE_PROP] = type_index
            _place(obj, tuft, transform)
            try:
                collection.objects.link(obj)
            except (AttributeError, RuntimeError, TypeError) as exc:
                logger.debug("could not link a tuft: %s", exc)
                return placed
            placed += 1

    if placed < field.total:
        logger.warning(
            "grass: placed %s of %s tuft(s) — the rest belong to model(s) "
            "that could not be loaded", placed, field.total,
        )
    else:
        logger.info("grass: %s tuft(s) from %s model(s)", placed, len(meshes))
    return placed


def _load_models(field, game_root: str | None, transform) -> dict:
    """One mesh per grass type, built once and shared by every tuft."""
    from blender_io.mesh_bridge import build_model_mesh
    from formats.exm.gam import read_model
    from formats.exm.model_catalog import resolve_game_relative_path

    meshes: dict = {}
    missing: list = []

    for type_index in sorted(field.by_type):
        reference = field.model_for(type_index)
        path = (
            resolve_game_relative_path(reference, game_root)
            if game_root and reference
            else None
        )
        if not path:
            missing.append(reference or f"type {type_index}")
            continue

        try:
            model = read_model(path)
            meshes[type_index] = build_model_mesh(
                model,
                f"ExM_Grass_{type_index}",
                transform=transform,
                game_root=game_root,
            )
            _report_model(type_index, path, model, game_root)
        except Exception as exc:  # noqa: BLE001 - counted, never fatal
            logger.debug("could not build %s: %s", path, exc)
            missing.append(reference)

    if missing:
        # The paths in the file name .sam models that do not exist in
        # any build examined; the reader rewrites them to .gam, and a
        # miss here means the .gam is absent too.
        logger.warning(
            "%s grass model(s) not found: %s",
            len(missing), ", ".join(sorted(set(missing))[:4]),
        )
    return meshes


def _report_model(type_index: int, path: str, model, game_root: str | None) -> None:
    """Say what each grass model asks for and whether it got it.

    Grass is foliage: its texture carries a cutout, and a cutout that
    does not resolve renders as a solid rectangle rather than as
    nothing — which looks like a texture that loaded wrongly rather
    than one that failed. Naming the file and the shader tells the two
    apart from the log.
    """
    import os

    from blender_io.texture_bridge import resolve_texture

    logger.info(
        "  grass %s: %s, %s material(s)",
        type_index, os.path.basename(path), len(model.materials),
    )
    for index, material in enumerate(model.materials):
        for slot, name in zip(material.slots, material.textures):
            found = resolve_texture(name, game_root) if game_root else None
            state = "ok" if found else "NOT FOUND"
            logger.info(
                "      material %s slot %s: %s %r -> %s",
                index, slot, name, material.shader, state,
            )
            if found:
                _report_alpha(found)


def _report_alpha(path: str) -> None:
    """Whether the texture carries the cutout foliage needs."""
    from core import dds

    try:
        data = open(path, "rb").read()
        header = dds.read_header(data)
        opaque, clear = dds.alpha_profile(data)
    except Exception as exc:  # noqa: BLE001 - a diagnostic must not raise
        logger.debug("could not profile %s: %s", path, exc)
        return

    logger.info(
        "          %s, %.1f%% solid, %.1f%% clear, alpha profile only "
        "(what makes it transparent is the shader)",
        header["format"], opaque * 100, clear * 100,
    )


def _place(obj, tuft, transform) -> None:
    """Position and turn one tuft.

    The heading is stored as a unit vector rather than an angle — the
    two components square to 1.000 on every record measured — so the
    turn comes from ``atan2`` of it.
    """
    from utils.math import Vector3

    if transform is not None:
        try:
            position = transform.game_to_blender_position(
                Vector3(tuft.x, tuft.y, tuft.z)
            )
            obj.location = (position.x, position.y, position.z)
        except (AttributeError, TypeError):
            obj.location = (tuft.x, tuft.z, tuft.y)
    else:
        obj.location = (tuft.x, tuft.z, tuft.y)

    cos, sin = tuft.heading
    try:
        obj.rotation_euler = (0.0, 0.0, math.atan2(sin, cos))
        scale = tuft.scale or 1.0
        obj.scale = (scale, scale, scale)
    except (AttributeError, TypeError):
        pass
