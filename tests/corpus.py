# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where the tests look for real game files.

A handful of tests check the code against actual shipped data — a real
``world.xml``, a real ``.gam``, a real ``.dds``. Those files are the
game's and are never part of this repository, so each such test skips
when they are absent, and the whole suite passes without them.

To run them, point ``EXM_CORPUS`` at a folder holding the files named
in the tests, or drop the files into ``samples/`` in the repository
root (``.gitignore`` already refuses to commit them)::

    set EXM_CORPUS=K:\\exm_corpus
    python run_tests.py
"""

from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The folder the optional corpus lives in.
CORPUS = os.environ.get("EXM_CORPUS") or os.path.join(ROOT, "samples")


#: An installed copy of the game, for the few tests that check the code
#: against the real thing. Unset by default, and those tests skip.
GAME_ROOT = os.environ.get("EXM_GAME_ROOT", "")


def game_data(*parts: str) -> str:
    """Path inside an installed game's ``data`` folder, or "" if unset."""
    return os.path.join(GAME_ROOT, "data", *parts) if GAME_ROOT else ""


def corpus(name: str) -> str:
    """Path to a corpus file. It usually does not exist; callers check."""
    return os.path.join(CORPUS, name)


def has_corpus(*names: str) -> bool:
    """Whether every named corpus file is present."""
    return all(os.path.isfile(corpus(name)) for name in names)
