# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The release shape: EXMeditor by name, research tools behind a
preference, no stale text where the user reads.

Cheap source-level checks, so a future edit that quietly brings the
old name or the research buttons back into the everyday panel is
caught by the suite rather than by a screenshot.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_the_add_on_is_named_exmeditor() -> None:
    init = _read("__init__.py")
    assert '"name": "EXMeditor"' in init
    assert "terrain import" not in init, "the MVP-era description"
    assert "milestone" not in init.lower()
    assert os.path.basename(ROOT) == "EXMeditor", "the folder is what Blender loads it as"
    from addon.preferences import ADDON_PACKAGE_NAME

    assert ADDON_PACKAGE_NAME == "EXMeditor"


def test_the_old_name_is_gone_from_what_the_user_reads() -> None:
    for rel in ("README.md", "SDK_STATUS.md"):
        text = _read(rel)
        # The one permitted mention: telling the user to remove the old install.
        stray = [
            line for line in text.splitlines()
            if "ExMachina SDK" in line and "remove" not in line.lower()
            and "earlier build" not in line.lower()
        ]
        assert not stray, (rel, stray)
    # In shipped code the old name survives in exactly two places: the
    # constant that names the install to retire, and the alias that
    # keeps `ExMachinaSDKError` importable for code written against it.
    allowed = {
        "__init__.py": '_PREVIOUS_NAME = "ExMachinaSDK"',
        os.path.join("utils", "errors.py"): "ExMachinaSDKError = EXMeditorError",
    }
    for rel in _every_source_file():
        if rel.startswith(("tests" + os.sep, "reverse" + os.sep)):
            continue
        text = _read(rel)
        assert "ExMachina SDK" not in text, rel
        text = text.replace(allowed.get(rel, "\0"), "")
        assert not re.search(r"ExMachinaSDK", text), rel
    from utils.errors import EXMeditorError, ExMachinaSDKError

    assert ExMachinaSDKError is EXMeditorError
    assert EXMeditorError.__name__ == "EXMeditorError", "what a traceback shows"


def test_research_tools_are_a_separate_gated_panel() -> None:
    """In a source checkout the panel exists, under the main one, off
    by default. (That the install archive has no such panel at all is
    test_release_archive's business.)"""
    from addon import panels
    from addon.preferences import EXM_AddonPreferences, research_tools_installed, show_research_tools
    from addon.research import panel as research_panel

    assert research_tools_installed() is True
    main = panels.EXM_PT_main_panel
    research = research_panel.EXM_PT_research_panel
    assert research.bl_parent_id == main.bl_idname
    assert "DEFAULT_CLOSED" in research.bl_options
    assert research not in panels._CLASSES

    class _Prefs:
        show_research_tools = False

    class _Context:
        class preferences:
            addons = {"EXMeditor": type("E", (), {"preferences": _Prefs()})()}

    assert research.poll(_Context()) is False
    assert show_research_tools(_Context()) is False
    _Prefs.show_research_tools = True
    assert research.poll(_Context()) is True
    # The preference itself defaults to off, and is drawn only when
    # the package is there to be switched on.
    source = _read("addon", "preferences.py")
    block = source[source.index("show_research_tools: bpy.props.BoolProperty("):]
    block = block[:block.index("\n    )\n")]
    assert "default=False" in block
    draw = source[source.index("def draw(self, context)"):]
    assert 'if research_tools_installed():\n            box.prop(self, "show_research_tools")' in draw
    assert EXM_AddonPreferences is not None


def test_the_everyday_panel_holds_no_research_buttons() -> None:
    everyday = _read("addon", "panels.py")
    research = _read("addon", "research", "panel.py")
    for research_only in (
        "mesh_inspector", "model_forensics", "format_census", "registration_audit",
        "diagnose_models", "analyze_scene", "coverage_report", "world_census",
    ):
        assert research_only not in everyday, research_only
        assert research_only in research, research_only
    for everyday_tool in (
        "import_map", "export_map", "create_map", "assign_node", "clear_node",
        "replace_model", "create_model", "model_doctor", "build_asset_previews",
        "clear_asset_previews", "browse_assets", "edit_texture", "fork_texture",
        "save_texture", "save_all_textures", "validate_map",
    ):
        assert everyday_tool in everyday, everyday_tool


def test_the_add_on_registers_research_only_by_presence() -> None:
    """`__init__` must not catch ImportError around the research
    package: a broken package would then look like an absent one."""
    init = _read("__init__.py")
    assert "if research_tools_installed():\n    from addon import research" in init
    assert "except ImportError" not in init
    body = init[init.index("def register() -> None:"):]
    assert "if research is not None:\n        research.register()" in body
    assert "if research is not None:\n        research.unregister()" in body


