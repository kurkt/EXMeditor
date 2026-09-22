# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""``data/models/ModelTextures.xml`` — how the engine finds a texture.

The question this answers
-------------------------

A model's skin chunk stores a bare filename. Where the engine turns
that into a path was never established from the data: textures beside
the model resolved, one in the root of ``data/models/textures`` did
not, and subfolders of that tree resolved without ever appearing in
the log. Several rounds went into guessing a search order that would
explain all three.

A static read of the editor answers it: ``DraftModel.cpp`` opens
``data/models/ModelTextures.xml``, walks the children of a ``Textures``
element, and registers each one by **two string attributes** — a name
and a file. There is no search. A texture resolves because the
registry names it, which is why one folder worked and another did not
for no reason visible on disk.

Failures are logged verbatim as ``Error: Can't open file:
data/models/ModelTextures.xml`` and ``... Can't read file: ...``.

The attributes are named
------------------------

``name`` and ``path``, one tag, and nothing else::

    <Textures>
      <file name="0_glass.dds" path="data\\models\\vehicles\\shared\\0_glass.dds" />
      <file name="grate.tga"   path="data\\models\\vehicles\\shared\\grate.tga" />
    </Textures>

``name`` is the key a material uses — a bare filename — and ``path`` is
where it resolves. There are no aliases: this is one substitution table
for shared textures, almost all of them under ``vehicles/shared``.

An earlier version guessed, taking whichever attribute looked like a
filename as the path. That gave the right answer for the wrong reason
and would diverge on any entry carrying two path-like values.
"""

from __future__ import annotations

import os
import re

from utils.logging import get_logger

logger = get_logger("formats.exm.texture_registry")

#: Where the editor looks for it, verbatim from the string constant.
REGISTRY_PATH = os.path.join("data", "models", "ModelTextures.xml")

#: Extensions the engine names as loadable, from its own string table:
#: ``.bmp``, ``.dds``, ``.jpg``, ``.tga``.
IMAGE_SUFFIXES = (".dds", ".tga", ".bmp", ".jpg")

_NODE_PATTERN = re.compile(r"<(\w+)\s+([^>]*?)/?>", re.S)
_ATTR_PATTERN = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def read_texture_registry(path: str) -> dict:
    """Read the registry into ``lowercased name -> game-relative path``.

    Both the bare filename and the full reference are registered, so a
    lookup works whether the caller has one or the other.
    """
    try:
        with open(path, "rb") as handle:
            text = handle.read().decode("cp1251", errors="replace")
    except OSError as exc:
        logger.info("no texture registry at %s: %s", path, exc)
        return {}

    registry: dict = {}
    for _tag, attributes in _NODE_PATTERN.findall(text):
        values = dict(_ATTR_PATTERN.findall(attributes))
        name = values.get("name") or values.get("Name") or ""
        path = values.get("path") or values.get("Path") or ""

        if name and path:
            registry.setdefault(_key(name), path)
            continue

        # A file that names only one of the two: fall back to whichever
        # value looks like an image, so a differently-shaped registry
        # still yields something rather than nothing.
        reference = next(
            (v for v in values.values() if v.lower().endswith(IMAGE_SUFFIXES)),
            "",
        )
        if reference:
            registry.setdefault(_key(reference), reference)

    logger.info("texture registry: %s entry(ies)", len(registry))
    return registry


def _key(value: str) -> str:
    """Lookup key: the bare filename, lowercased.

    The editor's own log lowercases what it reports — the skin chunk
    holds ``MininAO.dds`` and the log says ``mininao.dds`` — so the
    engine is not case-sensitive here and neither is this.
    """
    return os.path.basename(value.replace("\\", "/")).lower()


def find_texture_registry(game_root: str) -> dict:
    """Read the registry from a game folder, if it has one."""
    if not game_root:
        return {}

    path = os.path.join(game_root, REGISTRY_PATH)
    if os.path.isfile(path):
        return read_texture_registry(path)

    # Case differs between installs; the editor's own path is
    # capitalised and the shipped file may not be.
    folder = os.path.join(game_root, "data", "models")
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.lower() == "modeltextures.xml":
                return read_texture_registry(os.path.join(folder, name))

    logger.info("no %s under %s", REGISTRY_PATH, game_root)
    return {}
