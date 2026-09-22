# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The optional corpus of real game files.

Those files are the game's and are never in this repository, so every
test that wants one skips when it is absent — and the suite must pass
either way. What is checked here is the mechanism: where it looks,
that the environment wins, and that no test addresses the corpus by a
path that exists only on one machine.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpus as corpus_module  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))


@contextlib.contextmanager
def _environment(**values: str | None):
    """Reload the module under a changed environment, then restore it.

    A module object is a singleton: reloading it again to restore the
    default mutates the very object a test is holding, so the
    assertions have to run inside the block rather than after it.
    """
    previous = {name: os.environ.get(name) for name in values}
    try:
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield importlib.reload(corpus_module)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        importlib.reload(corpus_module)


def test_without_the_variable_it_looks_in_samples() -> None:
    with _environment(EXM_CORPUS=None) as module:
        assert module.CORPUS == os.path.join(ROOT, "samples")
    # And `samples/` is a folder git refuses to commit, so the default
    # can never turn into game data in the repository.
    with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
        assert "samples/" in fh.read()


def test_the_variable_wins() -> None:
    with tempfile.TemporaryDirectory() as folder:
        with _environment(EXM_CORPUS=folder) as module:
            assert module.CORPUS == folder
            assert module.corpus("world.xml") == os.path.join(folder, "world.xml")
            assert module.has_corpus("world.xml") is False
            open(os.path.join(folder, "world.xml"), "w").close()
            assert module.has_corpus("world.xml") is True
            assert module.has_corpus("world.xml", "missing.gam") is False


def test_a_missing_corpus_is_never_an_error() -> None:
    absent = os.path.join(tempfile.gettempdir(), "exm-corpus-that-is-not-there")
    with _environment(EXM_CORPUS=absent) as module:
        assert not os.path.isfile(module.corpus("anything.gam"))
        assert module.has_corpus("anything.gam") is False


def test_the_game_root_is_unset_by_default() -> None:
    """The tests that read an installed game skip unless pointed at one."""
    with _environment(EXM_GAME_ROOT=None) as module:
        assert module.GAME_ROOT == ""
        assert module.game_data("maps") == ""
    with _environment(EXM_GAME_ROOT=os.path.join("D:", os.sep, "Games", "ExM")) as module:
        assert module.game_data("maps", "r1m1") == os.path.join(
            "D:", os.sep, "Games", "ExM", "data", "maps", "r1m1"
        )


#: Paths that belong to one person's machine. A test addressing the
#: corpus this way is dead for everyone else and says nothing about
#: what it actually needs. ``/home/someone/model.gam`` as *test input*
#: is a different thing — that is the case being tested.
_MACHINE_PATHS = ("/mnt/user-data", "C:\\Users\\", "AppData\\Roaming",
                  "K:\\ex machine")


def test_no_test_addresses_the_corpus_by_a_local_path() -> None:
    for folder in (TESTS, ROOT):
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                text = fh.read()
            if name == os.path.basename(__file__):
                continue
            for machine_path in _MACHINE_PATHS:
                assert machine_path not in text, (name, machine_path)