def test_labels_fit_the_sidebar() -> None:
    """Blender's default sidebar clips a button label past ~26
    characters; 'Fork Texture For This Mo…' was the one that did."""
    from addon import assign_operator, fork_texture_operator

    assert fork_texture_operator.EXM_OT_fork_texture.bl_label == "Fork Texture"
    assert assign_operator.EXM_OT_assign_node.bl_label == "Assign Map Object"
    assert assign_operator.EXM_OT_clear_node.bl_label == "Clear Map Object"


def test_the_dead_ui_module_is_gone() -> None:
    assert not os.path.exists(os.path.join(ROOT, "addon", "ui.py"))


def test_readme_user_part_names_nothing_the_archive_lacks() -> None:
    """One README for the repository and the archive. A reader of the
    archive gets the user sections; the developer part says once that
    it refers to the repository. Repository-only files may appear in
    the user part only next to the word "source"."""
    import build_release

    readme = _read("README.md")
    marker = "## For developers"
    assert marker in readme
    user_part, dev_part = readme.split(marker, 1)
    user_lines = user_part.splitlines()
    for name in ("TECHNICAL_CONTEXT.md", "ARCHITECTURE.md", "Editor_Experiment_Findings.md",
                 "TEXTURE_CONTEXT.md", "tests/", "reverse/", "fake_bpy", ".gitignore",
                 "addon/research", "Show Research Tools"):
        assert name not in user_part, name
    for name in ("SDK_STATUS.md", "ROADMAP.md", "CONTRIBUTING.md", "build_release.py"):
        for i, line in enumerate(user_lines):
            if name in line:
                window = " ".join(user_lines[max(0, i - 2): i + 3])
                assert "source" in window, (name, line)
    # The developer part states what the archive carries; that list is
    # the builder's, not a remembered one.
    stated = dev_part[:dev_part.index("###")]
    for doc in sorted(build_release.SHIPPED_DOCS - {"README.md"}):
        assert f"`{doc}`" in stated, doc
    assert "this README" in stated


def test_the_third_party_dependency_is_credited_at_its_real_home() -> None:
    """The README once linked a fork URL that answers 404. The add-on
    the SDK drives is ThePlain's (MIT); say so, and say it is not
    bundled — the borrowing audit of 2026-09-20 confirmed no code of
    it is copied here."""
    readme = _read("README.md")
    assert "https://github.com/ThePlain/HTAToolchain" in readme
    assert "Kaesar-Jones" not in readme
    assert "MIT" in readme and "not bundled" in readme


#: REUSE-style header every source file starts with: one or more
#: copyright lines — whoever wrote the file — then the licence, which
#: is the same for the whole project.
SPDX_COPYRIGHT_PREFIX = "# SPDX-FileCopyrightText: "
SPDX_LICENCE_LINE = "# SPDX-License-Identifier: GPL-3.0-or-later"

#: Phrases every common licence text contains. A vendored file from
#: another project brings its header along; none belongs in this tree.
_FOREIGN_LICENCE_PHRASES = (
    "Permission is hereby granted",
    "GNU General Public License",
    "Redistribution and use in source and binary forms",
    "Licensed under the Apache License",
    "All rights reserved",
)


def _every_source_file():
    for dirpath, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for name in sorted(names):
            if name.endswith(".py"):
                yield os.path.relpath(os.path.join(dirpath, name), ROOT)


def _has_spdx_header(text: str) -> bool:
    lines = text.replace("\r\n", "\n").split("\n")
    holders = 0
    while holders < len(lines) and lines[holders].startswith(SPDX_COPYRIGHT_PREFIX):
        if len(lines[holders]) <= len(SPDX_COPYRIGHT_PREFIX):
            return False  # a copyright line with nobody on it
        holders += 1
    return holders >= 1 and holders < len(lines) and lines[holders] == SPDX_LICENCE_LINE


def test_every_source_file_carries_the_spdx_header() -> None:
    """Including empty package markers and the tests: the header is
    what says who holds the copyright and under what terms. Any
    holder — a contributor's file names the contributor, not the
    project's founder."""
    bare = [rel for rel in _every_source_file() if not _has_spdx_header(_read(rel))]
    assert not bare, bare
    # The rule as stated, checked against itself.
    assert _has_spdx_header(
        "# SPDX-FileCopyrightText: 2026 Kurkt\n# SPDX-FileCopyrightText: 2027 Someone Else\n"
        "# SPDX-License-Identifier: GPL-3.0-or-later\n\nx = 1\n"
    )
    assert not _has_spdx_header("# SPDX-License-Identifier: GPL-3.0-or-later\nx = 1\n")
    assert not _has_spdx_header("# SPDX-FileCopyrightText: 2026 Kurkt\n# SPDX-License-Identifier: MIT\n")
    assert not _has_spdx_header('"""doc"""\n# SPDX-FileCopyrightText: 2026 Kurkt\n')


