# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the registration audit.

The tool's value is the comparison: not "which file should we have
written" but "which file names a model that works and not this one".
These tests pin that the gap is found, that agreements are reported as
eliminations, and that a substring match cannot invent a registration
that is not there.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

from core.registration_audit import (  # noqa: E402
    audit,
    compare_nodes,
    format_audit,
)


def _game_folder(files: dict[str, str]) -> str:
    root = tempfile.mkdtemp()
    for name, text in files.items():
        path = os.path.join(root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="cp1251", errors="replace") as handle:
            handle.write(text)
    return root


def test_the_gap_is_the_file_naming_only_the_working_model() -> None:
    """The entire question, answered by search instead of theory."""
    root = _game_folder({
        "data/models/AnimModels.xml": (
            '<models><model id="civilhouse1" file="a.gam"/>'
            '<model id="CubeV3" file="b.gam"/></models>'
        ),
        "data/maps/m/servers.xml": '<s><Item id="civilhouse1" file="x"/></s>',
    })

    result = audit(root, "CubeV3", "civilhouse1")
    missing = result.missing_from()

    assert len(missing) == 1
    assert missing[0].endswith("servers.xml")
    assert any(p.endswith("AnimModels.xml") for p in result.matched())


def test_no_gap_is_reported_as_an_elimination() -> None:
    """"Registration is fine" is a finding, and must read as one."""
    root = _game_folder({
        "a.xml": '<model id="civilhouse1"/><model id="CubeV3"/>',
    })

    result = audit(root, "CubeV3", "civilhouse1")
    assert result.missing_from() == []

    text = "\n".join(format_audit(result))
    assert "No gap" in text
    assert "Registration is not the difference" in text


def test_a_missing_reference_stops_the_report_rather_than_misleading() -> None:
    """Nothing below means anything if the working model is not there."""
    root = _game_folder({"a.xml": '<model id="CubeV3"/>'})

    text = "\n".join(format_audit(audit(root, "CubeV3", "civilhouse1")))
    assert "was not found anywhere" in text
    assert "THE GAP" not in text, "a gap against nothing is not a gap"


def test_a_substring_does_not_count_as_a_registration() -> None:
    """Searching for 'cube' must not match 'cube_wall' and report a
    registration that is not there."""
    root = _game_folder({"a.xml": '<model id="cube_wall"/><model id="house"/>'})

    result = audit(root, "cube", "house")
    assert result.candidate_mentions == []
    assert len(result.missing_from()) == 1


def test_a_file_naming_only_the_candidate_is_reported_too() -> None:
    """Written somewhere the working model is not is its own fault."""
    root = _game_folder({
        "shared.xml": '<model id="house"/><model id="cube"/>',
        "odd.xml": '<model id="cube"/>',
    })

    result = audit(root, "cube", "house")
    stray = result.only_candidate()
    assert len(stray) == 1 and stray[0].endswith("odd.xml")


def test_the_matching_line_is_shown_as_the_shape_to_copy() -> None:
    root = _game_folder({
        "servers.xml": '<s>\n<Item id="house" file="data\\\\models\\\\A.xml" />\n</s>',
    })

    text = "\n".join(format_audit(audit(root, "cube", "house")))
    assert "Item id=" in text, "the working entry must be shown verbatim"
    assert "the shape the candidate needs" in text


def test_non_catalogue_files_are_not_searched() -> None:
    """A game folder holds gigabytes of textures and models; reading
    them would turn an audit into a stall."""
    root = _game_folder({
        "a.xml": '<model id="house"/>',
        "big.gam": "house" * 100,
    })

    result = audit(root, "cube", "house")
    assert result.files_searched == 1


def test_node_comparison_names_a_missing_attribute() -> None:
    """Same move as the registration gap, one file later."""
    root = _game_folder({"world.xml": (
        '<World LastId="99">'
        '<Node name="Object1" class="SgAnimatedModelNode" id="house" '
        'org="1 2 3" orgRel="1" ndmAction="0"/>'
        '<Node name="Object2" class="SgAnimatedModelNode" id="cube" '
        'org="4 5 6"/>'
        '</World>'
    )})

    lines = compare_nodes(os.path.join(root, "world.xml"), "cube", "house")
    text = "\n".join(lines)
    assert "attributes theirs has and ours lacks" in text
    assert "orgRel" in text and "ndmAction" in text


def test_node_comparison_reports_a_matching_shape_as_an_elimination() -> None:
    root = _game_folder({"world.xml": (
        '<World><Node name="A" class="SgAnimatedModelNode" id="house" org="1"/>'
        '<Node name="B" class="SgAnimatedModelNode" id="cube" org="2"/></World>'
    )})

    text = "\n".join(compare_nodes(os.path.join(root, "world.xml"), "cube", "house"))
    assert "No difference in element name or attribute set" in text


def test_a_model_placed_by_no_node_is_said_so_plainly() -> None:
    """Registered and loadable is not the same as placed."""
    root = _game_folder({"world.xml": '<World><Node id="house"/></World>'})

    text = "\n".join(compare_nodes(os.path.join(root, "world.xml"), "cube", "house"))
    assert "nothing on the map asks for it" in text


_ALL_TESTS = (
    test_node_comparison_names_a_missing_attribute,
    test_node_comparison_reports_a_matching_shape_as_an_elimination,
    test_a_model_placed_by_no_node_is_said_so_plainly,
    test_the_gap_is_the_file_naming_only_the_working_model,
    test_no_gap_is_reported_as_an_elimination,
    test_a_missing_reference_stops_the_report_rather_than_misleading,
    test_a_substring_does_not_count_as_a_registration,
    test_a_file_naming_only_the_candidate_is_reported_too,
    test_the_matching_line_is_shown_as_the_shape_to_copy,
    test_non_catalogue_files_are_not_searched,
)


if __name__ == "__main__":
    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            test_fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL: {test_fn.__name__}: {exc}")
    print(f"{len(_ALL_TESTS) - failures}/{len(_ALL_TESTS)} passed")


def test_an_absent_candidate_still_reports_the_reference() -> None:
    """A file missing only our node and a file missing both are
    different failures with different causes, and reporting the first
    without mentioning the second leaves the run's question open."""
    root = _game_folder({"world.xml": '<World><Node id="house"/></World>'})
    text = "\n".join(compare_nodes(os.path.join(root, "world.xml"), "cube", "house"))
    assert "IS placed here" in text

    empty = _game_folder({"world.xml": "<World/>"})
    text = "\n".join(compare_nodes(os.path.join(empty, "world.xml"), "cube", "house"))
    assert "took both" in text


def test_a_search_that_never_reaches_a_map_folder_says_so() -> None:
    """Every "no gap" from a folder holding only data\\models is scoped
    to a truncated view: a map's own servers.xml is never opened, so a
    model listed only there reads as unregistered and a gap that exists
    only there cannot appear at all.
    """
    root = _game_folder({"animmodels.xml": '<m id="house"/><m id="cube"/>'})
    result = audit(root, "cube", "house")

    assert result.covers_maps is False
    text = "\n".join(format_audit(result))
    assert "no data\\maps subtree" in text
    assert "the folder CONTAINING data" in text


def test_a_real_game_root_is_not_warned_about() -> None:
    root = _game_folder({
        "data/models/animmodels.xml": '<m id="house"/><m id="cube"/>',
        "data/maps/m/servers.xml": '<s><Item id="house"/></s>',
    })
    result = audit(root, "cube", "house")

    assert result.covers_maps is True
    assert "no data\\maps subtree" not in "\n".join(format_audit(result))
