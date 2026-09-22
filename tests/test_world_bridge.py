# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The buried-object guard.

Stands on its own: this file used to rely on some earlier test module
having installed the bpy double, which made it pass or fail by import
order rather than by what it tests.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()




def test_an_object_written_below_the_terrain_is_warned_about() -> None:
    """Measured: an icosphere modelled at what looked like ground level
    exported as org="2044.000 -396.415 2044.000" and was nowhere to be
    found in the game's editor.

    org Y on a top-level node is a height ABOVE THE GROUND, and the
    terrain sits at 227..541 in Blender units, so an object placed near
    Z=0 is some four hundred units underground. The file, the geometry
    and the registration were all correct; the object was buried, and
    nothing said so.
    """
    import logging

    from blender_io.world_bridge import (
        PLAUSIBLE_GROUND_OFFSET,
        _warn_about_buried_objects,
    )
    from core.objects import ObjectInstance
    from utils.math import Vector3

    assert PLAUSIBLE_GROUND_OFFSET > 82, "shipped nodes reach 82 above ground"

    buried = ObjectInstance(
        name="Object76476969",
        node_class="SgAnimatedModelNode",
        org=Vector3(2044.0, -396.415, 2044.0),
        asset_id="Icosphere",
    )
    fine = ObjectInstance(
        name="Object1",
        node_class="SgAnimatedModelNode",
        org=Vector3(2044.0, 0.0, 2044.0),
        asset_id="rock",
    )

    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    log = logging.getLogger("exmeditor.blender_io.world")
    handler = _Capture()
    log.addHandler(handler)
    try:
        _warn_about_buried_objects([buried, fine])
    finally:
        log.removeHandler(handler)

    # One warning for the buried object, and one summary at the end.
    # The object on the ground gets neither.
    assert len(records) == 2, "only the buried object is worth a warning"
    assert "396" in records[0] and "below" in records[0]
    assert "Icosphere" in records[0]

    # The summary exists because a warning per object gets lost among
    # everything else an export reports, and this one is the difference
    # between a model that is in the map and a model that is not. It
    # went unnoticed on a real export: the node was written at
    # org="2056.406 -373.506 2034.048" and the object was reported
    # missing from the map.
    assert "1 object(s) will not be visible" in records[1]
    assert "Icosphere (-396)" in records[1]
    assert "Place On Terrain" in records[1]


def test_a_node_scaled_like_no_shipped_node_is_warned_about() -> None:
    """MEASURED: 24 202 of 36 338 shipped nodes carry a scale, all in
    0.6 .. 2.4. The SDK-written maps had 258, 345, 1890, 5352 and -831
    — Blender objects scaled to size, written as node scale on top of
    geometry that already had it baked in. Doubly applied, the model
    is enormous, and from inside it looks like nothing at all."""
    import logging

    from blender_io.world_bridge import PLAUSIBLE_SCALE, _warn_about_buried_objects
    from core.objects import ObjectInstance
    from utils.math import Vector3

    assert PLAUSIBLE_SCALE[0] <= 0.6 and PLAUSIBLE_SCALE[1] >= 2.4

    huge = ObjectInstance(
        name="Object2", node_class="SgAnimatedModelNode",
        org=Vector3(2044.0, 0.0, 2044.0), scale=Vector3(258.55, 258.55, 258.55),
        asset_id="tripo_node",
    )
    shipped = ObjectInstance(
        name="Object3", node_class="SgAnimatedModelNode",
        org=Vector3(10.0, 0.0, 10.0), scale=Vector3(1.914, 1.914, 1.914),
        asset_id="house3",
    )
    negative = ObjectInstance(
        name="Object4", node_class="SgNode",
        org=Vector3(0.0, 0.0, 0.0), scale=Vector3(-831.486, -831.486, -831.486),
    )

    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    log = logging.getLogger("exmeditor.blender_io.world")
    handler = _Capture()
    log.addHandler(handler)
    try:
        _warn_about_buried_objects([huge, shipped, negative])
    finally:
        log.removeHandler(handler)

    about_scale = [r for r in records if "scale" in r]
    assert any("tripo_node" in r and "258.550" in r for r in about_scale)
    assert any("Object4" in r and "-831.486" in r for r in about_scale)
    assert not any("house3" in r for r in about_scale), "a shipped-range scale is not a fault"
    assert any(r.startswith("2 object(s) carry a scale") for r in about_scale)
