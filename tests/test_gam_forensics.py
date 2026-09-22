# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for ``.gam`` model forensics.

The tool's whole value is that it reports fields the importer skips, so
these tests pin exactly that: an undecoded header word must survive to
the report, a chunk the importer ignores must still be listed, and a
difference between two files must be reported while an agreement must
not — a diff that reports everything hides the one finding that
matters.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from addon.research import forensics_operator  # noqa: E402
from core.gam_forensics import (  # noqa: E402
    compare,
    describe,
    format_comparison,
    format_report,
    deviations,
    ChunkReport,
    MaterialRecord,
    measure_population,
    parse_skin,
    survey,
)
from formats.exm.gam import MAGIC  # noqa: E402


def _make_gam(
    *,
    stride: int = 44,
    components: int = 9,
    header_extra: dict[int, int] | None = None,
    skin_strings: list[str] | None = None,
    extra_chunk: tuple[int, bytes] | None = None,
) -> str:
    """A structurally real .gam, with the undecoded parts controllable."""
    header = bytearray(72)
    header[0:5] = b"mesh\x00"
    struct.pack_into("<I", header, 56, stride)
    struct.pack_into("<I", header, 60, components)
    struct.pack_into("<I", header, 64, 3)
    struct.pack_into("<I", header, 68, 1)
    for offset, value in (header_extra or {}).items():
        struct.pack_into("<I", header, offset, value)

    vertices = bytearray()
    for i in range(3):
        vertex = bytearray(stride)
        struct.pack_into("<3f", vertex, 0, float(i), float(i) * 2, float(i) * 3)
        struct.pack_into("<3f", vertex, 12, 0.0, 1.0, 0.0)
        vertices += vertex

    mesh_chunk = bytes(header) + bytes(vertices) + struct.pack("<3H", 0, 1, 2)
    mesh_chunk += struct.pack("<6f", 0.0, 0.0, 0.0, 2.0, 4.0, 6.0)

    payloads = [(4, mesh_chunk)]
    if skin_strings is not None:
        payloads.append((15, b"\x00".join(s.encode() for s in skin_strings) + b"\x00"))
    if extra_chunk is not None:
        payloads.append(extra_chunk)

    container = bytearray(MAGIC + b"\x00")
    container += struct.pack("<I", len(payloads))
    offset = 12 + 16 * len(payloads)
    for chunk_id, payload in payloads:
        container += struct.pack("<4I", chunk_id, len(payload), offset, 0)
        offset += len(payload)
    for _chunk_id, payload in payloads:
        container += payload

    path = os.path.join(tempfile.mkdtemp(), "model.gam")
    with open(path, "wb") as handle:
        handle.write(container)
    return path


# --- describe ---