def test_no_third_party_licence_text_is_vendored() -> None:
    this_file = os.path.relpath(os.path.abspath(__file__), ROOT)
    for rel in _every_source_file():
        if rel == this_file:
            continue  # quotes the phrases it hunts for
        text = _read(rel)
        # Any SPDX identifier other than ours marks a file from elsewhere.
        for line in text.splitlines():
            if "SPDX-License-Identifier:" in line:
                assert line.strip() == "# SPDX-License-Identifier: GPL-3.0-or-later", (rel, line)
        for phrase in _FOREIGN_LICENCE_PHRASES:
            assert phrase not in text, (rel, phrase)
    # And no file at all that is not ours: source, documentation, the
    # licence, and the repository's own metadata. Anything else — a
    # binary, an archive, a stray asset — has to be accounted for here
    # before it can sit in the tree.
    metadata = {".gitignore", ".gitattributes", "LICENSE"}
    for dirpath, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "dist")]
        for name in names:
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
            if rel.startswith(".github" + os.sep):
                assert name.endswith((".yml", ".yaml", ".md")), rel
                continue
            assert name.endswith((".py", ".md")) or name in metadata, rel


def test_license_file_is_the_verbatim_gpl3_text() -> None:
    """The FSF text, unchanged: its own notice says changing it is not
    allowed. The digest is that of gnu.org's gpl-3.0.txt, matched on
    2026-09-20 by four independent copies on the build machine
    (Blender, LibreOffice, Microsoft Edge, MB-Lab)."""
    import hashlib

    with open(os.path.join(ROOT, "LICENSE"), "rb") as fh:
        raw = fh.read()
    assert hashlib.sha256(raw).hexdigest() == (
        "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"
    )
    text = raw.decode("ascii")
    assert text.splitlines()[0].strip() == "GNU GENERAL PUBLIC LICENSE"
    assert "Version 3, 29 June 2007" in text
    assert "Copyright (C) 2007 Free Software Foundation, Inc." in text
    assert len(text.splitlines()) == 674


def test_the_licence_is_stated_where_the_user_reads() -> None:
    readme = _read("README.md")
    assert "GPL-3.0-or-later" in readme
    assert "Kurkt" in readme
    init = _read("__init__.py")
    assert '"author": "Kurkt"' in init
    assert "GPL-3.0-or-later" in _read("THIRD_PARTY.md")


_SHIPPED = ("__init__.py", "addon", "blender_io", "core", "formats", "utils")
_OWN_PACKAGES = {"addon", "blender_io", "core", "formats", "utils"}
_BLENDER_API = {"bpy", "bmesh", "mathutils", "addon_utils", "bpy_extras", "gpu", "blf"}


def _imports_of(path: str) -> set[str]:
    import ast

    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0])
    return found


def _shipped_imports() -> set[str]:
    found = set()
    for entry in _SHIPPED:
        full = os.path.join(ROOT, entry)
        if os.path.isfile(full):
            found |= _imports_of(full)
            continue
        for dirpath, dirs, names in os.walk(full):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in names:
                if name.endswith(".py"):
                    found |= _imports_of(os.path.join(dirpath, name))
    return found


def test_shipped_code_imports_only_stdlib_and_blender() -> None:
    """No third-party package may enter the release unnoticed: the
    add-on has no requirements.txt because it needs nothing, and
    THIRD_PARTY.md says so. Blender 3.6 runs Python 3.10, whose
    ``sys.stdlib_module_names`` is the reference."""
    stdlib = set(sys.stdlib_module_names)
    foreign = {
        name for name in _shipped_imports()
        if name not in stdlib and name not in _OWN_PACKAGES and name not in _BLENDER_API
    }
    assert not foreign, f"third-party imports in shipped code: {sorted(foreign)}"


def test_third_party_md_lists_every_stdlib_module_the_add_on_uses() -> None:
    """The list in THIRD_PARTY.md is measured, not remembered: adding
    an import means adding it there too."""
    used = sorted(name for name in _shipped_imports() if name in set(sys.stdlib_module_names))
    third_party = _read("THIRD_PARTY.md")
    missing = [name for name in used if f"`{name}`" not in third_party]
    assert not missing, missing
    assert "bundles no third-party code" in third_party
    assert "https://github.com/ThePlain/HTAToolchain" in third_party


def test_an_older_install_under_the_previous_name_is_retired() -> None:
    init = _read("__init__.py")
    assert '_PREVIOUS_NAME = "ExMachinaSDK"' in init
    assert "_retire_previous_install()" in init
    assert "_evict_foreign_flat_modules()" in init
    # The retirement comes first, before any class of ours registers.
    body = init[init.index("def register() -> None:"):]
    assert body.index("_retire_previous_install()") < body.index("preferences.register()")
