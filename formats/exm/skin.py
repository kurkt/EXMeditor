# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Codec for chunk 15 of a ``.gam`` — materials and their textures.

Why this replaces reading by string scan
----------------------------------------

``gam.py`` used to recover materials by scanning the chunk for ASCII
runs: a word without a dot started a material, a word ending in an
image extension was one of its textures. That got the *names* right and
could not get the *slots* right, because a filename does not say what
it is for. ``metal_elements.dds`` and ``MininAO.dds`` are a diffuse map
and a bump map, and nothing about either string says which.

The layout below was derived from record sizes and then checked
byte-exact against five shipped and generated models::

    uint32                      leading value — NOT a record count
    per material, 172 bytes:
        float[17]               D3DMATERIAL9: diffuse, ambient,
                                specular, emissive as RGBA, then power
        uint32                  texture_count
        char[100]               shader name
        per texture, 48 bytes:
            char[40]            filename
            uint32              uv_set
            uint32              slot

The texture record was first read as ``char[44] + uint32`` — the
names came out right, because every shipped name is 37 characters or
shorter and the NUL padding ends the string, and so did the slot. It
is wrong. MEASURED over 11 062 records in 1373 shipped models: bytes
40..43 hold 1 in 155 records and 2 in 36, in 107 models, while the
name is well short of 40 — ``lightmap_detail`` slot 2 on UV set 1,
``road_detail``/``diffuse_detail`` slot 4 on UV set 1 or 2,
``DiffuseAO`` slot 2 on UV set 1. HTAToolchain's own parser reads
``<40s``, ``<I`` uv, ``<I`` type. A 44-byte name write zeroed the UV
set of any record it touched: ``set_texture`` on ``bridge_stone``
material 1 slot 4 (``coverrock_detail.dds``, UV set 1) left the
detail map mapped through UV set 0.

Texture records follow **each** material, interleaved — not all
materials followed by all textures. ``big_flag01`` settles it: nine
materials carrying two textures each, and ``4 + 9*(172 + 2*48)`` is
2416, the exact chunk size. The alternative grouping would total the
same bytes and desynchronise on the first record.

Verified sizes::

    civilhouse1   664 = 4 + 3*172 + 3*48    3 materials, 1 texture each
    big_flag01   2416 = 4 + 9*172 + 18*48   9 materials, 2 textures each
    cube1111      272 = 4 + 1*172 + 2*48    1 material,  2 textures
    Cube44        176 = 4 + 1*172 + 0*48    1 material,  no textures
    factory_box   224 = 4 + 1*172 + 1*48    1 material,  1 texture

The leading uint32 is not the record count
------------------------------------------

``civilhouse1`` stores 1 there and carries three materials;
``big_flag01`` stores 9 and carries nine. Trusting it found one
material in a file with three. Records are walked to the end of the
chunk instead, and the value is preserved verbatim on write because
nobody knows what it means.

Preserving what is not understood
---------------------------------

