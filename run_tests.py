# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run the test suite. No pytest, no Blender.

``tests/fake_bpy.py`` stands in for Blender, so the whole suite runs in
an ordinary Python 3.10 interpreter::

    python run_tests.py              # everything
    python run_tests.py dynamic      # only modules whose name contains it
    python run_tests.py -q           # failures and the summary only

Each ``tests/test_*.py`` is imported with the repository root and the
tests folder on the path — the layout the test files expect — and every
module-level ``test_*`` callable is called with no arguments. Exit code
is 1 if anything failed, which is what CI reads.

One module (`test_terrain_forensics.py`) needs numpy; without it that
module is reported as skipped and the rest still runs.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.join(ROOT, "tests")


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    quiet = "-q" in argv
    only = next((a for a in argv if not a.startswith("-")), "")

    sys.path.insert(0, ROOT)
    sys.path.insert(0, TESTS)

    passed = failed = 0
    skipped: list[tuple[str, str]] = []
    failures: list[tuple[str, str, str]] = []

    for name in sorted(os.listdir(TESTS)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        if only and only not in name:
            continue
        try:
            module = _load(name[:-3], os.path.join(TESTS, name))
        except ImportError as exc:
            # A missing development-only package, not a broken test.
            skipped.append((name, str(exc)))
            continue
        except Exception:
            failed += 1
            failures.append((name, "<import>", traceback.format_exc()))
            continue
        for attr in sorted(vars(module)):
            if not attr.startswith("test_"):
                continue
            func = getattr(module, attr)
            if not callable(func):
                continue
            try:
                func()
                passed += 1
            except Exception:
                failed += 1
                failures.append((name, attr, traceback.format_exc()))

    for module_name, test, tb in failures:
        print(f"FAIL {module_name}::{test}")
        print(tb)
    for module_name, reason in skipped:
        print(f"SKIP {module_name}: {reason}")
    if not quiet and not failures:
        print(f"{len(skipped)} module(s) skipped" if skipped else "no skips")
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
