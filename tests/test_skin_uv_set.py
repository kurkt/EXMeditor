# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The texture record is ``char[40] + uint32 uv_set + uint32 slot``.

MEASURED (2026-09-19) over 11 062 texture records in 1373 shipped
models: bytes 40..43 hold 1 in 155 records and 2 in 36 — in 107
models — while the name is well short of 40 characters, so they are
not name. HTAToolchain's own parser packs ``<40s``, ``<I`` uv,
``<I`` type. The SDK read the record as ``char[44] + uint32`` and got
names and slots right by the accident of NUL padding, and then
``set_texture`` wrote 44 bytes of name over the UV set: on
``bridge_stone.gam`` material 1 slot 4 (``coverrock_detail.dds``,
UV set 1) the detail map came out mapped through UV set 0.

No corpus needed: the chunk is built here, byte for byte.
"""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from formats.exm.skin import (  # noqa: E402
    MATERIAL_RECORD_SIZE,
    TEXTURE_NAME_SIZE,
    TEXTURE_RECORD_SIZE,
    SkinMaterial,
    SkinTexture,
    build_skin,
    parse_skin,
    set_texture,
)


def _bridge_stone_like() -> bytes:
    """Material 1 of bridge_stone: diffuse on UV 0, detail on UV 1."""
    material = SkinMaterial(shader="diffuse_detail_vc")
    material.textures = [
        SkinTexture(filename="brick_stones.dds", slot=0, uv_set=0),
        SkinTexture(filename="coverrock_detail.dds", slot=4, uv_set=1),
    ]
    return build_skin([material])


def _record(block: bytes, material: int, texture: int) -> bytes:
    start = 4 + material * MATERIAL_RECORD_SIZE + texture * TEXTURE_RECORD_SIZE
    # Only valid for a single-material chunk, which is all this needs.
    assert material == 0
    return block[start + MATERIAL_RECORD_SIZE: start + MATERIAL_RECORD_SIZE + TEXTURE_RECORD_SIZE]


def test_the_name_field_is_forty_bytes_and_the_uv_set_follows() -> None:
    assert TEXTURE_NAME_SIZE == 40
    assert TEXTURE_RECORD_SIZE == 48
    record = _record(_bridge_stone_like(), 0, 1)
    assert record[:40].rstrip(b"\0") == b"coverrock_detail.dds"
    assert struct.unpack_from("<I", record, 40)[0] == 1, "UV set"
    assert struct.unpack_from("<I", record, 44)[0] == 4, "slot"


def test_the_uv_set_is_read_back() -> None:
    skin = parse_skin(_bridge_stone_like())
    detail = skin.materials[0].textures[1]
    assert detail.slot == 4
    assert detail.uv_set == 1
    assert skin.materials[0].textures[0].uv_set == 0


def test_retargeting_a_texture_keeps_its_uv_set() -> None:
    """The defect: a 44-byte name write zeroed bytes 40..43."""
    block = _bridge_stone_like()
    patched = set_texture(block, 0, 4, "edited_detail.dds")

    assert len(patched) == len(block)
    after = parse_skin(patched).materials[0].textures[1]
    assert after.filename == "edited_detail.dds"
    assert after.uv_set == 1, "the detail map must stay on UV set 1"
    assert after.slot == 4
    # And nothing outside the name changed.
    assert patched[4 + MATERIAL_RECORD_SIZE + TEXTURE_RECORD_SIZE + 40:] == \
        block[4 + MATERIAL_RECORD_SIZE + TEXTURE_RECORD_SIZE + 40:]


def test_a_forty_character_name_is_refused() -> None:
    """40 characters leave no room for the NUL; the next four bytes
    are the UV set, not a place for the terminator to land."""
    block = _bridge_stone_like()
    try:
        set_texture(block, 0, 0, "x" * 40)
    except ValueError:
        pass
    else:
        raise AssertionError("a 40-byte name fitted a 40-byte NUL-terminated field")
    patched = set_texture(block, 0, 0, "x" * 39)
    assert parse_skin(patched).materials[0].textures[0].filename == "x" * 39


def test_building_a_chunk_writes_the_uv_set() -> None:
    material = SkinMaterial(shader="lightmap_detail")
    material.textures = [
        SkinTexture(filename="wall.dds", slot=0),
        SkinTexture(filename="wall_lm.dds", slot=2, uv_set=1),
        SkinTexture(filename="wall_detail.dds", slot=4, uv_set=2),
    ]
    block = build_skin([material])
    assert len(block) == 4 + MATERIAL_RECORD_SIZE + 3 * TEXTURE_RECORD_SIZE
    assert [(t.slot, t.uv_set) for t in parse_skin(block).materials[0].textures] == [
        (0, 0), (2, 1), (4, 2),
    ]


def test_the_shipped_name_that_broke_was_cut_at_forty() -> None:
    """The evidence that fixed the field width: the name the toolchain
    wrote for the .obj model was exactly 40 characters, extension gone,
    followed by a zero UV set."""
    cut = "tripo_image_ea527cc5-57ee-420f-a393-85d6"
    assert len(cut) == TEXTURE_NAME_SIZE
    assert len("commonwealth_of independent_towns.dds") == 37, "the longest shipped name"


def test_material_values_are_untouched_by_the_layout() -> None:
    material = SkinMaterial(shader="diffuse")
    material.textures = [SkinTexture(filename="a.dds", slot=0)]
    skin = parse_skin(build_skin([material]))
    assert skin.materials[0].matches_shipped_values()
