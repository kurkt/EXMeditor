# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Map-folder snapshot: preserve everything the SDK doesn't understand.

The problem this solves: a map folder holds ~30 files, of which this
SDK currently parses a handful. A naive export that writes only the
files it understands would produce a folder missing ``grass.xml``,
``normalmap.xml``, ``level.tile``, the lightmap ``.dds`` set, and
everything else — i.e. a broken map, even though every file the SDK
*did* handle was written perfectly.

The strategy instead: on import, remember the source folder; on
export, copy the whole folder verbatim, then overwrite only the files
the SDK actually regenerated. Files the SDK has never even looked at
survive byte-for-byte, because they're copied, not rebuilt.

This is deliberately a *strategy*, not a parser — it costs very little
code and removes an entire class of "did we silently drop something"
risk. It also means adding support for a new format later changes
nothing here: that format simply moves from "copied verbatim" to
"copied then overwritten", with no change to this module.

No ``bpy`` import.
"""

from __future__ import annotations

import os
import shutil

from utils.errors import ErrorContext, ValidationError
from utils.logging import get_logger

logger = get_logger("core.snapshot")


def copy_map_folder(source_dir: str, target_dir: str, *, overwrite: bool = False) -> list[str]:
    """Copy every file from ``source_dir`` into ``target_dir``.

    Only files at the top level are copied — map folders observed so
    far are flat, and recursing would risk pulling in unrelated
    subdirectories if a user points this at something unexpected.

    Exporting **in place** (``target_dir`` == ``source_dir``) is
    supported and is in fact the common workflow — edit a map, save it
    back into the game, launch and test. In that case there is nothing
    to copy (every file is already where it needs to be) and this
    returns an empty list. Callers should back up the files they are
    about to overwrite; see ``backup_files``.

    Parameters
    ----------
    overwrite:
        If ``False`` (default) and ``target_dir`` already contains
        files, raises rather than silently merging into someone else's
        folder. Export operators should pass ``True`` only after the
        user has confirmed the destination.

    Returns
    -------
    The list of filenames copied, for logging/reporting. Empty when
    exporting in place.

    Raises
    ------
    ValidationError
        If ``source_dir`` isn't a directory, or ``target_dir`` is
        non-empty and ``overwrite`` is ``False``.
    """
    if not os.path.isdir(source_dir):
        raise ValidationError(
            "source map folder does not exist",
            context=ErrorContext(extra={"source_dir": source_dir}),
        )

    if is_same_folder(source_dir, target_dir):
        logger.info("Exporting in place — no copy needed")
        return []

    os.makedirs(target_dir, exist_ok=True)
    existing = [n for n in os.listdir(target_dir) if os.path.isfile(os.path.join(target_dir, n))]
    if existing and not overwrite:
        raise ValidationError(
            "export target folder is not empty (pass overwrite=True to proceed)",
            context=ErrorContext(extra={"target_dir": target_dir, "existing_file_count": len(existing)}),
        )

    copied: list[str] = []
    for name in sorted(os.listdir(source_dir)):
        src = os.path.join(source_dir, name)
        if not os.path.isfile(src):
            continue
        shutil.copy2(src, os.path.join(target_dir, name))
        copied.append(name)

    logger.info("Copied %d map files from %s", len(copied), source_dir)
    return copied


def is_same_folder(a: str, b: str) -> bool:
    """True if two paths refer to the same directory.

    Uses ``os.path.samefile`` when both exist (so symlinks, junctions
    and differing-but-equivalent spellings all resolve correctly),
    falling back to a normalized path comparison when one doesn't.
    """
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def contains_folder(outer: str, inner: str) -> bool:
    """True if ``inner`` is somewhere below ``outer``. Equal is False.

    Exists for one check: exporting a map into the folder that holds
    the map folders. The file browser's Accept takes the folder it is
    SHOWING, so one click too few sends the export a level up — the
    whole map gets copied in beside the other maps, the edited files
    are written where nothing reads them, and the map itself is
    untouched. It looks exactly like an export that did nothing.
    """
    if not outer or not inner:
        return False
    a = os.path.normcase(os.path.abspath(outer)).rstrip(os.sep)
    b = os.path.normcase(os.path.abspath(inner)).rstrip(os.sep)
    return b != a and b.startswith(a + os.sep)


def backup_files(folder: str, filenames: set[str]) -> list[str]:
    """Copy each named file in ``folder`` to ``<name>.bak``.

    Used before an in-place export, where the originals would otherwise
    be overwritten with no way back. Files that don't exist yet are
    skipped silently — there's nothing to preserve.

    Returns the list of backup filenames created.
    """
    created: list[str] = []
    for name in sorted(filenames):
        source = os.path.join(folder, name)
        if not os.path.isfile(source):
            continue
        backup = source + ".bak"
        try:
            shutil.copy2(source, backup)
        except OSError as exc:
            raise ValidationError(
                f"could not back up {name} before overwriting it",
                context=ErrorContext(extra={"file": source, "os_error": str(exc)}),
            ) from exc
        created.append(os.path.basename(backup))

    if created:
        logger.info("Backed up %d file(s) before overwriting: %s", len(created), ", ".join(created))
    return created


def verify_preserved(source_dir: str, target_dir: str, regenerated: set[str]) -> list[str]:
    """Check that every file NOT regenerated is byte-identical to the source.

    A safety net for the strategy above: if a file the SDK never
    claimed to write nonetheless differs after export, something
    unintended touched it, and that's worth surfacing rather than
    discovering later in-game.

    Parameters
    ----------
    regenerated:
        Filenames the SDK deliberately rewrote; these are expected to
        differ and are skipped. Compared case-insensitively, since map
        files are referenced in mixed case (see ``formats/exm/ssl.py``).

    Returns
    -------
    Filenames that unexpectedly differ, or are missing from the target.
    Empty list means everything untouched stayed untouched — including
    the in-place export case, where source and target are the same
    folder and there is by definition nothing to compare.
    """
    if is_same_folder(source_dir, target_dir):
        return []

    skip = {name.lower() for name in regenerated}
    problems: list[str] = []

    for name in sorted(os.listdir(source_dir)):
        src = os.path.join(source_dir, name)
        if not os.path.isfile(src) or name.lower() in skip:
            continue
        dst = os.path.join(target_dir, name)
        if not os.path.isfile(dst):
            problems.append(f"{name} (missing from export)")
            continue
        with open(src, "rb") as a, open(dst, "rb") as b:
            if a.read() != b.read():
                problems.append(f"{name} (content changed unexpectedly)")

    return problems
