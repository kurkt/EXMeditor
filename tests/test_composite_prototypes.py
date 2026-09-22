# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Turrets: prototypes assembled from parts, and prefabs of prototypes.

MEASURED on r1m1, read off the user's own autosave: all 38
SgGameUnitNode world nodes had geometry; the 14 objects that were
Empties were dynamic objects with prototypes staticAutoGun02/04/07/08,
and a StaticAutoGun has no ModelFile::

    <Prototype Class="StaticAutoGun" Name="staticAutoGun04">
        <MainPartDescription id="DOT">
            <PartDescription id="CANNON" lpName="LP_CANNON01" />
        </MainPartDescription>
        <Parts>
            <Part id="DOT"    Prototype="heavy_dot4" />
            <Part id="CANNON" Prototype="vulcan01" />
        </Parts>
    </Prototype>

heavy_dot4 -> ModelFile heavy_dot4 -> heavy_dot4.gam, whose node table
holds LP_CANNON01 at (-0.19, 8.75, 0.03); vulcan01 -> ModelFile
vulcan01 -> guns/sml_vulcan01.gam. With this chain 4385 of r1m1's 4443
dynamic objects resolve to a model; before it, 18 of 53 prototypes had
none.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.dynamic_objects import DynamicObject  # noqa: E402
from core.prototypes import PrototypeCatalog, read_prototype_file  # noqa: E402
from formats.exm.dynamic_scene import (  # noqa: E402
    model_for_prototype,
    parts_for_prototype,
    resolve_model_id,
    resolve_parts,
    sub_objects_for_prototype,
)
from formats.exm.gam import (  # noqa: E402
    NODE_CHUNK_ID,
    NODE_RECORD_SIZE,
    Chunk,
    read_locators,
    read_nodes,
    write_container,
)
from utils.math import Vector3  # noqa: E402

GAMEOBJECTS = """<?xml version="1.0" encoding="windows-1251"?>
<Folder>
  <Prototype Class="StaticAutoGun" Name="staticAutoGun04" MaxHealth="1500">
    <MainPartDescription id="DOT" partResourceType="STATIC_AUTO_GUN_DOT">
      <PartDescription id="CANNON" partResourceType="SMALL_GUN" lpName="LP_CANNON01" />
    </MainPartDescription>
    <Parts>
      <Part id="DOT" Prototype="heavy_dot4" />
      <Part id="CANNON" Prototype="vulcan01" />
    </Parts>
  </Prototype>
  <Prototype Class="VehiclePart" Name="heavy_dot4" ModelFile="heavy_dot4" />
  <Prototype Class="BulletLauncher" Name="vulcan01" ModelFile="vulcan01" />
  <Prototype Class="Barricade" Name="barricade4_wGw" Probability="0.5">
    <ObjInfos>
      <ObjInfo Prototype="Breakable_SackWall1" RelPos="-6 0 0" RelAngle="0" />
      <ObjInfo Prototype="Breakable_SackWall1" RelPos="6 0 0" RelAngle="0" />
      <ObjInfo Prototype="staticAutoGun04" RelPos="0 0 0" RelAngle="45" />
    </ObjInfos>
  </Prototype>
  <Prototype Class="Breakable" Name="Breakable_SackWall1" ModelFile="sack_wall1" />
  <Prototype Class="Vehicle" Name="Sml301" ParentPrototype="Sml3">
    <Parts>
      <Part id="CABIN" Prototype="sml3Cab01" />
      <Part id="CABIN_SMALL_GUN" Prototype="vulcan01" />
    </Parts>
  </Prototype>
  <Prototype Class="VehiclePart" Name="sml3Cab01" ModelFile="sml3cab01" />
</Folder>
"""


class _Catalog:
    """A model catalogue that knows these ids and nothing else."""

    def __init__(self, *ids):
        self._ids = set(ids)

    def get(self, model_id):
        return {"id": model_id} if model_id in self._ids else None


def _prototypes() -> PrototypeCatalog:
    path = os.path.join(tempfile.mkdtemp(), "gameobjects.xml")
    with open(path, "w", encoding="cp1251") as handle:
        handle.write(GAMEOBJECTS)
    catalog = PrototypeCatalog()
    for prototype in read_prototype_file(path):
        catalog.add(prototype)
    return catalog


CATALOG = _Catalog("heavy_dot4", "vulcan01", "sack_wall1", "sml3cab01")


def test_a_static_autogun_is_parsed_as_body_plus_cannon() -> None:
    gun = _prototypes().get("staticAutoGun04")

    assert gun.model_id is None
    assert gun.is_composite
    assert gun.parts == {"DOT": "heavy_dot4", "CANNON": "vulcan01"}
    assert gun.main_part == "DOT"
    assert gun.main_part_prototype == "heavy_dot4"
    assert gun.attachments == {"CANNON": "LP_CANNON01"}


