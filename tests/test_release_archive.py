# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The install archive holds the add-on and the documents that state
what it is — not the tests, not the research tools, not the developer
notes, and never a game file.

`build_release.py` is the packaging policy as code; these tests are the
checklist it is held to. The two that matter most: the archive is
closed under import (nothing in it imports something left out), and
the add-on registers from the extracted archive without the research
package present.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_release  # noqa: E402

ROOT = build_release.ROOT
TESTS = os.path.dirname(os.path.abspath(__file__))

MUST_SHIP = (
    "__init__.py", "LICENSE", "README.md", "THIRD_PARTY.md", "CHANGELOG.md",
    "addon/__init__.py", "addon/panels.py", "addon/preferences.py",
    "addon/operators.py", "addon/doctor_operator.py", "addon/asset_operator.py",
    "blender_io/scene_bridge.py", "core/dds.py", "core/diagnostics.py",
    "formats/exm/gam.py", "utils/math.py",
)

MUST_NOT_SHIP_PREFIXES = ("tests/", "reverse/", "addon/research/", ".github/")
MUST_NOT_SHIP = (
    "build_release.py", "run_tests.py", ".gitignore", ".gitattributes",
    # developer documentation
    "TECHNICAL_CONTEXT.md", "ARCHITECTURE.md", "Editor_Experiment_Findings.md",
    "SDK_STATUS.md", "TEXTURE_CONTEXT.md", "ROADMAP.md", "CONTRIBUTING.md",
    # modules only the research tools use
    "core/census.py", "core/coverage.py", "core/format_census.py",
    "core/gam_forensics.py", "core/geometry_audit.py", "core/registration_audit.py",
    "blender_io/coverage_bridge.py", "blender_io/diagnostics_bridge.py",
    "blender_io/mesh_inspector.py", "blender_io/scene_audit.py",
)


def _arcnames() -> set[str]:
    return {arc for _full, arc in build_release.release_members()}


def test_the_add_on_and_its_licence_documents_ship() -> None:
    names = _arcnames()
    for rel in MUST_SHIP:
        assert f"EXMeditor/{rel}" in names, rel


def test_tests_research_and_developer_material_stay_in_the_repository() -> None:
    names = _arcnames()
    for arc in names:
        rel = arc[len("EXMeditor/"):]
        assert not rel.startswith(MUST_NOT_SHIP_PREFIXES), arc
        assert rel not in MUST_NOT_SHIP, arc
        assert "__pycache__" not in arc and not arc.endswith((".pyc", ".zip", ".log")), arc
    # And they do exist in the repository — this test is about the
    # archive, not about deleting them.
    for rel in ("tests/fake_bpy.py", "addon/research/__init__.py", "reverse/inspect_binary.py",
                "ROADMAP.md", "CONTRIBUTING.md", "core/census.py", "run_tests.py",
                ".gitattributes", ".github/workflows/tests.yml"):
        assert os.path.exists(os.path.join(ROOT, rel)), rel


def test_everything_unpacks_into_one_folder_named_for_the_add_on() -> None:
    """Blender's *Install from file* wants exactly this shape."""
    for _full, arc in build_release.release_members():
        assert arc.startswith("EXMeditor/") and "\\" not in arc, arc


def test_the_listing_is_deterministic() -> None:
    assert build_release.release_members() == build_release.release_members()


def test_version_comes_from_bl_info() -> None:
    version = build_release.version_from_bl_info()
    assert version.count(".") == 2 and all(part.isdigit() for part in version.split("."))
    init = open(os.path.join(ROOT, "__init__.py"), encoding="utf-8").read()
    assert '"version": (' + ", ".join(version.split(".")) + ")" in init


# --- closure under import ---------------------------------------------

_OWN = ("addon", "blender_io", "core", "formats", "utils")


