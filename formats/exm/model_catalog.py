# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Resolve a ``world.xml`` object ``id`` to a ``.gam`` file on disk.

The chain, confirmed end to end on real data::

    world.xml   <Node id="house3" .../>
        |
    servers.xml <AnimatedModelsServer><Item id="house3"
                     file="data\\models\\AnimModels.xml"/></...>
        |
    AnimModels.xml  <model id="house3"
                        file="data\\models\\buildings\\region1\\house3.gam" .../>
        |
    house3.gam

``servers.xml`` acts as an index saying *which catalogue* an id lives
in; the catalogue then gives the actual file. Every entry on the
reference map pointed at the same catalogue, but the indirection is
real and is honoured rather than shortcut, since a map that used a
second catalogue would otherwise silently fail to resolve.

Paths inside these files are game-root-relative with Windows
separators, so resolving one to a real file needs the game root
directory — which the user supplies, since a map folder alone doesn't
reveal it.
"""

from __future__ import annotations

import dataclasses
import os
import re
import xml.etree.ElementTree as ET

from utils.errors import ErrorContext, ParsingError
from utils.logging import get_logger

logger = get_logger("formats.exm.model_catalog")

_ENCODING = "cp1251"


@dataclasses.dataclass
class ModelEntry:
    """One entry from a model catalogue (``AnimModels.xml``)."""

    model_id: str
    file_path: str            # game-root-relative, Windows separators
    shadow: str | None = None
    passable: str | None = None
    use_impostors: str | None = None
    raw_attrs: dict[str, str] = dataclasses.field(default_factory=dict)


class ModelCatalog:
    """Maps object ids to model files, across one or more catalogues."""

    def __init__(self) -> None:
        self._entries: dict[str, ModelEntry] = {}

    def add(self, entry: ModelEntry) -> None:
        self._entries[entry.model_id] = entry

    def get(self, model_id: str) -> ModelEntry | None:
        return self._entries.get(model_id)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._entries

    def entries(self):
        """Every entry, for merging catalogues together."""
        return list(self._entries.values())


def _game_path_to_local(game_path: str, game_root: str) -> str:
    """Turn a game-root-relative Windows path into a local filesystem path."""
    relative = game_path.replace("\\", os.sep).replace("/", os.sep)
    return os.path.join(game_root, relative)


def _resolve_case_insensitive(path: str) -> str | None:
    """Resolve ``path`` walking each component case-insensitively.

    Map data is referenced in mixed case while the shipped files are
    not consistently cased — invisible on Windows, fatal on
    Linux/macOS. This is the same problem already handled for map
    files, applied here to multi-level paths.
    """
    if os.path.isfile(path):
        return path

    current = os.path.dirname(path) or "."
    # Walk from the deepest existing ancestor downwards.
    parts: list[str] = []
    remaining = path
    while remaining and not os.path.isdir(current):
        remaining, tail = os.path.split(remaining)
        if not tail:
            break
        parts.insert(0, tail)
        current = os.path.dirname(remaining) or "."

    current = os.path.dirname(path) or "."
    if not os.path.isdir(current):
        # Rebuild the whole path component by component.
        components = path.replace("\\", os.sep).split(os.sep)
        resolved = components[0] + os.sep if path.startswith(os.sep) else components[0]
        for component in components[1:]:
            if not component:
                continue
            candidate = os.path.join(resolved, component)
            if os.path.exists(candidate):
                resolved = candidate
                continue
            try:
                entries = os.listdir(resolved or ".")
            except OSError:
                return None
            match = next((e for e in entries if e.lower() == component.lower()), None)
            if match is None:
                return None
            resolved = os.path.join(resolved, match)
        return resolved if os.path.isfile(resolved) else None

    target = os.path.basename(path).lower()
    try:
        for entry in os.listdir(current):
            if entry.lower() == target:
                return os.path.join(current, entry)
    except OSError:
        return None
    return None


_ENCODINGS = ("cp1251", "utf-8", "latin-1")

#: Matches a catalogue entry's id/file pair regardless of attribute
#: order, used only when strict XML parsing fails.
_ENTRY_PATTERN = re.compile(
    r"<(?:model|Item)\b[^>]*?\bid\s*=\s*\"([^\"]+)\"[^>]*?\bfile\s*=\s*\"([^\"]+)\"",
    re.IGNORECASE | re.DOTALL,
)
_ENTRY_PATTERN_REVERSED = re.compile(
    r"<(?:model|Item)\b[^>]*?\bfile\s*=\s*\"([^\"]+)\"[^>]*?\bid\s*=\s*\"([^\"]+)\"",
    re.IGNORECASE | re.DOTALL,
)


def _read_text(path: str) -> str:
    """Read a catalogue file, trying the encodings these files use.

    Shipped game XML is mostly windows-1251, but not uniformly, and a
    decode error would otherwise lose the whole catalogue.
    """
    with open(path, "rb") as f:
        raw = f.read()
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1251", errors="replace")


def _extract_entries_leniently(text: str) -> list[tuple[str, str]]:
    """Pull id/file pairs out of a catalogue that isn't valid XML.

    Some shipped catalogues fail a strict parse — an unescaped ``&``,
    a stray byte, or more than one root element is enough. Refusing
    them costs every model they list, which in practice meant whole
    categories (petrol stations, towns, pillboxes, fences) silently
    missing from an import. Scanning for the entries directly recovers
    them without pretending the file is well-formed.
    """
    found: dict[str, str] = {}
    for model_id, file_path in _ENTRY_PATTERN.findall(text):
        found.setdefault(model_id, file_path)
    for file_path, model_id in _ENTRY_PATTERN_REVERSED.findall(text):
        found.setdefault(model_id, file_path)
    return list(found.items())


def read_model_catalog(path: str) -> ModelCatalog:
    """Read an ``AnimModels.xml``-style catalogue.

    Falls back to a lenient scan if the file isn't well-formed XML,
    rather than discarding every entry it contains.
    """
    try:
        text = _read_text(path)
    except OSError as exc:
        raise ParsingError(
            "could not read model catalogue",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc

    catalog = ModelCatalog()
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        entries = _extract_entries_leniently(text)
        if not entries:
            raise ParsingError(
                "model catalogue is not well-formed XML and no entries could be "
                "recovered from it",
                context=ErrorContext(source_file=path, extra={"parse_error": str(exc)}),
            ) from exc
        logger.warning(
            "%s is not well-formed XML; recovered %d entries with a lenient scan",
            os.path.basename(path), len(entries),
        )
        for model_id, file_path in entries:
            catalog.add(ModelEntry(model_id=model_id, file_path=file_path))
        return catalog

    for element in root.iter("model"):
        model_id = element.get("id")
        file_path = element.get("file")
        if not model_id or not file_path:
            continue
        catalog.add(ModelEntry(
            model_id=model_id,
            file_path=file_path,
            shadow=element.get("shadow"),
            passable=element.get("passable"),
            use_impostors=element.get("useImpostors"),
            raw_attrs={
                k: v for k, v in element.attrib.items()
                if k not in ("id", "file", "shadow", "passable", "useImpostors")
            },
        ))
    return catalog


def read_catalogs_from_servers(
    servers_path: str, game_root: str, *, problems: list[str] | None = None,
) -> ModelCatalog:
    """Follow ``servers.xml`` to every model catalogue it names.

    Catalogues that can't be found are logged and skipped rather than
    raising: a map may reference a catalogue that isn't shipped, and
    losing one shouldn't prevent the rest of the models from loading.
    """
    try:
        text = _read_text(servers_path)
    except OSError as exc:
        raise ParsingError(
            "could not read servers.xml",
            context=ErrorContext(source_file=servers_path, extra={"os_error": str(exc)}),
        ) from exc

    #: Only XML files can be model catalogues. A servers index also
    #: points at particle systems (.psys), textures and other assets;
    #: trying to read those as catalogues — and reporting each missing
    #: one — buries the real problems under hundreds of irrelevant
    #: lines.
    catalog_paths: set[str] = set()
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # Same tolerance as the catalogues themselves: a malformed index
        # file would otherwise cost every model it points at.
        logger.warning(
            "%s is not well-formed XML; scanning it leniently",
            os.path.basename(servers_path),
        )
        for match in re.finditer(r'\bfile\s*=\s*"([^"]+)"', text):
            catalog_paths.add(match.group(1))
    else:
        for item in root.iter("Item"):
            file_ref = item.get("file")
            if file_ref:
                catalog_paths.add(file_ref)

    catalog_paths = {p for p in catalog_paths if p.lower().endswith(".xml")}

    combined = ModelCatalog()
    for game_path in sorted(catalog_paths):
        local = _resolve_case_insensitive(_game_path_to_local(game_path, game_root))
        if local is None:
            # Debug, not warning: an index points at decal, light and
            # projector definitions as well as model catalogues, and a
            # missing one of those is not a model problem. The
            # diagnostics report collects them via `problems`, where
            # they can be shown in context instead of shouting in the
            # log during every import.
            logger.debug("catalogue file not found: %s", game_path)
            if problems is not None:
                problems.append(f"catalogue file not found: {game_path}")
            continue
        for entry in read_model_catalog(local).entries():
            combined.add(entry)

    if combined:
        logger.info(
            "%s: %d model entries from %d catalogue file(s)",
            os.path.basename(servers_path), len(combined), len(catalog_paths),
        )
    else:
        # Not a failure: commonservers.xml legitimately indexes
        # particles, lights, decals and music and contains no models at
        # all. Logging it as "0 entries" alongside a real catalogue read
        # like a problem when it isn't one.
        logger.info(
            "%s: no model entries (indexes %d non-model catalogue file(s))",
            os.path.basename(servers_path), len(catalog_paths),
        )
    return combined


def resolve_model_file(entry: ModelEntry, game_root: str) -> str | None:
    """Return the local path of a catalogue entry's ``.gam``, or ``None``."""
    return _resolve_case_insensitive(_game_path_to_local(entry.file_path, game_root))


