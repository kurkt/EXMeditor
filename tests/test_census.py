# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the ``world.xml`` census.

The census exists because every other report counts parsed results and
is therefore structurally unable to mention what it skipped. So the
central test is not "does it count correctly" but "does it count things
the parser refuses to handle" — if it shared the parser's blind spots
it would be worthless.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.census import MODELLED_ATTRIBUTES, census_world  # noqa: E402
from core.unknown_nodes import UnknownNodeReport  # noqa: E402
from formats.exm.world import read_world  # noqa: E402
from utils.errors import ParsingError  # noqa: E402
from corpus import CORPUS, corpus  # noqa: E402

_MIXED = """<?xml version="1.0" encoding="windows-1251"?>
<World name="O1" class="SgNode" LastId="100">
\t<Node name="O2" class="SgAnimatedModelNode" org="1 2 3" orgRel="1" id="rock" lodDistance="120" />
\t<Node name="Smoke01" class="SgSpriteNode" org="5 0 5" orgRel="1" id="smoke" spriteType="2" />
\t<Node name="Smoke02" class="SgSpriteNode" org="6 0 6" orgRel="1" id="smoke" />
\t<Node name="Group" class="SgNode" org="0 0 0" orgRel="1">
\t\t<Node name="P1" class="SgParticleNode" org="1 1 1" orgRel="0" effect="fire" />
\t</Node>
</World>
"""