Between the shader name and the texture records sit bytes that look
like uninitialised stack from the original 2005 exporter — pointer-like
values such as ``0x004015b2``. They are meaningless, but they are also
not ours to invent, so :func:`patch_materials` edits the fields it is
asked to edit and copies every other byte through unchanged. Only
:func:`build_skin`, which writes a chunk that did not exist before,
starts from zeroes.
"""

from __future__ import annotations

import dataclasses
import struct

from utils.errors import ErrorContext, ParsingError

#: Chunk id, repeated here so callers can import one module.
SKIN_CHUNK_ID = 15

#: ``float[17] + uint32 + char[100]``.
MATERIAL_RECORD_SIZE = 172

#: ``char[40] + uint32 uv_set + uint32 slot``.
TEXTURE_RECORD_SIZE = 48

SHADER_NAME_SIZE = 100
TEXTURE_NAME_SIZE = 40

#: Offsets inside a texture record, past the name.
_UV_SET_OFFSET = 40
_SLOT_OFFSET = 44

#: Offset of the texture count inside a material record: 17 floats in.
_TEXTURE_COUNT_OFFSET = 68

#: Offset of the shader name: past the count.
_SHADER_NAME_OFFSET = 72

#: How many bytes at the head of a material record the D3DMATERIAL9
#: occupies. :func:`patch_materials` never writes past this.
D3DMATERIAL9_SIZE = 68

#: Texture slot numbers, from two independent samples. ``big_flag01``
#: is shipped, runs the ``bump`` shader and stores ``big_flag01.dds``
#: in slot 0 with ``big_flag01b.dds`` in slot 1 — the ``b`` suffix is
#: the bump map by the original artist's own naming. ``cube1111`` comes
#: from HTAToolchain and puts the base colour in 0 and an ambient
#: occlusion map in 1. Slots above 1 have never been observed in any
#: model measured, so they are deliberately absent from this table
#: rather than guessed from HTAToolchain's node-name order.
SLOT_DIFFUSE = 0
SLOT_BUMP = 1

#: Slot 2, measured on TheTown: all six of its materials carry
#: ``MininAO.dds`` there under the ``DiffuseAO`` and ``SpecularAO``
#: shaders — an ambient occlusion map. Slots 3 and up remain unseen.
SLOT_LIGHTMAP = 2

#: Slot 3 and 4, corroborated by the census rather than by a single
#: model: 1856 records in slot 3 against 1596 materials running
#: ``BumpDiffuse_EnvAlphaGloss_Spec``, which wants an environment map;
#: 616 in slot 4 against the ``*_detail`` shaders. The names come from
#: the node names the toolchain matches on, and the counts agree with
#: what those shaders need.
SLOT_CUBE = 3
SLOT_DETAIL = 4

SLOT_NAMES = {
    SLOT_DIFFUSE: "Diffuse",
    SLOT_BUMP: "Bump",
    SLOT_LIGHTMAP: "Lightmap",
    SLOT_CUBE: "Cube",
    SLOT_DETAIL: "Detail",
}

#: Every shader the census found across 1388 models, lowercased. Being
#: in this set means the name is real game data rather than a misread
#: chunk — not that its vertex requirements are understood.
KNOWN_SHADERS = frozenset(
    {
        "diffuse",
        "diffuse_vc",
        "diffuse_detail",
        "diffuse_detail_vc",
        "diffuseao",
        "specular",
        "specular_vc",
        "specularao",
        "bump",
        "bump_vc",
        "bumpdiffuse_envalphagloss_spec",
        "lightmap_detail",
        "road",
        "road_detail",
        "skinned",
        "tree_nolights",
        "rope",
    }
)

#: The D3DMATERIAL9 every shipped model carries, byte-identical across
#: ``civilhouse1`` (``diffuse_vc``, ``specular_vc``), ``big_flag01``
#: (``bump``) and ``factory_box`` (``diffuse``) — three models, four
#: shaders, three vertex formats, no deviation. 0.8/0.8/0.8 is the
#: default diffuse of the 3ds Max material these assets were authored
#: in, which is consistent with nobody having touched these fields.
#:
#: HTAToolchain writes all seventeen floats as 1.0. The interesting
#: half of that difference is not the diffuse but ``specular`` and
#: ``power``: the game ships with the specular term switched off
#: everywhere, and generated models turn it fully on.
SHIPPED_DIFFUSE = (0.8, 0.8, 0.8, 1.0)
SHIPPED_AMBIENT = (0.0, 0.0, 0.0, 1.0)
SHIPPED_SPECULAR = (0.0, 0.0, 0.0, 0.0)
SHIPPED_EMISSIVE = (0.0, 0.0, 0.0, 1.0)
SHIPPED_POWER = 0.0

#: Shaders seen on shipped models. ``DiffuseAO`` and ``SpecularAO``
#: appear on TheTown and take an occlusion map in slot 2; the vertex
#: formats they run on have not been measured, so they are absent from
#: the profile table below rather than guessed into it.
AO_SHADERS = ("DiffuseAO", "SpecularAO")

#: Shader to (vertex type, stride), as measured. The vertex format
#: follows the shader: ``bump`` needs tangents, ``diffuse_vc`` needs a
#: vertex colour, ``diffuse`` needs neither. There is no single correct
#: format, and forcing one was a category error this project has
#: already paid for once.
def normalise_shader(shader: str) -> str:
    """Fold a shader name to the form the tables are keyed by.

    The game spells the same shader several ways. Across 1388 models
    the census finds ``BumpDiffuse_EnvAlphaGloss_Spec`` alongside
    ``bumpdiffuse_envalphagloss_spec``, ``Diffuse`` alongside
    ``diffuse``, ``DiffuseAO`` alongside ``diffuseAO``, and
    ``tree_noLights`` alongside ``tree_nolights``. They are the same
    shader; only the capitalisation differs.
    """
    return (shader or "").lower()


SHADER_PROFILES = {
    "diffuse": (7, 32),
    "diffuse_vc": (8, 36),
    "specular_vc": (8, 36),
    "bump": (15, 48),
    "bumpdiffuse_envalphagloss_spec": (15, 48),
}

#: The lightest profile the game itself ships: one diffuse texture, no
#: vertex colour, no tangents. ``factory_box`` — a shipped map
#: decoration — runs it, which is what makes it a safe target for
#: simple static props generated from Blender.
SIMPLE_SHADER = "diffuse"

#: Shaders that take an occlusion or lightmap in a slot of their own,
#: lowercased. From the census: ``DiffuseAO``, ``diffuseAO``,
#: ``SpecularAO``, ``lightmap_detail``.
LIGHTMAP_SHADERS = ("diffuseao", "specularao", "lightmap_detail")

#: Which slots each shader uses, from ``Shaders.txt`` — the config of
#: TARGEM's own Maya exporter, which is what decided what went into the
#: ``.gam`` in the first place::
#:
#:     diffuse                            Diffuse
#:     diffuse_vc                    VC   Diffuse
#:     bump                          TS   Diffuse  Bump
#:     BumpDiffuse_EnvAlphaGloss_*   TS   Diffuse  Bump      Cubemap
#:     diffuse_detail                     Diffuse                   Detail
#:     diffuse_detail_vc             VC   Diffuse                   Detail
#:     lightmap_detail                    Diffuse  Lightmap         Detail
#:     embm_mask                     TS   Diffuse  Bump  Cubemap    Mask
#:     road_detail                        Diffuse                   Detail
#:
#: ``Shaders.txt`` implies Cubemap and Detail never appear together.
#: **That is wrong.** A census of 1355 models finds ``Skinned`` using
#: all five slots at once on 213 materials, and ``diffuse`` occasionally
#: combining 0, 1, 3 and 4. Slot use across the whole corpus::
#:
#:     slot 0  3447  diffuse
#:     slot 1  1872  normal map
#:     slot 2   247  lightmap (Skinned and lightmap_detail only)
#:     slot 3  1206  reflection cube map
#:     slot 4   438  detail
#:
#: The exporter config describes what its own presets emit, not what
#: the format allows.
SHADER_SLOTS = {
    "diffuse": ("diffuse",),
    "diffuse_vc": ("diffuse",),
    "specular": ("diffuse",),
    "specular_vc": ("diffuse",),
    "bump": ("diffuse", "bump"),
    "bump_vc": ("diffuse", "bump"),
    "bumpdiffuse_envalphagloss_spec": ("diffuse", "bump", "cubemap"),
    "diffuse_detail": ("diffuse", "detail"),
    "diffuse_detail_vc": ("diffuse", "detail"),
    "lightmap_detail": ("diffuse", "lightmap", "detail"),
    "road_detail": ("diffuse", "detail"),
    "diffuseao": ("diffuse", "lightmap"),
    "specularao": ("diffuse", "lightmap"),
    "embm_mask": ("diffuse", "bump", "cubemap", "mask"),
    "skinned": ("diffuse", "bump", "lightmap", "cubemap", "detail"),
}

#: Shaders that take a detail texture, derived from the table above.
DETAIL_SHADERS = tuple(
    name for name, slots in SHADER_SLOTS.items() if "detail" in slots
)


def uses_detail(shader: str) -> bool:
    """Whether a shader's own definition says it reads a detail map."""
    return normalise_shader(shader) in DETAIL_SHADERS


