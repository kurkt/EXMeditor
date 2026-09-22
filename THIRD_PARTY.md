# Third-party software

EXMeditor itself is © 2026 Kurkt, licensed GPL-3.0-or-later (see
`LICENSE`). Every component below is either provided by the host
(Blender, Python) or installed separately by the user; **none is
distributed with EXMeditor**, and each licence is GPL-compatible.

EXMeditor **bundles no third-party code**. Its shipped source has no
bundled third-party Python dependencies: the modules in the archive
(`__init__.py`, `addon/`, `blender_io/`, `core/`, `formats/`, `utils/`)
import only Python's standard library and Blender's own Python API.
One thing is loaded beyond that, at runtime and only on demand: model
export (*Create Model from Mesh*) dynamically loads a **separately
installed HTAToolchain** — `blender_io/gam_export.py` finds the copy
in the user's Blender add-ons folder and loads its parser with
`importlib`. It is a runtime dependency, not a bundled one, and the
add-on imports and registers without it. There is no
`requirements.txt` because there is nothing to require.

Everything the add-on runs on or talks to is listed here with its
licence, as measured on 2026-09-20 by parsing every `import` in the
tree (`ast`, so imports inside functions and `try` blocks count) and
classifying each module against `sys.stdlib_module_names`. The test
`tests/test_release_polish.py::test_shipped_code_imports_only_stdlib_and_blender`
re-runs that measurement; a new dependency fails it until this file is
updated.

## Runtime — provided by the host, not distributed with EXMeditor

| Component | How EXMeditor uses it | Licence | GPL-compatible |
| --- | --- | --- | --- |
| **Blender 3.6** (also Goo Engine builds of 3.6) — `bpy`, `bmesh`, `mathutils`, `addon_utils` | Host application and API. Every operator, panel and bridge module. | GNU GPL v2 or later (blender.org/about/license) | Yes. This is also why EXMeditor itself must be published under a GPL-compatible licence: Blender states that scripts using its Python API, "if published", must be "shared under a GPL compliant license". |
| **Python 3.10 standard library** (bundled with Blender): `__future__`, `abc`, `array`, `ast`, `collections`, `contextlib`, `csv`, `dataclasses`, `enum`, `functools`, `hashlib`, `importlib`, `json`, `logging`, `math`, `os`, `pathlib`, `re`, `shutil`, `struct`, `subprocess`, `sys`, `tempfile`, `threading`, `typing`, `uuid`, `xml` | Parsing, file I/O, logging, XML, background Blender launches. | Python Software Foundation License 2.0 (`license/Python.txt` in the Blender distribution) | Yes |
| **HTAToolchain** ≥ 3.5 — ThePlain (Alexander Fateev), <https://github.com/ThePlain/HTAToolchain> | Optional. `blender_io/gam_export.py` locates the copy installed in the user's Blender add-ons folder and drives its `.gam` writer and parser through `importlib` and its registered export operator. Needed only for writing models; import and map editing work without it. **Not bundled, and none of its code is copied into this project** (verified by the borrowing audit below). | MIT (`LICENSE` in that repository, © 2020 Alexander) | Yes |

## Development only — not in the install archive

These are used by the `reverse/` forensics scripts and one test
(`tests/test_terrain_forensics.py`). `build_release.py` leaves
`reverse/`, `tests/` and the research tools out of the archive;
nothing in the shipped add-on imports them.

| Package | Where | Licence | Availability |
| --- | --- | --- | --- |
| **numpy** | `reverse/inspect_binary.py`, `reverse/terrain_forensics.py`, `tests/test_terrain_forensics.py` | BSD-3-Clause (numpy's own `PKG-INFO`: "License: BSD") | Bundled with Blender 3.6 (1.23.5); any system Python with `pip install numpy` |
| **Pillow** (`PIL`) | `reverse/inspect_binary.py` only, to write probe images | MIT-CMU (`License-Expression` of Pillow 12.2.0) | Not bundled with Blender; `pip install pillow` in a system Python |

## Data

No game data is included. Ex Machina / Hard Truck Apocalypse assets
remain the property of their respective rights holders; `.gitignore`
refuses every game file type and `build_release.py` stops on one.
Test fixtures are short hand-written examples of the file *schemas*
(element and attribute names, a few prototype names) — facts needed
for interoperability, not copies of any shipped file.

## Borrowing audit (2026-09-20)

Beyond the import audit, the whole tree was compared token-by-token
(shingles of 12 exact tokens and of 24 tokens with identifiers
normalised, so renamed copies would show) against HTAToolchain, every
Python file shipped with Blender 3.6 (6 702 files: bundled add-ons,
`bl_ui`, `bl_operators`) and every add-on installed on the development
machine. The longest run in common with any of them was 22 windows —
Python and Blender idioms (`@property`/setter pairs, `from_pydata`,
identity-matrix literals, a quad-grid loop) — spread evenly across
dozens of unrelated add-ons. The DDS/DXT codec (`core/dds.py`), the
math primitives (`utils/math.py`) and the `.gam` reader
(`formats/exm/gam.py`) are original implementations documented by
their own measurements; `.gam` chunk numbers coincide with
HTAToolchain's because both read the same format.

## Trademarks

"Ex Machina", "Hard Truck Apocalypse" and "M3DEditor" are names of
products whose rights remain with their respective holders; they are
used here only to say what the tool works with. EXMeditor is an
unofficial community project, not affiliated with or endorsed by the
game's developers or publishers.