def normalise_game_root(folder: str) -> str | None:
    """Return the folder that actually contains ``data``, or ``None``.

    Model paths are written relative to the game's install folder, so
    the root must be the folder *containing* ``data``. Pointing it at
    ``GC\\data\\models`` instead — natural, since that is where the
    model files visibly live — makes every path resolve to
    ``GC\\data\\models\\data\\models\\...`` and nothing is found.

    Lives here rather than in the add-on so that every caller
    (importer, diagnostics, any future CLI) applies the same rule.
    Two callers disagreeing about the root produced a diagnostics
    report that contradicted the import it was meant to explain.
    """
    current = os.path.abspath(folder)
    for _ in range(8):  # generous depth limit; game trees are not deeper
        try:
            entries = {name.lower(): name for name in os.listdir(current)}
        except OSError:
            return None
        if "data" in entries and os.path.isdir(os.path.join(current, entries["data"])):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


def resolve_game_relative_path(reference: str, game_root: str) -> str | None:
    """Locate a game-root-relative reference (``data\\maps\\...``) on disk.

    Manifest entries such as ``SERVERS`` and ``STATICSERVERS`` are
    paths from the game's install folder, not from the map folder, and
    routinely point outside it. Case-insensitive, like every other path
    lookup in this SDK.
    """
    return _resolve_case_insensitive(_game_path_to_local(reference, game_root))


