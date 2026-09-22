# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for unrecognised node classes in ``world.xml``.

The behaviour under test exists because of a real failure: a map using
a node class the SDK had never seen (`SgSpriteNode`) aborted the whole
read, reporting "0 models loaded" for a map whose other 1500 objects
were perfectly valid.

Two properties matter, and they pull in opposite directions:

* the rest of the map must survive an unknown class;
* the SDK must not quietly invent a meaning for it.

Skipping and recording satisfies both.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.unknown_nodes import UnknownNodeReport  # noqa: E402
from formats.exm.world import read_world  # noqa: E402

_WORLD_WITH_UNKNOWN = """<?xml version="1.0" encoding="windows-1251"?>
<World name="Object1" class="SgNode" LastId="100">
\t<Node name="Object2" class="SgAnimatedModelNode" org="1.0 2.0 3.0" orgRel="1" id="rock1" />
\t<Node name="Object3" class="SgSpriteNode" org="5.0 0.0 5.0" orgRel="1" id="minin" texture="fire.dds" flags="2" />
\t<Node name="Object4" class="SgNode" org="0.0 0.0 0.0" orgRel="1">
\t\t<Node name="Object5" class="SgSpriteNode" org="1.0 0.0 1.0" orgRel="0" model="town01" />
\t\t<Node name="Object6" class="SgGameUnitNode" org="2.0 0.0 2.0" orgRel="0" id="house1" />
\t</Node>
</World>
"""


def _write(content: str = _WORLD_WITH_UNKNOWN) -> str:
    path = os.path.join(tempfile.mkdtemp(), "world.xml")
    with open(path, "w", encoding="cp1251") as f:
        f.write(content)
    return path


def _walk(objects):
    for obj in objects:
        yield obj
        yield from _walk(obj.children)


# --- the map survives ---


def test_unknown_class_does_not_abort_the_read() -> None:
    """The regression: one unrecognised class used to discard the whole
    file, turning a valid map into '0 models loaded'."""
    objects, root = read_world(_write())
    assert len(objects) == 2  # the model node and the container
    assert root["LastId"] == "100"


def test_known_siblings_and_children_still_load() -> None:
    objects, _root = read_world(_write())
    names = {o.name for o in _walk(objects)}
    assert "Object2" in names   # top-level known node
    assert "Object6" in names   # known node nested beside an unknown one


def test_unknown_nodes_are_not_invented_into_objects() -> None:
    """Skipping means skipping: no placeholder object is created, since
    the SDK does not know what the class means."""
    objects, _root = read_world(_write())
    names = {o.name for o in _walk(objects)}
    assert "Object3" not in names
    assert "Object5" not in names


# --- what gets recorded ---


def test_report_counts_every_occurrence() -> None:
    report = UnknownNodeReport()
    read_world(_write(), unknown=report)
    assert report.total_skipped == 2
    assert set(report.classes) == {"SgSpriteNode"}
    assert report.classes["SgSpriteNode"].count == 2


def test_report_collects_the_full_attribute_set() -> None:
    """Which attribute carries the model reference is the key question
    for a new class, and it is not always called 'id' — one sample here
    uses 'model' and another uses 'texture'."""
    report = UnknownNodeReport()
    read_world(_write(), unknown=report)
    info = report.classes["SgSpriteNode"]
    assert {"id", "model", "texture", "flags", "org", "orgRel"} <= info.attribute_names


def test_report_identifies_reference_like_attributes() -> None:
    report = UnknownNodeReport()
    read_world(_write(), unknown=report)
    references = report.classes["SgSpriteNode"].referenced_values()
    assert "minin" in references.get("id", [])
    assert "town01" in references.get("model", [])
    assert "fire.dds" in references.get("texture", [])


def test_report_records_hierarchy_position() -> None:
    """Whether an unknown class appears at the top level or nested under
    a container distinguishes a new object type from a new sub-part."""
    report = UnknownNodeReport()
    read_world(_write(), unknown=report)
    info = report.classes["SgSpriteNode"]
    assert "SgNode" in info.parent_classes
    assert info.child_classes == set()  # leaf nodes in this sample


def test_report_keeps_verbatim_examples() -> None:
    report = UnknownNodeReport()
    read_world(_write(), unknown=report)
    samples = report.classes["SgSpriteNode"].samples
    assert len(samples) == 2
    assert samples[0].attributes["class"] == "SgSpriteNode"


def test_reading_without_a_collector_still_works() -> None:
    """The collector is optional; a caller that doesn't want the detail
    must not be forced to provide one."""
    objects, _root = read_world(_write())
    assert len(objects) == 2


def test_empty_report_is_falsey_and_says_so() -> None:
    clean = """<?xml version="1.0" encoding="windows-1251"?>
<World name="O1" class="SgNode" LastId="1">
\t<Node name="O2" class="SgAnimatedModelNode" org="0 0 0" orgRel="1" id="rock" />
</World>
"""
    report = UnknownNodeReport()
    read_world(_write(clean), unknown=report)
    assert not report
    assert report.total_skipped == 0
    assert "No unknown node classes." in report.summary_lines()


def test_report_serialises_to_json() -> None:
    report = UnknownNodeReport()
    read_world(_write(), unknown=report)
    path = os.path.join(tempfile.mkdtemp(), "unknown_nodes.json")
    report.write_json(path)

    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    entry = data["unknown_classes"][0]
    assert entry["class"] == "SgSpriteNode"
    assert entry["count"] == 2
    assert "texture" in entry["attributes"]
    assert entry["samples"]


def test_several_unknown_classes_are_tracked_separately() -> None:
    content = """<?xml version="1.0" encoding="windows-1251"?>
<World name="O1" class="SgNode" LastId="1">
\t<Node name="O2" class="SgSpriteNode" org="0 0 0" orgRel="1" id="a" />
\t<Node name="O3" class="SgParticleNode" org="0 0 0" orgRel="1" effect="smoke" />
\t<Node name="O4" class="SgSpriteNode" org="0 0 0" orgRel="1" id="b" />
</World>
"""
    report = UnknownNodeReport()
    read_world(_write(content), unknown=report)
    assert report.classes["SgSpriteNode"].count == 2
    assert report.classes["SgParticleNode"].count == 1
    # summary lists the most frequent first
    assert "SgSpriteNode: 2" in report.summary_lines()[1]


_ALL_TESTS = (
    test_unknown_class_does_not_abort_the_read,
    test_known_siblings_and_children_still_load,
    test_unknown_nodes_are_not_invented_into_objects,
    test_report_counts_every_occurrence,
    test_report_collects_the_full_attribute_set,
    test_report_identifies_reference_like_attributes,
    test_report_records_hierarchy_position,
    test_report_keeps_verbatim_examples,
    test_reading_without_a_collector_still_works,
    test_empty_report_is_falsey_and_says_so,
    test_report_serialises_to_json,
    test_several_unknown_classes_are_tracked_separately,
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
