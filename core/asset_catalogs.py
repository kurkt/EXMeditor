# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The catalogue tree the Asset Browser groups models under.

Tags are a filter; catalogues are the tree down the left of the browser,
and they are what "show me this map's buildings" actually needs. Blender
keeps them in a plain text file, ``blender_assets.cats.txt``, in the
asset library's own folder — which for a Current File library means
beside the ``.blend``. An unsaved file has no folder, so it can have
tags and no tree; that is a fact about Blender, not a choice made here.

The shape is ``Ex Machina / <map> / <category>``: the add-on's own root
so the game's models never mix with the user's other assets, then the
map, so two maps open in one file stay apart, then what the model is.

File format, from Blender's own writer::

    VERSION 1

    <uuid>:<catalog/path>:<simple name>

The UUID is what an asset stores, so it has to survive a rebuild: these
are derived from the path rather than drawn at random, and the same
path gives the same id on every machine and every run. A catalogue that
changed its id each time would orphan every asset already filed under
it.
"""

from __future__ import annotations

import uuid

#: Everything this add-on files goes under one root, so a user's own
#: assets and the game's never share a branch.
ROOT = "Ex Machina"

#: Fixed namespace for the derived ids. Any constant UUID would do; what
#: matters is that it never changes, because it is half of every id
#: already written into a ``.blend``.
_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

#: Blender splits catalogue paths on "/" and separates the three fields
#: of a line with ":", so neither can appear inside a name.
_FORBIDDEN = ":/\\"

VERSION_LINE = "VERSION 1"

_HEADER = (
    "# This is an Asset Catalog Definition file for Blender.\n"
    "#\n"
    "# Empty lines and lines starting with `#` will be ignored.\n"
    "# The first non-ignored line should be the version indicator.\n"
    '# Other lines are of the format "UUID:catalog/path/for/assets:simple '
    'catalog name"\n'
    "\n"
)


def _clean(part: str) -> str:
    """A name that can sit in a catalogue path."""
    text = (part or "").strip()
    for character in _FORBIDDEN:
        text = text.replace(character, "-")
    return text or "unnamed"


def catalog_path(map_name: str, category: str = "", subcategory: str = "") -> str:
    """Where a model files, as ``Ex Machina/<map>/<category>``.

    The subcategory becomes a fourth level when it says something the
    category does not — ``nature/region1`` rather than ``nature/nature``.
    """
    parts = [ROOT, _clean(map_name)]
    if category:
        parts.append(_clean(category))
        if subcategory and subcategory != category:
            parts.append(_clean(subcategory))
    return "/".join(parts)


def catalog_uuid(path: str) -> str:
    """The id for a catalogue path. Same path, same id, always."""
    return str(uuid.uuid5(_NAMESPACE, path))


def simple_name(path: str) -> str:
    """The one-line label Blender shows. Its own writer uses ``-``."""
    return path.replace("/", "-")


def ancestors(path: str) -> list:
    """Every catalogue a path implies, parents first.

    Blender does not invent the intermediate levels: a file naming only
    ``Ex Machina/r1m1/buildings`` shows that one entry flat, not a tree
    with ``Ex Machina`` at its head.
    """
    parts = path.split("/")
    return ["/".join(parts[: index + 1]) for index in range(len(parts))]


def parse(text: str) -> dict:
    """Read a catalogue file into ``{path: (uuid, simple name)}``.

    Unparseable lines are skipped rather than dropped from the file —
    see :func:`merge`. The user's own catalogues live in here too.
    """
    found = {}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("VERSION"):
            continue
        pieces = stripped.split(":")
        if len(pieces) < 2:
            continue
        identifier, path = pieces[0], pieces[1]
        label = pieces[2] if len(pieces) > 2 else simple_name(path)
        found[path] = (identifier, label)
    return found


def merge(existing_text: str, paths) -> str:
    """Add ``paths`` to a catalogue file, keeping everything already in it.

    The file is shared: a user's own catalogues sit beside ours, and a
    rebuild that rewrote it from scratch would delete them. Entries
    already present keep the id they have, even where it differs from
    the derived one — an id in the file is one assets may already
    reference.
    """
    catalogs = parse(existing_text)

    wanted = set()
    for path in paths:
        wanted.update(ancestors(path))

    for path in sorted(wanted):
        if path not in catalogs:
            catalogs[path] = (catalog_uuid(path), simple_name(path))

    lines = [_HEADER, VERSION_LINE, ""]
    for path in sorted(catalogs):
        identifier, label = catalogs[path]
        lines.append(f"{identifier}:{path}:{label}")
    return "\n".join(lines) + "\n"