def register_model(
    catalog_path: str,
    model_id: str,
    file_path: str,
    *,
    shadow: str = "1",
    windwavy: str = "0",
    trans: str = "0",
    composite: str = "0",
    replace: bool = True,
) -> bool:
    """Add a model to an ``AnimModels.xml`` catalogue.

    Without this a newly written ``.gam`` is invisible: the game finds
    models by id through the catalogue, never by scanning the disk. A
    file alone changes nothing, which is why converting one and
    dropping it in place appeared to do nothing at all.

    Edits the file as text rather than reparsing and rewriting it. The
    catalogue is a thousand-entry file the game ships with, and a
    round trip through ElementTree would reformat every line, lose the
    comments, and turn a one-line change into a diff nobody can
    review. Appending before the closing tag is both smaller and
    safer.

    Returns True if the file was modified.
    """
    text = _read_text(catalog_path)

    existing = re.search(
        rf'<model\b[^>]*\bid\s*=\s*"{re.escape(model_id)}"[^>]*/?>',
        text,
        re.IGNORECASE,
    )
    if existing is not None and not replace:
        return False

    # Attribute set copied from what the game's own editor writes,
    # observed on a model it integrated successfully:
    #   shadow="1" windwavy="0" trans="0" composite="0"
    # Shipped entries carry the first three; the editor adds composite.
    # Writing only `shadow`, as an earlier version did, produced entries
    # unlike anything either the game or its editor creates.
    entry = (
        f'\t<model id="{model_id}" file="{file_path}" shadow="{shadow}" '
        f'windwavy="{windwavy}" trans="{trans}" composite="{composite}" />'
    )

    if existing is not None:
        text = text[: existing.start()] + entry.lstrip("\t") + text[existing.end():]
    else:
        # Insert before the closing root tag, keeping the file's own
        # line ending so the diff stays to one line.
        closing = re.search(r"\n?[ \t]*</[A-Za-z_][\w.-]*>\s*$", text)
        if closing is None:
            raise ParsingError(
                "model catalogue has no closing tag to insert before",
                context=ErrorContext(source_file=catalog_path),
            )
        newline = "\r\n" if "\r\n" in text else "\n"
        text = text[: closing.start()] + newline + entry + text[closing.start():]

    try:
        # newline="" is essential, not tidiness. The text was read
        # from a file that already uses CRLF, and without this Python
        # translates every \n on the way out — turning \r\n into
        # \r\r\n. Every write added another carriage return to every
        # line already in the file: a shipped servers.xml reached 13 of
        # them per line, 34070 CR for 2636 LF, and the game's editor
        # stopped loading it and silently fell back to another map's
        # catalogue.
        with open(
            catalog_path, "w", encoding="cp1251", errors="replace", newline=""
        ) as handle:
            handle.write(text)
    except OSError as exc:
        raise ParsingError(
            "could not write the model catalogue",
            context=ErrorContext(source_file=catalog_path, extra={"os_error": str(exc)}),
        ) from exc
    return True


