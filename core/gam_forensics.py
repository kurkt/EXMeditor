# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Structural measurement of ``.gam`` model files.

Game-agnostic and ``bpy``-free, like the rest of ``core``; the Blender
operator lives in ``addon/forensics_operator.py``.

Why this exists
---------------

An SDK-produced model **loads** — the engine accepts it, this SDK's own
parser reads it back, Blender re-imports it — but the game's editor
draws it as wireframe. That asymmetry is the useful part: the runtime
is satisfied and the editor is not, so the fault lives in data the
editor consumes and the runtime either ignores or defaults.

``formats/exm/gam.py`` reads only what it needs to display geometry: it
uses four fields out of a 72-byte mesh header and skips every chunk
except mesh (4) and skin (15). Everything it steps over is, by
elimination, where the difference has to be. This module therefore
reports the *skipped* material rather than the understood material:

* the whole chunk table, including chunk ids nobody has decoded;
* all 72 header bytes per mesh, with the 40 unknown ones shown as
  hex, as uint32 and as float32, since which of those they are is not
  yet established;
* how many vertex buffers precede the index buffer;
* every ASCII string in every chunk, which is where texture names,
  shader names and tool version stamps live.

``compare`` then puts a model that renders next to one that does not
and reports only what differs. That is the experiment: not "read the
generated file and see whether it looks right", but "find the field
where a working file and a broken file disagree".
"""

from __future__ import annotations

import dataclasses
import os
import re
import struct

from formats.exm.gam import (
    MAGIC,
    MESH_CHUNK_ID,
    SKIN_CHUNK_ID,
    _find_index_buffer,
)

#: Bytes 0..72 of every mesh, of which the parser interprets four
#: fields. The rest is what this module exists to expose.
MESH_HEADER_SIZE = 72

#: Offsets inside the header nobody has explained yet. Rendering state
#: — a material index, render flags, a skin reference — would have to
#: live here or in a chunk the parser skips, and this is the cheaper
#: half to look at first.
UNKNOWN_HEADER_RANGE = range(16, 56, 4)

#: Chunk ids seen in shipped models. Anything outside this is worth
#: reporting; anything in here that a generated file LACKS is worth
#: reporting louder.
CHUNK_NAMES = {
    1: "header",
    2: "node hierarchy (names indexed by mesh header +44)",
    4: "mesh",
    8: "animation (empty in every model measured)",
    15: "skin (materials + textures)",
    16: "UNDECODED — seen on civilhouse1",
    32: "UNDECODED — seen on civilhouse1",
    128: "UNDECODED — seen on big_flag01, a windwavy model",
    256: "UNDECODED — seen on big_flag01, a windwavy model",
    240: "UNDECODED — collection name",
    61441: "HTATools metadata",
    61442: "HTATools metadata",
    61443: "HTATools version stamp (absent from shipped models)",
    61444: "HTATools signature (absent from shipped models)",
}

_MIN_STRING = 4

#: Strings beyond this are truncated in the formatted dump — a vehicle
#: skin chunk names nine textures per material and the interesting part
#: is the first few.
_MAX_STRINGS_SHOWN = 40


@dataclasses.dataclass
class MeshReport:
    """Everything measurable about one mesh, decoded and not."""

    index: int
    offset: int
    name: str
    stride: int
    components: int
    vertex_count: int
    triangle_count: int
    unknown: list[int]
    vertex_buffers: int | None
    index_offset: int | None
    max_index: int | None


@dataclasses.dataclass
class ChunkReport:
    chunk_id: int
    #: Size the table DECLARES, which may exceed what the file holds.
    size: int
    strings: list[str]
    materials: list = dataclasses.field(default_factory=list)
    declared_offset: int = 0
    #: Bytes actually present. Differs from ``size`` on a truncated file.
    available: int = 0
    truncated: bool = False

    @property
    def label(self) -> str:
        return CHUNK_NAMES.get(self.chunk_id, "UNDECODED")


@dataclasses.dataclass
class ModelReport:
    path: str
    size: int
    subtype: int
    chunks: list[ChunkReport]
    meshes: list[MeshReport]
    trailing_bounds: tuple[float, ...] | None
    #: Structural faults found while reading. Non-empty means the game
    #: cannot load this file either.
    problems: list[str] = dataclasses.field(default_factory=list)

    def chunk_ids(self) -> list[int]:
        return [c.chunk_id for c in self.chunks]

    def strings_by_chunk(self) -> dict[int, list[str]]:
        return {c.chunk_id: c.strings for c in self.chunks}


@dataclasses.dataclass
class Comparison:
    """What differs between two models, and nothing that agrees.

    Kept as a list of findings rather than formatted text so the
    operator can put a count in the status bar without parsing its own
    report back.
    """

    reference: ModelReport
    subject: ModelReport
    differences: list[str] = dataclasses.field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not self.differences


def _strings(block: bytes) -> list[str]:
    """Every printable ASCII run in a chunk, in order.

    Order matters: the skin chunk interleaves shader names and texture
    filenames, and losing the sequence would lose which texture belongs
    to which shader.
    """
    return [
        match.group().decode("ascii", errors="replace")
        for match in re.finditer(rb"[ -~]{%d,}" % _MIN_STRING, block)
    ]


def _read_mesh_headers(block: bytes) -> tuple[list[MeshReport], tuple[float, ...] | None]:
    """Walk a mesh chunk, reporting headers without reading vertices.

    The walk mirrors ``gam._read_meshes`` so mesh boundaries agree with
    what the importer sees, but it stops at the header: this module is
    about the fields the importer does *not* read.
    """
    meshes: list[MeshReport] = []
    position = 0
    index = 0

    while position + MESH_HEADER_SIZE <= len(block):
        name = block[position:position + 16].split(b"\x00")[0].decode("ascii", errors="replace")
        stride = struct.unpack_from("<I", block, position + 56)[0]
        components = struct.unpack_from("<I", block, position + 60)[0]
        vertex_count = struct.unpack_from("<I", block, position + 64)[0]
        triangle_count = struct.unpack_from("<I", block, position + 68)[0]

        if not (12 <= stride <= 256 and 0 < vertex_count < 500_000 and 0 <= triangle_count < 500_000):
            break  # past the last mesh: what follows is the bounding box

        unknown = [
            struct.unpack_from("<I", block, position + offset)[0]
            for offset in UNKNOWN_HEADER_RANGE
        ]

        data_offset = position + MESH_HEADER_SIZE
        index_offset = _find_index_buffer(
            block, data_offset, stride, vertex_count, triangle_count
        )

        buffers = None
        max_index = None
        if index_offset is not None:
            span = index_offset - data_offset
            divisor = vertex_count * stride
            buffers = span // divisor if divisor else None
            # Bounds-checked before unpacking: this tool is pointed at
            # files suspected of being malformed, and a bad triangle
            # count would otherwise ask struct for an allocation sized
            # by whatever garbage the header held.
            needed = triangle_count * 3 * 2
            if triangle_count and index_offset + needed <= len(block):
                max_index = max(
                    struct.unpack_from(f"<{triangle_count * 3}H", block, index_offset)
                )

        meshes.append(
            MeshReport(
                index=index,
                offset=position,
                name=name,
                stride=stride,
                components=components,
                vertex_count=vertex_count,
                triangle_count=triangle_count,
                unknown=unknown,
                vertex_buffers=buffers,
                index_offset=index_offset,
                max_index=max_index,
            )
        )
        index += 1

        if index_offset is None:
            # An unreadable index buffer means the next mesh's position
            # is unknown; stopping is honest, guessing would invent
            # meshes that are not there.
            break
        position = index_offset + triangle_count * 3 * 2

    trailing = None
    if position + 24 <= len(block):
        trailing = struct.unpack_from("<6f", block, position)
    return meshes, trailing


def _read_container_tolerantly(path: str) -> tuple[int, list[ChunkReport], list[str]]:
    """Read the chunk table without requiring it to be valid.

    ``formats/exm/gam.read_container`` refuses a file whose chunks do
    not fit, which is correct for an importer and useless here: a file
    that fails that check is exactly the file this module is pointed
    at. The first thing HTAToolchain produced could not be read back at
    all, and the tool meant to explain why could not open it either.

    So the table is read directly and every entry reported, with the
    ones that do not fit named rather than fatal.
    """
    with open(path, "rb") as handle:
        data = handle.read()

    problems: list[str] = []

    if len(data) < 12:
        return -1, [], [f"file is {len(data)} bytes; a container header needs 12"]

    if not data.startswith(MAGIC):
        problems.append(
            f"wrong magic: expected {MAGIC!r}, found {data[:len(MAGIC)]!r}"
        )

    subtype = data[7]
    chunk_count = struct.unpack_from("<I", data, 8)[0]

    table_end = 12 + chunk_count * 16
    if table_end > len(data):
        problems.append(
            f"the chunk table claims {chunk_count} chunks, needing {table_end} "
            f"bytes, but the file is {len(data)}"
        )
        chunk_count = max(0, (len(data) - 12) // 16)

    chunks: list[ChunkReport] = []
    position = 12
    for index in range(chunk_count):
        chunk_id, size, offset, _reserved = struct.unpack_from("<4I", data, position)
        position += 16

        available = max(0, len(data) - offset)
        truncated = offset + size > len(data)
        if truncated:
            problems.append(
                f"chunk {index} (id {chunk_id}) declares {size} bytes at offset "
                f"{offset}, which ends at {offset + size} — past the end of a "
                f"{len(data)}-byte file; {available} bytes are actually there"
            )

        block = data[offset:offset + min(size, available)]
        chunks.append(
            ChunkReport(
                chunk_id=chunk_id,
                size=size,
                strings=_strings(block),
                materials=parse_skin(block) if chunk_id == SKIN_CHUNK_ID else [],
                declared_offset=offset,
                available=len(block),
                truncated=truncated,
            )
        )

    return subtype, chunks, problems


def describe(path: str) -> ModelReport:
    """Measure one ``.gam``, interpreting nothing optional."""
    subtype, chunk_reports, problems = _read_container_tolerantly(path)

    meshes: list[MeshReport] = []
    trailing: tuple[float, ...] | None = None
    with open(path, "rb") as handle:
        data = handle.read()
    for chunk in chunk_reports:
        if chunk.chunk_id != MESH_CHUNK_ID:
            continue
        block = data[chunk.declared_offset:chunk.declared_offset + chunk.available]
        found, trailing = _read_mesh_headers(block)
        meshes.extend(found)

    return ModelReport(
        path=path,
        size=os.path.getsize(path),
        subtype=subtype,
        chunks=chunk_reports,
        meshes=meshes,
        trailing_bounds=trailing,
        problems=problems,
    )


def format_report(report: ModelReport) -> list[str]:
    """A human-readable dump, ordered from the container outwards."""
    lines = [
        f"{os.path.basename(report.path)} — {report.size} bytes, "
        f"container subtype {report.subtype}",
    ]
    if report.problems:
        lines += ["", "STRUCTURAL FAULTS — the game cannot load this file:"]
        lines += [f"  ! {problem}" for problem in report.problems]
    lines += ["", "Chunks:"]
    for chunk in report.chunks:
        marker = "  TRUNCATED" if chunk.truncated else ""
        lines.append(
            f"  id {chunk.chunk_id:<4} {chunk.size:>8} bytes declared at "
            f"{chunk.declared_offset:>8}, {chunk.available:>8} present   "
            f"{chunk.label}{marker}"
        )
        if chunk.chunk_id == SKIN_CHUNK_ID:
            lines += format_skin(chunk.materials)
            continue
        for text in chunk.strings[:_MAX_STRINGS_SHOWN]:
            lines.append(f"        {text!r}")
        if len(chunk.strings) > _MAX_STRINGS_SHOWN:
            lines.append(
                f"        ... {len(chunk.strings) - _MAX_STRINGS_SHOWN} more strings"
            )

    lines += ["", f"Meshes: {len(report.meshes)}"]
    for mesh in report.meshes:
        lines += [
            "",
            f"  [{mesh.index}] {mesh.name!r} at chunk offset {mesh.offset}",
            f"      stride {mesh.stride}  components {mesh.components}  "
            f"vertices {mesh.vertex_count}  triangles {mesh.triangle_count}",
            f"      vertex buffers {mesh.vertex_buffers}  "
            f"index buffer at {mesh.index_offset}  max index {mesh.max_index}",
            "      undecoded header bytes 16..56:",
        ]
        for slot, offset in enumerate(UNKNOWN_HEADER_RANGE):
            value = mesh.unknown[slot]
            as_float = struct.unpack("<f", struct.pack("<I", value))[0]
            lines.append(
                f"        +{offset:<3} 0x{value:08x}  uint {value:<12} float {as_float:g}"
            )

    if report.trailing_bounds is not None:
        formatted = ", ".join(f"{v:.3f}" for v in report.trailing_bounds)
        lines += ["", f"Trailing bounding box: {formatted}"]
    return lines


def compare(reference_path: str, subject_path: str) -> Comparison:
    """Diff a model that renders against one that does not.

    Records differences only. A field that agrees is not evidence, and
    recording it buries the field that does not.
    """
    reference = describe(reference_path)
    subject = describe(subject_path)
    result = Comparison(reference=reference, subject=subject)
    note = result.differences.append

    for problem in subject.problems:
        note(f"the subject is structurally invalid: {problem}")

    if reference.subtype != subject.subtype:
        note(
            f"container subtype: reference={reference.subtype} "
            f"subject={subject.subtype}"
        )

    reference_ids = set(reference.chunk_ids())
    subject_ids = set(subject.chunk_ids())

    missing = sorted(reference_ids - subject_ids)
    if missing:
        described = ", ".join(f"{i} ({CHUNK_NAMES.get(i, 'UNDECODED')})" for i in missing)
        note(f"chunks the reference has and the subject LACKS: {described}")

    extra = sorted(subject_ids - reference_ids)
    if extra:
        note(f"chunks the subject has and the reference does not: {extra}")

    reference_strings = reference.strings_by_chunk()
    subject_strings = subject.strings_by_chunk()
    for chunk_id in sorted(reference_ids & subject_ids):
        if reference_strings[chunk_id] == subject_strings[chunk_id]:
            continue
        label = CHUNK_NAMES.get(chunk_id, "UNDECODED")
        note(f"chunk {chunk_id} ({label}) strings differ:")
        note(f"    reference: {reference_strings[chunk_id]}")
        note(f"    subject:   {subject_strings[chunk_id]}")
        if chunk_id == SKIN_CHUNK_ID and not subject_strings[chunk_id]:
            note(
                "    the subject's skin chunk names no shader and no texture. "
                "A model the editor cannot bind a texture for is a candidate "
                "cause of wireframe display."
            )

    if len(reference.meshes) != len(subject.meshes):
        note(
            f"mesh count: reference={len(reference.meshes)} "
            f"subject={len(subject.meshes)}"
        )

    for left, right in zip(reference.meshes, subject.meshes):
        found: list[str] = []
        if left.stride != right.stride:
            found.append(f"stride {left.stride} vs {right.stride}")
        if left.components != right.components:
            found.append(f"components {left.components} vs {right.components}")
        if left.vertex_buffers != right.vertex_buffers:
            found.append(
                f"vertex buffers {left.vertex_buffers} vs {right.vertex_buffers}"
            )
        for slot, offset in enumerate(UNKNOWN_HEADER_RANGE):
            if left.unknown[slot] != right.unknown[slot]:
                found.append(
                    f"header +{offset} 0x{left.unknown[slot]:08x} vs "
                    f"0x{right.unknown[slot]:08x}"
                )
        if found:
            note(f"mesh [{left.index}] {left.name!r} vs {right.name!r}:")
            for line in found:
                note(f"    {line}")

    return result


@dataclasses.dataclass
class Population:
    """What a body of known-good models looks like, measured.

    The game ships over a thousand models that certainly work, because
    the game runs. That makes them a reference population: anything a
    candidate does that none of them does is suspicious on statistical
    grounds rather than on somebody's hunch, and — just as usefully —
    anything a candidate shares with them is ruled out.
    """

    count: int
    sizes: list[float]
    offsets: list[float]
    formats: dict[tuple[int, int], int]
    chunk_ids: dict[int, int]
    unreadable: list[str]

    def size_range(self) -> tuple[float, float]:
        return (min(self.sizes), max(self.sizes)) if self.sizes else (0.0, 0.0)

    def largest_offset(self) -> float:
        return max(self.offsets) if self.offsets else 0.0


def _extent_and_offset(report: ModelReport) -> tuple[float, float] | None:
    """Largest dimension, and how far the box centre sits from origin."""
    bounds = report.trailing_bounds
    if not bounds or len(bounds) != 6:
        return None
    if any(abs(v) > 1e9 for v in bounds):
        return None  # garbage from a truncated chunk, not a measurement
    size = max(bounds[i + 3] - bounds[i] for i in range(3))
    centre = [(bounds[i] + bounds[i + 3]) / 2 for i in range(3)]
    return size, max(abs(c) for c in centre)


def measure_population(paths: list[str]) -> Population:
    """Measure a body of models to compare candidates against."""
    sizes: list[float] = []
    offsets: list[float] = []
    formats: dict[tuple[int, int], int] = {}
    chunk_ids: dict[int, int] = {}
    unreadable: list[str] = []
    count = 0

    for path in paths:
        try:
            report = describe(path)
        except (OSError, struct.error):
            unreadable.append(os.path.basename(path))
            continue
        if report.problems:
            unreadable.append(os.path.basename(path))
            continue

        count += 1
        measured = _extent_and_offset(report)
        if measured is not None:
            sizes.append(measured[0])
            offsets.append(measured[1])
        for mesh in report.meshes:
            key = (mesh.components, mesh.stride)
            formats[key] = formats.get(key, 0) + 1
        for chunk in report.chunks:
            chunk_ids[chunk.chunk_id] = chunk_ids.get(chunk.chunk_id, 0) + 1

    return Population(
        count=count,
        sizes=sizes,
        offsets=offsets,
        formats=formats,
        chunk_ids=chunk_ids,
        unreadable=unreadable,
    )


def deviations(report: ModelReport, population: Population) -> list[str]:
    """Where a candidate falls outside everything known to work.

    Reports agreements too, briefly. A dimension on which the candidate
    matches the population is a suspect eliminated, and eliminating
    suspects is most of what this is for.
    """
    findings: list[str] = []

    for problem in report.problems:
        findings.append(f"! structurally invalid: {problem}")

    smallest, largest = population.size_range()
    measured = _extent_and_offset(report)
    if measured is None:
        findings.append("! no usable bounding box — cannot compare size")
    else:
        size, offset = measured
        if size > largest:
            findings.append(
                f"! largest dimension {size:.0f} exceeds every shipped model "
                f"(the biggest is {largest:.0f})"
            )
        elif size < smallest:
            findings.append(
                f"! largest dimension {size:.0f} is under every shipped model "
                f"(the smallest is {smallest:.0f})"
            )
        else:
            findings.append(
                f"  size {size:.0f} sits inside the shipped range "
                f"{smallest:.0f}–{largest:.0f} — not the difference"
            )

        worst = population.largest_offset()
        if offset > worst:
            findings.append(
                f"! geometry centre sits {offset:.0f} from the origin; no "
                f"shipped model exceeds {worst:.0f}. Its world position is "
                "baked into the vertices"
            )
        else:
            findings.append(
                f"  centre {offset:.0f} from origin, within the shipped "
                f"maximum {worst:.0f} — not the difference"
            )

    # Every shipped material measured names at least one texture. A
    # material with a shader and no image is not a subtle deviation:
    # the engine binds whatever is already there, which is exactly the
    # "random texture" everyone sees.
    bare = [
        m for c in report.chunks if c.chunk_id == SKIN_CHUNK_ID
        for m in c.materials if not m.textures
    ]
    if bare:
        findings.append(
            f"! {len(bare)} of the model's materials name a shader but no "
            "texture at all. The engine binds whatever happens to be "
            "loaded, which looks like a random texture"
        )

    used = {(m.components, m.stride) for m in report.meshes}
    unknown = sorted(used - set(population.formats))
    if unknown:
        known = ", ".join(
            f"{c}/{s} ({n}x)"
            for (c, s), n in sorted(population.formats.items(), key=lambda kv: -kv[1])
        )
        findings.append(
            f"! vertex format(s) {unknown} appear in no shipped model. "
            f"Shipped models use: {known}"
        )
    elif used:
        findings.append(f"  vertex format {sorted(used)} is used by shipped models")

    # A chunk the whole population carries and this model lacks is a
    # stronger signal than a rare one it happens to be missing.
    universal = {i for i, n in population.chunk_ids.items() if n == population.count}
    missing = sorted(universal - set(report.chunk_ids()))
    if missing:
        described = ", ".join(f"{i} ({CHUNK_NAMES.get(i, 'UNDECODED')})" for i in missing)
        findings.append(
            f"! every one of the {population.count} shipped models carries "
            f"chunk(s) {described}; this one does not"
        )
    else:
        findings.append("  carries every chunk the shipped models all carry")

    return findings


#: Size of one material record inside the skin chunk. Derived, not
#: guessed: teeth_top.gam declares two materials and its skin chunk is
#: 348 bytes, so 4 + 2 x 172.
MATERIAL_RECORD_SIZE = 172

#: The record opens with a D3DMATERIAL9: diffuse, ambient, specular and
#: emissive as RGBA, then power. 17 floats, 68 bytes, and every sample
#: measured so far holds 1.0 in all of them.
MATERIAL_FLOATS = 17

#: Fixed-width shader name filling the rest of the record.
SHADER_NAME_SIZE = MATERIAL_RECORD_SIZE - MATERIAL_FLOATS * 4 - 4

#: A texture reference: a fixed-width filename, a UV set and a slot
#: number. See ``formats/exm/skin.py`` for the measurement behind the
#: 40-byte name (bytes 40..43 are the UV set, non-zero in 191 shipped
#: records).
TEXTURE_RECORD_SIZE = 48
TEXTURE_NAME_SIZE = 40
_SLOT_OFFSET = 44


@dataclasses.dataclass
class TextureRef:
    name: str
    #: WHICH map this is. The reason textures land in the wrong places:
    #: read the filenames without this and nothing says which is the
    #: diffuse map and which is the bump map — they can only be
    #: assigned in the order they happen to appear.
    slot: int


@dataclasses.dataclass
class MaterialRecord:
    index: int
    shader: str
    textures: list[TextureRef]
    #: 17 floats of D3DMATERIAL9. Reported so a model whose colours are
    #: not all 1.0 is visible rather than silently averaged away.
    colours: tuple[float, ...]

    @property
    def all_default(self) -> bool:
        return all(abs(v - 1.0) < 1e-6 for v in self.colours)


def _fixed_string(block: bytes, offset: int, size: int) -> str:
    raw = block[offset:offset + size]
    return raw.split(b"\x00")[0].decode("ascii", errors="replace")


def parse_skin(block: bytes) -> list[MaterialRecord]:
    """Decode chunk 15 into materials and their texture references.

    Layout, read off four models rather than assumed::

        uint32                material_count
        per material, 172 bytes:
            float[17]         D3DMATERIAL9 (diffuse/ambient/specular/
                              emissive RGBA, then power)
            uint32            texture_count
            char[100]         shader name
        per texture, 48 bytes:
            char[44]          filename
            uint32            slot

    NOT yet established: whether the texture records follow each
    material or all of them. Only one sample carries textures at all,
    and it has a single material, so the two layouts are
    indistinguishable in the evidence available. This reads them as
    following the material that declares them, and says so rather than
    presenting a guess as fact.
    """
    if len(block) < 4 + MATERIAL_RECORD_SIZE:
        return []

    # The leading uint32 is NOT the record count. civilhouse1.gam — a
    # shipped model — holds 1 there and carries three material records,
    # and reading the field as a count found only the first: one
    # texture instead of three, which is precisely how textures end up
    # attached to the wrong thing. Records are walked to the end of the
    # chunk instead, and the header value is reported as the unknown it
    # still is.
    materials: list[MaterialRecord] = []
    position = 4
    index = 0

    while position + MATERIAL_RECORD_SIZE <= len(block):

        colours = struct.unpack_from(f"<{MATERIAL_FLOATS}f", block, position)
        texture_count = struct.unpack_from("<I", block, position + MATERIAL_FLOATS * 4)[0]
        shader = _fixed_string(
            block, position + MATERIAL_FLOATS * 4 + 4, SHADER_NAME_SIZE
        )
        position += MATERIAL_RECORD_SIZE

        textures: list[TextureRef] = []
        for _ in range(min(texture_count, 64)):
            if position + TEXTURE_RECORD_SIZE > len(block):
                break
            name = _fixed_string(block, position, TEXTURE_NAME_SIZE)
            slot = struct.unpack_from("<I", block, position + _SLOT_OFFSET)[0]
            textures.append(TextureRef(name=name, slot=slot))
            position += TEXTURE_RECORD_SIZE

        materials.append(
            MaterialRecord(
                index=index, shader=shader, textures=textures, colours=colours
            )
        )
        index += 1
        if index > 4096:
            break  # a chunk this layout does not fit; stop rather than spin

    return materials


def format_skin(materials: list[MaterialRecord]) -> list[str]:
    """The decoded skin chunk, in the terms that matter for rendering."""
    if not materials:
        return ["  (no material records decoded)"]

    lines = [f"  {len(materials)} material(s):"]
    for material in materials:
        lines.append(f"    [{material.index}] shader {material.shader!r}")
        if not material.textures:
            lines.append(
                "        NO TEXTURE REFERENCES — the engine has a shader and "
                "no image to bind, and shows whatever is already bound"
            )
        for texture in material.textures:
            lines.append(f"        slot {texture.slot}: {texture.name!r}")
        if not material.all_default:
            lines.append(f"        D3DMATERIAL9 is not all 1.0: {material.colours}")
    return lines


def survey(paths: list[str]) -> list[str]:
    """One line per model, aligned, for comparing many at once.

    Built for an intermittent fault. When some models can be placed in
    the game's editor and others cannot, no single file explains
    anything: the answer is whatever differs between the group that
    works and the group that does not. A full dump per model buries
    that under a thousand lines, so this reports only the handful of
    numbers that could plausibly separate them, one row each, and lets
    the pattern show itself.

    Offset is reported separately from size because they fail
    differently: an oversized model is placeable but wrong, while a
    model carrying its world position inside its geometry lands
    somewhere else entirely.
    """
    header = (
        f"{'model':<24} {'bytes':>9} {'mesh':>5} {'fmt':>7} "
        f"{'size x/y/z':>22} {'offset from origin':>22}  notes"
    )
    lines = [header, "-" * len(header)]

    for path in sorted(paths):
        name = os.path.basename(path)
        try:
            report = describe(path)
        except OSError as exc:
            lines.append(f"{name:<24} unreadable: {exc}")
            continue

        formats = {(m.stride, m.components) for m in report.meshes}
        if len(formats) == 1:
            stride, components = next(iter(formats))
            fmt = f"{components}/{stride}"
        elif formats:
            fmt = "mixed"
        else:
            fmt = "-"

        bounds = report.trailing_bounds
        if bounds and len(bounds) == 6:
            size = "/".join(f"{bounds[i + 3] - bounds[i]:.0f}" for i in range(3))
            # Distance of the box centre from the origin: a model whose
            # geometry carries its world position shows a large value
            # here while its size stays ordinary.
            centre = [(bounds[i] + bounds[i + 3]) / 2 for i in range(3)]
            offset = "/".join(f"{c:.0f}" for c in centre)
        else:
            size = offset = "-"

        notes = []
        if report.problems:
            notes.append(f"{len(report.problems)} STRUCTURAL FAULT(S)")
        skin = [c for c in report.chunks if c.chunk_id == SKIN_CHUNK_ID]
        textures = [
            s for c in skin for s in c.strings if s.lower().endswith((".dds", ".tga"))
        ]
        notes.append(f"{len(textures)} texture(s)")
        if not skin:
            notes.append("no skin chunk")

        lines.append(
            f"{name:<24} {report.size:>9} {len(report.meshes):>5} {fmt:>7} "
            f"{size:>22} {offset:>22}  {', '.join(notes)}"
        )

    lines += [
        "",
        "Compare the rows that can be placed against the rows that cannot. "
        "The column that splits them is the one worth chasing; the columns "
        "that agree are ruled out.",
    ]
    return lines


def format_comparison(result: Comparison) -> list[str]:
    """The comparison as printable lines."""
    lines = [
        f"Reference (renders): {os.path.basename(result.reference.path)}  "
        f"{result.reference.size} bytes",
        f"Subject  (suspect):  {os.path.basename(result.subject.path)}  "
        f"{result.subject.size} bytes",
        "",
    ]
    if result.identical:
        lines.append(
            "No structural difference found. The two files agree on chunk ids, "
            "chunk strings and every mesh header field, decoded or not — so "
            "whatever differs is inside the vertex or index data, or is not in "
            "the file at all."
        )
        return lines

    lines += [f"! {line}" if not line.startswith(" ") else line
              for line in result.differences]
    return lines