@dataclasses.dataclass
class SkinTexture:
    """One 48-byte texture record."""

    filename: str
    slot: int
    #: Which of the mesh's UV sets the map is read through. 0 for
    #: 10 871 of 11 062 shipped records; 1 or 2 for a lightmap, AO or
    #: detail map on a second set. Preserved byte-for-byte on edit.
    uv_set: int = 0

    @property
    def slot_name(self) -> str:
        """A human label, or the bare number when the slot is unknown.

        Unknown slots are reported as unknown rather than folded into
        the nearest guess: slots above 1 have not been seen, and naming
        one would put an invention in a diagnostic report.
        """
        return SLOT_NAMES.get(self.slot, f"slot {self.slot}")


@dataclasses.dataclass
class SkinMaterial:
    """One 172-byte material record and the textures that follow it."""

    shader: str = ""
    diffuse: tuple[float, float, float, float] = SHIPPED_DIFFUSE
    ambient: tuple[float, float, float, float] = SHIPPED_AMBIENT
    specular: tuple[float, float, float, float] = SHIPPED_SPECULAR
    emissive: tuple[float, float, float, float] = SHIPPED_EMISSIVE
    power: float = SHIPPED_POWER
    textures: list[SkinTexture] = dataclasses.field(default_factory=list)

    def texture_for_slot(self, slot: int) -> str | None:
        """The filename in ``slot``, or None.

        This is the whole reason the chunk is parsed structurally. Order
        of appearance is not slot: a material may carry slot 1 without
        slot 0, and picking ``textures[0]`` as the diffuse map is how
        images end up on the wrong channel in both directions.
        """
        for texture in self.textures:
            if texture.slot == slot:
                return texture.filename
        return None

    @property
    def material_values(self) -> tuple[float, ...]:
        """The seventeen D3DMATERIAL9 floats in file order."""
        return (
            *self.diffuse,
            *self.ambient,
            *self.specular,
            *self.emissive,
            self.power,
        )

    def matches_shipped_values(self) -> bool:
        """Whether the D3DMATERIAL9 equals the shipped signature."""
        shipped = (
            *SHIPPED_DIFFUSE,
            *SHIPPED_AMBIENT,
            *SHIPPED_SPECULAR,
            *SHIPPED_EMISSIVE,
            SHIPPED_POWER,
        )
        return all(
            abs(a - b) < 1e-6 for a, b in zip(self.material_values, shipped)
        )


