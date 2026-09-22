# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""``.gam`` model reader.

Two layers: the generic ``ecbnt,t`` chunk container (shared with
``grass.xml``, ``shoreline.xml``, ``level.tile``, ``player_passmap.bin``
and ``normalmap.xml`` — the extension does not predict the format), and
the mesh chunk inside it.

Mesh chunk layout, reverse-engineered and verified against real data —
the stored bounding box matched the actual vertex extents exactly,
which is a strong independent confirmation the field offsets are right::

    byte  0..15  mesh name (NUL-terminated ASCII)
    byte     56  stride (bytes per vertex) = 44
    byte     60  component count = 9
    byte     64  vertex count
    byte     68  triangle count
    byte     72  vertex buffer starts

    vertex (44 bytes):
        0..11   position   3 x float32
       12..23   normal     3 x float32  (unit length, verified 259/259)
       24..27   colour     RGBA 4 x uint8
       28..35   uv         2 x float32
       36..43   uv2        2 x float32

    then: index buffer, triangle_count * 3 * uint16
    then: 6 x float32 bounding box (min xyz, max xyz)

Write support is deliberately absent: exporting geometry back into the
game would need the rest of the chunks (material, shadow, the ``IVR``
and ``HTAParser`` metadata blocks) understood well enough to
regenerate, which they aren't. Import-only is honest about what's
actually solved.
"""

from __future__ import annotations

import dataclasses
import math
import os
import re
import struct

from core import color as color_codec
from core.mesh import Material, MeshData, Model
from utils.errors import ErrorContext, ParsingError
from utils.logging import get_logger
from utils.math import AABB, Vector3

logger = get_logger("formats.exm.gam")

#: Container signature — 7 bytes, NOT 8. Byte 7 is a separate subtype
#: field (0 for models and grass, 3/11/14 for other file kinds), which
#: is easy to mistake for part of the magic since it happens to be 0
#: in a model file.
MAGIC = b"ecbnt,t"

#: Chunk id holding mesh geometry.
MESH_CHUNK_ID = 4

#: Chunk id holding materials and the textures they reference.
SKIN_CHUNK_ID = 15

#: Chunk id holding the node table: the model's own scene graph, and
#: with it the LOCATORS — named empty nodes other things mount on.
NODE_CHUNK_ID = 2

#: One node record. MEASURED on heavy_dot4.gam: chunk 2 is 544 bytes
#: and holds four nodes, 544 / 4 = 136 — ``char[40]`` name, ``int32``
#: parent, ``float[3]`` location, ``float[4]`` rotation quaternion,
#: ``float[16]`` matrix. The layout HTAToolchain reads and writes.
NODE_RECORD_SIZE = 136

#: A locator's name starts with this. ``LP_CANNON01`` on every pillbox
#: body, ``LP_GUN`` on every gun.
LOCATOR_PREFIX = "LP_"

#: Texture filenames are ASCII and always carry an extension; anything
#: shorter than this is noise from the surrounding binary.
_MIN_STRING = 3

_VERTEX_STRIDE_OFFSET = 56
_COMPONENT_COUNT_OFFSET = 60
_VERTEX_COUNT_OFFSET = 64
_TRIANGLE_COUNT_OFFSET = 68
_VERTEX_DATA_OFFSET = 72
#: The vertex layout confirmed field-by-field against real data
#: (position, normal, colour, uv, uv2). Other strides exist and are
#: handled by detection rather than assumption — see _read_mesh_chunk.
#: Header offset holding which NODE in chunk 2 this mesh belongs to.
#: ``machine_house1`` settles it: 30 meshes storing 0..29, and chunk 2
#: names exactly 30 nodes.
#:
#: This was read as the material index for several versions, because on
#: every model available at the time the node list and the material
#: list happened to be the same length and in the same order. On
#: ``machine_house1`` they are not — 30 nodes against 6 materials — and
#: the result was 24 of its meshes clamped onto material 0, which is
#: how a building came to be covered in its own antenna texture.
NODE_INDEX_OFFSET = 44

#: Header offset holding which material in the skin chunk the mesh
#: uses. Proven on the whole corpus: this value stays inside the
#: material count on every model, including ``machine_house1`` where
#: the node index does not, and it accounts for every triangle::
#:
#:     machine_house1  30 meshes, 6 materials, uses 0,1,2,3,4,5
#:     bridge_concrete  4 meshes, 4 materials, uses 0,1,2,3
#:     big_flag01       5 meshes, 9 materials, uses 0 only
#:
#: ``big_flag01`` is the one that could not be explained by mesh order
#: either: five meshes all drawing with the first of nine materials.
MATERIAL_INDEX_OFFSET = 52

_CONFIRMED_STRIDE = 44
_BOUNDS_FLOATS = 6

#: Header offset holding the vertex type. Named for what it turned out
#: to be after ``factory_box`` supplied a third value; it was reached
#: for as a component count before there was anything to compare.
VERTEX_TYPE_OFFSET = _COMPONENT_COUNT_OFFSET

#: Size in bytes of each named vertex field.
VERTEX_FIELD_SIZES = {
    "position": 12,
    "normal": 12,
    "colour": 4,
    "uv": 8,
    "uv2": 8,
    "uv3": 8,
    "tangent": 16,
}

#: Vertex type to (stride, field order), measured field-by-field by
#: decoding real vertices and checking that normals come out unit
#: length, UVs land in 0..1 and tangents carry a ±1 handedness in their
#: fourth float::
#:
#:     7   factory_box  (shipped, shader ``diffuse``)
#:     8   civilhouse1  (shipped, ``diffuse_vc``/``specular_vc``)
#:     15  big_flag01   (shipped, ``bump``), and everything HTAToolchain
#:                      writes
#:
#: Type 9 does not appear in any model measured, despite having been
#: assumed necessary for map decorations: ``factory_box`` is a shipped
#: map decoration and is type 7.
VERTEX_LAYOUTS = {
    7: (32, ("position", "normal", "uv")),
    8: (36, ("position", "normal", "colour", "uv")),
    15: (48, ("position", "normal", "uv", "tangent")),
}

#: Layouts derived from the strides the census found, and checked
#: against the data before use — see ``_layout_from_vertex_type``.
#:
#: A census of 1388 models turned up four types the table above does
#: not cover, on 388 meshes::
#:
#:     type 10  stride 40   174 meshes   oracle_house_r0m0
#:     type  9  stride 44   116 meshes   oracle_house_r0m0
#:     type 11  stride 48    78 meshes   midgard
#:     type 16  stride 52    20 meshes   cabt1
#:
#: Every one of them is a known type plus one more field, and the
#: arithmetic leaves no slack::
#:
#:     10 = 7 + uv2       32 + 8  = 40
#:      9 = 8 + uv2       36 + 8  = 44
#:     16 = 8 + tangent   36 + 16 = 52
#:     11 = 9 + uv3       44 + 4? — see below
#:
#: Type 11 is the one that does not fall out cleanly. 48 is reachable
#: as ``position+normal+uv+uv2+uv3`` (12+12+8+8+8), which fits the
#: ``lightmap_detail`` shader ``midgard`` runs — a lightmap and a
#: detail map each wanting coordinates of their own. That is a
#: hypothesis, and the validation below is what keeps a wrong one from
#: reaching the geometry.
CANDIDATE_VERTEX_LAYOUTS = {
    10: (40, ("position", "normal", "uv", "uv2")),
    9: (44, ("position", "normal", "colour", "uv", "uv2")),
    16: (52, ("position", "normal", "colour", "uv", "tangent")),
    11: (48, ("position", "normal", "uv", "uv2", "uv3")),
}


@dataclasses.dataclass
class ModelNode:
    """One entry of a model's node table."""

    name: str
    #: Index of the parent node, or a negative sentinel for the root
    #: (-1 and -6666 both seen).
    parent: int
    location: Vector3
    #: Quaternion as stored: four floats, w last. ``(0, 0, 0, -1)`` —
    #: the identity with its sign flipped — on every locator measured.
    rotation: tuple[float, float, float, float]

    @property
    def is_locator(self) -> bool:
        return self.name.upper().startswith(LOCATOR_PREFIX)


