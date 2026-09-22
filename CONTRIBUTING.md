# Contributing

## Licence of contributions

EXMeditor is GPL-3.0-or-later (see `LICENSE`). By contributing you
agree that your contribution is licensed the same way — inbound equals
outbound; there is no contributor agreement beyond that. Every `.py`
file starts with a REUSE-style header — one or more copyright lines
naming whoever wrote it, then the project licence — and the test
suite refuses a file without one. A file you write names *you*:

```
# SPDX-FileCopyrightText: 2027 Your Name
# SPDX-License-Identifier: GPL-3.0-or-later
```

When you substantially change an existing file, add your line beneath
the ones already there; the licence line stays as it is.

## What must never be in the repository

**Game data.** Ex Machina / Hard Truck Apocalypse maps, models,
textures and configuration remain the property of their respective
rights holders. `.gitignore`
lists the file types; `build_release.py` refuses to package one; the
tests check the tree. When a format needs documenting, describe the
measurement (offsets, sizes, counts, the file it came from) in
`Editor_Experiment_Findings.md` — do not add the file.

**Code from elsewhere.** The add-on bundles nothing; its shipped
source imports only Python's standard library and Blender's API, and a
test fails on any other import. (HTAToolchain is a separately
installed runtime dependency, loaded dynamically for model export —
see `THIRD_PARTY.md`.) If a task really needs a third-party
package, say so in the pull request and update `THIRD_PARTY.md` with
its licence. Code copied from another project needs that project's
licence to permit it and its notice kept — and needs to be said.

**Files of the original M3DEditor** or scripts extracted from it.

## Working principles

Not style preferences; each was learned the expensive way.

1. **Never rewrite game data you do not understand.** Preserve unknown
   bytes and attributes; write only what Blender explicitly edited.
2. **Measure first, then code.** When something is wrong, extend the
   diagnostics until they show the cause; do not fix a guess.
3. **Mark what you know.** Findings are CONFIRMED, HYPOTHESIS, UNKNOWN
   or REFUTED, and say which. Roles are never inferred from file
   names, positions or statistics — only from direct observation.
4. **Every fix comes with the test that would have caught it.**

## Layout

- `core/`, `formats/`, `utils/` — pure Python; must import without
  `bpy` and are tested without Blender.
- `blender_io/` — the bridge to `bpy`; `addon/` — operators, panels,
  preferences.
- `addon/research/` — the measurement operators (censuses, forensics,
  coverage). Source repository only: `build_release.py` leaves the
  package and the `core`/`blender_io` modules only it uses out of the
  install archive, and `__init__` registers it only when present. A
  new research tool goes here and in `_MODULES` of its `__init__`; an
  everyday feature must not import from it or from the modules in
  `RESEARCH_ONLY_MODULES` — the archive is tested to be closed under
  import.
- `tests/fake_bpy.py` stands in for Blender. When a bug slips through
  a passing test, the fake is usually the reason: extend it to
  reproduce the real behaviour before fixing the code.
- `reverse/` — forensics scripts for the developer; not part of the
  add-on and not shipped.

## Running the tests

No pytest, no Blender. From the repository root:

```bash
python run_tests.py            # everything
python run_tests.py dynamic    # only modules whose name contains this
python run_tests.py -q         # failures and the summary only
```

The suite passes with nothing installed but Python. The handful of
tests that check against real game files skip unless `EXM_CORPUS`
points at a folder of them, or `EXM_GAME_ROOT` at an installed game;
those files are never committed (`tests/corpus.py`).

## Building a release

```bash
python build_release.py --out dist
```

Writes `dist/EXMeditor-<version>.zip` (version from `bl_info`),
unpacking to one `EXMeditor/` folder for Blender's *Install from
file*. The source repository and the install archive are different
things:

| | repository | install archive |
|---|---|---|
| add-on packages (`addon/`, `blender_io/`, `core/`, `formats/`, `utils/`) | yes | yes |
| `README.md`, `CHANGELOG.md`, `THIRD_PARTY.md`, `LICENSE` | yes | yes |
| `addon/research/` and the modules only it uses | yes | no |
| `tests/`, `reverse/`, `build_release.py` | yes | no |
| developer docs (`ARCHITECTURE`, `SDK_STATUS`, `TECHNICAL_CONTEXT`, findings, `ROADMAP`, this file) | yes | no |
| game data | never | refused — the build stops |

`tests/test_release_archive.py` builds the archive, checks that list,
and registers the add-on from the extracted archive without the
research package. Before tagging: bump `bl_info["version"]`, add the
entry to `CHANGELOG.md`, run the tests, build.