def test_the_turret_resolves_to_the_pillbox_model() -> None:
    """The fourth source, after ModelName / ModelFile / the name itself:
    the body part's model. This is the step whose absence made all 14
    turrets Empties."""
    obj = DynamicObject(name="staticAutoGun044", prototype="staticAutoGun04",
                        position=Vector3(1273.6, 297.3, 2898.4))

    assert resolve_model_id(obj, _prototypes(), CATALOG) == "heavy_dot4"


def test_the_cannon_is_a_part_on_the_bodys_locator() -> None:
    obj = DynamicObject(name="g", prototype="staticAutoGun04", position=Vector3(0, 0, 0))

    parts = resolve_parts(obj, _prototypes(), CATALOG)

    assert [(p.part_id, p.model_id, p.locator) for p in parts] == [
        ("CANNON", "vulcan01", "LP_CANNON01")
    ]


def test_a_plain_prototype_has_no_parts_and_resolves_as_before() -> None:
    obj = DynamicObject(name="w", prototype="Breakable_SackWall1", position=Vector3(0, 0, 0))

    assert resolve_model_id(obj, _prototypes(), CATALOG) == "sack_wall1"
    assert resolve_parts(obj, _prototypes(), CATALOG) == []


def test_a_vehicle_without_a_main_part_takes_its_first_part_as_body() -> None:
    """Sml301 inherits its MainPartDescription from Sml3, which this
    reader does not follow. The first part listed is the cabin, which
    is what should stand on the map — and its gun has no locator to
    mount on, so it is left out rather than placed anywhere."""
    prototypes = _prototypes()

    assert model_for_prototype("Sml301", prototypes, CATALOG) == "sml3cab01"
    assert parts_for_prototype("Sml301", prototypes, CATALOG) == []


def test_a_barricade_is_a_prefab_of_three_objects() -> None:
    """One of which is a turret, which is why prefabs matter here."""
    prototypes = _prototypes()
    wall = prototypes.get("barricade4_wGw")

    assert wall.is_prefab and not wall.is_composite
    assert model_for_prototype("barricade4_wGw", prototypes, CATALOG) is None

    subs = sub_objects_for_prototype("barricade4_wGw", prototypes)
    assert [(s.prototype, s.rel_pos, s.rel_angle) for s in subs] == [
        ("Breakable_SackWall1", (-6.0, 0.0, 0.0), 0.0),
        ("Breakable_SackWall1", (6.0, 0.0, 0.0), 0.0),
        ("staticAutoGun04", (0.0, 0.0, 0.0), 45.0),
    ]
    # And the turret inside it assembles like any other.
    assert model_for_prototype("staticAutoGun04", prototypes, CATALOG) == "heavy_dot4"
    assert parts_for_prototype("staticAutoGun04", prototypes, CATALOG)[0].locator == "LP_CANNON01"


# --- the node table -----------------------------------------------------


def _node_record(name: str, parent: int, location, rotation=(0.0, 0.0, 0.0, -1.0)) -> bytes:
    """One 136-byte record, laid out as heavy_dot4.gam has them."""
    matrix = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
              0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    return (name.encode("cp1251").ljust(40, b"\x00")
            + struct.pack("<i", parent)
            + struct.pack("<3f", *location)
            + struct.pack("<4f", *rotation)
            + struct.pack("<16f", *matrix))


def _model_with_nodes(*records) -> str:
    data = b"".join(records)
    assert len(data) == NODE_RECORD_SIZE * len(records)
    path = os.path.join(tempfile.mkdtemp(), "body.gam")
    with open(path, "wb") as handle:
        handle.write(write_container(0, [Chunk(NODE_CHUNK_ID, data)]))
    return path


def test_the_node_record_is_136_bytes() -> None:
    """MEASURED: heavy_dot4.gam's chunk 2 is 544 bytes and names four
    nodes — Heavy_Dot_4, pCube34, pasted__polySurface549, LP_CANNON01."""
    assert NODE_RECORD_SIZE == 136
    assert 544 == 4 * NODE_RECORD_SIZE


def test_locators_are_read_by_name_with_their_position() -> None:
    path = _model_with_nodes(
        _node_record("Heavy_Dot_4", -6666, (0.0, 0.0, 0.0)),
        _node_record("pCube34", 0, (-0.07, 0.0, -0.04)),
        _node_record("LP_CANNON01", 0, (-0.19, 8.75, 0.03)),
    )

    nodes = read_nodes(path)
    assert [n.name for n in nodes] == ["Heavy_Dot_4", "pCube34", "LP_CANNON01"]
    assert nodes[0].parent == -6666

    locators = read_locators(path)
    assert list(locators) == ["LP_CANNON01"]
    cannon = locators["LP_CANNON01"]
    assert abs(cannon.location.y - 8.75) < 1e-5
    assert cannon.is_locator
    assert not nodes[1].is_locator


def test_a_model_without_a_node_table_has_no_locators() -> None:
    path = os.path.join(tempfile.mkdtemp(), "bare.gam")
    with open(path, "wb") as handle:
        handle.write(write_container(0, [Chunk(0x0F, b"\x00" * 4)]))

    assert read_nodes(path) == []
    assert read_locators(path) == {}