def _write(content: str = _MIXED) -> str:
    path = os.path.join(tempfile.mkdtemp(), "world.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write(content)
    return path


# --- the point of the module ---


def test_counts_nodes_the_parser_skips() -> None:
    """The census must see what the parser refuses to handle. Sharing
    the parser's blind spots would make it useless."""
    path = _write()
    census = census_world(path)

    unknown = UnknownNodeReport()
    parsed, _root = read_world(path, unknown=unknown)

    assert census.total_nodes == 5
    assert census.unknown_nodes == 3   # 2 sprites + 1 particle
    # the parser produced fewer objects than the file contains
    assert sum(1 for _ in _walk(parsed)) == 2


def _walk(objects):
    for obj in objects:
        yield obj
        yield from _walk(obj.children)


def test_names_each_unknown_class_with_a_count_and_example() -> None:
    census = census_world(_write())
    unknown = {c.class_name: c for c in census.unknown_classes()}
    assert unknown["SgSpriteNode"].count == 2
    assert unknown["SgSpriteNode"].first_name == "Smoke01"
    assert unknown["SgParticleNode"].count == 1


def test_counts_nested_nodes() -> None:
    """A node nested inside a container is as real as a top-level one."""
    census = census_world(_write())
    assert census.classes["SgParticleNode"].depths == {2}


def test_known_and_unknown_totals_add_up() -> None:
    census = census_world(_write())
    assert census.known_nodes + census.unknown_nodes == census.total_nodes


# --- attribute coverage ---


def test_reports_attributes_the_sdk_does_not_interpret() -> None:
    """These round-trip fine, but the SDK assigns them no meaning — a
    difference between the game and Blender could hide in one."""
    census = census_world(_write())
    ignored = census.ignored_attribute_summary()
    assert ignored["lodDistance"] == 1
    assert ignored["spriteType"] == 1
    assert ignored["effect"] == 1


def test_flags_uninterpreted_attributes_on_supported_classes() -> None:
    """The subtle case: a class the SDK handles, carrying an attribute
    it ignores. Nothing errors, nothing is skipped, and the data is
    silently unused."""
    census = census_world(_write())
    entry = census.classes["SgAnimatedModelNode"]
    assert "lodDistance" in entry.ignored_attributes
    assert "org" in entry.modelled_attributes


def test_attribute_instance_totals_are_consistent() -> None:
    census = census_world(_write())
    assert (
        census.modelled_attribute_instances + census.ignored_attribute_instances
        == census.total_attribute_instances
    )


def test_samples_are_collected_for_inspection() -> None:
    census = census_world(_write())
    samples = census.classes["SgSpriteNode"].attribute_samples
    assert samples["name"] == ["Smoke01", "Smoke02"]


# --- clean files ---


def test_a_fully_handled_file_reports_complete_coverage() -> None:
    clean = """<?xml version="1.0" encoding="windows-1251"?>
<World name="O1" class="SgNode" LastId="1">
\t<Node name="O2" class="SgAnimatedModelNode" org="0 0 0" orgRel="1" id="rock" />
</World>
"""
    census = census_world(_write(clean))
    assert census.unknown_nodes == 0
    assert census.ignored_attribute_instances == 0
    assert any("none" in line for line in census.summary_lines())


def test_the_real_map_is_fully_accounted_for() -> None:
    """A claim worth being able to make: this map has no blind spots."""
    real = corpus("world.xml")
    if not os.path.isfile(real):
        return
    census = census_world(real)
    assert census.total_nodes > 1000
    assert census.unknown_nodes == 0
    assert census.ignored_attribute_instances == 0


def test_modelled_attribute_list_matches_the_parser() -> None:
    """If the parser gains a field and this list isn't updated, the
    census would report a handled attribute as ignored — a false alarm
    that erodes trust in the report."""
    from formats.exm.world import _MODELED_ATTRS

    assert MODELLED_ATTRIBUTES == frozenset(_MODELED_ATTRS)


# --- the second placement layer ---


def test_dynamic_scene_census_counts_placed_objects() -> None:
    """world.xml is not the only file that places objects. A report
    covering only it can claim 'everything imported' while thousands of
    objects sit unread in another file."""
    from core.census import census_dynamic_scene

    content = """<?xml version="1.0" encoding="windows-1251"?>
<DynamicScene>
\t<Object Name="fence1" Prototype="Breakable_WoodFence1" Pos="1 2 3" />
\t<Object Name="fence2" Prototype="Breakable_WoodFence1" Pos="4 5 6" />
\t<Object Name="tree1" Prototype="Breakable_Dub3" Pos="7 8 9" />
\t<Object Name="npc1" Prototype="NPC" ModelName="r1_man" />
</DynamicScene>
"""
    path = os.path.join(tempfile.mkdtemp(), "dynamicscene.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write(content)

    census = census_dynamic_scene(path)
    assert census.total_objects == 4
    assert census.placed_objects == 3      # the NPC has no Pos
    assert census.prototypes["Breakable_WoodFence1"] == 2
    assert census.model_names["r1_man"] == 1


def test_dynamic_scene_census_on_real_data() -> None:
    from core.census import census_dynamic_scene

    real = corpus("dynamicscene.xml")
    if not os.path.isfile(real):
        return
    census = census_dynamic_scene(real)
    assert census.placed_objects > 1000, "this layer is large and entirely unimported"
    assert "Breakable_WoodFence1" in census.prototypes


def test_dynamic_scene_census_rejects_a_missing_file() -> None:
    from core.census import census_dynamic_scene

    try:
        census_dynamic_scene("/no/such/dynamicscene.xml")
        raise AssertionError("expected ParsingError")
    except ParsingError:
        pass


def test_prototype_resolvability_is_split() -> None:
    """Some prototypes name a model directly and would load with no new
    machinery; the rest need a definition file the SDK has never seen.
    One combined number hides that the problem has two halves of very
    different difficulty."""
    from core.census import census_dynamic_scene
    from formats.exm.model_catalog import ModelCatalog, ModelEntry

    content = """<?xml version="1.0" encoding="windows-1251"?>
<DynamicScene>
\t<Object Name="a" Prototype="lamppost2" Pos="1 2 3" />
\t<Object Name="b" Prototype="lamppost2" Pos="4 5 6" />
\t<Object Name="c" Prototype="Breakable_WoodFence1" Pos="7 8 9" />
</DynamicScene>
"""
    path = os.path.join(tempfile.mkdtemp(), "dynamicscene.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write(content)

    catalog = ModelCatalog()
    catalog.add(ModelEntry(model_id="lamppost2", file_path="data\\models\\lamppost2.gam"))

    census = census_dynamic_scene(path)
    direct, needs = census.split_by_resolvability(catalog)
    assert direct == [("lamppost2", 2)]
    assert needs == [("Breakable_WoodFence1", 1)]


# --- errors ---


def test_rejects_missing_file() -> None:
    try:
        census_world("/no/such/world.xml")
        raise AssertionError("expected ParsingError")
    except ParsingError:
        pass


def test_rejects_malformed_xml() -> None:
    try:
        census_world(_write("<World><Node"))
        raise AssertionError("expected ParsingError")
    except ParsingError:
        pass


def test_class_detail_for_an_absent_class_says_so() -> None:
    census = census_world(_write())
    lines = census.class_detail_lines("SgNoSuchNode")
    assert "does not appear" in lines[0]


_ALL_TESTS = (
    test_counts_nodes_the_parser_skips,
    test_names_each_unknown_class_with_a_count_and_example,
    test_counts_nested_nodes,
    test_known_and_unknown_totals_add_up,
    test_reports_attributes_the_sdk_does_not_interpret,
    test_flags_uninterpreted_attributes_on_supported_classes,
    test_attribute_instance_totals_are_consistent,
    test_samples_are_collected_for_inspection,
    test_a_fully_handled_file_reports_complete_coverage,
    test_the_real_map_is_fully_accounted_for,
    test_modelled_attribute_list_matches_the_parser,
    test_dynamic_scene_census_counts_placed_objects,
    test_dynamic_scene_census_on_real_data,
    test_dynamic_scene_census_rejects_a_missing_file,
    test_prototype_resolvability_is_split,
    test_rejects_missing_file,
    test_rejects_malformed_xml,
    test_class_detail_for_an_absent_class_says_so,
)


if __name__ == "__main__":
    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            test_fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL: {test_fn.__name__}: {exc}")
        else:
            print(f"PASS: {test_fn.__name__}")
    print(f"\n{len(_ALL_TESTS) - failures}/{len(_ALL_TESTS)} passed")
    sys.exit(1 if failures else 0)


def test_the_id_counter_is_reported_beside_the_numbers_in_use() -> None:
    """A map issued Object76476712 where every node the editor had ever
    written was numbered in the thousands, and the editor dropped that
    node on the next save. New names are allocated above LastId, so a
    counter far past everything in use is worth seeing rather than
    inferring from a node name after the fact.
    """
    import os
    import tempfile

    from core.census import census_world

    path = os.path.join(tempfile.mkdtemp(), "world.xml")
    with open(path, "w") as handle:
        handle.write(
            '<World name="O" class="SgNode" LastId="76476711">'
            '<Node name="Object7008" class="SgAnimatedModelNode" id="a"/>'
            "</World>"
        )

    lines = census_world(path).id_counter_lines()
    text = "\n".join(lines)
    assert "76476711" in text and "7008" in text
    assert "past the highest node in the file" in text


def test_a_counter_behind_the_file_is_reported_too() -> None:
    import os
    import tempfile

    from core.census import census_world

    path = os.path.join(tempfile.mkdtemp(), "world.xml")
    with open(path, "w") as handle:
        handle.write(
            '<World name="O" class="SgNode" LastId="5">'
            '<Node name="Object900" class="SgAnimatedModelNode" id="a"/>'
            "</World>"
        )

    text = "\n".join(census_world(path).id_counter_lines())
    assert "BEHIND the file" in text


def test_a_healthy_counter_is_reported_without_alarm() -> None:
    import os
    import tempfile

    from core.census import census_world

    path = os.path.join(tempfile.mkdtemp(), "world.xml")
    with open(path, "w") as handle:
        handle.write(
            '<World name="O" class="SgNode" LastId="7010">'
            '<Node name="Object7008" class="SgAnimatedModelNode" id="a"/>'
            "</World>"
        )

    text = "\n".join(census_world(path).id_counter_lines())
    assert "!" not in text