def read_nodes(path: str) -> list[ModelNode]:
    """The model's node table, in file order. Empty if there is none."""
    _subtype, chunks = read_container(path)
    for chunk in chunks:
        if chunk.chunk_id != NODE_CHUNK_ID:
            continue
        data = chunk.data
        if len(data) % NODE_RECORD_SIZE:
            raise ParsingError(
                "node chunk is not a whole number of records",
                context=ErrorContext(
                    source_file=path,
                    extra={"size": len(data), "record": NODE_RECORD_SIZE},
                ),
            )
        nodes = []
        for index in range(len(data) // NODE_RECORD_SIZE):
            offset = index * NODE_RECORD_SIZE
            raw_name = data[offset:offset + 40].split(b"\x00", 1)[0]
            parent = struct.unpack_from("<i", data, offset + 40)[0]
            location = struct.unpack_from("<3f", data, offset + 44)
            rotation = struct.unpack_from("<4f", data, offset + 56)
            nodes.append(ModelNode(
                name=raw_name.decode("cp1251", errors="replace"),
                parent=parent,
                location=Vector3(*location),
                rotation=tuple(rotation),
            ))
        return nodes
    return []


def read_locators(path: str) -> dict[str, ModelNode]:
    """The ``LP_`` nodes of a model, by name (upper-cased).

    MEASURED: heavy_dot4's ``LP_CANNON01`` is at (-0.19, 8.75, 0.03) —
    on top of a body whose Y runs -2.65 .. 8.77 — which is where the
    game mounts the gun.
    """
    return {n.name.upper(): n for n in read_nodes(path) if n.is_locator}


@dataclasses.dataclass
class Chunk:
    """One entry from the container's table of contents."""

    chunk_id: int
    data: bytes


def read_container(path: str) -> tuple[int, list[Chunk]]:
    """Read any ``ecbnt,t`` file into its chunks.

    Returns ``(subtype, chunks)``. Chunk payloads are returned as raw
    bytes — this function doesn't interpret them, so it works for every
    file kind that uses this container, not just models.

    Raises
    ------
    ParsingError
        If the file is unreadable, lacks the magic, or its chunk table
        doesn't add up (offsets are contiguous and must end exactly at
        the file size — verified on six different real files).
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise ParsingError(
            "could not read container file",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc

    if len(data) < 12 or data[:7] != MAGIC:
        raise ParsingError(
            "not an ecbnt,t container (wrong magic)",
            context=ErrorContext(source_file=path, extra={"first_bytes": data[:8].hex()}),
        )

    subtype = data[7]
    chunk_count = struct.unpack_from("<I", data, 8)[0]

    table_end = 12 + chunk_count * 16
    if table_end > len(data):
        raise ParsingError(
            "container chunk table does not fit in the file",
            context=ErrorContext(
                source_file=path,
                extra={"chunk_count": chunk_count, "file_size": len(data)},
            ),
        )

    chunks: list[Chunk] = []
    position = 12
    for _ in range(chunk_count):
        chunk_id, size, offset, _reserved = struct.unpack_from("<4I", data, position)
        position += 16
        if offset + size > len(data):
            raise ParsingError(
                "container chunk extends past the end of the file",
                context=ErrorContext(
                    source_file=path,
                    extra={"chunk_id": chunk_id, "offset": offset, "size": size},
                ),
            )
        chunks.append(Chunk(chunk_id=chunk_id, data=data[offset:offset + size]))

    return subtype, chunks


@dataclasses.dataclass
class VertexLayout:
    """Where each attribute sits inside a vertex, and how wide it is.

    Taken from the vertex type the header declares at offset 60,
    falling back to inspecting the data for a type never seen before.

    That field was previously dismissed as a "component count" that
    described nothing, because 7, 9 and 15 match no obvious count of
    fields. It is not a count — it is an enum, and once ``factory_box``
    supplied a third value the correspondence was exact::

        type 7   stride 32   pos, normal, uv
        type 8   stride 36   pos, normal, colour, uv
        type 15  stride 48   pos, normal, uv, tangent (+ handedness)

    Reading it matters, because the fallback gets a whole class of
    models wrong. It identifies the normal field by testing for unit
    length, so a model whose object scale was never applied in Blender
    has no field that passes — and the UVs are then read from offset
    12, which is the normal. Every polygon lands on one small patch of
    the texture. ``cube1111`` is that case, and it is the only mesh in
    the reference corpus the fallback misreads.

    Confirmed formats, all with position at 0 and normal at 12::

        stride 32:  pos, normal, uv
        stride 44:  pos, normal, colour, uv, uv2
        stride 48:  pos, normal, uv, tangent (+ handedness)

    Position is always validated against the mesh's stored bounding
    box before anything is trusted, so an unrecognised layout is
    refused rather than misread.
    """

    stride: int
    normal: int | None = None
    color: int | None = None
    uv: int | None = None
    uv2: int | None = None


def write_container(subtype: int, chunks: list[Chunk]) -> bytes:
    """Serialise chunks back into an ``ecbnt,t`` container.

    The inverse of :func:`read_container`, and deliberately literal: it
    writes the chunks it is given, in the order it is given them, with
    payloads laid out contiguously after the table. That matches every
    real file measured — offsets are contiguous and the last one ends
    exactly at the file size — and it means a file read and written
    back unchanged is byte-identical, which is the property that makes
    editing a single chunk safe.

    ``reserved`` is written as zero. It is zero in every model
    examined; if a file is ever found where it is not, this is the line
    that has to learn why rather than the place to guess now.
    """
    table_size = 12 + len(chunks) * 16
    out = bytearray(MAGIC + bytes([subtype]) + struct.pack("<I", len(chunks)))

    offset = table_size
    for chunk in chunks:
        out += struct.pack("<4I", chunk.chunk_id, len(chunk.data), offset, 0)
        offset += len(chunk.data)

    for chunk in chunks:
        out += chunk.data

    return bytes(out)


def _looks_like_packed_color(block: bytes, base: int, offset: int, stride: int, count: int) -> bool:
    """True if the 4 bytes at ``offset`` read as a packed colour, not a float.

    A packed RGBA value interpreted as float32 is almost always
    non-finite or absurdly large, whereas a real UV coordinate is a
    small finite number. That difference is what separates the 44-byte
    layout (colour at 24) from the 48-byte one (uv at 24).
    """
    suspicious = 0
    sample = min(count, 32)
    for index in range(sample):
        value = struct.unpack_from("<f", block, base + index * stride + offset)[0]
        if not math.isfinite(value) or abs(value) > 1e6:
            suspicious += 1
    return suspicious > sample // 2


def _build_layout(stride: int, fields: tuple[str, ...]) -> VertexLayout:
    layout = VertexLayout(stride=stride)
    offset = 0
    for field in fields:
        if field == "normal":
            layout.normal = offset
        elif field == "colour":
            layout.color = offset
        elif field == "uv":
            layout.uv = offset
        elif field == "uv2":
            layout.uv2 = offset
        offset += VERTEX_FIELD_SIZES[field]
    return layout


def _layout_from_vertex_type(
    vertex_type: int,
    stride: int,
    block: bytes | None = None,
    base: int = 0,
    vertex_count: int = 0,
) -> VertexLayout | None:
    """The layout the header declares, or None for an unknown type.

    The declared stride has to agree with the type's own, or the header
    is describing something this table does not cover.

    A measured layout is trusted outright. A candidate one — derived
    from its stride rather than confirmed on a real file — is checked
    against the data first: the normal has to come out unit length and
    the texture coordinates have to be plausible. A candidate that
    fails is discarded rather than applied, and the caller falls back
    to inspecting the data. That way a wrong guess costs nothing beyond
    what the fallback already cost.
    """
    known = VERTEX_LAYOUTS.get(vertex_type)
    if known is not None and known[0] == stride:
        return _build_layout(stride, known[1])

    candidate = CANDIDATE_VERTEX_LAYOUTS.get(vertex_type)
    if candidate is None or candidate[0] != stride:
        return None

    layout = _build_layout(stride, candidate[1])
    if block is None or vertex_count <= 0:
        return layout
    if _layout_fits_the_data(block, base, stride, vertex_count, layout):
        return layout

    logger.warning(
        "vertex type %s at stride %s: the derived layout does not fit this "
        "model's data. Inspecting it instead.",
        vertex_type, stride,
    )
    return None


def _layout_fits_the_data(
    block: bytes, base: int, stride: int, vertex_count: int, layout: VertexLayout
) -> bool:
    """Whether a derived layout reads as the fields it claims.

    Normals are the discriminator: unit length is a property no other
    field has by accident. UVs are checked only for being finite and
    within plausible tiling, which rules out reading a position or a
    tangent as a coordinate.
    """
    sample = min(vertex_count, 64)
    unit = 0
    checked = 0

    for index in range(sample):
        at = base + index * stride
        if at + stride > len(block):
            break
        checked += 1

        if layout.normal is not None:
            x, y, z = struct.unpack_from("<3f", block, at + layout.normal)
            length = math.sqrt(x * x + y * y + z * z)
            if abs(length - 1.0) < 0.02:
                unit += 1

        for attribute in ("uv", "uv2"):
            offset = getattr(layout, attribute, None)
            if offset is None:
                continue
            u, v = struct.unpack_from("<2f", block, at + offset)
            for value in (u, v):
                if value != value or abs(value) > _MAX_PLAUSIBLE_UV:
                    return False

    if not checked:
        return False
    # Unapplied object scale makes every normal the same wrong length,
    # so a model can legitimately fail this while its layout is right.
    # Accept it when the normals are at least consistent in length.
    return unit >= checked * 0.8 or _normals_are_consistent(
        block, base, stride, checked, layout
    )


def _normals_are_consistent(
    block: bytes, base: int, stride: int, count: int, layout: VertexLayout
) -> bool:
    """Whether the normal field holds one consistent magnitude.

    ``cube1111`` carries normals of magnitude 187.4784 — its own scale,
    left unapplied in Blender — on every vertex. Wrong, and still a
    normal field: what a misread offset gives instead is lengths all
    over the place.
    """
    if layout.normal is None:
        return False

    lengths = []
    for index in range(count):
        at = base + index * stride + layout.normal
        if at + 12 > len(block):
            break
        x, y, z = struct.unpack_from("<3f", block, at)
        length = math.sqrt(x * x + y * y + z * z)
        if length <= 0.0 or length != length:
            return False
        lengths.append(length)

    if not lengths:
        return False
    return max(lengths) / min(lengths) < 1.05


#: A texture coordinate beyond this is not a texture coordinate. Real
#: models tile — ``house2`` runs its V to 5.4 and ``factory_box`` to 4 —
#: so the limit is generous. What it catches is a field that is not UV
#: at all: a guessed layout on ``agro_post`` produced a V of -4.2e37,
#: which is a float32 read out of a normal or a tangent.
_MAX_PLAUSIBLE_UV = 64.0


def _reject_implausible_uvs(
    block: bytes,
    base: int,
    stride: int,
    vertex_count: int,
    layout: "VertexLayout",
    name: str,
) -> None:
    """Drop a guessed UV field whose values cannot be coordinates.

    The fallback identifies fields by what their values look like, and
    on an unmeasured vertex type it can settle on the wrong offset. A
    wrong primary UV is bad; a wrong SECOND UV is worse, because it
    creates a UV layer that did not exist and Blender may render with
    it. Better to carry no second set than an invented one.
    """
    for attribute in ("uv2", "uv"):
        offset = getattr(layout, attribute, None)
        if offset is None:
            continue

        worst = 0.0
        for index in range(min(vertex_count, 256)):
            at = base + index * stride + offset
            if at + 8 > len(block):
                break
            u, v = struct.unpack_from("<2f", block, at)
            for value in (u, v):
                if value != value or abs(value) == float("inf"):
                    worst = float("inf")
                    break
                worst = max(worst, abs(value))
            if worst == float("inf"):
                break

        if worst > _MAX_PLAUSIBLE_UV:
            logger.warning(
                "%s: the guessed %s field at offset %s reaches %.3g — not a "
                "texture coordinate. Dropping it.",
                name, attribute, offset, worst,
            )
            setattr(layout, attribute, None)


def _detect_layout(block: bytes, base: int, stride: int, vertex_count: int) -> VertexLayout:
    """Work out where each vertex attribute lives by inspecting the data.

    The fallback for a vertex type not in ``VERTEX_LAYOUTS``. Prefer
    :func:`_layout_from_vertex_type` — see ``VertexLayout``'s docstring
    for what this gets wrong and when.
    """
    layout = VertexLayout(stride=stride)

    # Normal: the only field that is a unit-length 3-vector.
    if stride >= 24 and _is_unit_vector_field(block, base, stride, vertex_count, 12):
        layout.normal = 12

    remaining_offset = 24 if layout.normal is not None else 12
    remaining = stride - remaining_offset
    if remaining < 8:
        return layout  # position (and maybe normal) only

    if _looks_like_packed_color(block, base, remaining_offset, stride, vertex_count):
        # colour, then uv, then (if there's room) a second uv set
        layout.color = remaining_offset
        layout.uv = remaining_offset + 4
        if remaining >= 20:
            layout.uv2 = remaining_offset + 12
    else:
        layout.uv = remaining_offset
        # Anything after the uv is tangent/binormal data, which this
        # SDK doesn't need — Blender recomputes tangents from the UVs.
        if remaining >= 20 and not _is_unit_vector_field(
            block, base, stride, vertex_count, remaining_offset + 8
        ):
            layout.uv2 = remaining_offset + 8

    return layout


def _is_unit_vector_field(
    block: bytes, base: int, stride: int, vertex_count: int, offset: int,
) -> bool:
    """True if the 3 floats at ``offset`` are unit length for every vertex."""
    if offset + 12 > stride:
        return False
    sample = min(vertex_count, 64)
    for index in range(sample):
        values = struct.unpack_from("<3f", block, base + index * stride + offset)
        if not all(map(math.isfinite, values)):
            return False
        length = math.sqrt(sum(v * v for v in values))
        if not 0.97 < length < 1.03:
            return False
    return True


def _find_index_buffer(
    block: bytes,
    vertex_data_start: int,
    stride: int,
    vertex_count: int,
    triangle_count: int,
    max_buffers: int = 4,
) -> int | None:
    """Return the offset of the index buffer, or ``None`` if not found.

    Some meshes store more than one vertex buffer back to back — a
    second one holding what looks like an alternate vertex state
    (observed on 3 of 9 meshes in one model, all sharing a header flag
    this parser deliberately doesn't rely on, having seen it take only
    two distinct values).

    Rather than trust that flag, the index buffer is located by
    validation: after N buffers, the next ``triangle_count * 3``
    uint16 values must all be valid vertex indices. Garbage almost
    never satisfies that for a whole buffer, and the check costs
    nothing compared to misreading the geometry.
    """
    if triangle_count == 0:
        return vertex_data_start + vertex_count * stride

    needed = triangle_count * 3
    for buffers in range(1, max_buffers + 1):
        offset = vertex_data_start + buffers * vertex_count * stride
        if offset + needed * 2 > len(block):
            return None
        indices = struct.unpack_from(f"<{needed}H", block, offset)
        if max(indices) < vertex_count:
            return offset
    return None


#: The axis orders a bounding box can be stored on. Only the identity
#: is correct; the rest are named so a file written on one of them can
#: be reported precisely rather than as "not understood".
_AXIS_ORDERS = {
    (0, 1, 2): "XYZ",
    (0, 2, 1): "X, then Z and Y swapped",
    (1, 0, 2): "Y and X swapped",
    (1, 2, 0): "YZX",
    (2, 0, 1): "ZXY",
    (2, 1, 0): "Z and X swapped",
}


def _permutation_matching(actual, raw_bounds, span: float):
    """Which axis order the stored box would be right on, or None.

    Called only once the box has already failed to match. A hit says
    the vertices were read correctly — the numbers are all there, in
    the wrong slots — and that is a different finding from a vertex
    layout this reader does not understand. Telling them apart is the
    difference between "this road is gone" and "this file was exported
    by something that swapped two axes".
    """
    for order, name in _AXIS_ORDERS.items():
        if order == (0, 1, 2):
            continue
        permuted = tuple(actual[i] for i in order) + tuple(
            actual[3 + i] for i in order
        )
        if all(abs(a - b) <= 0.01 * span for a, b in zip(permuted, raw_bounds)):
            return name
    return None


def _read_meshes(block: bytes, source_file: str) -> list[MeshData]:
    """Read every mesh in a mesh chunk.

    A chunk holds one or more meshes back to back — a rock is a single
    mesh, a vehicle cab is 28 — each consisting of a 72-byte header,
    one or more vertex buffers, and an index buffer. A single bounding
    box follows the last mesh and covers the union of all of them
    (verified: it matches the combined vertex extents exactly on every
    file examined).
    """
    meshes: list[MeshData] = []
    position = 0

    while position + _VERTEX_DATA_OFFSET <= len(block):
        name = block[position:position + 16].split(b"\x00")[0].decode("ascii", errors="replace")
        stride = struct.unpack_from("<I", block, position + _VERTEX_STRIDE_OFFSET)[0]
        vertex_type = struct.unpack_from("<I", block, position + VERTEX_TYPE_OFFSET)[0]
        material_index = struct.unpack_from(
            "<I", block, position + MATERIAL_INDEX_OFFSET
        )[0]
        node_index = struct.unpack_from("<I", block, position + NODE_INDEX_OFFSET)[0]
        vertex_count = struct.unpack_from("<I", block, position + _VERTEX_COUNT_OFFSET)[0]
        triangle_count = struct.unpack_from("<I", block, position + _TRIANGLE_COUNT_OFFSET)[0]

        if not (12 <= stride <= 256 and 0 < vertex_count < 500_000 and 0 <= triangle_count < 500_000):
            break  # past the last mesh: what follows is the bounding box

        data_offset = position + _VERTEX_DATA_OFFSET
        index_offset = _find_index_buffer(
            block, data_offset, stride, vertex_count, triangle_count
        )
        if index_offset is None:
            raise ParsingError(
                "could not locate a valid index buffer for this mesh",
                context=ErrorContext(
                    source_file=source_file,
                    extra={
                        "mesh": name,
                        "stride": stride,
                        "vertex_count": vertex_count,
                        "triangle_count": triangle_count,
                    },
                ),
            )

        end = index_offset + triangle_count * 3 * 2
        if end > len(block):
            raise ParsingError(
                "mesh extends past the end of its chunk",
                context=ErrorContext(
                    source_file=source_file,
                    extra={"mesh": name, "vertex_count": vertex_count, "stride": stride},
                ),
            )

        layout = _layout_from_vertex_type(
            vertex_type, stride, block, data_offset, vertex_count
        )
        if layout is None:
            logger.warning(
                "vertex type %s at stride %s is not in the measured table "
                "(known: %s). Falling back to inspecting the data, which "
                "gets some models wrong — please report this type.",
                vertex_type, stride, sorted(VERTEX_LAYOUTS),
            )
            layout = _detect_layout(block, data_offset, stride, vertex_count)
            _reject_implausible_uvs(
                block, data_offset, stride, vertex_count, layout, name
            )
        mesh = MeshData(
            name=name, material_index=material_index, node_index=node_index
        )

        for index in range(vertex_count):
            vertex_base = data_offset + index * stride
            mesh.positions.append(Vector3(*struct.unpack_from("<3f", block, vertex_base)))
            if layout.normal is not None:
                mesh.normals.append(
                    Vector3(*struct.unpack_from("<3f", block, vertex_base + layout.normal))
                )
            if layout.color is not None:
                # Through core.color, not unpack_from("<4B"): the four
                # bytes are B, G, R, A on disk, and reading them
                # positionally swaps red and blue. That went unnoticed
                # because every vertex colour in the reference corpus
                # is greyscale.
                mesh.colors.append(
                    color_codec.unpack_bytes(block, vertex_base + layout.color)
                )
            if layout.uv is not None:
                mesh.uvs.append(struct.unpack_from("<2f", block, vertex_base + layout.uv))
            if layout.uv2 is not None:
                mesh.uv2s.append(struct.unpack_from("<2f", block, vertex_base + layout.uv2))

        indices = struct.unpack_from(f"<{triangle_count * 3}H", block, index_offset)
        for i in range(0, len(indices), 3):
            mesh.triangles.append((indices[i], indices[i + 1], indices[i + 2]))

        meshes.append(mesh)
        position = end

    if not meshes:
        raise ParsingError(
            "mesh chunk contains no readable mesh",
            context=ErrorContext(source_file=source_file, extra={"chunk_size": len(block)}),
        )

    # The trailing bounding box validates the whole read: if the
    # positions just extracted don't reproduce it, some part of the
    # layout was wrong and the geometry can't be trusted.
    if position + _BOUNDS_FLOATS * 4 <= len(block):
        raw_bounds = struct.unpack_from("<6f", block, position)
        combined = AABB.from_points(p for mesh in meshes for p in mesh.positions)
        if combined is not None:
            span = max(abs(v) for v in raw_bounds) or 1.0
            actual = combined.min_corner.as_tuple() + combined.max_corner.as_tuple()
            if any(abs(a - b) > 0.01 * span for a, b in zip(actual, raw_bounds)):
                axes = _permutation_matching(actual, raw_bounds, span)
                if axes is None:
                    raise ParsingError(
                        "vertex positions do not match the stored bounding box — "
                        "this vertex layout is not understood",
                        context=ErrorContext(
                            source_file=source_file, extra={"meshes": len(meshes)},
                        ),
                    )
                # The box is wrong and the GEOMETRY IS NOT. A permutation
                # that fits means the vertices were read correctly and the
                # writer stored the box on transposed axes — which is a
                # broken file, not a layout this reader misunderstands.
                # Refusing it loses a whole road for a metadata field
                # nothing draws.
                logger.warning(
                    "%s: the stored bounding box is on %s, while the vertices "
                    "are not. The model was written by a tool that transposed "
                    "it; the geometry reads correctly and the box is being "
                    "recomputed.",
                    os.path.basename(source_file), axes,
                )
                meshes[-1].stored_bounds = combined
                return meshes
        meshes[-1].stored_bounds = AABB(Vector3(*raw_bounds[:3]), Vector3(*raw_bounds[3:]))

    return meshes


def read_materials(block: bytes) -> list[Material]:
    """Read materials and their texture names from a skin chunk.

    Structural first: the layout is established and verified byte-exact
    on five models, and only a structural read recovers the slot
    numbers. Slots are the point — a filename cannot say whether it is
    the diffuse map or the bump map, so reading by name alone can only
    assign textures in the order they happen to appear, which is how
    images land on the wrong channel in both directions.

    The string scan below survives as a fallback for anything the
    structural walk rejects. It gets names right and slots not at all,
    which is worse but not nothing, and a chunk that does not fit the
    known layout is a file worth still reading rather than refusing.
    """
    from formats.exm.skin import parse_skin

    try:
        skin = parse_skin(block)
    except ParsingError:
        pass
    else:
        return [
            Material(
                name=material.shader,
                shader=material.shader,
                textures=[t.filename for t in material.textures],
                slots=[t.slot for t in material.textures],
            )
            for material in skin.materials
        ]

    return _scan_materials(block)


def _scan_materials(block: bytes) -> list[Material]:
    """Recover material names by scanning for ASCII runs.

    A word without a dot starts a material; a word ending in an image
    extension is one of its textures. Unambiguous as far as it goes,
    and it goes no further than names.
    """
    materials: list[Material] = []
    current: Material | None = None

    for match in re.finditer(rb"[ -~]{%d,}" % _MIN_STRING, block):
        text = match.group().decode("ascii", errors="replace")
        lowered = text.lower()

        if lowered.endswith((".dds", ".tga", ".bmp", ".png")):
            if current is None:
                # A texture before any shader name: still worth keeping
                # rather than discarding data we cannot place.
                current = Material(name=f"material_{len(materials)}")
                materials.append(current)
            if text not in current.textures:
                current.textures.append(text)
        elif "." not in text:
            # A shader name starts a new material.
            current = Material(name=text, shader=text)
            materials.append(current)

    return materials


def read_model(path: str) -> Model:
    """Read a ``.gam`` file's geometry.

    Chunks other than the mesh chunk (material, shadow volume, tool
    metadata) are ignored — they aren't needed to display the model and
    aren't understood well enough to use.

    Raises
    ------
    ParsingError
        On a bad container, an unsupported vertex format, a truncated
        mesh chunk, or an out-of-range index.
    """
    _subtype, chunks = read_container(path)

    model = Model()
    for chunk in chunks:
        if chunk.chunk_id == MESH_CHUNK_ID:
            model.meshes.extend(_read_meshes(chunk.data, path))
        elif chunk.chunk_id == SKIN_CHUNK_ID:
            model.materials.extend(read_materials(chunk.data))

    if not model.meshes:
        raise ParsingError(
            "file contains no mesh chunk",
            context=ErrorContext(
                source_file=path,
                extra={"chunk_ids": sorted({c.chunk_id for c in chunks})},
            ),
        )

    return model
