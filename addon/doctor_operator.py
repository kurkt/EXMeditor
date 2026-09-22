# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Model Doctor operator.

Two buttons' worth of behaviour behind one: with no repair checked it
reports and changes nothing, and with any checked it writes a repaired
copy beside the original rather than over it.

Never in place. A repair here is a hypothesis being tested in the game
— "does the shipped material signature fix the shading" is a question,
not a known fix — and the answer needs the original to compare against.
Writing over the input would destroy the control half of the
experiment.

Reports go to the system console like the other diagnostics; the
operator's own report line carries only the verdict, since Blender's
status bar truncates anything longer than a sentence.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, StringProperty

from core.model_doctor import RepairOptions, diagnose, format_report, repair
from utils.errors import EXMeditorError
from utils.logging import get_logger

logger = get_logger("addon.doctor")


class EXM_OT_model_doctor(bpy.types.Operator):
    """Measure a .gam against shipped models, and optionally repair it."""

    bl_idname = "exmachina.model_doctor"
    bl_label = "Model Doctor"
    bl_description = (
        "Check a .gam for the defects that separate generated models from "
        "shipped ones — unnormalised normals, material values no shipped "
        "model uses, a shader the vertices cannot satisfy, textures the "
        "engine will not find — and optionally write a repaired copy"
    )
    # REGISTER and UNDO for the same reason as Model Forensics: without
    # an undo step Blender never shows "Adjust Last Operation", and the
    # properties below become unreachable.
    bl_options = {"REGISTER", "UNDO"}

    model: StringProperty(
        name="Model",
        description="The .gam file to examine",
        subtype="FILE_PATH",
    )

    normalise_normals: BoolProperty(
        name="Normalise Normals",
        description=(
            "Rescale every normal to unit length. Object scale left "
            "unapplied in Blender rides into the normal and the model draws "
            "with wrong lighting"
        ),
        default=False,
    )

    shipped_material_values: BoolProperty(
        name="Shipped Material Values",
        description=(
            "Set D3DMATERIAL9 to the values every shipped model carries: "
            "diffuse 0.8, specular off, power 0. HTAToolchain writes all "
            "seventeen floats as 1.0"
        ),
        default=False,
    )

    simplify_shader: BoolProperty(
        name="Simplify To 'diffuse'",
        description=(
            "Retarget the material to the lightest shader the game itself "
            "ships — one diffuse texture, no vertex colour, no tangents — "
            "and convert the vertices to type 7 to match. Discards tangent "
            "data, which that shader does not use"
        ),
        default=False,
    )

    #: No ``invoke`` override, for the reason spelled out in
    #: ``forensics_operator``: the file browser writing back into a
    #: popup's properties crashed Blender outright.

    def execute(self, context):
        path = self._absolute(self.model)
        if not path:
            self.report(
                {"INFO"},
                "Set Model in the 'Adjust Last Operation' panel at the "
                "bottom left, or press F9",
            )
            return {"FINISHED"}

        if not os.path.isfile(path):
            self.report({"ERROR"}, f"No such file: {path}")
            return {"CANCELLED"}

        options = RepairOptions(
            normalise_normals=self.normalise_normals,
            shipped_material_values=self.shipped_material_values,
            simplify_shader=self.simplify_shader,
        )

        try:
            report = diagnose(path)
            print(format_report(report))

            from addon.preferences import get_game_root
            from formats.exm.model_catalog import normalise_game_root

            try:
                root = normalise_game_root(get_game_root(context))
            except Exception:  # noqa: BLE001 - preferences are optional here
                root = None
            for line in report_texture_chain(path, root):
                print(line)

            if not any(
                (
                    options.normalise_normals,
                    options.shipped_material_values,
                    options.simplify_shader,
                )
            ):
                verdict = (
                    f"{len(report.problems)} problem(s), "
                    f"{len(report.warnings)} warning(s) — see the system console"
                )
                self.report(
                    {"WARNING"} if report.problems else {"INFO"}, verdict
                )
                return {"FINISHED"}

            destination = self._repaired_path(path)
            actions = repair(path, destination, options)

            print()
            print(f"Repaired copy written to {destination}")
            for action in actions:
                print(f"  - {action}")
            print()
            print(format_report(diagnose(destination)))

            self.report({"INFO"}, f"Wrote {os.path.basename(destination)}")
            return {"FINISHED"}

        except EXMeditorError as exc:
            logger.error("model doctor failed on %s: %s", path, exc.message)
            self.report({"ERROR"}, f"Model Doctor failed: {exc.message}")
            return {"CANCELLED"}
        except (OSError, ValueError) as exc:
            # ValueError is how a refused conversion arrives — a mesh
            # with several vertex buffers, or a target format needing a
            # field the source lacks. Both are answers, not crashes.
            logger.error("model doctor failed on %s: %s", path, exc)
            self.report({"ERROR"}, f"Model Doctor failed: {exc}")
            return {"CANCELLED"}

    @staticmethod
    def _absolute(value: str) -> str:
        return bpy.path.abspath(value) if value else ""

    @staticmethod
    def _repaired_path(path: str) -> str:
        """``foo.gam`` -> ``foo_repaired.gam``, never overwriting."""
        stem, extension = os.path.splitext(path)
        candidate = f"{stem}_repaired{extension}"
        counter = 2
        while os.path.exists(candidate):
            candidate = f"{stem}_repaired{counter}{extension}"
            counter += 1
        return candidate


