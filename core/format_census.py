# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tabulate the vertex formats and shaders a whole game folder uses.

Why
---

The vertex layout table in ``formats/exm/gam.py`` was built from six
models. A type not in it falls back to guessing from the data, and the
guess is wrong often enough to matter: it put a UV of -4.2e37 on
``agro_post``, and reads UVs out of the normal field on any model whose
scale was never applied.

Six models cannot say which formats a 1072-entry catalogue contains.
This walks all of them and counts, so the table grows from measurement
rather than from whichever model happened to be uploaded next. Same for
shaders, which keep arriving one surprise at a time — ``diffuse``,
then ``bump``, then ``DiffuseAO``, then ``diffuse_detail``.

Nothing here interprets anything. It counts what is there and names
what is not understood.
"""

from __future__ import annotations

import collections
import dataclasses
import os
import struct

from formats.exm.gam import (
    MESH_CHUNK_ID,
    VERTEX_LAYOUTS,
    VERTEX_TYPE_OFFSET,
    Chunk,
    _find_index_buffer,
    read_container,
)
from formats.exm.skin import SHADER_PROFILES, SKIN_CHUNK_ID, parse_skin
from utils.logging import get_logger

logger = get_logger("core.format_census")

_MESH_HEADER_SIZE = 72
_STRIDE_OFFSET = 56
_VERTEX_COUNT_OFFSET = 64
_TRIANGLE_COUNT_OFFSET = 68


@dataclasses.dataclass
class Census:
    """What a folder of models contains."""

    models_read: int = 0
    models_failed: int = 0

    #: (vertex_type, stride) -> number of meshes
    vertex_formats: collections.Counter = dataclasses.field(
        default_factory=collections.Counter
    )
    #: (vertex_type, stride) -> one model that carries it
    vertex_examples: dict = dataclasses.field(default_factory=dict)

    #: shader name -> number of materials
    shaders: collections.Counter = dataclasses.field(
        default_factory=collections.Counter
    )
    shader_examples: dict = dataclasses.field(default_factory=dict)

    #: texture slot -> number of texture records
    slots: collections.Counter = dataclasses.field(
        default_factory=collections.Counter
    )

    #: model -> (mesh count, material count) where the two disagree
    mesh_material_mismatch: dict = dataclasses.field(default_factory=dict)

    failures: dict = dataclasses.field(default_factory=dict)


def _walk_mesh_headers(block: bytes):
    """Yield (vertex_type, stride) for each mesh, without decoding it."""
    position = 0
    while position + _MESH_HEADER_SIZE <= len(block):
        stride = struct.unpack_from("<I", block, position + _STRIDE_OFFSET)[0]
        vertex_type = struct.unpack_from("<I", block, position + VERTEX_TYPE_OFFSET)[0]
        count = struct.unpack_from("<I", block, position + _VERTEX_COUNT_OFFSET)[0]
        triangles = struct.unpack_from(
            "<I", block, position + _TRIANGLE_COUNT_OFFSET
        )[0]

        if not (12 <= stride <= 256 and 0 < count < 500_000):
            return
        if triangles >= 500_000:
            return

        yield vertex_type, stride

        data_offset = position + _MESH_HEADER_SIZE
        index_offset = _find_index_buffer(block, data_offset, stride, count, triangles)
        if index_offset is None:
            return
        position = index_offset + triangles * 6


def take_census(folder: str, limit: int | None = None) -> Census:
    """Walk every ``.gam`` under ``folder`` and count what they contain."""
    census = Census()

    for root, _dirs, files in os.walk(folder):
        for filename in sorted(files):
            if not filename.lower().endswith(".gam"):
                continue
            if limit is not None and census.models_read >= limit:
                return census

            path = os.path.join(root, filename)
            try:
                _subtype, chunks = read_container(path)
            except Exception as exc:  # noqa: BLE001 - counted, not raised
                census.models_failed += 1
                census.failures[filename] = str(exc)[:120]
                continue

            census.models_read += 1
            _count_one(census, filename, chunks)

    return census


def _count_one(census: Census, filename: str, chunks: list[Chunk]) -> None:
    by_id = {c.chunk_id: c.data for c in chunks}

    mesh_count = 0
    if MESH_CHUNK_ID in by_id:
        for vertex_type, stride in _walk_mesh_headers(by_id[MESH_CHUNK_ID]):
            mesh_count += 1
            key = (vertex_type, stride)
            census.vertex_formats[key] += 1
            census.vertex_examples.setdefault(key, filename)

    material_count = 0
    if SKIN_CHUNK_ID in by_id:
        try:
            skin = parse_skin(by_id[SKIN_CHUNK_ID], filename)
        except Exception:  # noqa: BLE001 - a skin we cannot read is counted below
            census.failures.setdefault(filename, "skin chunk unreadable")
            return

        material_count = len(skin.materials)
        for material in skin.materials:
            # Counted as written, so the census still shows how the
            # game spells each one — that inconsistency is itself a
            # finding, and folding it away here would hide it.
            census.shaders[material.shader] += 1
            census.shader_examples.setdefault(material.shader, filename)
            for texture in material.textures:
                census.slots[texture.slot] += 1

    if mesh_count and material_count and mesh_count != material_count:
        census.mesh_material_mismatch[filename] = (mesh_count, material_count)


def format_census(census: Census) -> str:
    """Render a census for the system console."""
    lines = [
        "=" * 68,
        "Vertex Format Census",
        "=" * 68,
        f"{census.models_read} model(s) read, {census.models_failed} unreadable",
        "",
        "Vertex formats (type, stride) -> meshes:",
    ]

    for (vertex_type, stride), count in census.vertex_formats.most_common():
        known = VERTEX_LAYOUTS.get(vertex_type)
        if known is not None and known[0] == stride:
            status = "+".join(known[1])
        else:
            status = "*** NOT IN THE MEASURED TABLE ***"
        example = census.vertex_examples[(vertex_type, stride)]
        lines.append(
            f"  type {vertex_type:3} stride {stride:3}  {count:6} mesh(es)  "
            f"{status}   e.g. {example}"
        )

    lines += ["", "Shaders -> materials:"]
    for shader, count in census.shaders.most_common():
        known = (
            ""
            if shader.lower() in {k.lower() for k in SHADER_PROFILES}
            else "   (no measured profile)"
        )
        lines.append(
            f"  {shader!r:44} {count:6}{known}   e.g. {census.shader_examples[shader]}"
        )

    lines += ["", "Texture slots -> records:"]
    for slot, count in sorted(census.slots.items()):
        lines.append(f"  slot {slot}: {count}")

    if census.mesh_material_mismatch:
        lines += [
            "",
            f"{len(census.mesh_material_mismatch)} model(s) whose mesh count "
            "differs from their material count.",
            "  How a mesh picks its material is settled only where the two "
            "lists are parallel; these are the models that would tell them "
            "apart.",
        ]
        for name, (meshes, materials) in sorted(
            census.mesh_material_mismatch.items()
        )[:12]:
            lines.append(f"    {name}: {meshes} mesh(es), {materials} material(s)")

    if census.failures:
        lines += ["", f"{len(census.failures)} model(s) could not be read:"]
        for name, reason in sorted(census.failures.items())[:12]:
            lines.append(f"    {name}: {reason}")

    return "\n".join(lines)