def register_in_servers(
    servers_path: str,
    model_id: str,
    catalogue_ref: str,
) -> bool:
    """List a model in a map's ``servers.xml``.

    Registering in ``AnimModels.xml`` alone is not enough, which took a
    while to establish. That file is the full catalogue — 1036 entries
    on the reference install — while ``servers.xml`` names the 814 a
    given map actually loads. Every one of the 114 models the reference
    map places appears there, without exception, so a model missing
    from it is a model the game never loads no matter how correctly it
    is catalogued.

    Edited as text for the same reason as the catalogue: a round trip
    through ElementTree would reformat an 800-entry file the game
    ships, turning a one-line addition into an unreviewable diff.

    Returns True if the file was modified.
    """
    text = _read_text(servers_path)

    if re.search(
        rf'<Item\b[^>]*\bid\s*=\s*"{re.escape(model_id)}"', text, re.IGNORECASE,
    ):
        return False

    section = re.search(
        r"(</AnimatedModelsServer>)", text, re.IGNORECASE,
    )
    if section is None:
        raise ParsingError(
            "servers.xml has no <AnimatedModelsServer> section to add to",
            context=ErrorContext(source_file=servers_path),
        )

    newline = "\r\n" if "\r\n" in text else "\n"
    entry = f'\t\t<Item{newline}\t\tid="{model_id}"{newline}\t\tfile="{catalogue_ref}" />{newline}\t'
    text = text[: section.start()] + entry + text[section.start():]

    try:
        # newline="": see the note in the catalogue writer above. The
        # same defect corrupted this file too.
        with open(
            servers_path, "w", encoding="cp1251", errors="replace", newline=""
        ) as handle:
            handle.write(text)
    except OSError as exc:
        raise ParsingError(
            "could not write servers.xml",
            context=ErrorContext(source_file=servers_path, extra={"os_error": str(exc)}),
        ) from exc
    return True
