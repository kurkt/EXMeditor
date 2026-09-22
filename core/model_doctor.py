# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Measure a ``.gam`` against what shipped models actually contain.

Why this is separate from ``gam_forensics``
-------------------------------------------

Forensics dumps structure and diffs two files: it answers "where do
these disagree" without knowing which side is right. This module knows.
Every check below is a property that holds across every shipped model
measured and fails on at least one generated one, so a finding here
names a specific defect rather than a difference.

That asymmetry is the whole value, and it is also the limit: a check
only exists here when there is a working file and a broken file that
disagree on it. Nothing is flagged because it looks unusual.

What it checks, and what proved each one
----------------------------------------

**Normals must be unit length.** ``cube1111`` carries normals of
magnitude 187.4784 — exactly its own half-size — while ``Cube44``,
from the same exporter with the same vertex type, carries unit normals.
The pair is what makes this a defect rather than a convention: object
scale left unapplied in Blender rides through into the normal, and the
model still renders, only lit wrongly. This is the cheapest explanation
yet found for "the generated model looks wrong and everything else
checks out".

**D3DMATERIAL9 should match the shipped signature.** Three shipped
models, four shaders, three vertex types, byte-identical values;
HTAToolchain writes all seventeen floats as 1.0. Specular full-on
against a game that ships with it off everywhere.

**Shader and vertex type must agree.** The format follows the shader —
``bump`` needs tangents, ``diffuse_vc`` needs a vertex colour. A model
declaring a shader whose inputs its vertices do not carry is asking for
something unsatisfiable.

**A material should name at least one texture.** ``Cube44`` names
``bumpdiffuse_envalphagloss_spec`` — a shader wanting bump, environment,
gloss and specular maps — and lists no images at all.

**Named textures should be findable.** Beside the model works, and so
do subfolders of ``data/models/textures`` — which is where shipped
models actually keep theirs. The root of that folder does not. See
``_texture_roots``.

Repairs
-------