_CLASSES = (EXM_OT_model_doctor,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


def report_texture_chain(model_path: str, game_root: str | None) -> list[str]:
    """Follow every step between a texture name and a visible image.

    The Model Doctor answers "is the file on disk", which is a
    different question from "does the texture appear", and the gap
    between them is where this has been stuck. A name can resolve to a
    real file and still produce a flat-coloured model, because the
    importer's own resolver is not the doctor's, because Blender can
    decline a format, or because an empty image datablock left over
    from an earlier import is being reused by name.

    So this walks the actual import path, in the user's own Blender,
    and says which step failed.
    """
    from formats.exm.gam import read_model
    from blender_io.texture_bridge import (
        _has_pixels,
        build_texture_index,
        resolve_texture,
    )

    lines = ["", "=" * 68, "Texture chain", "=" * 68]

    if not game_root:
        lines.append("No Game Folder is set in the add-on preferences.")
        lines.append("Nothing can resolve without it.")
        return lines

    indexed = build_texture_index(game_root, refresh=True)
    lines.append(f"game folder: {game_root}")
    lines.append(f"texture index: {indexed} image file(s)")

    model = read_model(model_path)
    if not model.materials:
        lines.append("This model declares no materials.")
        return lines

    for number, material in enumerate(model.materials):
        lines.append(f"  material[{number}] shader {material.shader!r}")
        if not material.textures:
            lines.append("      names no texture at all")
            continue

        for position, name in enumerate(material.textures):
            slot = material.slots[position] if position < len(material.slots) else position
            lines.append(f"      slot {slot}: {name}")

            path = resolve_texture(name, game_root)
            if path is None:
                lines.append("          NOT IN THE INDEX — the importer cannot find it")
                continue
            lines.append(f"          resolved: {path}")

            existing = bpy.data.images.get(name)
            if existing is not None:
                state = "has pixels" if _has_pixels(existing) else "EMPTY — no pixel data"
                lines.append(
                    f"          already in this .blend: {tuple(existing.size)} {state}"
                )

            try:
                probe = bpy.data.images.load(path, check_existing=True)
            except RuntimeError as exc:
                lines.append(f"          BLENDER REFUSED IT: {exc}")
                lines.append("          the SDK decoder handles this on import")
                continue

            if _has_pixels(probe):
                lines.append(
                    f"          Blender loaded it: {tuple(probe.size)}, "
                    f"{getattr(probe, 'depth', '?')}-bit"
                )
            else:
                lines.append("          Blender returned an EMPTY image for it")

    return lines
