# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the skin chunk codec and the model doctor.

The arithmetic below is the evidence, not an illustration of it: each
size assertion is the check that established the layout in the first
place, and a change that breaks one has changed what the format is
believed to be.

Tests needing real models skip when the corpus is absent, the same way
the other format tests do — the suite has to pass on a machine without
a game install.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.model_doctor import (  # noqa: E402
    PROBLEM,
    RepairOptions,
    diagnose,
    repair,
)
from formats.exm.gam import (  # noqa: E402
    VERTEX_LAYOUTS,
    read_container,
    write_container,
)
from formats.exm.skin import (  # noqa: E402
    SKIN_CHUNK_ID,
    SkinMaterial,
    SkinTexture,
    build_skin,
    expected_size,
    parse_skin,
    patch_materials,
    set_texture,
    shipped_values,
)
from utils.errors import ParsingError  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402


#: model -> (materials, textures), counted by hand from the byte
#: arithmetic that confirmed the record sizes.
POPULATION = {
    "civilhouse1.gam": (3, 3),
    "big_flag01.gam": (9, 18),
    "cube1111.gam": (1, 2),
    "Cube44.gam": (1, 0),
    "factory_box.gam": (1, 1),
}


def _skin_of(name: str) -> bytes | None:
    path = os.path.join(CORPUS, name)
    if not os.path.isfile(path):
        return None
    _subtype, chunks = read_container(path)
    for chunk in chunks:
        if chunk.chunk_id == SKIN_CHUNK_ID:
            return chunk.data
    return None


# --- the layout itself ----------------------------------------------


def test_record_sizes_account_for_every_byte() -> None:
    """4 + 172*M + 48*T is the whole chunk, exactly.

    The check that settled the layout. Records are interleaved —
    textures follow each material — and big_flag01, nine materials with
    two textures each, is the sample that distinguishes that from the
    grouped alternative.
    """
    for name, (materials, textures) in POPULATION.items():
        block = _skin_of(name)
        if block is None:
            continue
        assert len(block) == expected_size(materials, textures), name

        skin = parse_skin(block, name)
        assert len(skin.materials) == materials, name
        assert sum(len(m.textures) for m in skin.materials) == textures, name


def test_the_leading_uint32_is_not_a_record_count() -> None:
    """civilhouse1 stores 1 and carries three materials.

    Reading it as a count found one texture in a model with three. The
    parser must walk to the chunk end regardless of this value.
    """
    block = _skin_of("civilhouse1.gam")
    if block is None:
        return
    skin = parse_skin(block)
    assert skin.leading == 1
    assert len(skin.materials) == 3


def test_a_truncated_chunk_is_reported_not_absorbed() -> None:
    block = _skin_of("factory_box.gam")
    if block is None:
        return
    try:
        parse_skin(block[:-8], "truncated")
    except ParsingError:
        return
    raise AssertionError("a short chunk parsed without complaint")


# --- slots ----------------------------------------------------------


def test_slot_zero_is_diffuse_and_slot_one_is_bump() -> None:
    """Two independent samples, one shipped and one generated.

    big_flag01 is the game's own: big_flag01.dds in slot 0 and
    big_flag01b.dds in slot 1, the artist's own bump-map suffix.
    """
    block = _skin_of("big_flag01.gam")
    if block is None:
        return
    material = parse_skin(block).materials[0]
    assert material.texture_for_slot(0) == "big_flag01.dds"
    assert material.texture_for_slot(1) == "big_flag01b.dds"


def test_a_missing_slot_reads_as_none_not_as_the_next_texture() -> None:
    material = SkinMaterial(
        shader="bump", textures=[SkinTexture("only_a_bump.dds", 1)]
    )
    assert material.texture_for_slot(0) is None
    assert material.texture_for_slot(1) == "only_a_bump.dds"


# --- round trips ----------------------------------------------------


def test_a_container_read_and_written_back_is_identical() -> None:
    for name in POPULATION:
        path = os.path.join(CORPUS, name)
        if not os.path.isfile(path):
            continue
        subtype, chunks = read_container(path)
        assert write_container(subtype, chunks) == open(path, "rb").read(), name


def test_building_a_chunk_reproduces_its_own_size() -> None:
    for name in POPULATION:
        block = _skin_of(name)
        if block is None:
            continue
        skin = parse_skin(block)
        assert len(build_skin(skin.materials, skin.leading)) == len(block), name