@dataclasses.dataclass
class Skin:
    """A parsed skin chunk."""

    #: Preserved verbatim. Not a count — see the module docstring.
    leading: int = 1
    materials: list[SkinMaterial] = dataclasses.field(default_factory=list)

    #: Byte offset of each material record within the chunk, so a
    #: caller can patch in place without re-deriving the walk.
    offsets: list[int] = dataclasses.field(default_factory=list)


def _read_string(block: bytes, offset: int, size: int) -> str:
    raw = block[offset : offset + size]
    return raw.split(b"\0")[0].decode("latin-1", errors="replace")


def _pack_string(text: str, size: int) -> bytes:
    """Encode to a fixed-size NUL-padded field.

    Raises rather than truncating. A silently shortened texture name
    produces a model that loads and shows the wrong image, which is
    far more expensive to notice than a refused export.
    """
    encoded = text.encode("latin-1", errors="replace")
    if len(encoded) >= size:
        raise ValueError(f"{text!r} does not fit in {size} bytes (needs a NUL)")
    return encoded.ljust(size, b"\0")


def parse_skin(block: bytes, source_file: str = "") -> Skin:
    """Read a skin chunk into materials and textures.

    Walks records to the end of the chunk. A chunk whose records do not
    land exactly on its end is reported rather than silently truncated:
    a partial walk means the layout assumption is wrong for that file,
    and that is a finding, not a detail to absorb.
    """
    if len(block) < 4:
        return Skin(leading=0)

    leading = struct.unpack_from("<I", block, 0)[0]
    skin = Skin(leading=leading)

    position = 4
    while position + MATERIAL_RECORD_SIZE <= len(block):
        record_start = position
        values = struct.unpack_from("<17f", block, position)
        texture_count = struct.unpack_from(
            "<I", block, position + _TEXTURE_COUNT_OFFSET
        )[0]
        shader = _read_string(block, position + _SHADER_NAME_OFFSET, SHADER_NAME_SIZE)
        position += MATERIAL_RECORD_SIZE

        material = SkinMaterial(
            shader=shader,
            diffuse=values[0:4],
            ambient=values[4:8],
            specular=values[8:12],
            emissive=values[12:16],
            power=values[16],
        )

        for _ in range(texture_count):
            if position + TEXTURE_RECORD_SIZE > len(block):
                raise ParsingError(
                    "skin chunk ends inside a texture record",
                    context=ErrorContext(
                        source_file=source_file,
                        extra={
                            "chunk_size": len(block),
                            "position": position,
                            "shader": shader,
                        },
                    ),
                )
            material.textures.append(
                SkinTexture(
                    filename=_read_string(block, position, TEXTURE_NAME_SIZE),
                    slot=struct.unpack_from("<I", block, position + _SLOT_OFFSET)[0],
                    uv_set=struct.unpack_from("<I", block, position + _UV_SET_OFFSET)[0],
                )
            )
            position += TEXTURE_RECORD_SIZE

        skin.materials.append(material)
        skin.offsets.append(record_start)

    if position != len(block):
        raise ParsingError(
            "skin chunk records do not end at the chunk boundary",
            context=ErrorContext(
                source_file=source_file,
                extra={
                    "chunk_size": len(block),
                    "records_end": position,
                    "materials": len(skin.materials),
                },
            ),
        )

    return skin