def _own_imports(path: str) -> set[str]:
    """Dotted names of this add-on's modules that ``path`` imports."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.split(".")[0] in _OWN)
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in _OWN:
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


def _module_of(arc: str) -> str:
    rel = arc[len("EXMeditor/"):-3]
    return rel[:-9] if rel.endswith("/__init__") else rel.replace("/", ".")


def test_the_archive_is_closed_under_import() -> None:
    """Every module a shipped module imports is itself shipped.

    This is what makes RESEARCH_ONLY_MODULES safe to leave out: the
    day an everyday operator starts importing ``core.census``, this
    fails and the module has to be either shipped or not imported.
    """
    members = build_release.release_members()
    shipped = {_module_of(arc) for _full, arc in members if arc.endswith(".py")}
    shipped |= {"addon", "blender_io", "core", "formats", "formats.exm", "utils"}
    broken = []
    for full, arc in members:
        if not arc.endswith(".py"):
            continue
        for name in _own_imports(full):
            # `from core.mesh import Model`: `core.mesh.Model` is not a
            # module — accept a name whose parent module is shipped.
            if name in shipped or name.rsplit(".", 1)[0] in shipped:
                continue
            broken.append((arc, name))
    assert not broken, broken


def test_research_only_modules_are_really_only_used_by_research() -> None:
    """Re-measure the claim the exclusion list makes."""
    excluded = {p.replace(os.sep, "/")[:-3].replace("/", ".") for p in build_release.RESEARCH_ONLY_MODULES}
    for folder in ("addon", "blender_io", "core", "formats", "utils"):
        for dirpath, dirs, names in os.walk(os.path.join(ROOT, folder)):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "research")]
            for name in names:
                if not name.endswith(".py"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), ROOT).replace(os.sep, "/")
                if rel[:-3].replace("/", ".") in excluded:
                    continue
                users = _own_imports(os.path.join(dirpath, name))
                hit = {u for u in users if u in excluded or u.rsplit(".", 1)[0] in excluded}
                assert not hit, (rel, hit)


# --- the archive itself ------------------------------------------------


def _copy_tree_with(extra: dict[str, bytes]) -> str:
    """A scratch copy of the repository with extra files planted."""
    tmp = tempfile.mkdtemp(prefix="exm_release_")
    root = os.path.join(tmp, "EXMeditor")
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    for rel, data in extra.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    return root


def test_a_game_file_anywhere_in_the_tree_stops_the_build() -> None:
    """Refused, not skipped: game data in the tree is a mistake to fix."""
    for planted in ("samples_of_mine/barrel1.gam", "core/world.xml", "addon/t.dds",
                    "formats/exm/anim.sam", "docs/level.tile"):
        root = _copy_tree_with({planted: b"\0" * 16})
        try:
            try:
                build_release.release_members(root)
            except build_release.GameDataInTree as exc:
                assert planted.replace("/", os.sep) in str(exc), (planted, exc)
            else:
                raise AssertionError(f"{planted} was packaged")
        finally:
            shutil.rmtree(os.path.dirname(root), ignore_errors=True)


_REGISTER_FROM_ARCHIVE = r"""
import os, sys
sys.path.insert(0, sys.argv[1])          # the source tests/ dir, for fake_bpy
import fake_bpy
fake_bpy.install()
sys.path.insert(0, sys.argv[2])          # the folder the archive unpacked into
import EXMeditor
assert EXMeditor.research is None, "research package found in the archive"
from addon.preferences import research_tools_installed, show_research_tools
assert research_tools_installed() is False
import bpy
assert show_research_tools(bpy.context) is False
EXMeditor.register()
import addon.panels as panels
assert panels.EXM_PT_main_panel.is_registered if hasattr(panels.EXM_PT_main_panel, "is_registered") else True
EXMeditor.unregister()
print("REGISTERED-OK")
"""


def test_the_archive_builds_extracts_and_registers_without_the_research_tools() -> None:
    """End to end: what a user installs is what is tested here."""
    out = tempfile.mkdtemp(prefix="exm_zip_")
    try:
        path = build_release.build(out)
        assert os.path.basename(path) == f"EXMeditor-{build_release.version_from_bl_info()}.zip"
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None
            names = set(archive.namelist())
            unpacked = os.path.join(out, "unpacked")
            archive.extractall(unpacked)
        assert names == _arcnames()
        assert not os.path.exists(os.path.join(unpacked, "EXMeditor", "addon", "research"))
        result = subprocess.run(
            [sys.executable, "-c", _REGISTER_FROM_ARCHIVE, TESTS, unpacked],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0 and "REGISTERED-OK" in result.stdout, (
            result.stdout[-2000:], result.stderr[-4000:]
        )
    finally:
        shutil.rmtree(out, ignore_errors=True)
