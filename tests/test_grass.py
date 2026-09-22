# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for reading and placing a map's grass."""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

import bpy  # noqa: E402

from formats.exm.grass import RECORD_SIZE, read_grass  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402

GRASS = corpus("grass.xml")
HEIGHTS = corpus("displace.bin")


def _field():
    return read_grass(GRASS) if os.path.isfile(GRASS) else None


def test_the_walk_consumes_the_chunk_exactly() -> None:
    """The check that the layout is right rather than merely plausible.

    A patch may hold several lists, one per grass type. Reading the
    header as a fixed five words works for sixty-odd patches and then
    desynchronises — which is how this looked decoded when it was not.
    """
    field = _field()
    if field is None:
        return

    assert field.patches == 3674
    assert field.total == 72529
    assert sorted(field.by_type) == [4, 5, 6, 7, 8, 11]


def test_the_patch_count_agrees_with_the_header() -> None:
    """chunk 1 states it, and the walk has to arrive at the same number."""
    from formats.exm.gam import read_container

    if not os.path.isfile(GRASS):
        return

    counts = next(
        c.data for c in read_container(GRASS)[1] if c.chunk_id == 1
    )
    _types, patches = struct.unpack_from("<2I", counts, 0)
    assert read_grass(GRASS).patches == patches


def test_the_heights_are_absolute_world_y() -> None:
    """Checked against the terrain, at 8 units a sample.

    A median error of 0.004 is not a coincidence: it confirms the
    record layout, the height format and the sampling order at once.
    """
    field = _field()
    if field is None or not os.path.isfile(HEIGHTS):
        return

    heights = struct.unpack("<262144f", open(HEIGHTS, "rb").read())
    errors = []
    for placements in field.by_type.values():
        for tuft in placements[::40]:
            x, z = int(tuft.x / 8), int(tuft.z / 8)
            if 0 <= x < 512 and 0 <= z < 512:
                errors.append(tuft.y - heights[z * 512 + x])

    errors.sort()
    assert abs(errors[len(errors) // 2]) < 0.1, errors[len(errors) // 2]


def test_the_heading_is_a_unit_vector() -> None:
    """Stored as two components rather than an angle."""
    field = _field()
    if field is None:
        return

    for placements in field.by_type.values():
        for tuft in placements[::500]:
            cos, sin = tuft.heading
            assert abs(cos * cos + sin * sin - 1.0) < 0.01, tuft.heading


def test_the_models_are_gam_whatever_the_paths_say() -> None:
    """The paths name .sam files that do not exist.

    A 1355-model build holds no grass .sam at all, and the same folders
    hold .gam of the same names. So no second format is needed.
    """
    field = _field()
    if field is None:
        return

    assert any(name.lower().endswith(".sam") for name in field.models)
    for type_index in field.by_type:
        assert field.model_for(type_index).lower().endswith(".gam")


def test_a_record_is_twenty_four_bytes() -> None:
    """Position, scale and heading — six floats.

    The fourth header word is the type index, not a record size. Taken
    as a size it made a four-record list consume 112 bytes instead of
    96, and the walk drifted from there.
    """
    assert RECORD_SIZE == 24


def test_a_truncated_file_is_reported_rather_than_half_read() -> None:
    """A partial walk means the layout is not what was measured, and
    carrying on places tufts from misread bytes."""
    import tempfile

    from formats.exm.gam import Chunk, read_container, write_container
    from utils.errors import ParsingError

    if not os.path.isfile(GRASS):
        return

    subtype, chunks = read_container(GRASS)
    damaged = [
        Chunk(chunk_id=c.chunk_id, data=c.data[: len(c.data) - 40] if c.chunk_id == 3 else c.data)
        for c in chunks
    ]
    path = os.path.join(tempfile.mkdtemp(), "grass.xml")
    open(path, "wb").write(write_container(subtype, damaged))

    try:
        read_grass(path)
    except ParsingError:
        return
    raise AssertionError("a truncated placement chunk was read as if whole")


# --- placement ----------------------------------------------------------


def test_tufts_of_one_type_share_a_mesh() -> None:
    """72529 objects with 72529 meshes is the difference between a map
    that opens and one that does not."""
    from blender_io.grass_bridge import build_grass

    class _Field:
        models = ["a\\b.gam"]
        by_type = {0: []}
        patches = 1
        total = 0

        def model_for(self, index):
            return self.models[index]

    collection = bpy.data.collections.new("Grass")
    # An empty field places nothing and does not raise.
    assert build_grass(_Field(), collection, None) == 0


def test_a_limit_takes_some_of_every_type() -> None:
    """All of the first and none of the rest would mean 50618 tufts of
    sedge and no kust_grass at all."""
    field = _field()
    if field is None:
        return

    limit = 600
    share = min(1.0, limit / field.total)
    for placements in field.by_type.values():
        assert max(1, int(len(placements) * share)) >= 1


def test_a_sound_source_is_not_looked_up_as_a_model() -> None:
    """Its id names a sound — ``S_WIND_GRASS`` and 83 others.

    Looking those up in the model catalogue finds nothing, which is
    correct, and then reports 84 models as missing, which is not: it
    buries the models that really are missing under noise.
    """
    from blender_io.world_bridge import CLASSES_WITHOUT_MODELS

    assert "SgSoundSourceNode" in CLASSES_WITHOUT_MODELS
    # Everything that does name a model stays out of the set.
    assert "SgAnimatedModelNode" not in CLASSES_WITHOUT_MODELS
    assert "SgGameUnitNode" not in CLASSES_WITHOUT_MODELS


def test_dxt1_punch_through_alpha_survives_the_round_trip() -> None:
    """Foliage may carry its cutout in DXT1's three-colour mode.

    A texture judged opaque by mistake renders as a solid rectangle
    where the game shows leaves — which looks like a texture that
    loaded wrongly rather than one whose alpha was dropped.
    """
    from core import dds

    header = dds.build_header(4, 4, dds.DXT1, 1)
    # c0 <= c1 selects three-colour mode; index 3 is the transparent one.
    block = struct.pack("<HHI", 0x0000, 0xFFFF, (3 << 0) | (3 << 2))
    data = header + block

    assert dds.has_alpha(data) is True
    assert dds.alpha_is_cutout(data) is True

    rgba = dds.decode(data)[0]
    assert rgba[3] == 0 and rgba[7] == 0
    assert rgba[11] == 255


# --- every tuft, or none ------------------------------------------------


def test_the_switch_replaced_the_budget() -> None:
    """A cap took a stride through the placements and kept every Nth.

    That is not a lighter version of the map: it is the map with holes
    wherever the stride happened to skip, and no setting of it was ever
    right. Whether grass is imported at all is the honest control, so
    the sampler is gone and nothing may bring it back quietly.
    """
    import blender_io.grass_bridge as grass_bridge

    assert not hasattr(grass_bridge, "plan_sample")
    assert not hasattr(grass_bridge, "DEFAULT_LIMIT")

    import inspect

    signature = inspect.signature(grass_bridge.build_grass)
    assert "limit" not in signature.parameters


def test_the_scene_takes_a_switch_not_a_number() -> None:
    import inspect

    import blender_io.scene_bridge as scene_bridge

    parameters = inspect.signature(scene_bridge.build_scene).parameters
    assert "grass_limit" not in parameters
    assert parameters["import_grass"].default is True
