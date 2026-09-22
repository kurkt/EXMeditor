# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""``.ssl`` manifest codec.

Format, confirmed by direct forensic analysis (see the architecture
doc): an Ini-style document wrapped in XML tags
(``<Ini><Section name="..."><Key name="...">value</Key></Section></Ini>``),
``windows-1251`` encoded, ending cleanly with no trailing/hidden data.
This module is the SDK's entry point for map-wide file discovery — see
``core.manifest.FILE_REFERENCE_KEYS``/``FIXED_FILENAME_EXCEPTIONS`` for
the confirmed key list and the confirmed exceptions to it.
"""

from __future__ import annotations

import os

from core.manifest import FILE_REFERENCE_KEYS, FIXED_FILENAME_EXCEPTIONS, LevelManifest
from utils.errors import ErrorContext, ParsingError

_ENCODING = "cp1251"


def _parse_int(value: str, *, key: str, source_file: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ParsingError(
            f"manifest key {key!r} is not a valid integer",
            context=ErrorContext(source_file=source_file, field=key, extra={"value": value}),
        ) from exc


def _parse_float(value: str, *, key: str, source_file: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ParsingError(
            f"manifest key {key!r} is not a valid float",
            context=ErrorContext(source_file=source_file, field=key, extra={"value": value}),
        ) from exc


def read_manifest(path: str) -> LevelManifest:
    """Read a ``.ssl`` file into a ``LevelManifest``.

    Parses the ``LEVEL`` section's ``<Key>`` entries into
    ``LevelManifest.raw_keys`` (every key, unconditionally), and
    additionally into the named convenience fields where recognized.
    Sections other than ``LEVEL`` (``CAMERA``, ``DEMO``,
    ``ILLUMINATION`` — confirmed present in real files, not modeled by
    this SDK yet) are intentionally not parsed; nothing currently needs
    them, and skipping them keeps this function's failure surface
    limited to the section that actually matters for file discovery.

    Raises
    ------
    ParsingError
        If the file isn't valid XML in the expected shape, or a
        recognized numeric key's value doesn't parse as its expected
        type.
    """
    try:
        with open(path, encoding=_ENCODING) as f:
            text = f.read()
    except OSError as exc:
        raise ParsingError(
            "could not read .ssl file",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc

    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ParsingError(
            ".ssl content is not well-formed XML",
            context=ErrorContext(source_file=path, extra={"parse_error": str(exc)}),
        ) from exc

    level_section = None
    for section in root.findall("Section"):
        if section.get("name") == "LEVEL":
            level_section = section
            break
    if level_section is None:
        raise ParsingError(
            ".ssl file has no <Section name=\"LEVEL\"> — not a recognized manifest",
            context=ErrorContext(source_file=path),
        )

    # LEVEL first, then every other section. A manifest is four
    # sections — LEVEL, CAMERA, DEMO, ILLUMINATION — and reading only
    # the first of them lost the whole of the map's lighting COLOUR:
    # MODEL_AMBIENT, MODEL_DIFFUSE, LS_COLOR and LS_DIFFUSE all live in
    # ILLUMINATION while SUN_AZIMUTH and the ascensions live in LEVEL.
    # The importer took the angles and defaulted the colours to white,
    # so a map whose MODEL_AMBIENT is "101 116 44" was lit as if it
    # were grey — and the game's foliage textures, which carry no
    # colour of their own, came out grey with it.
    #
    # Merging is safe: across all 41 shipped manifests no key name
    # appears in two sections. LEVEL is read first anyway, so if one
    # ever does, the section this reader has always used wins.
    raw_keys: dict[str, str] = {}
    for key_elem in level_section.findall("Key"):
        name = key_elem.get("name")
        if name is None:
            continue
        raw_keys[name] = (key_elem.text or "").strip()

    for section in root.iter("Section"):
        if section is level_section:
            continue
        for key_elem in section.findall("Key"):
            name = key_elem.get("name")
            if name is None or name in raw_keys:
                continue
            raw_keys[name] = (key_elem.text or "").strip()

    manifest = LevelManifest(raw_keys=raw_keys)

    if "LEVELSIZE" in raw_keys:
        manifest.level_size = _parse_int(raw_keys["LEVELSIZE"], key="LEVELSIZE", source_file=path)
    if "WATERLEVEL" in raw_keys:
        manifest.water_level = _parse_float(raw_keys["WATERLEVEL"], key="WATERLEVEL", source_file=path)
    if "BASEWATERLEVEL" in raw_keys:
        manifest.base_water_level = _parse_float(raw_keys["BASEWATERLEVEL"], key="BASEWATERLEVEL", source_file=path)
    if "PASSMAPCELLSIZE" in raw_keys:
        manifest.passmap_cell_size = _parse_int(raw_keys["PASSMAPCELLSIZE"], key="PASSMAPCELLSIZE", source_file=path)
    if "MINSAFEX" in raw_keys:
        manifest.min_safe_x = _parse_float(raw_keys["MINSAFEX"], key="MINSAFEX", source_file=path)
    if "MINSAFEY" in raw_keys:
        manifest.min_safe_y = _parse_float(raw_keys["MINSAFEY"], key="MINSAFEY", source_file=path)
    if "MAXSAFEX" in raw_keys:
        manifest.max_safe_x = _parse_float(raw_keys["MAXSAFEX"], key="MAXSAFEX", source_file=path)
    if "MAXSAFEY" in raw_keys:
        manifest.max_safe_y = _parse_float(raw_keys["MAXSAFEY"], key="MAXSAFEY", source_file=path)

    return manifest


def resolve_in_folder(folder: str, filename: str) -> str | None:
    """Find ``filename`` in ``folder``, case-insensitively.

    Necessary, not a nicety: real ``.ssl`` files reference names in
    CamelCase (``LevelRoads.xml``, ``NormalMap.xml``, ``ShoreLine.xml``)
    while the files as shipped are lower-case on disk. The game runs on
    Windows, whose filesystem is case-insensitive, so the mismatch is
    invisible there — but Blender runs on Linux/macOS too, where a
    case-sensitive lookup silently fails to find most of a map's files.
    Confirmed against real map data: 6 of 10 initially-"missing" files
    were case mismatches, not absences.

    Returns the actual on-disk path — in the file's own spelling, even
    on Windows, where ``os.path.isfile`` would accept any case and the
    path would then be wrong the moment it is used on a
    case-sensitive filesystem — or ``None`` if no case-variant exists.
    """
    direct = os.path.join(folder, filename)
    try:
        entries = os.listdir(folder)
    except OSError:
        return direct if os.path.isfile(direct) else None
    if filename in entries and os.path.isfile(direct):
        return direct
    target = filename.lower()
    for entry in entries:
        if entry.lower() == target and os.path.isfile(os.path.join(folder, entry)):
            return os.path.join(folder, entry)
    return None


def scan(folder: str, manifest: LevelManifest) -> dict[str, bool]:
    """Report which of a map's referenced files actually exist in ``folder``.

    Checks every key in ``core.manifest.FILE_REFERENCE_KEYS`` that's
    present in ``manifest.raw_keys`` (existence only — a key with a
    value but a missing file is reported ``False``, not raised), plus
    the confirmed fixed-filename exceptions (``FIXED_FILENAME_EXCEPTIONS``)
    that never appear as a manifest key at all. Paths in the manifest
    may contain backslashes (Windows-style) or be absolute from the
    game root — this function only checks the plain filename relative
    to ``folder``, which is correct for the common per-map-file case
    but will not locate a shared/game-root asset (e.g. ``ROADSET``
    pointing at a catalog file outside the map folder); callers that
    need those should resolve them separately.
    """
    found: dict[str, bool] = {}
    for key in FILE_REFERENCE_KEYS:
        raw_value = manifest.raw_keys.get(key)
        if raw_value is None:
            continue
        filename = os.path.basename(raw_value.replace("\\", "/"))
        found[key] = resolve_in_folder(folder, filename) is not None

    for fixed_name in FIXED_FILENAME_EXCEPTIONS:
        found[fixed_name] = resolve_in_folder(folder, fixed_name) is not None

    return found