def expected_size(material_count: int, texture_count: int) -> int:
    """Chunk size for a given record population.

    The arithmetic that confirmed the layout, exposed so tests and
    diagnostics can state it rather than restate it.
    """
    return (
        4
        + material_count * MATERIAL_RECORD_SIZE
        + texture_count * TEXTURE_RECORD_SIZE
    )


def patch_materials(
    block: bytes,
    *,
    values: tuple[float, ...] | None = None,
    shader: str | None = None,
    source_file: str = "",
) -> bytes:
    """Return a copy of the chunk with named fields replaced.

    Every byte not named is copied through. That includes the leading
    uint32, the texture records, and the uninitialised tail after each
    shader name — none of which is understood well enough to
    regenerate, and all of which the game evidently tolerates as it is.

    The chunk keeps its size, so the container's table stays valid and
    the file can be written back in place.
    """
    skin = parse_skin(block, source_file=source_file)
    patched = bytearray(block)

    for offset in skin.offsets:
        if values is not None:
            if len(values) != 17:
                raise ValueError("D3DMATERIAL9 needs exactly 17 floats")
            struct.pack_into("<17f", patched, offset, *values)
        if shader is not None:
            patched[
                offset + _SHADER_NAME_OFFSET : offset
                + _SHADER_NAME_OFFSET
                + SHADER_NAME_SIZE
            ] = _pack_string(shader, SHADER_NAME_SIZE)

    return bytes(patched)


def shipped_values() -> tuple[float, ...]:
    """The seventeen floats every shipped model carries."""
    return (
        *SHIPPED_DIFFUSE,
        *SHIPPED_AMBIENT,
        *SHIPPED_SPECULAR,
        *SHIPPED_EMISSIVE,
        SHIPPED_POWER,
    )


def set_texture(
    block: bytes,
    material_index: int,
    slot: int,
    filename: str,
    source_file: str = "",
) -> bytes:
    """Point one texture slot at a different file, in place.

    Same size in, same size out. This is what lets a texture be swapped
    without regenerating the model: the engine resolves a bare filename
    against the model's own folder and nothing else, so re-pointing a
    slot plus dropping the image beside the ``.gam`` is the entire
    operation.

    Raises if the material has no record for that slot — creating one
    would change the chunk size, and a caller asking to edit a slot
    that isn't there has a different problem than a short write.
    """
    skin = parse_skin(block, source_file=source_file)
    if not 0 <= material_index < len(skin.materials):
        raise IndexError(
            f"material {material_index} of {len(skin.materials)}"
        )

    material = skin.materials[material_index]
    offset = skin.offsets[material_index] + MATERIAL_RECORD_SIZE

    for texture in material.textures:
        if texture.slot == slot:
            patched = bytearray(block)
            patched[offset : offset + TEXTURE_NAME_SIZE] = _pack_string(
                filename, TEXTURE_NAME_SIZE
            )
            return bytes(patched)
        offset += TEXTURE_RECORD_SIZE

    raise KeyError(
        f"material {material_index} has no texture in slot {slot} "
        f"(has {[t.slot for t in material.textures]})"
    )


def build_skin(materials: list[SkinMaterial], leading: int = 1) -> bytes:
    """Write a skin chunk from scratch.

    Used when generating a model rather than editing one, so there are
    no unknown bytes to preserve and every field is written
    deliberately. ``leading`` defaults to 1, the value four of the five
    measured models carry.
    """
    out = bytearray(struct.pack("<I", leading))

    for material in materials:
        out += struct.pack("<17f", *material.material_values)
        out += struct.pack("<I", len(material.textures))
        out += _pack_string(material.shader, SHADER_NAME_SIZE)
        for texture in material.textures:
            out += _pack_string(texture.filename, TEXTURE_NAME_SIZE)
            out += struct.pack("<I", texture.uv_set)
            out += struct.pack("<I", texture.slot)

    return bytes(out)
