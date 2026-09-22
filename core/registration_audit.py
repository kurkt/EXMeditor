# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Find every file that mentions a model id, and what a working one has.

``bpy``-free, like the rest of ``core``; the operator lives in
``addon/registration_operator.py``.

Why this exists
---------------

A model can be written correctly, catalogued correctly, and still never
appear in the game's editor. Each round of that has been answered by
reasoning about which file ought to matter — the catalogue, then
``servers.xml`` — and each answer has been plausible and wrong.

The reasoning is the problem. Somewhere in the game folder there are
files that mention a model the editor *does* offer, and the useful
question is not "which file should we write" but "which file mentions
the working model and not ours". That is a search, not a theory, and
the answer does not depend on anyone being right about the format.

So this takes two ids — a candidate and a reference known to appear —
and reports, per file:

* files naming both: registration that matches, ruled out;
* files naming the reference and NOT the candidate: the gap, and the
  entire answer if there is one;
* files naming the candidate and not the reference: written somewhere
  the working model is not, which is its own kind of wrong.

It reads. It never edits, so a wrong guess about which file matters
costs nothing.
"""

from __future__ import annotations

import dataclasses
import os
import re

#: Extensions worth searching. The catalogues, indexes and manifests
#: are all XML; .ssl manifests are text with the same references.
SEARCHED_SUFFIXES = (".xml", ".ssl", ".cfg", ".txt")

#: Files above this are not catalogues, and reading them whole would
#: turn an audit into a stall.
MAX_FILE_BYTES = 32 * 1024 * 1024


@dataclasses.dataclass
class Mention:
    """One line naming a model id."""

    path: str
    line_number: int
    text: str


@dataclasses.dataclass
class AuditResult:
    game_root: str
    candidate: str
    reference: str
    files_searched: int
    candidate_mentions: list[Mention]
    reference_mentions: list[Mention]
    unreadable: list[str]
    #: Whether the search reached any map folder. A search confined to
    #: data\models cannot see a map's own servers.xml, and every
    #: "no gap" it reports is scoped to a truncated view of the game.
    covers_maps: bool = False

    def _paths(self, mentions: list[Mention]) -> set[str]:
        return {m.path for m in mentions}

    def missing_from(self) -> list[str]:
        """Files naming the reference but not the candidate.

        The whole point of the comparison: these are the registrations
        a working model has and this one does not.
        """
        return sorted(self._paths(self.reference_mentions) - self._paths(self.candidate_mentions))

    def matched(self) -> list[str]:
        return sorted(self._paths(self.reference_mentions) & self._paths(self.candidate_mentions))

    def only_candidate(self) -> list[str]:
        return sorted(self._paths(self.candidate_mentions) - self._paths(self.reference_mentions))


def _files_to_search(game_root: str) -> list[str]:
    found: list[str] = []
    for root, _dirs, names in os.walk(game_root):
        for name in names:
            if not name.lower().endswith(SEARCHED_SUFFIXES):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            found.append(path)
    return sorted(found)


def _mentions(text: str, model_id: str, path: str) -> list[Mention]:
    """Lines where the id appears as a whole value, not as a substring.

    Bounded deliberately: searching for ``cube`` must not match
    ``cube_wall`` and report a registration that is not there.
    """
    pattern = re.compile(
        rf'(?<![\w.-]){re.escape(model_id)}(?![\w-])', re.IGNORECASE
    )
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            found.append(Mention(path=path, line_number=number, text=line.strip()[:200]))
    return found


def audit(game_root: str, candidate: str, reference: str) -> AuditResult:
    """Search the whole game folder for two model ids."""
    candidate_mentions: list[Mention] = []
    reference_mentions: list[Mention] = []
    unreadable: list[str] = []
    searched = 0

    candidate_paths: set[str] = set()
    reference_paths: set[str] = set()

    for path in _files_to_search(game_root):
        try:
            # cp1251 with replacement: the game's XML is not UTF-8 and
            # a decode error must not silently drop a catalogue.
            with open(path, "r", encoding="cp1251", errors="replace") as handle:
                text = handle.read()
        except OSError:
            unreadable.append(path)
            continue

        searched += 1
        found = _mentions(text, candidate, path)
        if found:
            candidate_paths.add(path)
        candidate_mentions += found
        if reference:
            found = _mentions(text, reference, path)
            if found:
                reference_paths.add(path)
            reference_mentions += found

    covers_maps = any(
        os.sep + "maps" + os.sep in path.lower()
        for path in (candidate_paths | reference_paths | {""})
    ) or os.path.isdir(os.path.join(game_root, "data", "maps"))

    return AuditResult(
        covers_maps=covers_maps,
        game_root=game_root,
        candidate=candidate,
        reference=reference,
        files_searched=searched,
        candidate_mentions=candidate_mentions,
        reference_mentions=reference_mentions,
        unreadable=unreadable,
    )


def compare_nodes(
    world_path: str, candidate_id: str, reference_id: str
) -> list[str]:
    """Put our ``<Node>`` beside one the editor already draws.

    The same move that found the registration gap, applied one file
    later. A model can be registered perfectly, resolve to a real
    ``.gam``, import into Blender and still not appear in the game's
    own editor — at which point the remaining difference is in the node
    that places it, and the map already holds over a thousand nodes the
    editor draws without complaint.

    Attributes are compared as sets rather than as text, because the
    interesting answer is "ours carries something theirs does not, or
    lacks something they all have" and not "the numbers differ".
    """
    import xml.etree.ElementTree as ET

    try:
        tree = ET.parse(world_path)
    except (OSError, ET.ParseError) as exc:
        return [f"Could not read {world_path}: {exc}"]

    root = tree.getroot()
    candidate_nodes = []
    reference_nodes = []
    for element in root.iter():
        model_id = element.get("id") or element.get("Model") or ""
        if model_id == candidate_id:
            candidate_nodes.append(element)
        elif reference_id and model_id == reference_id:
            reference_nodes.append(element)

    lines = [
        f"World: {world_path}",
        f"  nodes placing {candidate_id!r}: {len(candidate_nodes)}",
        f"  nodes placing {reference_id!r}: {len(reference_nodes)}",
        "",
    ]

    if not candidate_nodes:
        # Both counts are reported above BEFORE returning. An earlier
        # version stopped here without mentioning the reference, which
        # left the one question the run was asked unanswered: a file
        # missing only our node and a file missing both are different
        # failures with different causes, and they read identically.
        lines.append(
            f"No node places {candidate_id!r} in this map. It is registered "
            "and loadable, but nothing on the map asks for it."
        )
        if reference_nodes:
            lines.append(
                f"  {reference_id!r} IS placed here, so this file kept one "
                "node and not the other."
            )
        else:
            lines.append(
                f"  {reference_id!r} is not placed here either — this file "
                "holds neither, so whatever removed them took both."
            )
        return lines

    def describe(element, label: str) -> list[str]:
        attrs = " ".join(f'{k}="{v}"' for k, v in element.attrib.items())
        return [f"  {label}: <{element.tag} {attrs}/>"]

    lines.append(f"{len(candidate_nodes)} node(s) place {candidate_id!r}:")
    for element in candidate_nodes[:5]:
        lines += describe(element, "ours")

    if not reference_nodes:
        lines += [
            "",
            f"No node places {reference_id!r} in this map, so there is nothing "
            "to compare against. Pick a model id the coverage report lists as "
            "used by this map.",
        ]
        return lines

    lines += ["", f"A node the editor draws ({reference_id!r}):"]
    lines += describe(reference_nodes[0], "theirs")

    ours = set(candidate_nodes[0].attrib)
    theirs = set(reference_nodes[0].attrib)
    lines += ["", "=" * 60]
    if candidate_nodes[0].tag != reference_nodes[0].tag:
        lines.append(
            f"! element name differs: ours <{candidate_nodes[0].tag}>, "
            f"theirs <{reference_nodes[0].tag}>"
        )
    missing = sorted(theirs - ours)
    if missing:
        lines.append(f"! attributes theirs has and ours lacks: {missing}")
        for key in missing:
            lines.append(f"      {key}={reference_nodes[0].get(key)!r}")
    extra = sorted(ours - theirs)
    if extra:
        lines.append(f"! attributes ours has and theirs does not: {extra}")
    if not missing and not extra and candidate_nodes[0].tag == reference_nodes[0].tag:
        lines.append(
            "No difference in element name or attribute set. The node is "
            "shaped like one the editor draws, so whatever stops it is not "
            "here."
        )
    lines.append("=" * 60)
    return lines


def format_audit(result: AuditResult) -> list[str]:
    """The report, ordered so the gap is impossible to miss."""
    lines = [
        f"Searched {result.files_searched} file(s) under {result.game_root}",
        f"  candidate: {result.candidate!r} — "
        f"{len(result.candidate_mentions)} mention(s)",
        f"  reference: {result.reference!r} — "
        f"{len(result.reference_mentions)} mention(s)",
    ]

    if not result.covers_maps:
        lines += [
            "",
            "! This folder holds no data\\maps subtree, so no map's own "
            "servers.xml was searched. A model listed only there would read "
            "as unregistered, and a gap that exists only there would not "
            "appear below. Set the Game Folder to the game's root — the "
            "folder CONTAINING data — and run this again.",
        ]

    if not result.reference_mentions and result.reference:
        lines += [
            "",
            f"The reference {result.reference!r} was not found anywhere. Either "
            "the id is spelled differently in the files, or the Game Folder is "
            "not the one the editor loads. Nothing below means anything until "
            "that is sorted out.",
        ]
        return lines

    missing = result.missing_from()
    lines += ["", "=" * 60]
    if missing:
        lines.append(
            f"THE GAP — {len(missing)} file(s) name the working model and not "
            "this one:"
        )
        by_path = {}
        for mention in result.reference_mentions:
            by_path.setdefault(mention.path, mention)
        for path in missing:
            lines.append(f"  {path}")
            example = by_path[path]
            lines.append(f"      line {example.line_number}: {example.text}")
            lines.append("      ^ this is the shape the candidate needs")
    else:
        lines.append(
            "No gap: every file naming the working model names this one too. "
            "Registration is not the difference, and whatever is wrong is in "
            "the model or in how the editor reads it."
        )
    lines.append("=" * 60)

    matched = result.matched()
    if matched:
        lines += ["", f"Both present in {len(matched)} file(s) — ruled out:"]
        lines += [f"  {path}" for path in matched]

    stray = result.only_candidate()
    if stray:
        lines += [
            "",
            f"{len(stray)} file(s) name this model and NOT the working one. "
            "Written somewhere the working model is not:",
        ]
        lines += [f"  {path}" for path in stray]

    if result.unreadable:
        lines += ["", f"{len(result.unreadable)} file(s) could not be read"]
    return lines