Each repair changes only the bytes its finding named, and the container
is rewritten from the chunks it was read as — a file passed through
with no repairs selected comes out byte-identical.
"""

from __future__ import annotations

import dataclasses
import math
import os
import struct

from formats.exm.gam import (
    CANDIDATE_VERTEX_LAYOUTS,
    MESH_CHUNK_ID,
    VERTEX_FIELD_SIZES,
    VERTEX_LAYOUTS,
    VERTEX_TYPE_OFFSET,
    Chunk,
    _find_index_buffer,
    read_container,
    write_container,
)
from formats.exm.skin import (
    KNOWN_SHADERS,
    SHADER_PROFILES,
    SLOT_NAMES,
    SIMPLE_SHADER,
    SKIN_CHUNK_ID,
    SLOT_DIFFUSE,
    parse_skin,
    patch_materials,
    shipped_values,
)
from utils.errors import ErrorContext, ParsingError

_MESH_HEADER_SIZE = 72
_STRIDE_OFFSET = 56
_VERTEX_COUNT_OFFSET = 64
_TRIANGLE_COUNT_OFFSET = 68

#: How far a normal's length may drift from 1 before it is called
#: broken. Generous: the failing sample is off by a factor of 187, and
#: quantisation in a legitimate file never approaches this.
_UNIT_TOLERANCE = 0.01

#: Chunk ids HTAToolchain adds and shipped models never carry. Not a
#: defect — recorded because it identifies where a file came from,
#: which is the first thing worth knowing when a model misbehaves.
_TOOLCHAIN_CHUNKS = (61443, 61444)

OK = "ok"
WARNING = "warning"
PROBLEM = "problem"


@dataclasses.dataclass
class Finding:
    """One check, its verdict, and what it measured."""

    level: str
    code: str
    message: str
    detail: str = ""

    def __str__(self) -> str:
        mark = {OK: "  ok", WARNING: "warn", PROBLEM: "FAIL"}[self.level]
        line = f"[{mark}] {self.message}"
        return f"{line}\n        {self.detail}" if self.detail else line


@dataclasses.dataclass
class MeshSummary:
    """What one mesh in the mesh chunk holds."""

    index: int
    stride: int
    vertex_type: int
    vertex_count: int
    triangle_count: int
    data_offset: int
    index_offset: int | None
    normal_min: float = 0.0
    normal_max: float = 0.0

    @property
    def normals_are_unit(self) -> bool:
        return (
            abs(self.normal_min - 1.0) < _UNIT_TOLERANCE
            and abs(self.normal_max - 1.0) < _UNIT_TOLERANCE
        )

    @property
    def buffer_count(self) -> int:
        """How many vertex buffers sit before the index buffer."""
        if self.index_offset is None or self.vertex_count == 0:
            return 1
        span = self.index_offset - self.data_offset
        return max(1, span // (self.vertex_count * self.stride))


@dataclasses.dataclass
class Report:
    """Everything measured about one model."""

    path: str
    subtype: int = 0
    chunk_ids: list[int] = dataclasses.field(default_factory=list)
    meshes: list[MeshSummary] = dataclasses.field(default_factory=list)
    findings: list[Finding] = dataclasses.field(default_factory=list)
    from_toolchain: bool = False

    @property
    def problems(self) -> list[Finding]:
        return [f for f in self.findings if f.level == PROBLEM]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == WARNING]

    @property
    def healthy(self) -> bool:
        return not self.problems


def _walk_meshes(block: bytes) -> list[MeshSummary]:
    """Enumerate meshes without interpreting their geometry.

    Stops at the first header that fails a sanity check rather than
    raising: a mesh chunk that runs out early is itself reportable, and
    the meshes already read are still worth reporting.
    """
    meshes: list[MeshSummary] = []
    position = 0

    while position + _MESH_HEADER_SIZE <= len(block):
        stride = struct.unpack_from("<I", block, position + _STRIDE_OFFSET)[0]
        vertex_type = struct.unpack_from("<I", block, position + VERTEX_TYPE_OFFSET)[0]
        vertex_count = struct.unpack_from("<I", block, position + _VERTEX_COUNT_OFFSET)[0]
        triangle_count = struct.unpack_from(
            "<I", block, position + _TRIANGLE_COUNT_OFFSET
        )[0]

        if not (12 <= stride <= 256 and 0 < vertex_count < 500_000):
            break
        if triangle_count >= 500_000:
            break

        data_offset = position + _MESH_HEADER_SIZE
        index_offset = _find_index_buffer(
            block, data_offset, stride, vertex_count, triangle_count
        )

        summary = MeshSummary(
            index=len(meshes),
            stride=stride,
            vertex_type=vertex_type,
            vertex_count=vertex_count,
            triangle_count=triangle_count,
            data_offset=data_offset,
            index_offset=index_offset,
        )

        lengths = []
        for i in range(vertex_count):
            at = data_offset + i * stride + VERTEX_FIELD_SIZES["position"]
            if at + 12 > len(block):
                break
            x, y, z = struct.unpack_from("<3f", block, at)
            lengths.append(math.sqrt(x * x + y * y + z * z))
        if lengths:
            summary.normal_min = min(lengths)
            summary.normal_max = max(lengths)

        meshes.append(summary)

        if index_offset is None:
            break
        position = index_offset + triangle_count * 6

    return meshes


def diagnose(path: str) -> Report:
    """Measure a model and return every finding, good and bad."""
    subtype, chunks = read_container(path)
    report = Report(path=path, subtype=subtype)
    report.chunk_ids = [c.chunk_id for c in chunks]
    report.from_toolchain = any(
        c.chunk_id in _TOOLCHAIN_CHUNKS for c in chunks
    )

    by_id = {c.chunk_id: c.data for c in chunks}

    if MESH_CHUNK_ID in by_id:
        report.meshes = _walk_meshes(by_id[MESH_CHUNK_ID])

    skin = None
    if SKIN_CHUNK_ID in by_id:
        try:
            skin = parse_skin(by_id[SKIN_CHUNK_ID], source_file=path)
        except ParsingError as exc:
            report.findings.append(
                Finding(
                    PROBLEM,
                    "skin_unreadable",
                    "Skin chunk does not follow the known layout",
                    exc.message,
                )
            )
    else:
        report.findings.append(
            Finding(PROBLEM, "skin_missing", "No skin chunk — the model has no materials")
        )

    _check_normals(report)
    _check_shader_and_type(report, skin)
    _check_material_values(report, skin)
    _check_textures(report, skin, path)

    return report


def _check_normals(report: Report) -> None:
    broken = [m for m in report.meshes if not m.normals_are_unit]
    if not report.meshes:
        return
    if not broken:
        report.findings.append(
            Finding(OK, "normals_unit", f"Normals are unit length in all {len(report.meshes)} mesh(es)")
        )
        return

    worst = max(broken, key=lambda m: abs(m.normal_max - 1.0))
    report.findings.append(
        Finding(
            PROBLEM,
            "normals_not_unit",
            f"Normals are not unit length in {len(broken)} of {len(report.meshes)} mesh(es)",
            (
                f"mesh {worst.index}: |normal| ranges {worst.normal_min:.4f}..{worst.normal_max:.4f}. "
                "This is what object scale left unapplied in Blender looks like — the scale "
                "rides into the normal. The model still draws; its lighting is wrong. "
                "Apply scale (Ctrl+A) before export, or use the Normalise Normals repair."
            ),
        )
    )


def _check_shader_and_type(report: Report, skin) -> None:
    if skin is None or not skin.materials:
        return

    types = {m.vertex_type for m in report.meshes}
    shaders = {m.shader for m in skin.materials if m.shader}

    lowered = {key.lower(): value for key, value in SHADER_PROFILES.items()}

    for shader in sorted(shaders):
        # Case-insensitive: the game spells the same shader several
        # ways — 1224 materials say BumpDiffuse_EnvAlphaGloss_Spec and
        # 372 say bumpdiffuse_envalphagloss_spec.
        profile = lowered.get(shader.lower())
        if profile is None and shader.lower() in KNOWN_SHADERS:
            continue
        if profile is None:
            report.findings.append(
                Finding(
                    WARNING,
                    "shader_unknown",
                    f"Shader {shader!r} has not been seen in any measured model",
                    "Its vertex requirements are unknown, so nothing here can check them.",
                )
            )
            continue
        wanted_type, wanted_stride = profile
        if types and wanted_type not in types:
            report.findings.append(
                Finding(
                    PROBLEM,
                    "shader_type_mismatch",
                    f"Shader {shader!r} wants vertex type {wanted_type} (stride {wanted_stride})",
                    f"the mesh carries type {sorted(types)}. The vertex format follows the "
                    "shader; a shader whose inputs the vertices do not carry cannot be satisfied.",
                )
            )

    for vertex_type in sorted(types):
        if vertex_type in VERTEX_LAYOUTS:
            continue
        if vertex_type in CANDIDATE_VERTEX_LAYOUTS:
            stride, fields = CANDIDATE_VERTEX_LAYOUTS[vertex_type]
            report.findings.append(
                Finding(
                    OK,
                    "vertex_type_derived",
                    f"Vertex type {vertex_type} reads as {'+'.join(fields)}",
                    "Derived from its stride rather than confirmed on a file, "
                    "and checked against this model's own vertices before use.",
                )
            )
            continue
        report.findings.append(
            Finding(
                WARNING,
                "vertex_type_unknown",
                f"Vertex type {vertex_type} has not been seen in any measured model",
            )
        )


def _check_material_values(report: Report, skin) -> None:
    if skin is None or not skin.materials:
        return

    off = [i for i, m in enumerate(skin.materials) if not m.matches_shipped_values()]
    if not off:
        report.findings.append(
            Finding(
                OK,
                "material_values_shipped",
                f"D3DMATERIAL9 matches the shipped signature in all {len(skin.materials)} material(s)",
            )
        )
        return

    sample = skin.materials[off[0]]
    report.findings.append(
        Finding(
            WARNING,
            "material_values_unshipped",
            f"{len(off)} of {len(skin.materials)} material(s) carry values no shipped model uses",
            (
                f"material {off[0]}: specular {tuple(round(v, 3) for v in sample.specular)}, "
                f"power {sample.power:g}. Every shipped model measured carries specular "
                "(0,0,0,0) and power 0 — the game ships with the specular term off. "
                "UNCONFIRMED as a cause of anything; the Shipped Material Values repair "
                "exists to test it in-game."
            ),
        )
    )


def _texture_roots(model_path: str) -> list[str]:
    """Where a bare texture filename can resolve.

    **Beside the model is PROVEN.** A generated texture named
    ``mininao.dds`` was dropped into ``data/models/custom``, where
    ``cube1111.gam`` had been failing to find it every session. The
    six log lines about it vanished, and reappeared the moment the file
    was moved out. That is the first positive observation of a texture
    resolving anywhere, and it is what this SDK writes against.

    **The root of ``data/models/textures`` is PROVEN NOT searched.**
    The same file placed there, with the editor restarted, produced the
    same ``Cannot open data/models/custom/mininao.dds`` as before. So
    there is no blanket search of the shared tree.

    That leaves a loose end rather than a contradiction.
    ``metal_elements.dds`` is requested by the same model, is not in
    ``data/models/custom``, and never appears in any log. Something
    resolves it. The most economical explanation is a texture cache
    keyed on the bare filename — another model loaded it earlier in the
    session from its own folder, and the second request never touched
    the disk. That would need no search path at all. UNTESTED, and
    named here so it is not mistaken for a finding.

    The shared tree is still searched by this function, because a
    texture found there is worth reporting. It is reported as a warning
    rather than an ok: nothing has shown the engine will find it.
    """
    folder = os.path.dirname(os.path.abspath(model_path))
    roots = [folder]

    # Walk up by dirname rather than splitting on separators and
    # rejoining. On Windows, os.path.join("K:", "GC") produces "K:GC",
    # a path relative to the current directory on drive K — which is
    # not the same directory and generally does not exist. That bug
    # made this function silently find nothing on the one platform the
    # game runs on.
    current = folder
    while True:
        if os.path.basename(current).lower() == "models":
            shared = os.path.join(current, "textures")
            if os.path.isdir(shared):
                roots.append(shared)
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    return roots


def _find_texture(filename: str, roots: list[str]) -> str | None:
    """Locate a bare filename, searching shared roots recursively.

    Case-insensitive: the chunk stores ``MininAO.dds`` and the editor
    logs ``mininao.dds``, so the engine is clearly not case-sensitive
    on Windows, and neither is this.
    """
    wanted = filename.lower()

    for root in roots:
        direct = os.path.join(root, filename)
        if os.path.isfile(direct):
            return direct

    for root in roots[1:]:
        for folder, _dirs, files in os.walk(root):
            for name in files:
                if name.lower() == wanted:
                    return os.path.join(folder, name)

    return None


def _check_textures(report: Report, skin, path: str) -> None:
    if skin is None or not skin.materials:
        return

    roots = _texture_roots(path)
    model_folder = roots[0]

    bare = [i for i, m in enumerate(skin.materials) if not m.textures]
    if bare:
        report.findings.append(
            Finding(
                PROBLEM,
                "material_without_texture",
                f"{len(bare)} material(s) name a shader and no texture at all",
                f"material {bare[0]}: shader {skin.materials[bare[0]].shader!r}. "
                "HTAToolchain writes no textures unless the material's image node is named "
                "for it (Diffuse, Bump, Lightmap, Cube, Detail).",
            )
        )

    missing: list[str] = []
    elsewhere: list[str] = []
    beside: list[str] = []
    unknown_slots: list[int] = []

    for material in skin.materials:
        for texture in material.textures:
            if texture.slot >= len(SLOT_NAMES):
                unknown_slots.append(texture.slot)

            found = _find_texture(texture.filename, roots)
            if found is None:
                missing.append(texture.filename)
            elif os.path.dirname(found) == model_folder:
                beside.append(texture.filename)
            else:
                elsewhere.append(f"{texture.filename} -> {os.path.dirname(found)}")

    if missing:
        report.findings.append(
            Finding(
                PROBLEM,
                "texture_not_found",
                f"{len(missing)} texture(s) are in neither the model's folder nor the "
                "shared texture tree",
                f"missing: {', '.join(sorted(set(missing))[:4])}",
            )
        )

    if elsewhere:
        report.findings.append(
            Finding(
                WARNING,
                "texture_in_shared_tree_only",
                f"{len(elsewhere)} texture(s) exist only in the shared texture tree",
                (
                    "; ".join(sorted(set(elsewhere))[:3])
                    + ". Subfolders of data/models/textures do resolve — that is where "
                    "shipped models keep their textures. What does NOT resolve is the "
                    "root of that folder: a file placed directly there was still "
                    "reported missing after a restart, while the same file beside the "
                    "model was found. Nothing to do unless the texture is in the root."
                ),
            )
        )

    if beside:
        report.findings.append(
            Finding(
                OK,
                "textures_beside_model",
                f"{len(beside)} texture(s) are beside the model",
            )
        )

    if unknown_slots:
        report.findings.append(
            Finding(
                WARNING,
                "slot_unknown",
                f"Texture slot(s) {sorted(set(unknown_slots))} have no measured meaning",
                "Slots 0 to 4 are accounted for: diffuse, bump, lightmap, cube, "
                "detail. Anything above that has not been seen.",
            )
        )


# --- repairs ---------------------------------------------------------


@dataclasses.dataclass
class RepairOptions:
    """Which repairs to apply. All default off."""

    normalise_normals: bool = False
    shipped_material_values: bool = False
    simplify_shader: bool = False


def _normalise_mesh_normals(block: bytes) -> tuple[bytes, int]:
    """Rescale every normal to unit length, leaving all else alone."""
    patched = bytearray(block)
    fixed = 0
    offset = VERTEX_FIELD_SIZES["position"]

    for mesh in _walk_meshes(block):
        if mesh.normals_are_unit:
            continue
        for i in range(mesh.vertex_count):
            at = mesh.data_offset + i * mesh.stride + offset
            x, y, z = struct.unpack_from("<3f", patched, at)
            length = math.sqrt(x * x + y * y + z * z)
            if length <= 0.0:
                continue
            struct.pack_into("<3f", patched, at, x / length, y / length, z / length)
            fixed += 1

    return bytes(patched), fixed


def _convert_vertices(block: bytes, target_type: int) -> bytes:
    """Rewrite the mesh chunk into another vertex type.

    Only fields both layouts share survive; the rest are dropped. Going
    from type 15 to type 7 discards tangents, which is exactly what the
    ``diffuse`` shader does not need. Fields the target has and the
    source lacks would have to be invented, so that direction is
    refused.

    Meshes carrying more than one vertex buffer are refused outright.
    Some models store a second buffer whose purpose is not established,
    and rewriting a buffer nobody has explained is how unknown data gets
    destroyed.
    """
    target_stride, target_fields = VERTEX_LAYOUTS[target_type]
    out = bytearray()
    position = 0

    for mesh in _walk_meshes(block):
        if mesh.vertex_type not in VERTEX_LAYOUTS:
            raise ValueError(f"vertex type {mesh.vertex_type} has no measured layout")

        source_stride, source_fields = VERTEX_LAYOUTS[mesh.vertex_type]
        if source_stride != mesh.stride:
            raise ValueError(
                f"mesh {mesh.index}: type {mesh.vertex_type} implies stride "
                f"{source_stride} but the header says {mesh.stride}"
            )
        missing = [f for f in target_fields if f not in source_fields]
        if missing:
            raise ValueError(
                f"mesh {mesh.index}: target needs {missing}, which type "
                f"{mesh.vertex_type} does not carry"
            )
        if mesh.buffer_count != 1:
            raise ValueError(
                f"mesh {mesh.index} has {mesh.buffer_count} vertex buffers; "
                "conversion only handles one"
            )
        if mesh.index_offset is None:
            raise ValueError(f"mesh {mesh.index}: index buffer not located")

        source_at = {}
        cursor = 0
        for field in source_fields:
            source_at[field] = cursor
            cursor += VERTEX_FIELD_SIZES[field]

        header = bytearray(block[position : position + _MESH_HEADER_SIZE])
        struct.pack_into("<I", header, _STRIDE_OFFSET, target_stride)
        struct.pack_into("<I", header, VERTEX_TYPE_OFFSET, target_type)
        out += header

        for i in range(mesh.vertex_count):
            base = mesh.data_offset + i * mesh.stride
            for field in target_fields:
                start = base + source_at[field]
                out += block[start : start + VERTEX_FIELD_SIZES[field]]

        index_end = mesh.index_offset + mesh.triangle_count * 6
        out += block[mesh.index_offset : index_end]
        position = index_end

    out += block[position:]
    return bytes(out)


def repair(path: str, destination: str, options: RepairOptions) -> list[str]:
    """Apply the selected repairs and write a new model.

    Returns one line per action taken. With no options selected the
    output is byte-identical to the input, which is the property that
    makes this safe to run on a shipped file by accident.
    """
    subtype, chunks = read_container(path)
    actions: list[str] = []
    rebuilt: list[Chunk] = []

    target_type = None
    if options.simplify_shader:
        target_type = SHADER_PROFILES[SIMPLE_SHADER][0]

    for chunk in chunks:
        data = chunk.data

        if chunk.chunk_id == MESH_CHUNK_ID:
            if options.normalise_normals:
                data, fixed = _normalise_mesh_normals(data)
                if fixed:
                    actions.append(f"normalised {fixed} normal(s)")
            if target_type is not None:
                before = len(data)
                data = _convert_vertices(data, target_type)
                actions.append(
                    f"converted vertices to type {target_type} "
                    f"({before} -> {len(data)} bytes)"
                )

        elif chunk.chunk_id == SKIN_CHUNK_ID:
            if options.shipped_material_values:
                data = patch_materials(data, values=shipped_values(), source_file=path)
                actions.append("set D3DMATERIAL9 to the shipped signature")
            if options.simplify_shader:
                skin = parse_skin(data, source_file=path)
                dropped = [
                    t.filename
                    for m in skin.materials
                    for t in m.textures
                    if t.slot != SLOT_DIFFUSE
                ]
                data = patch_materials(data, shader=SIMPLE_SHADER, source_file=path)
                actions.append(f"set shader to {SIMPLE_SHADER!r}")
                if dropped:
                    actions.append(
                        "note: non-diffuse texture records are left in place "
                        f"({', '.join(dropped)}); the shader ignores them"
                    )

        rebuilt.append(Chunk(chunk_id=chunk.chunk_id, data=data))

    with open(destination, "wb") as f:
        f.write(write_container(subtype, rebuilt))

    if not actions:
        actions.append("nothing to change — written back unmodified")
    return actions


def format_report(report: Report) -> str:
    """Render a report for the system console."""
    lines = [
        "=" * 68,
        f"Model Doctor: {os.path.basename(report.path)}",
        "=" * 68,
        f"source: {'HTAToolchain' if report.from_toolchain else 'no toolchain stamp (shipped, or written by this SDK)'}",
        f"chunks: {report.chunk_ids}",
    ]

    for mesh in report.meshes:
        layout = VERTEX_LAYOUTS.get(mesh.vertex_type) or CANDIDATE_VERTEX_LAYOUTS.get(
            mesh.vertex_type
        )
        fields = "+".join(layout[1]) if layout else "unknown layout"
        lines.append(
            f"  mesh {mesh.index}: type {mesh.vertex_type} stride {mesh.stride} "
            f"({fields}), {mesh.vertex_count} verts, {mesh.triangle_count} tris"
        )

    lines.append("")
    for finding in report.findings:
        lines.append(str(finding))

    lines.append("")
    if report.healthy:
        lines.append(f"No problems. {len(report.warnings)} warning(s).")
    else:
        lines.append(
            f"{len(report.problems)} problem(s), {len(report.warnings)} warning(s)."
        )
    return "\n".join(lines)