def test_reports_undecoded_header_words() -> None:
    """The 40 bytes the importer steps over must reach the report."""
    report = describe(_make_gam(header_extra={16: 0xDEADBEEF, 40: 7}))
    mesh = report.meshes[0]
    assert mesh.unknown[0] == 0xDEADBEEF
    assert mesh.unknown[(40 - 16) // 4] == 7


def test_reports_chunks_the_importer_ignores() -> None:
    """An undecoded chunk is the point, not noise to be filtered out."""
    report = describe(_make_gam(extra_chunk=(999, b"IVR metadata\x00")))
    assert 999 in report.chunk_ids()
    chunk = next(c for c in report.chunks if c.chunk_id == 999)
    assert chunk.label == "UNDECODED"
    assert "IVR metadata" in chunk.strings


def test_reports_vertex_buffer_count() -> None:
    report = describe(_make_gam())
    assert report.meshes[0].vertex_buffers == 1


def test_reports_skin_strings_in_order() -> None:
    """Order carries which texture belongs to which shader."""
    report = describe(_make_gam(skin_strings=["diffuse_detail_vc", "rock01.dds"]))
    skin = next(c for c in report.chunks if c.chunk_id == 15)
    assert skin.strings == ["diffuse_detail_vc", "rock01.dds"]


def test_formatted_report_names_undecoded_chunks() -> None:
    text = "\n".join(format_report(describe(_make_gam(extra_chunk=(240, b"\x00" * 8)))))
    assert "UNDECODED" in text
    assert "id 240" in text


def test_a_truncated_container_is_reported_not_refused() -> None:
    """The file the tool exists for is the one that will not open.

    HTAToolchain wrote a .gam whose chunk table pointed past the end of
    the file. The importer refuses it — correctly — and the diagnostic
    that should explain why used the importer and refused it too.
    """
    path = _make_gam(skin_strings=["shader", "a.dds"])
    with open(path, "rb") as handle:
        data = handle.read()
    with open(path, "wb") as handle:
        handle.write(data[:-40])   # lop off the tail the table promises

    report = describe(path)

    assert report.problems, "a truncated container must be reported"
    assert any("past the end" in p for p in report.problems)
    assert any(c.truncated for c in report.chunks)

    text = "\n".join(format_report(report))
    assert "STRUCTURAL FAULTS" in text


def test_a_file_that_is_not_a_container_is_described_not_raised() -> None:
    path = os.path.join(tempfile.mkdtemp(), "junk.gam")
    with open(path, "wb") as handle:
        handle.write(b"this is not a gam file at all, not even close")

    report = describe(path)
    assert any("magic" in p for p in report.problems)


def test_compare_names_a_structurally_invalid_subject() -> None:
    reference = _make_gam(skin_strings=["shader", "a.dds"])
    subject = _make_gam(skin_strings=["shader", "a.dds"])
    with open(subject, "rb") as handle:
        data = handle.read()
    with open(subject, "wb") as handle:
        handle.write(data[:-40])

    result = compare(reference, subject)
    assert any("structurally invalid" in line for line in result.differences)


# --- compare ---


def test_compare_reports_a_differing_vertex_format() -> None:
    result = compare(_make_gam(components=9), _make_gam(components=15))
    assert any("components 9 vs 15" in line for line in result.differences)


def test_compare_reports_a_missing_chunk() -> None:
    """The failure mode the tool exists for: the subject lacks something."""
    result = compare(_make_gam(skin_strings=["shader", "a.dds"]), _make_gam())
    assert any("LACKS" in line and "15" in line for line in result.differences)


def test_compare_flags_an_empty_skin_chunk_as_a_wireframe_candidate() -> None:
    result = compare(
        _make_gam(skin_strings=["diffuse_detail_vc", "rock01.dds"]),
        _make_gam(skin_strings=[]),
    )
    assert any("wireframe" in line for line in result.differences)


def test_compare_reports_a_differing_undecoded_header_word() -> None:
    result = compare(_make_gam(), _make_gam(header_extra={32: 1}))
    assert any("header +32" in line for line in result.differences)


def test_compare_stays_quiet_when_nothing_differs() -> None:
    """A diff that reports agreements buries the disagreement."""
    result = compare(_make_gam(), _make_gam())
    assert result.identical
    assert result.differences == []


def test_identical_comparison_says_what_it_rules_out() -> None:
    """'No differences' is a finding, and must read as one."""
    text = "\n".join(format_comparison(compare(_make_gam(), _make_gam())))
    assert "No structural difference" in text


# --- skin chunk ---


def _skin_bytes(materials) -> bytes:
    """Build a chunk 15 to the layout read off four real models."""
    import struct as _s

    block = _s.pack("<I", len(materials))
    for shader, textures in materials:
        block += _s.pack("<17f", *([1.0] * 17))
        block += _s.pack("<I", len(textures))
        block += shader.encode().ljust(100, b"\x00")
        for name, slot in textures:
            block += name.encode().ljust(44, b"\x00") + _s.pack("<I", slot)
    return block


def test_a_material_record_is_172_bytes() -> None:
    """Derived: teeth_top declares 2 materials in a 348-byte chunk."""
    block = _skin_bytes([("shader_a", []), ("shader_b", [])])
    assert len(block) == 348

    materials = parse_skin(block)
    assert [m.shader for m in materials] == ["shader_a", "shader_b"]


def test_texture_slots_are_decoded_not_just_filenames() -> None:
    """The whole point. Filenames alone cannot say which is the diffuse
    map and which is the bump map, so they can only be assigned in the
    order they appear — which is how textures end up in wrong places.
    """
    block = _skin_bytes([("shader", [("base.dds", 0), ("bump.dds", 1)])])
    material = parse_skin(block)[0]

    assert [(t.name, t.slot) for t in material.textures] == [
        ("base.dds", 0),
        ("bump.dds", 1),
    ]


def test_a_material_with_no_textures_is_distinguishable() -> None:
    """A shader with nothing to bind is a different fault from a
    shader bound to the wrong file, and must not read the same."""
    material = parse_skin(_skin_bytes([("shader", [])]))[0]
    assert material.textures == []
    assert material.all_default


def test_a_non_default_d3d_material_is_visible() -> None:
    import struct as _s

    block = bytearray(_skin_bytes([("shader", [])]))
    _s.pack_into("<f", block, 4, 0.25)   # first diffuse channel
    assert parse_skin(bytes(block))[0].all_default is False


def test_a_truncated_skin_chunk_yields_what_it_can() -> None:
    block = _skin_bytes([("shader", [("base.dds", 0)])])
    materials = parse_skin(block[:-20])
    assert len(materials) == 1
    assert materials[0].textures == [], "a half-read texture must not be invented"


def test_an_absurd_material_count_is_refused() -> None:
    """Pointed at a chunk this layout does not fit, it must say nothing
    rather than allocate on a garbage number."""
    assert parse_skin(b"\xff\xff\xff\xff" + b"\x00" * 64) == []


# --- survey ---


def test_survey_puts_each_model_on_one_line() -> None:
    """Built for an intermittent fault: many models, one screen."""
    paths = [_make_gam(), _make_gam(components=15, stride=48)]
    lines = survey(paths)

    body = [line for line in lines if line.startswith("model.gam")]
    assert len(body) == 2
    assert any("9/44" in line for line in body)
    assert any("15/48" in line for line in body)


def test_survey_separates_size_from_offset() -> None:
    """They fail differently: too big is placeable but wrong, while
    geometry carrying a world position lands somewhere else."""
    lines = survey([_make_gam()])
    header = lines[0]
    assert "size" in header and "offset from origin" in header


def test_survey_flags_a_structurally_broken_model_without_stopping() -> None:
    """One bad file must not cost the whole table."""
    good = _make_gam()
    bad = _make_gam()
    with open(bad, "rb") as handle:
        data = handle.read()
    with open(bad, "wb") as handle:
        handle.write(data[:-40])

    text = "\n".join(survey([good, bad]))
    assert "STRUCTURAL FAULT" in text
    assert text.count("model.gam") >= 2, "the readable model must still appear"


# --- against a known-good population ---


def test_a_candidate_bigger_than_every_known_good_model_is_flagged() -> None:
    population = measure_population([_make_gam(), _make_gam()])
    huge = describe(_make_gam())
    huge.trailing_bounds = (0.0, 0.0, 0.0, 5000.0, 5000.0, 5000.0)

    findings = deviations(huge, population)
    assert any(line.startswith("!") and "exceeds" in line for line in findings)


def test_baked_world_position_is_reported_separately_from_size() -> None:
    """They fail differently: too big is placeable but wrong, while a
    model carrying its world position lands somewhere else."""
    population = measure_population([_make_gam(), _make_gam()])
    offset = describe(_make_gam())
    offset.trailing_bounds = (1000.0, 1000.0, 1000.0, 1002.0, 1004.0, 1006.0)

    findings = deviations(offset, population)
    assert any("baked into the vertices" in line for line in findings)
    assert not any("largest dimension" in line and line.startswith("!")
                   for line in findings), (
        "an ordinary-sized model must not be reported as oversized"
    )


def test_agreements_are_reported_so_suspects_can_be_eliminated() -> None:
    """Ruling a dimension out is most of what this is for."""
    population = measure_population([_make_gam(), _make_gam()])
    findings = deviations(describe(_make_gam()), population)

    assert any(line.startswith("  ") for line in findings)
    assert any("not the difference" in line for line in findings)


def test_a_material_with_no_texture_is_flagged_against_the_population() -> None:
    """Measured: every shipped material names at least one texture.
    civilhouse1 has three materials with three .dds files, big_flag01
    has nine with two each. A material with a shader and no image is
    what "random texture" looks like from the engine's side.
    """
    population = measure_population([_make_gam(skin_strings=["s", "a.dds"])])
    report = describe(_make_gam())
    report.chunks.append(
        ChunkReport(
            chunk_id=15,
            size=0,
            strings=[],
            materials=[
                MaterialRecord(
                    index=0, shader="bump", textures=[], colours=(1.0,) * 17
                )
            ],
        )
    )

    findings = deviations(report, population)
    assert any("no texture at all" in line for line in findings)


def test_a_vertex_format_no_known_good_model_uses_is_flagged() -> None:
    population = measure_population([_make_gam(components=15, stride=48)])
    findings = deviations(describe(_make_gam(components=9)), population)

    assert any("appear in no shipped model" in line for line in findings)


def test_unreadable_models_are_excluded_from_the_population() -> None:
    """A broken file must not widen the range it is measured against."""
    broken = _make_gam()
    with open(broken, "rb") as handle:
        data = handle.read()
    with open(broken, "wb") as handle:
        handle.write(data[:-40])

    population = measure_population([_make_gam(), broken])
    assert population.count == 1
    assert len(population.unreadable) == 1


# --- operator ---


def _operator(subject: str, reference: str = ""):
    operator = forensics_operator.EXM_OT_model_forensics()
    operator.subject = subject
    operator.reference = reference
    return operator


def test_operator_refuses_a_missing_subject() -> None:
    assert _operator("/nonexistent/model.gam").execute(None) == {"CANCELLED"}


def test_operator_with_no_subject_finishes_so_the_redo_panel_appears() -> None:
    """Reported: "Model Forensics shows no fields, just a result".

    Blender only offers "Adjust Last Operation" for an operator that
    FINISHED. Cancelling on an empty path hid the fields the message
    told the user to fill in.
    """
    assert _operator("").execute(None) == {"FINISHED"}


def test_the_operator_pushes_an_undo_step_so_its_fields_are_reachable() -> None:
    """Reported twice as "there are no fields, just a result".

    Blender offers "Adjust Last Operation" only for an operator that
    pushed an undo step. With REGISTER alone the operator ran on its
    defaults and the panel it pointed the user at never appeared.
    """
    options = forensics_operator.EXM_OT_model_forensics.bl_options
    assert "UNDO" in options, "without UNDO there is no redo panel to fill in"


def test_operator_does_not_open_a_popup_dialog() -> None:
    """Regression: Blender crashed with EXCEPTION_ACCESS_VIOLATION in
    file_browse_exec -> RNA_property_string_set -> IDP_AddToGroup.

    The operator opened invoke_props_dialog and its FILE_PATH fields
    carried browse buttons; the file browser writes the chosen path
    back into properties owned by a popup that has already gone. Paths
    are taken through the 'Adjust Last Operation' panel instead, as
    every other diagnostic in this add-on does, so there must be no
    invoke override to reintroduce the popup.
    """
    assert not hasattr(forensics_operator.EXM_OT_model_forensics, "invoke")


def test_operator_describes_a_single_model() -> None:
    assert _operator(_make_gam()).execute(None) == {"FINISHED"}


def test_operator_compares_two_models() -> None:
    operator = _operator(
        _make_gam(skin_strings=[]),
        reference=_make_gam(skin_strings=["shader", "a.dds"]),
    )
    assert operator.execute(None) == {"FINISHED"}


def test_operator_describes_an_unparseable_file_rather_than_refusing() -> None:
    """A file that cannot be read is the finding, not an obstacle to it.

    The operator used to cancel here. That made it useless for the one
    case it was built for: a model the game rejects.
    """
    broken = os.path.join(tempfile.mkdtemp(), "broken.gam")
    with open(broken, "wb") as handle:
        handle.write(b"not a container at all, but long enough to have a header")

    assert _operator(broken).execute(None) == {"FINISHED"}
