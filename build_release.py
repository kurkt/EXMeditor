# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build the install archive: ``EXMeditor-<version>.zip``.

The source repository and the install archive are different things.
The repository holds everything — the add-on, its tests, the research
tools it was built with, the forensics scripts, the developer
documentation. The archive holds what a user installs into Blender:
the add-on and the documents that state what it is and under which
terms. The policy lives here as code, and ``tests/test_release_archive.py``
holds it to its word:

* **In:** the add-on's Python packages and ``__init__.py``; ``README.md``,
  ``CHANGELOG.md``, ``THIRD_PARTY.md`` and ``LICENSE``.
* **Out:** ``addon/research/`` and the ``core``/``blender_io`` modules
  only it uses (``RESEARCH_ONLY_MODULES``); ``tests/``; ``reverse/``;
  the developer documentation; the repository's own metadata
  (``.github/``, ``.gitignore``, ``.gitattributes``, ``run_tests.py``,
  this script); ``__pycache__`` and ``*.pyc``; editor and build
  leftovers. The add-on's ``__init__`` registers the research tools
  only when their package is present, so the archive loads without
  them.
* **Refused:** any game file. Ex Machina / Hard Truck Apocalypse data
  remains the property of its rights holders; a stray ``.gam`` or
  ``world.xml`` in the tree is a mistake to fix, so the build stops
  rather than quietly skipping it.

The archive unpacks to a single ``EXMeditor/`` folder, which is what
Blender's *Install from file* expects. Run from anywhere::

    python build_release.py            # writes next to the repository
    python build_release.py --out DIR  # writes into DIR

No Blender needed: the version is read from ``bl_info`` textually.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PACKAGE_NAME = "EXMeditor"

#: The documents a user gets. Everything else in Markdown is for
#: whoever works on the source.
SHIPPED_DOCS = frozenset({"README.md", "CHANGELOG.md", "THIRD_PARTY.md", "LICENSE"})

#: Folders that never enter the archive (matched by name, any depth).
EXCLUDED_DIRS = frozenset({"__pycache__", ".git", ".github", ".vscode", ".idea",
                           ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist",
                           "samples", "gamedata"})

#: Top-level folders that are developer material, not add-on code.
EXCLUDED_TOP_DIRS = frozenset({"reverse", "tests"})

#: Sub-packages that stay in the repository.
EXCLUDED_PACKAGE_DIRS = frozenset({os.path.join("addon", "research")})

#: Modules only the research tools import, measured from the import
#: graph (see tests/test_release_archive.py, which re-measures it: an
#: everyday module that starts importing one of these fails the suite).
RESEARCH_ONLY_MODULES = frozenset({
    os.path.join("blender_io", "coverage_bridge.py"),
    os.path.join("blender_io", "diagnostics_bridge.py"),
    os.path.join("blender_io", "mesh_inspector.py"),
    os.path.join("blender_io", "scene_audit.py"),
    os.path.join("core", "census.py"),
    os.path.join("core", "coverage.py"),
    os.path.join("core", "format_census.py"),
    os.path.join("core", "gam_forensics.py"),
    os.path.join("core", "geometry_audit.py"),
    os.path.join("core", "registration_audit.py"),
})

#: Files that never enter the archive.
EXCLUDED_FILES = frozenset({"build_release.py", "run_tests.py", ".gitignore",
                            ".gitattributes", ".DS_Store"})
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".zip", ".blend", ".blend1", ".log", ".swp", ".orig", ".bak")

#: Game data. Mirrors the "GAME DATA" block of .gitignore; a file with
#: one of these names or suffixes stops the build.
GAME_SUFFIXES = (".gam", ".sam", ".dds", ".tga", ".raw", ".bin", ".ssl")
GAME_FILENAMES = frozenset({
    "world.xml", "dynamicscene.xml", "servers.xml", "commonservers.xml",
    "animmodels.xml", "level.tile", "player_passmap.bin",
})


class GameDataInTree(RuntimeError):
    """A file that belongs to the game was found where the add-on lives."""


def is_game_file(name: str) -> bool:
    lower = name.lower()
    return lower in GAME_FILENAMES or lower.endswith(GAME_SUFFIXES)


def version_from_bl_info(root: str = ROOT) -> str:
    """``"0.52.0"`` from the ``bl_info`` dict in ``__init__.py``, read as text."""
    with open(os.path.join(root, "__init__.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "bl_info" for t in node.targets
        ):
            info = ast.literal_eval(node.value)
            return ".".join(str(part) for part in info["version"])
    raise LookupError("bl_info not found in __init__.py")


def release_members(root: str = ROOT) -> list[tuple[str, str]]:
    """``(absolute path, archive name)`` for every file that ships.

    Sorted, so two builds of the same tree produce the same listing.
    Raises :class:`GameDataInTree` before returning anything if a game
    file is present anywhere that would otherwise be packaged.
    """
    members: list[tuple[str, str]] = []
    offenders: list[str] = []
    for folder, dirs, files in os.walk(root):
        rel_folder = os.path.relpath(folder, root)
        dirs[:] = sorted(
            d for d in dirs
            if d not in EXCLUDED_DIRS
            and not (rel_folder == "." and d in EXCLUDED_TOP_DIRS)
            and os.path.normpath(os.path.join(rel_folder, d)) not in EXCLUDED_PACKAGE_DIRS
        )
        for name in sorted(files):
            rel = os.path.normpath(os.path.join(rel_folder, name)) if rel_folder != "." else name
            if is_game_file(name):
                offenders.append(rel)
                continue
            if name in EXCLUDED_FILES or name.lower().endswith(EXCLUDED_SUFFIXES):
                continue
            if rel in RESEARCH_ONLY_MODULES:
                continue
            if not name.endswith(".py") and rel not in SHIPPED_DOCS:
                continue  # developer documentation stays in the repository
            arcname = PACKAGE_NAME + "/" + rel.replace(os.sep, "/")
            members.append((os.path.join(folder, name), arcname))
    if offenders:
        raise GameDataInTree(
            "game data must never be packaged; remove from the tree: " + ", ".join(offenders)
        )
    return members


def build(out_dir: str, root: str = ROOT) -> str:
    """Write the archive and return its path."""
    version = version_from_bl_info(root)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{PACKAGE_NAME}-{version}.zip")
    members = release_members(root)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for full, arcname in members:
            archive.write(full, arcname)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=os.path.dirname(ROOT),
                        help="folder to write the archive into (default: next to the repository)")
    args = parser.parse_args(argv)
    try:
        path = build(args.out)
    except GameDataInTree as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    count = len(release_members())
    print(f"{path}: {count} files, {os.path.getsize(path)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