def test_patching_touches_only_the_material_floats() -> None:
    """Everything not named is copied through.

    The uninitialised tail after each shader name is meaningless and
    still not ours to regenerate.
    """
    block = _skin_of("cube1111.gam")
    if block is None:
        return
    patched = patch_materials(block, values=shipped_values())

    assert len(patched) == len(block)
    changed = [i for i in range(len(block)) if block[i] != patched[i]]
    assert changed, "the patch changed nothing"
    assert max(changed) < 4 + 68, "the patch reached past D3DMATERIAL9"

    after = parse_skin(patched)
    assert all(m.matches_shipped_values() for m in after.materials)
    assert [t.filename for t in after.materials[0].textures] == [
        "metal_elements.dds",
        "MininAO.dds",
    ]


def test_retargeting_a_texture_keeps_the_chunk_size() -> None:
    block = _skin_of("factory_box.gam")
    if block is None:
        return
    patched = set_texture(block, 0, 0, "factory_box.tga")
    assert len(patched) == len(block)
    assert parse_skin(patched).materials[0].texture_for_slot(0) == "factory_box.tga"


def test_a_name_too_long_for_its_field_is_refused() -> None:
    block = _skin_of("factory_box.gam")
    if block is None:
        return
    try:
        set_texture(block, 0, 0, "x" * 44)
    except ValueError:
        return
    raise AssertionError("a 44-byte name fitted a 44-byte NUL-terminated field")


# --- the doctor -----------------------------------------------------


def test_a_shipped_model_is_clean() -> None:
    path = os.path.join(CORPUS, "factory_box.gam")
    if not os.path.isfile(path):
        return
    report = diagnose(path)
    assert report.healthy, [str(f) for f in report.problems]
    assert not report.from_toolchain


def test_unapplied_scale_shows_up_as_a_non_unit_normal() -> None:
    """cube1111 against Cube44: same exporter, same vertex type.

    One carries normals of magnitude 187.4784 — its own half-size — and
    the other unit normals. The pair is what makes this a defect rather
    than a convention.
    """
    broken = os.path.join(CORPUS, "cube1111.gam")
    fine = os.path.join(CORPUS, "Cube44.gam")
    if not (os.path.isfile(broken) and os.path.isfile(fine)):
        return

    codes = {f.code for f in diagnose(broken).findings if f.level == PROBLEM}
    assert "normals_not_unit" in codes

    codes = {f.code for f in diagnose(fine).findings if f.level == PROBLEM}
    assert "normals_not_unit" not in codes


def test_repairing_nothing_writes_the_file_back_unchanged() -> None:
    path = os.path.join(CORPUS, "cube1111.gam")
    if not os.path.isfile(path):
        return
    with tempfile.TemporaryDirectory() as folder:
        out = os.path.join(folder, "out.gam")
        repair(path, out, RepairOptions())
        assert open(out, "rb").read() == open(path, "rb").read()


def test_conversion_to_the_diffuse_profile_keeps_geometry() -> None:
    """Positions, UVs and indices survive; tangents are dropped.

    Type 15 to type 7 is a discard, not a recomputation, so anything
    both formats carry has to come out bit-identical.
    """
    path = os.path.join(CORPUS, "cube1111.gam")
    if not os.path.isfile(path):
        return

    with tempfile.TemporaryDirectory() as folder:
        out = os.path.join(folder, "out.gam")
        repair(path, out, RepairOptions(simplify_shader=True))

        before = _first_mesh(path)
        after = _first_mesh(out)

        assert after["vertex_type"] == 7
        assert after["stride"] == VERTEX_LAYOUTS[7][0]
        assert after["indices"] == before["indices"]
        assert [v[:3] for v in after["vertices"]] == [
            v[:3] for v in before["vertices"]
        ]
        # UV sits at float 6 in type 15 and float 6 in type 7 alike:
        # position, normal, uv. Same slice on both sides by coincidence
        # of layout, and asserted rather than assumed.
        assert [v[6:8] for v in after["vertices"]] == [
            v[6:8] for v in before["vertices"]
        ]


def _first_mesh(path: str) -> dict:
    _subtype, chunks = read_container(path)
    block = next(c.data for c in chunks if c.chunk_id == 4)
    stride = struct.unpack_from("<I", block, 56)[0]
    vertex_type = struct.unpack_from("<I", block, 60)[0]
    count = struct.unpack_from("<I", block, 64)[0]
    triangles = struct.unpack_from("<I", block, 68)[0]
    vertices = [
        struct.unpack_from(f"<{stride // 4}f", block, 72 + i * stride)
        for i in range(count)
    ]
    indices = struct.unpack_from(
        f"<{triangles * 3}H", block, 72 + count * stride
    )
    return {
        "stride": stride,
        "vertex_type": vertex_type,
        "vertices": vertices,
        "indices": indices,
    }
