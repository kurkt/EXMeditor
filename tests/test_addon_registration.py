# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests that the add-on can actually be enabled.

Every failure this guards against is total: Blender refuses to load the
add-on at all and every feature disappears at once. It has happened —
a new module was written over an existing one of the same name, and
the import in ``__init__`` then named something that no longer existed.

These are cheap and they run without Blender.
"""

from __future__ import annotations

import ast
import importlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _imported_modules() -> list[str]:
    """The names ``__init__.py`` imports from ``addon``."""
    source = open(os.path.join(ROOT, "__init__.py")).read()
    block = re.search(r"from addon import \(([^)]*)\)", source, re.S)
    assert block, "__init__.py no longer imports from addon as a block"
    return re.findall(r"^\s*(\w+),\s*$", block.group(1), re.M)


def test_every_imported_module_exists_on_disk() -> None:
    for name in _imported_modules():
        path = os.path.join(ROOT, "addon", f"{name}.py")
        assert os.path.isfile(path), f"__init__ imports {name}, which is not there"


def test_every_addon_module_actually_imports() -> None:
    """Import them all, then put the fake Blender back as it was.

    Importing an add-on module can bind or replace things on ``bpy``,
    and every other test in the suite shares that one object. Restoring
    it keeps this test from deciding whether later ones pass.
    """
    import bpy

    before = {
        name: getattr(bpy.data, name)
        for name in dir(bpy.data)
        if not name.startswith("_")
    }
    try:
        for name in _imported_modules():
            importlib.import_module(f"addon.{name}")
    finally:
        for name, value in before.items():
            setattr(bpy.data, name, value)


def test_every_module_registers_and_unregisters_exactly_once() -> None:
    """Twice would raise on enable; never would make the feature invisible."""
    source = open(os.path.join(ROOT, "__init__.py")).read()
    for name in _imported_modules():
        # The lookbehind keeps `format_census_operator.register()` from
        # counting as `census_operator.register()`.
        registers = re.findall(r"(?<![\w.])" + name + r"\.register\(\)", source)
        unregisters = re.findall(r"(?<![\w.])" + name + r"\.unregister\(\)", source)
        assert len(registers) == 1, (name, len(registers))
        assert len(unregisters) == 1, (name, len(unregisters))


def _addon_sources() -> list[str]:
    """Every .py under addon/, the research sub-package included."""
    found = []
    for dirpath, dirs, names in os.walk(os.path.join(ROOT, "addon")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        found.extend(os.path.join(dirpath, n) for n in names if n.endswith(".py"))
    return sorted(found)


def test_operator_ids_are_unique() -> None:
    """Two operators sharing a bl_idname: the second replaces the first."""
    seen: dict[str, str] = {}
    for path in _addon_sources():
        filename = os.path.relpath(path, ROOT)
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if getattr(target, "id", None) != "bl_idname":
                    continue
                if not isinstance(node.value, ast.Constant):
                    continue
                identifier = node.value.value
                assert identifier not in seen, (
                    f"{identifier} is declared in both {seen[identifier]} "
                    f"and {filename}"
                )
                seen[identifier] = filename


def test_every_panel_button_names_a_real_operator() -> None:
    """A panel calling an operator that does not exist draws a dead button."""
    wanted = set()
    for rel in (("addon", "panels.py"), ("addon", "research", "panel.py")):
        panel = open(os.path.join(ROOT, *rel), encoding="utf-8").read()
        wanted |= set(re.findall(r'operator\(\s*"([\w.]+)"', panel))

    declared = set()
    for path in _addon_sources():
        declared.update(
            re.findall(r'bl_idname\s*=\s*"([\w.]+)"', open(path, encoding="utf-8").read())
        )

    missing = {name for name in wanted if name.startswith("exmachina.")} - declared
    assert not missing, f"panels call operators that do not exist: {missing}"


def test_the_research_package_registers_every_module_it_holds() -> None:
    """The sub-package has its own register list; a module dropped in
    without being listed is a dead operator."""
    folder = os.path.join(ROOT, "addon", "research")
    source = open(os.path.join(folder, "__init__.py"), encoding="utf-8").read()
    for filename in sorted(os.listdir(folder)):
        if filename.endswith(".py") and filename != "__init__.py":
            name = filename[:-3]
            assert re.search(r"^    " + name + r",$", source, re.M), f"{name} not in _MODULES"
            importlib.import_module(f"addon.research.{name}")
